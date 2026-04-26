"""Sauvegarde automatique des donnees critiques.

Cree un zip horodate dans backups/ avec :
- users_db.json
- experience_log.json
- permissions_requests.json
- memory_*.json, agenda_*.json
- mail_summaries_*.json, mail_tracking_*.json, triaged_mails_*.json

Garde au maximum 7 zips (les plus anciens sont supprimes).
Ne bloque JAMAIS le demarrage : toutes les erreurs sont swallowed.
"""
import os
import glob
import zipfile
from datetime import datetime

from modules.logger import get_logger

_log = get_logger("backup")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKUP_DIR = os.path.join(_BASE_DIR, "backups")
_MAX_BACKUPS = 7

# Fichiers fixes (ajoutes seulement s'ils existent)
_FIXED_FILES = [
    "users_db.json",
    "experience_log.json",
    "permissions_requests.json",
    "neural_memory.json",
]

# Motifs glob relatifs a _BASE_DIR
_PATTERNS = [
    "memory_*.json",
    "agenda_*.json",
    "mail_summaries_*.json",
    "mail_tracking_*.json",
    "triaged_mails_*.json",
]


def _collect_files():
    files = []
    for name in _FIXED_FILES:
        p = os.path.join(_BASE_DIR, name)
        if os.path.exists(p):
            files.append(p)
    for pat in _PATTERNS:
        for p in glob.glob(os.path.join(_BASE_DIR, pat)):
            if os.path.exists(p):
                files.append(p)
    return files


def _prune_old():
    try:
        zips = sorted(glob.glob(os.path.join(_BACKUP_DIR, "backup_*.zip")))
        while len(zips) > _MAX_BACKUPS:
            old = zips.pop(0)
            try:
                os.remove(old)
                _log.info("Backup supprime : %s", os.path.basename(old))
            except Exception as e:
                _log.warning("Echec suppression backup %s : %s", old, e)
    except Exception as e:
        _log.warning("Echec pruning backups : %s", e)


def make_backup():
    """Cree un backup horodate et nettoie les anciens. Renvoie le chemin, ou None."""
    try:
        os.makedirs(_BACKUP_DIR, exist_ok=True)
    except Exception as e:
        _log.warning("Impossible de creer %s : %s", _BACKUP_DIR, e)
        return None

    files = _collect_files()
    if not files:
        _log.info("Aucun fichier a sauvegarder.")
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(_BACKUP_DIR, f"backup_{stamp}.zip")
    try:
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for f in files:
                try:
                    z.write(f, arcname=os.path.basename(f))
                except Exception as e:
                    _log.warning("Impossible d'ajouter %s : %s", f, e)
        _log.info("Backup cree : %s (%d fichiers)", os.path.basename(out_path), len(files))
    except Exception as e:
        _log.error("Echec creation backup %s : %s", out_path, e)
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            pass
        return None

    _prune_old()
    return out_path


def make_backup_async():
    """Lance make_backup dans un thread daemon pour ne pas bloquer."""
    import threading
    threading.Thread(target=make_backup, daemon=True).start()
