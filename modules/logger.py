"""Logging centralise de l'Assistant.

Fichier tournant dans logs/assistant.log (1 Mo x 5 backups).
Niveau INFO par defaut, DEBUG si variable d'env ASSISTANT_DEBUG=1.
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "assistant.log")

_configured = False


def _ensure_dir():
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except Exception:
        pass


def setup_logging(level: int = None):
    """A appeler une seule fois au demarrage."""
    global _configured
    if _configured:
        return
    _ensure_dir()
    if level is None:
        level = logging.DEBUG if os.environ.get("ASSISTANT_DEBUG") == "1" else logging.INFO

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Nettoyage eventuel
    for h in list(root.handlers):
        root.removeHandler(h)

    try:
        fh = RotatingFileHandler(
            _LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(level)
        root.addHandler(fh)
    except Exception as e:
        print(f"[logger] Impossible d'ouvrir {_LOG_FILE} : {e}", file=sys.stderr)

    # Console (surtout pour DEBUG)
    try:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(logging.WARNING)  # print() remplace par logger.info() ; l'ecran reste propre
        root.addHandler(sh)
    except Exception:
        pass

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Raccourci pour recuperer un logger nomme."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
