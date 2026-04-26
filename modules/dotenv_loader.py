"""Mini-loader .env (sans dependance externe).

Cherche un fichier `.env` a la racine du projet et copie ses variables dans
os.environ (sans ecraser celles deja definies par le shell).

Format supporte :
    KEY=valeur
    KEY="valeur avec espaces"
    # commentaire
    KEY='valeur'

Tout est tolere (lignes vides, commentaires en fin de ligne, etc.).

A appeler en TOUT PREMIER, AVANT tout module qui lit os.environ.
"""
import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_FILE = os.path.join(_BASE_DIR, ".env")


def load_dotenv(path: str = _ENV_FILE, override: bool = False) -> int:
    """Charge un fichier .env. Retourne le nombre de cles ajoutees."""
    if not os.path.exists(path):
        return 0
    added = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                # Strip quotes
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if not key:
                    continue
                if not override and key in os.environ:
                    continue
                os.environ[key] = val
                added += 1
    except Exception:
        pass
    return added
