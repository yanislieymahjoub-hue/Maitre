"""Chiffrement transparent au repos (Fernet, AES-128-CBC + HMAC).

Cible : memory_<user>.json et tout autre fichier sensible auquel on appliquera
encrypt_text/decrypt_text.

Format des fichiers chiffres : prefixe ASCII "ENC1:" + token Fernet (base64).
Si la lecture rencontre un fichier qui ne commence pas par ENC1:, il est traite
comme legacy non-chiffre — la prochaine ecriture le re-encryptera.

Si la lib `cryptography` ou keyring sont absentes, on retombe en mode "passthru"
(rien n'est chiffre, mais le code continue de fonctionner). Ainsi cette feature
n'introduit AUCUN risque de blocage.

Usage :
    text = read_text(path)        # decrypte si necessaire
    write_text(path, text)        # ecrit chiffre si possible
"""
from __future__ import annotations
import os
import base64
import hashlib

from modules.logger import get_logger

_log = get_logger("crypto")

_MAGIC = b"ENC1:"
_KR_SERVICE = "AssistantAI.crypto"

try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore
    _HAS_CRYPTO = True
except Exception as _e:
    Fernet = None
    InvalidToken = Exception
    _HAS_CRYPTO = False
    _log.warning("cryptography indisponible (pip install cryptography) — fichiers en clair : %s", _e)

try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:
    keyring = None
    _HAS_KEYRING = False


def _get_or_create_master_key(username: str) -> bytes | None:
    """Recupere/cree une cle Fernet.

    Ordre de priorite en LECTURE :
      1. ASSISTANT_CRYPTO_KEY_<USER> (env)  — override per-user
      2. ASSISTANT_CRYPTO_KEY (env)         — override global
      3. Keyring (Credential Manager / Keychain)
      4. Fichier .crypto_key a la racine du projet (portable)

    En l'absence des 4, on en genere une nouvelle. Elle est ecrite dans
    keyring ; en dernier recours dans .crypto_key (qui voyage avec le
    projet — utile pour migrer).
    """
    if not _HAS_CRYPTO:
        return None

    # 1. Env vars (prioritaire pour tests / migration)
    env_user = os.environ.get(f"ASSISTANT_CRYPTO_KEY_{(username or '').upper()}")
    env_global = os.environ.get("ASSISTANT_CRYPTO_KEY")
    env_key = env_user or env_global
    if env_key:
        try:
            return env_key.encode("ascii") if isinstance(env_key, str) else env_key
        except Exception:
            pass

    key_b64 = None
    # 2. Keyring
    if _HAS_KEYRING:
        try:
            key_b64 = keyring.get_password(_KR_SERVICE, username)
        except Exception as e:
            _log.warning("keyring lecture cle echouee : %s", e)
            key_b64 = None
    # 3. Fichier local (.crypto_key)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key_file = os.path.join(base_dir, ".crypto_key")
    if not key_b64 and os.path.exists(key_file):
        try:
            with open(key_file, "rb") as f:
                key_b64 = f.read().decode("ascii").strip()
        except Exception as e:
            _log.warning("Lecture %s echouee : %s", key_file, e)
            key_b64 = None

    if not key_b64:
        # Generation
        new_key = Fernet.generate_key()  # bytes
        key_b64 = new_key.decode("ascii")
        # Ecriture : keyring si dispo, sinon fichier local
        ok = False
        if _HAS_KEYRING:
            try:
                keyring.set_password(_KR_SERVICE, username, key_b64)
                ok = True
            except Exception as e:
                _log.warning("keyring ecriture cle echouee : %s", e)
        if not ok:
            try:
                with open(key_file, "wb") as f:
                    f.write(key_b64.encode("ascii"))
                try:
                    os.chmod(key_file, 0o600)
                except Exception:
                    pass
            except Exception as e:
                _log.error("Impossible de stocker la cle (%s). Chiffrement desactive.", e)
                return None
        _log.info("Nouvelle cle de chiffrement generee. "
                  "Pour migrer, exporter ASSISTANT_CRYPTO_KEY ou copier .crypto_key.")
    try:
        return key_b64.encode("ascii") if isinstance(key_b64, str) else key_b64
    except Exception:
        return None


def _fernet_for(username: str):
    if not _HAS_CRYPTO:
        return None
    key = _get_or_create_master_key(username)
    if not key:
        return None
    try:
        return Fernet(key)
    except Exception as e:
        _log.error("Fernet init echoue : %s", e)
        return None


def encrypt_text(text: str, username: str) -> bytes:
    """Renvoie les bytes a ecrire sur disque (chiffres si possible)."""
    f = _fernet_for(username)
    if f is None:
        return text.encode("utf-8")
    try:
        token = f.encrypt(text.encode("utf-8"))
        return _MAGIC + token
    except Exception as e:
        _log.warning("Chiffrement echoue : %s. Ecriture en clair.", e)
        return text.encode("utf-8")


def decrypt_text(blob: bytes, username: str) -> str:
    """Renvoie le contenu en clair (decrypte si chiffre, sinon tel quel)."""
    if not blob:
        return ""
    if not blob.startswith(_MAGIC):
        # Legacy : pas chiffre
        try:
            return blob.decode("utf-8")
        except Exception:
            return blob.decode("utf-8", errors="ignore")
    if not _HAS_CRYPTO:
        _log.warning("Fichier chiffre mais lib cryptography manquante — donnees illisibles.")
        return ""
    f = _fernet_for(username)
    if f is None:
        return ""
    try:
        plain = f.decrypt(blob[len(_MAGIC):])
        return plain.decode("utf-8")
    except InvalidToken:
        _log.error("Token Fernet invalide (cle differente ?) — donnees perdues.")
        return ""
    except Exception as e:
        _log.error("Dechiffrement echoue : %s", e)
        return ""


def read_text(path: str, username: str) -> str:
    """Charge un fichier (chiffre ou non). Renvoie '' si absent."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as fh:
            return decrypt_text(fh.read(), username)
    except Exception as e:
        _log.error("Lecture %s echouee : %s", path, e)
        return ""


def write_text(path: str, text: str, username: str) -> bool:
    """Ecrit un fichier (chiffre si possible)."""
    blob = encrypt_text(text, username)
    try:
        with open(path, "wb") as fh:
            fh.write(blob)
        return True
    except Exception as e:
        _log.error("Ecriture %s echouee : %s", path, e)
        return False
