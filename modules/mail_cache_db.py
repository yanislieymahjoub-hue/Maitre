"""Cache des resumes de mails en SQLite (remplace mail_summaries_<user>.json).

Pourquoi : `mail_summaries_<user>.json` etait parse + reecrit en bloc a
chaque fetch d'inbox (65 Ko a chaque tour). Avec SQLite :
- Lookup par UID en O(log n)
- Insertions incrementales (1 ligne par mail au lieu de tout reecrire)
- Multi-user clair (un schema, une colonne username)
- Cap des 5000 mails plus anciens propre via SQL.

API publique (compatible avec l'ancien _load_mail_cache / _save_mail_cache) :
    load_cache(username) -> dict          # {uid: mail_data}
    save_cache(username, cache)           # remplace tout
    get_one(username, uid) -> dict|None
    set_one(username, uid, data)
    remove(username, uid) -> bool
    trim_oldest(username, keep=5000)
    stats(username) -> dict

Migration : a la premiere lecture pour un user, si le JSON legacy existe
et que la table est vide pour ce user, on aspire le contenu.
"""
from __future__ import annotations
import os
import json
import sqlite3
from datetime import datetime

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE_DIR, "mail_cache.db")


def _legacy_json_path(username: str) -> str:
    return os.path.join(_BASE_DIR, f"mail_summaries_{username}.json")


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return c


def _init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mail_cache (
            username TEXT NOT NULL,
            uid TEXT NOT NULL,
            payload TEXT,
            cached_at TEXT,
            PRIMARY KEY (username, uid)
        );
        CREATE INDEX IF NOT EXISTS idx_mc_user ON mail_cache(username);
        CREATE INDEX IF NOT EXISTS idx_mc_user_date
            ON mail_cache(username, cached_at);
        """)


_init_db()


# ===========================================================================
# Migration auto par utilisateur
# ===========================================================================

# Pour eviter de retenter la migration a chaque load_cache, on memorise les
# users deja "checkes" dans un set en memoire.
_migrated_users = set()


def _migrate_legacy_if_needed(username: str):
    """Si le JSON legacy existe et que la table est vide pour cet user,
    on aspire le contenu une fois pour toutes."""
    if username in _migrated_users:
        return
    _migrated_users.add(username)  # set tot pour eviter les boucles
    legacy = _legacy_json_path(username)
    if not os.path.exists(legacy):
        return
    try:
        with _conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM mail_cache WHERE username=?",
                (username,)).fetchone()[0]
        if n > 0:
            # Table deja peuplee pour cet user : on ne re-importe pas.
            # On peut quand meme renommer le JSON s'il traine encore.
            try:
                os.rename(legacy, legacy + ".legacy.bak")
            except Exception:
                pass
            return
    except Exception as e:
        print(f"[mail_cache_db] check : {e}")
        return
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
    except Exception as e:
        print(f"[mail_cache_db] lecture JSON legacy : {e}")
        return
    if not data:
        try:
            os.rename(legacy, legacy + ".legacy.bak")
        except Exception:
            pass
        return
    inserted = 0
    now = datetime.now().isoformat()
    with _conn() as c:
        for uid, payload in data.items():
            try:
                c.execute(
                    """INSERT OR IGNORE INTO mail_cache
                       (username, uid, payload, cached_at)
                       VALUES (?, ?, ?, ?)""",
                    (username, str(uid),
                     json.dumps(payload or {}, ensure_ascii=False),
                     now))
                inserted += 1
            except Exception as e:
                print(f"[mail_cache_db] ligne ignoree : {e}")
    try:
        os.rename(legacy, legacy + ".legacy.bak")
    except Exception as e:
        print(f"[mail_cache_db] backup JSON : {e}")
    print(f"[mail_cache_db] migration {username} : {inserted} mails -> SQLite")


# ===========================================================================
# API publique : compat dict { uid: mail_data }
# ===========================================================================

def load_cache(username: str) -> dict:
    """Renvoie le cache complet sous forme de dict {uid: mail_data}."""
    _migrate_legacy_if_needed(username)
    out = {}
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT uid, payload FROM mail_cache WHERE username=?",
                (username,)).fetchall()
        for r in rows:
            try:
                out[r["uid"]] = (json.loads(r["payload"])
                                 if r["payload"] else {})
            except Exception:
                out[r["uid"]] = {}
    except Exception as e:
        print(f"[mail_cache_db] load_cache : {e}")
    return out


def save_cache(username: str, cache: dict):
    """Remplace integralement le cache de cet user. Cap 5000 entrees
    (les plus anciennes virees, comme l'ancienne logique)."""
    if not isinstance(cache, dict):
        return
    # Cap 5000 — on garde les 5000 derniers UIDs (ordre d'insertion en
    # Python 3.7+, conserve par dict).
    if len(cache) > 5000:
        keys = list(cache.keys())
        cache = {k: cache[k] for k in keys[-5000:]}
    now = datetime.now().isoformat()
    try:
        with _conn() as c:
            # Approche transactionnelle : on supprime tout pour l'user
            # puis on ré-insère. Plus simple que diff.
            c.execute("DELETE FROM mail_cache WHERE username=?", (username,))
            for uid, payload in cache.items():
                c.execute(
                    """INSERT INTO mail_cache
                       (username, uid, payload, cached_at)
                       VALUES (?, ?, ?, ?)""",
                    (username, str(uid),
                     json.dumps(payload or {}, ensure_ascii=False), now))
    except Exception as e:
        print(f"[mail_cache_db] save_cache : {e}")


def get_one(username: str, uid: str) -> dict:
    """Lookup d'un mail par uid. None si absent."""
    _migrate_legacy_if_needed(username)
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT payload FROM mail_cache WHERE username=? AND uid=?",
                (username, str(uid))).fetchone()
        if not row:
            return None
        return json.loads(row["payload"]) if row["payload"] else {}
    except Exception as e:
        print(f"[mail_cache_db] get_one : {e}")
        return None


def set_one(username: str, uid: str, data: dict):
    """Insere ou remplace un mail. Beaucoup plus rapide que save_cache."""
    now = datetime.now().isoformat()
    try:
        with _conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO mail_cache
                   (username, uid, payload, cached_at)
                   VALUES (?, ?, ?, ?)""",
                (username, str(uid),
                 json.dumps(data or {}, ensure_ascii=False), now))
    except Exception as e:
        print(f"[mail_cache_db] set_one : {e}")


def remove(username: str, uid: str) -> bool:
    try:
        with _conn() as c:
            cur = c.execute(
                "DELETE FROM mail_cache WHERE username=? AND uid=?",
                (username, str(uid)))
            return (cur.rowcount or 0) > 0
    except Exception as e:
        print(f"[mail_cache_db] remove : {e}")
        return False


def trim_oldest(username: str, keep: int = 5000) -> int:
    """Garde les `keep` plus recents pour cet user. Renvoie le nombre
    de lignes supprimees."""
    if keep <= 0:
        return 0
    try:
        with _conn() as c:
            cur = c.execute(
                """DELETE FROM mail_cache
                   WHERE username = ?
                     AND uid NOT IN (
                       SELECT uid FROM mail_cache
                       WHERE username = ?
                       ORDER BY cached_at DESC
                       LIMIT ?
                     )""",
                (username, username, keep))
            return cur.rowcount or 0
    except Exception as e:
        print(f"[mail_cache_db] trim : {e}")
        return 0


def stats(username: str = None) -> dict:
    """Stats globales ou par user."""
    try:
        with _conn() as c:
            if username:
                n = c.execute(
                    "SELECT COUNT(*) FROM mail_cache WHERE username=?",
                    (username,)).fetchone()[0]
                return {"username": username, "count": int(n or 0)}
            rows = c.execute(
                "SELECT username, COUNT(*) AS n FROM mail_cache "
                "GROUP BY username").fetchall()
            return {r["username"]: int(r["n"]) for r in rows}
    except Exception as e:
        print(f"[mail_cache_db] stats : {e}")
        return {}
