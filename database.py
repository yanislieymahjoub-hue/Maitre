import json
import hashlib
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "users_db.json")
EXPERIENCE_LOG = os.path.join(BASE_DIR, "experience_log.json")
PERMISSIONS_LOG = os.path.join(BASE_DIR, "permissions_requests.json")

# --- Stockage securise optionnel (keyring) ---------------------------------
# On s'appuie dessus quand il est dispo pour ne PLUS conserver les mots de
# passe d'application mail en clair dans users_db.json. Fallback transparent.
try:
    from modules.secure_creds import (
        get_app_password as _kr_get,
        set_app_password as _kr_set,
        has_keyring as _kr_available,
    )
except Exception:
    def _kr_get(_u): return None
    def _kr_set(_u, _p): return False
    def _kr_available(): return False

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def _load_db() -> dict:
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {}

def _save_db(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def create_user(username: str, password: str, permissions: list = None,
                role: str = "", business_role: str = "",
                fiscal_config: dict = None) -> bool:
    db = _load_db()
    if username in db:
        return False
    db[username] = {
        "password_hash": _hash_password(password),
        # "mdp" (password en clair) supprime : on ne conserve que le hash SHA-256.
        "permissions": permissions if permissions else [1],
        "email_config": None,
        "role": role,
        "business_role": business_role,
        # fiscal_config : dict {regime, tva_franchise, siret, iban, raison_sociale}
        # None = pas encore renseigne. Un wizard peut le proposer plus tard.
        "fiscal_config": fiscal_config or None,
        "created_at": datetime.now().isoformat(),
        "last_seen": None,
        "is_online": False
    }
    _save_db(db)
    return True

# ==========================================
#   REGIME FISCAL (utilise par modules/finance.py)
# ==========================================

# Codes acceptes pour le regime fiscal.
FISCAL_REGIMES = (
    "auto_entrepreneur_bic",   # vente / commerce (micro-BIC)
    "auto_entrepreneur_bnc",   # prestation de services (micro-BNC)
    "auto_entrepreneur_lib",   # liberal (micro-BNC libl)
    "sas",                     # societe par actions simplifiee (IS)
    "sarl",                    # SARL / EURL (IS)
    "bnc_reel",                # declaration controlee (liberal)
    "none",                    # pas de calcul fiscal demande
)


def get_fiscal_config(username: str) -> dict:
    """Renvoie le dict fiscal_config (regime, tva_franchise, siret, iban,
    raison_sociale) ou un dict vide si rien n'est defini."""
    db = _load_db()
    cfg = db.get(username, {}).get("fiscal_config")
    if not cfg or not isinstance(cfg, dict):
        return {}
    return cfg


def set_fiscal_config(username: str, regime: str = None,
                      tva_franchise: bool = None,
                      siret: str = None, iban: str = None,
                      raison_sociale: str = None) -> bool:
    """Met a jour le fiscal_config de maniere incrementale (None = pas de
    changement)."""
    db = _load_db()
    if username not in db:
        return False
    cfg = db[username].get("fiscal_config") or {}
    if regime is not None:
        if regime not in FISCAL_REGIMES:
            return False
        cfg["regime"] = regime
    if tva_franchise is not None:
        cfg["tva_franchise"] = bool(tva_franchise)
    if siret is not None:
        cfg["siret"] = siret.strip()
    if iban is not None:
        cfg["iban"] = iban.strip().replace(" ", "").upper()
    if raison_sociale is not None:
        cfg["raison_sociale"] = raison_sociale.strip()
    db[username]["fiscal_config"] = cfg
    _save_db(db)
    return True


def has_fiscal_config(username: str) -> bool:
    """True si l'utilisateur a deja choisi un regime fiscal."""
    cfg = get_fiscal_config(username)
    return bool(cfg.get("regime"))


# ==========================================
#   PROFIL ETENDU (prenom, nom, civilite)
# ==========================================

def get_profile(username: str) -> dict:
    """Renvoie le dict profile (first_name, last_name, civilite, phone)."""
    db = _load_db()
    p = db.get(username, {}).get("profile")
    return p if isinstance(p, dict) else {}


def set_profile(username: str, **fields) -> bool:
    """Mise a jour partielle du profil utilisateur."""
    db = _load_db()
    if username not in db:
        return False
    p = db[username].get("profile") or {}
    for k, v in fields.items():
        if v is not None:
            p[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    db[username]["profile"] = p
    _save_db(db)
    return True


# ==========================================
#   PREFERENCES IA (ton, style, langue, longueur)
# ==========================================

# Valeurs autorisees pour chaque preference.
AI_TONES = ("neutre", "direct", "pedagogue", "decontracte", "formel")
AI_LENGTHS = ("court", "moyen", "long")
AI_LANGUAGES = ("fr", "en", "es", "de", "it")


def get_ai_preferences(username: str) -> dict:
    """Renvoie les prefs IA (ton, longueur, langue, tutoiement, technique)."""
    db = _load_db()
    p = db.get(username, {}).get("ai_preferences")
    if not isinstance(p, dict):
        return {}
    return p


def set_ai_preferences(username: str, ton: str = None,
                       longueur: str = None, langue: str = None,
                       tutoiement: bool = None,
                       niveau_technique: str = None) -> bool:
    """Mise a jour partielle des prefs IA (None = inchange)."""
    db = _load_db()
    if username not in db:
        return False
    p = db[username].get("ai_preferences") or {}
    if ton is not None and ton in AI_TONES:
        p["ton"] = ton
    if longueur is not None and longueur in AI_LENGTHS:
        p["longueur"] = longueur
    if langue is not None and langue in AI_LANGUAGES:
        p["langue"] = langue
    if tutoiement is not None:
        p["tutoiement"] = bool(tutoiement)
    if niveau_technique is not None:
        p["niveau_technique"] = niveau_technique
    db[username]["ai_preferences"] = p
    _save_db(db)
    return True


def has_ai_preferences(username: str) -> bool:
    return bool(get_ai_preferences(username).get("ton"))


# ==========================================
#   CONFIGURATION BROKER (Actions)
# ==========================================

# Brokers supportes (cle = code stocke en DB)
BROKERS = ("alpaca_paper", "alpaca_live", "binance", "none")


def get_broker_config(username: str) -> dict:
    """Renvoie {broker, mode, max_order_eur, max_orders_day}.
    Les CLES API ne sont JAMAIS dans le users_db.json — uniquement dans le
    keyring ou les variables d'environnement (cf. modules/actions.py)."""
    db = _load_db()
    cfg = db.get(username, {}).get("broker_config")
    return cfg if isinstance(cfg, dict) else {}


def set_broker_config(username: str, broker: str = None,
                      max_order_eur: float = None,
                      max_orders_day: int = None) -> bool:
    db = _load_db()
    if username not in db:
        return False
    cfg = db[username].get("broker_config") or {}
    if broker is not None:
        if broker not in BROKERS:
            return False
        cfg["broker"] = broker
    if max_order_eur is not None:
        try:
            cfg["max_order_eur"] = float(max_order_eur)
        except Exception:
            pass
    if max_orders_day is not None:
        try:
            cfg["max_orders_day"] = int(max_orders_day)
        except Exception:
            pass
    db[username]["broker_config"] = cfg
    _save_db(db)
    return True

def verify_user(username: str, password: str) -> bool:
    db = _load_db()
    if username not in db:
        return False
    return db[username]["password_hash"] == _hash_password(password)

def get_email_config(username):
    db = _load_db()
    if username not in db:
        return None
    cfg = db[username].get("email_config")
    if not cfg:
        return None
    email = cfg.get("email")
    stored_pwd = cfg.get("app_password") or ""

    # Priorite au keyring : s'il contient une valeur, on l'utilise.
    kr_pwd = _kr_get(username) if _kr_available() else None
    if kr_pwd:
        # Si le JSON contient encore la copie en clair, on la purge
        # (migration en douceur au premier chargement).
        if stored_pwd:
            try:
                db[username]["email_config"]["app_password"] = ""
                _save_db(db)
            except Exception:
                pass
        return {"email": email, "app_password": kr_pwd}

    # Pas dans le keyring mais present en JSON : on le migre vers le keyring
    # puis on vide le JSON. Si keyring indisponible, on garde le comportement legacy.
    if stored_pwd and _kr_available():
        if _kr_set(username, stored_pwd):
            try:
                db[username]["email_config"]["app_password"] = ""
                _save_db(db)
            except Exception:
                pass
            return {"email": email, "app_password": stored_pwd}

    # Fallback pur JSON (keyring indisponible)
    return {"email": email, "app_password": stored_pwd}

def set_email_config(username, email, app_password):
    db = _load_db()
    if username not in db:
        return False
    # Keyring si dispo : on ne stocke PAS le mot de passe dans users_db.json.
    if _kr_available() and _kr_set(username, app_password or ""):
        db[username]["email_config"] = {"email": email, "app_password": ""}
    else:
        db[username]["email_config"] = {"email": email, "app_password": app_password}
    _save_db(db)
    return True

def user_exists(username: str) -> bool:
    return username in _load_db()

def get_permissions(username: str) -> list:
    return _load_db().get(username, {}).get("permissions", [1])

def get_user_level(username: str) -> int:
    perms = get_permissions(username)
    return perms[0] if perms else 1

def get_user_role(username: str) -> str:
    return _load_db().get(username, {}).get("role", "")

def get_user_business_role(username: str) -> str:
    return _load_db().get(username, {}).get("business_role", "")

def set_user_role(username: str, role: str, business_role: str = "") -> bool:
    db = _load_db()
    if username in db:
        db[username]["role"] = role
        db[username]["business_role"] = business_role
        _save_db(db)
        return True
    return False

def set_user_online(username: str, status: bool):
    db = _load_db()
    if username in db:
        db[username]["is_online"] = status
        db[username]["last_seen"] = datetime.now().isoformat()
        _save_db(db)

def get_online_users() -> list:
    db = _load_db()
    return [u for u, d in db.items() if d.get("is_online", False)]

def get_all_users_info() -> dict:
    db = _load_db()
    result = {}
    for username, data in db.items():
        result[username] = {
            "permissions": data.get("permissions", [1]),
            "role": data.get("role", ""),
            "business_role": data.get("business_role", ""),
            "is_online": data.get("is_online", False),
            "last_seen": data.get("last_seen", None),
            "has_email": data.get("email_config") is not None
        }
    return result

def list_users() -> list:
    return list(_load_db().keys())

def get_user_data(username: str) -> dict:
    return _load_db().get(username, {})

def update_user_permission(username: str, level: int) -> bool:
    db = _load_db()
    if username in db:
        db[username]["permissions"] = [level]
        _save_db(db)
        return True
    return False

# ==========================================
#   EXPERIENCE LOG
# ==========================================

def load_experience_log() -> list:
    if not os.path.exists(EXPERIENCE_LOG):
        return []
    with open(EXPERIENCE_LOG, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return []

def save_experience_log(log: list):
    with open(EXPERIENCE_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=4, ensure_ascii=False)

def add_experience_entry(username: str, action_type: str, details: str, outcome: str = "pending", score: float = 0.0):
    log = load_experience_log()
    log.append({
        "id": int(datetime.now().timestamp() * 1000),
        "username": username,
        "action_type": action_type,
        "details": details,
        "outcome": outcome,
        "score": score,
        "timestamp": datetime.now().isoformat(),
        "reviewed": False,
        "correction": None
    })
    if len(log) > 500:
        log = log[-500:]
    save_experience_log(log)

def update_experience_outcome(entry_id: int, outcome: str, score: float, correction: str = None):
    log = load_experience_log()
    for entry in log:
        if entry.get("id") == entry_id:
            entry["outcome"] = outcome
            entry["score"] = score
            entry["reviewed"] = True
            if correction:
                entry["correction"] = correction
            break
    save_experience_log(log)

# ==========================================
#   PERMISSIONS MODULE
# ==========================================

def load_permission_requests() -> list:
    if not os.path.exists(PERMISSIONS_LOG):
        return []
    with open(PERMISSIONS_LOG, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return []

def save_permission_requests(requests: list):
    with open(PERMISSIONS_LOG, "w", encoding="utf-8") as f:
        json.dump(requests, f, indent=4, ensure_ascii=False)

def add_permission_request(username: str, req_type: str, title: str, description: str, action_data: dict = None) -> int:
    requests = load_permission_requests()
    entry_id = int(datetime.now().timestamp() * 1000)
    requests.append({
        "id": entry_id,
        "username": username,
        "type": req_type,
        "title": title,
        "description": description,
        "action_data": action_data or {},
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "resolved_at": None
    })
    save_permission_requests(requests)
    return entry_id

def resolve_permission_request(entry_id: int, status: str) -> dict:
    requests = load_permission_requests()
    resolved = None
    for req in requests:
        if req.get("id") == entry_id:
            req["status"] = status
            req["resolved_at"] = datetime.now().isoformat()
            resolved = req
            break
    save_permission_requests(requests)
    return resolved

def get_pending_requests(username: str = None) -> list:
    requests = load_permission_requests()
    pending = [r for r in requests if r["status"] == "pending"]
    if username:
        pending = [r for r in pending if r["username"] == username]
    return pending

def clear_resolved_requests():
    requests = load_permission_requests()
    save_permission_requests([r for r in requests if r["status"] == "pending"])
