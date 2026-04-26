"""Stockage des demandes de permissions en SQLite (remplace le JSON).

Pourquoi : `permissions_requests.json` atteignait 186 Ko, et chaque appel
a add_permission_request() forcait un parse + reecriture complet. SQLite
elimine ce probleme : INSERT/UPDATE en O(log n), index sur (status,
username), pas de race condition.

API publique (identique a l'ancienne, dans database.py) :
    add_permission_request(username, req_type, title, description, action_data=None) -> int
    resolve_permission_request(entry_id, status) -> dict
    get_pending_requests(username=None) -> list
    clear_resolved_requests()
    load_permission_requests() -> list           (legacy)
    save_permission_requests(requests: list)     (legacy)

Migration : au premier import, si la table est vide et que le JSON
legacy existe, on copie le contenu. Le JSON legacy est ensuite renomme
en .legacy.bak (jamais supprime, par securite).
"""
from __future__ import annotations
import os
import json
import sqlite3
from datetime import datetime

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE_DIR, "permissions.db")
LEGACY_JSON = os.path.join(_BASE_DIR, "permissions_requests.json")


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10.0)
    c.row_factory = sqlite3.Row
    # WAL : meilleure concurrence (lecteurs en parallele d'un ecrivain)
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return c


def _init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS permission_requests (
            id INTEGER PRIMARY KEY,
            username TEXT,
            type TEXT,
            title TEXT,
            description TEXT,
            action_data TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pr_status ON permission_requests(status);
        CREATE INDEX IF NOT EXISTS idx_pr_user   ON permission_requests(username);
        CREATE INDEX IF NOT EXISTS idx_pr_created ON permission_requests(created_at);
        """)
    _migrate_legacy_json_if_needed()


def _migrate_legacy_json_if_needed():
    """Si la table est vide et qu'un JSON existe a la racine, on importe."""
    try:
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM permission_requests").fetchone()[0]
        if n > 0:
            return
    except Exception as e:
        print(f"[permissions_db] init check : {e}")
        return
    if not os.path.exists(LEGACY_JSON):
        return
    try:
        with open(LEGACY_JSON, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except Exception as e:
        print(f"[permissions_db] lecture JSON legacy : {e}")
        return
    if not legacy:
        # JSON vide : on le renomme aussi pour eviter une re-tentative
        try:
            os.rename(LEGACY_JSON, LEGACY_JSON + ".legacy.bak")
        except Exception:
            pass
        return
    inserted = 0
    with _conn() as c:
        for r in legacy:
            try:
                c.execute(
                    """INSERT OR IGNORE INTO permission_requests
                       (id, username, type, title, description, action_data,
                        status, created_at, resolved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r.get("id"),
                        r.get("username", ""),
                        r.get("type", ""),
                        r.get("title", ""),
                        r.get("description", ""),
                        json.dumps(r.get("action_data") or {},
                                   ensure_ascii=False),
                        r.get("status", "pending"),
                        r.get("created_at", ""),
                        r.get("resolved_at"),
                    ),
                )
                inserted += 1
            except Exception as e:
                print(f"[permissions_db] ligne ignoree : {e}")
    # Backup et renommage du JSON legacy
    try:
        os.rename(LEGACY_JSON, LEGACY_JSON + ".legacy.bak")
    except Exception as e:
        print(f"[permissions_db] backup JSON : {e}")
    print(f"[permissions_db] migration JSON -> SQLite OK ({inserted} entrees)")


# Initialisation a l'import du module (idempotent).
_init_db()


# ===========================================================================
# Helpers
# ===========================================================================

def _row_to_dict(row) -> dict:
    """Convertit une row SQLite en dict, en deserialisant action_data."""
    if row is None:
        return None
    d = dict(row)
    try:
        d["action_data"] = (json.loads(d.get("action_data") or "")
                            if d.get("action_data") else {})
    except Exception:
        d["action_data"] = {}
    return d


# ===========================================================================
# API publique
# ===========================================================================

def add_permission_request(username: str, req_type: str, title: str,
                           description: str,
                           action_data: dict = None) -> int:
    """Cree une nouvelle demande de permission. Renvoie l'id."""
    entry_id = int(datetime.now().timestamp() * 1000)
    created = datetime.now().isoformat()
    payload = json.dumps(action_data or {}, ensure_ascii=False)
    # Petite boucle anti-collision sur l'id (timestamp ms peut se repeter
    # si 2 appels dans la meme milliseconde).
    for _ in range(5):
        try:
            with _conn() as c:
                c.execute(
                    """INSERT INTO permission_requests
                       (id, username, type, title, description, action_data,
                        status, created_at, resolved_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)""",
                    (entry_id, username, req_type, title, description,
                     payload, created),
                )
            return entry_id
        except sqlite3.IntegrityError:
            entry_id += 1
        except Exception as e:
            print(f"[permissions_db] add : {e}")
            return entry_id
    return entry_id


def resolve_permission_request(entry_id: int, status: str) -> dict:
    """Marque une demande comme resolved/rejected. Renvoie la requete."""
    resolved_at = datetime.now().isoformat()
    try:
        with _conn() as c:
            c.execute(
                "UPDATE permission_requests SET status=?, resolved_at=? "
                "WHERE id=?",
                (status, resolved_at, entry_id),
            )
            row = c.execute(
                "SELECT * FROM permission_requests WHERE id=?",
                (entry_id,)).fetchone()
        return _row_to_dict(row)
    except Exception as e:
        print(f"[permissions_db] resolve : {e}")
        return None


def get_pending_requests(username: str = None) -> list:
    """Renvoie les demandes pending (eventuellement filtrees par user)."""
    try:
        if username:
            q = ("SELECT * FROM permission_requests "
                 "WHERE status='pending' AND username=? "
                 "ORDER BY created_at DESC")
            args = (username,)
        else:
            q = ("SELECT * FROM permission_requests "
                 "WHERE status='pending' "
                 "ORDER BY created_at DESC")
            args = ()
        with _conn() as c:
            rows = c.execute(q, args).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        print(f"[permissions_db] get_pending : {e}")
        return []


def clear_resolved_requests(retention_days: int = 30) -> int:
    """Supprime les demandes non-pending plus vieilles que retention_days.
    Renvoie le nombre de lignes supprimees."""
    try:
        with _conn() as c:
            cur = c.execute(
                "DELETE FROM permission_requests "
                "WHERE status != 'pending' "
                "AND (resolved_at IS NULL OR resolved_at < date('now', ?))",
                (f"-{retention_days} days",),
            )
            return cur.rowcount or 0
    except Exception as e:
        print(f"[permissions_db] clear : {e}")
        return 0


# ===========================================================================
# API legacy (compat avec database.py historique)
# ===========================================================================

def load_permission_requests() -> list:
    """Renvoie TOUTES les demandes (pending + resolved). Pour compat."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM permission_requests "
                "ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        print(f"[permissions_db] load_all : {e}")
        return []


def save_permission_requests(requests: list):
    """Remplace integralement la table. Utilise par d'eventuels callers
    historiques (ex. rapports). Le pattern recommande reste add/resolve."""
    try:
        with _conn() as c:
            c.execute("DELETE FROM permission_requests")
            for r in requests or []:
                try:
                    c.execute(
                        """INSERT INTO permission_requests
                           (id, username, type, title, description,
                            action_data, status, created_at, resolved_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (r.get("id"),
                         r.get("username", ""),
                         r.get("type", ""),
                         r.get("title", ""),
                         r.get("description", ""),
                         json.dumps(r.get("action_data") or {},
                                    ensure_ascii=False),
                         r.get("status", "pending"),
                         r.get("created_at", ""),
                         r.get("resolved_at")),
                    )
                except Exception as e:
                    print(f"[permissions_db] save row : {e}")
    except Exception as e:
        print(f"[permissions_db] save_all : {e}")


# ===========================================================================
# Stats / debug
# ===========================================================================

def stats() -> dict:
    """Stats compactes : nb total, pending, resolved, rejected."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT status, COUNT(*) AS n FROM permission_requests "
                "GROUP BY status").fetchall()
        out = {"total": 0, "pending": 0, "approved": 0,
               "rejected": 0, "other": 0}
        for r in rows:
            n = int(r["n"])
            out["total"] += n
            if r["status"] in out:
                out[r["status"]] = n
            else:
                out["other"] += n
        return out
    except Exception as e:
        print(f"[permissions_db] stats : {e}")
        return {"total": 0, "pending": 0, "approved": 0,
                "rejected": 0, "other": 0}
