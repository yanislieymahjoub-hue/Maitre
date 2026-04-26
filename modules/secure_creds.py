"""Stockage securise des mots de passe d'application mail.

Ordre de priorite a la LECTURE :
  1. Variable d'env ASSISTANT_EMAIL_PASSWORD (override total ; pratique pour
     le CI, Docker, tests, et la migration vers une autre machine).
  2. Variable d'env ASSISTANT_EMAIL_PASSWORD_<USER> (ex:
     ASSISTANT_EMAIL_PASSWORD_YALA) si plusieurs comptes coexistent.
  3. Keyring (Credential Manager Windows / GNOME Keyring / Mac Keychain).
  4. Fallback JSON-en-clair (legacy).

A l'ECRITURE : on prefere keyring si dispo, sinon JSON. On ne touche jamais
aux variables d'env (le shell est seul maitre).

But : pouvoir tester ici puis migrer ailleurs en copiant juste un .env, sans
rien recreer dans le keyring de la nouvelle machine.
"""
import os
from modules.logger import get_logger

_log = get_logger("secure_creds")

_SERVICE = "AssistantAI.email"

try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception as _e:
    keyring = None
    _HAS_KEYRING = False
    _log.warning("keyring indisponible (pip install keyring) — fallback JSON : %s", _e)


def has_keyring() -> bool:
    return _HAS_KEYRING


def _env_password(username: str):
    """Renvoie le mot de passe depuis l'env ou None."""
    # Variante per-user d'abord (ASSISTANT_EMAIL_PASSWORD_YALA)
    if username:
        v = os.environ.get(f"ASSISTANT_EMAIL_PASSWORD_{username.upper()}")
        if v:
            return v
    return os.environ.get("ASSISTANT_EMAIL_PASSWORD") or None


def get_app_password(username: str):
    # 1. Env vars (prioritaire — outil de migration/tests)
    env_pwd = _env_password(username)
    if env_pwd:
        return env_pwd
    # 2. Keyring
    if not _HAS_KEYRING:
        return None
    try:
        return keyring.get_password(_SERVICE, username)
    except Exception as e:
        _log.warning("keyring.get_password a echoue : %s", e)
        return None


def set_app_password(username: str, password: str) -> bool:
    # On ne re-ecrit jamais dans l'env (immutable depuis le shell). On
    # privilegie keyring ; la couche d'appel decide du fallback JSON.
    if not _HAS_KEYRING:
        return False
    try:
        keyring.set_password(_SERVICE, username, password or "")
        return True
    except Exception as e:
        _log.warning("keyring.set_password a echoue : %s", e)
        return False


def delete_app_password(username: str) -> bool:
    if not _HAS_KEYRING:
        return False
    try:
        keyring.delete_password(_SERVICE, username)
        return True
    except Exception:
        return False
