"""Extraction et classement des pieces jointes d'emails.

Sauvegarde les PJ sous PJ/<Categorie>/<expediteur>/<date>_<filename>.

La categorie est choisie par l'IA parmi une whitelist (voir CATEGORIES) ;
en cas d'echec (no API, 429, exception) on retombe sur une classification
par extension (Photos / Administration / Autres).

Modes :
- save_attachments(msg, ...) : ecrit directement dans la categorie finale
  (utilise par le mode "Lacher Prise" : aucune validation requise).
- save_pending(msg, uid, ...) : ecrit dans PJ/_pending/<uid>/, en attendant
  validation via la boite Permissions.
- finalize_pending(...) : appele apres approbation d'une permission
  attachment_extract -> classifie et deplace dans PJ/<Categorie>/<sender>/.
- discard_pending(...) : appele apres rejet d'une permission -> supprime.

Le dossier racine est :
    1. $ASSISTANT_ATTACHMENTS_DIR si defini
    2. <projet>/PJ/ par defaut
"""
from __future__ import annotations
import os
import re
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime

from modules.logger import get_logger

_log = get_logger("attachment_extractor")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_ROOT = os.path.join(_BASE_DIR, "PJ")

# Categories autorisees (whitelist anti-hallucination LLM).
CATEGORIES = (
    "Factures",
    "Contrats",
    "Devis",
    "Photos",
    "Identite",
    "Administration",
    "Travail",
    "Autres",
)

# Caracteres autorises dans les noms de fichiers et de dossiers.
_SAFE_RE = re.compile(r"[^A-Za-z0-9._\- ]+")
_MAX_NAME = 80


# ---------------------------------------------------------------------------
# Utilitaires chemins / noms
# ---------------------------------------------------------------------------

def get_attachments_root() -> str:
    """Renvoie le dossier racine ou stocker les PJ. Configurable via env."""
    env = os.environ.get("ASSISTANT_ATTACHMENTS_DIR")
    root = env.strip() if env else _DEFAULT_ROOT
    try:
        os.makedirs(root, exist_ok=True)
    except Exception as e:
        _log.error("Impossible de creer %s : %s", root, e)
    return root


def _sanitize(name: str) -> str:
    """Nettoie un nom pour qu'il soit safe en filesystem (no path traversal)."""
    if not name:
        return "inconnu"
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    name = _SAFE_RE.sub("_", name)
    name = name.strip(" ._")
    if not name:
        name = "inconnu"
    return name[:_MAX_NAME]


def _sender_dir_name(sender: str) -> str:
    """Sender ressemble a 'Toto <toto@x.com>'. On garde l'email comme cle."""
    if not sender:
        return "inconnu"
    m = re.search(r"<([^<>]+@[^<>]+)>", sender)
    if m:
        email_addr = m.group(1)
    else:
        m = re.search(r"([^\s<>]+@[^\s<>]+)", sender)
        email_addr = m.group(1) if m else sender
    return _sanitize(email_addr.lower())


def _decode_filename(filename: str) -> str:
    """Decode un filename MIME (=?utf-8?B?...?=)."""
    try:
        parts = decode_header(filename)
        out = []
        for part, enc in parts:
            if isinstance(part, bytes):
                out.append(part.decode(enc or "utf-8", errors="ignore"))
            else:
                out.append(part)
        return "".join(out)
    except Exception:
        return filename


def _date_prefix(date_str: str | None) -> str:
    """YYYY-MM-DD a partir d'un header Date IMAP, ou aujourd'hui en fallback."""
    try:
        if date_str:
            dt = parsedate_to_datetime(date_str)
            if dt:
                return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d")


def _unique_target(directory: str, base_name: str) -> str:
    """Renvoie un chemin unique : si <base> existe, ajoute _1, _2, ..."""
    target = os.path.join(directory, base_name)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(base_name)
    i = 1
    while True:
        candidate = os.path.join(directory, f"{stem}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_by_extension(filename: str) -> str:
    """Fallback robuste si l'IA n'est pas dispo."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp"):
        return "Photos"
    if ext in (".pdf", ".docx", ".doc", ".odt", ".rtf"):
        return "Administration"
    if ext in (".xlsx", ".xls", ".ods", ".csv"):
        return "Travail"
    return "Autres"


def _classify_attachment(filename: str, text_excerpt: str,
                         sender: str, subject: str) -> str:
    """Demande a l'IA de choisir une categorie parmi CATEGORIES.

    En cas d'erreur -> classification par extension (toujours une reponse).
    """
    try:
        from modules.config import URL, API_KEY, MODELE
        import requests as _rq
        prompt = (
            "Tu classes une piece jointe d'email dans UNE seule categorie parmi :\n"
            f"{', '.join(CATEGORIES)}.\n"
            "Reponds par UNIQUEMENT le nom exact de la categorie, rien d'autre.\n\n"
            f"Expediteur : {sender}\n"
            f"Sujet : {subject}\n"
            f"Nom du fichier : {filename}\n"
            f"Extrait du contenu : {(text_excerpt or '')[:600]}"
        )
        headers = {"Authorization": f"Bearer {API_KEY}",
                   "Content-Type": "application/json"}
        body = {
            "model": MODELE, "temperature": 0.0,
            "messages": [
                {"role": "system",
                 "content": "Tu classes des fichiers en categories. Reponse en un seul mot."},
                {"role": "user", "content": prompt},
            ],
        }
        r = _rq.post(URL, headers=headers, json=body, timeout=15)
        if r.status_code == 200:
            txt = (r.json()["choices"][0]["message"]["content"] or "").strip()
            for c in CATEGORIES:
                if c.lower() in txt.lower():
                    return c
        else:
            _log.info("Classif IA HTTP %s -> fallback", r.status_code)
    except Exception as e:
        _log.info("Classif IA exception (%s) -> fallback", e)
    return _classify_by_extension(filename)


def _extract_excerpt(payload: bytes, filename: str) -> str:
    """Extrait un court extrait texte pour aider la classification IA."""
    if not payload:
        return ""
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".pdf":
            try:
                import fitz
                with fitz.open(stream=payload, filetype="pdf") as doc:
                    text = ""
                    for page in doc:
                        text += page.get_text()
                        if len(text) > 800:
                            break
                return text[:800]
            except Exception:
                return ""
        if ext == ".docx":
            try:
                import docx
                import io
                d = docx.Document(io.BytesIO(payload))
                return "\n".join(p.text for p in d.paragraphs if p.text.strip())[:800]
            except Exception:
                return ""
        if ext in (".txt", ".md", ".csv", ".json", ".log"):
            try:
                return payload.decode("utf-8", errors="ignore")[:800]
            except Exception:
                return ""
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Walk + ecriture
# ---------------------------------------------------------------------------

def _walk_attachments(msg):
    """Itere sur les parts qui sont des PJ. Yield (filename, payload_bytes)."""
    if msg is None:
        return
    if not msg.is_multipart():
        return
    for part in msg.walk():
        cdisp = str(part.get("Content-Disposition", "")).lower()
        fname = part.get_filename()
        if not fname and "attachment" not in cdisp:
            continue
        if not fname:
            continue
        fname = _decode_filename(fname)
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not payload:
            continue
        yield fname, payload


def save_attachments(msg, sender, subject, date_str=None,
                     classify=True, root=None):
    """Sauve toutes les PJ d'un message dans PJ/<Categorie>/<sender>/.

    Renvoie une liste de descripteurs :
        [{"path": ..., "filename": ..., "category": ..., "size": ...}, ...]
    """
    saved = []
    root = root or get_attachments_root()
    sender_dir = _sender_dir_name(sender)
    date_pref = _date_prefix(date_str)

    for fname, payload in _walk_attachments(msg):
        clean_name = _sanitize(fname)
        if classify:
            excerpt = _extract_excerpt(payload, fname)
            category = _classify_attachment(clean_name, excerpt,
                                            sender or "", subject or "")
        else:
            category = _classify_by_extension(clean_name)

        cat_dir = os.path.join(root, _sanitize(category), sender_dir)
        try:
            os.makedirs(cat_dir, exist_ok=True)
        except Exception as e:
            _log.error("mkdir %s : %s", cat_dir, e)
            continue

        target = _unique_target(cat_dir, f"{date_pref}_{clean_name}")
        try:
            with open(target, "wb") as f:
                f.write(payload)
            saved.append({
                "path": target,
                "filename": clean_name,
                "category": category,
                "size": len(payload),
            })
            _log.info("PJ sauvegardee : %s (%s, %d octets)",
                      target, category, len(payload))
        except Exception as e:
            _log.error("Ecriture %s : %s", target, e)
    return saved


def save_pending(msg, uid, sender, subject, date_str=None, root=None) -> str:
    """Sauve les PJ dans PJ/_pending/<uid>/ en attendant validation.

    Renvoie le chemin du dossier (vide si aucune PJ ecrite).
    """
    root = root or get_attachments_root()
    pending_dir = os.path.join(root, "_pending", _sanitize(str(uid)))
    try:
        os.makedirs(pending_dir, exist_ok=True)
    except Exception as e:
        _log.error("mkdir pending %s : %s", pending_dir, e)
        return ""

    count = 0
    for fname, payload in _walk_attachments(msg):
        clean_name = _sanitize(fname)
        target = _unique_target(pending_dir, clean_name)
        try:
            with open(target, "wb") as f:
                f.write(payload)
            count += 1
        except Exception as e:
            _log.error("Pending %s : %s", target, e)
    if count == 0:
        # Cleanup si vide
        try:
            os.rmdir(pending_dir)
        except Exception:
            pass
        return ""
    return pending_dir


def finalize_pending(pending_dir: str, sender: str, subject: str,
                     date_str: str = "", root=None):
    """Re-classe puis deplace les fichiers d'un dossier _pending vers
    PJ/<Categorie>/<sender>/. Renvoie les descripteurs des fichiers
    deplaces."""
    if not pending_dir or not os.path.isdir(pending_dir):
        return []
    root = root or get_attachments_root()
    sender_dir = _sender_dir_name(sender)
    date_pref = _date_prefix(date_str)
    saved = []

    try:
        files = os.listdir(pending_dir)
    except Exception:
        files = []

    for fname in files:
        src = os.path.join(pending_dir, fname)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "rb") as f:
                payload = f.read()
        except Exception:
            payload = b""

        excerpt = _extract_excerpt(payload, fname) if payload else ""
        category = _classify_attachment(fname, excerpt,
                                        sender or "", subject or "")
        cat_dir = os.path.join(root, _sanitize(category), sender_dir)
        try:
            os.makedirs(cat_dir, exist_ok=True)
        except Exception as e:
            _log.error("mkdir %s : %s", cat_dir, e)
            continue

        target = _unique_target(cat_dir, f"{date_pref}_{fname}")
        try:
            os.replace(src, target)
            saved.append({
                "path": target,
                "filename": fname,
                "category": category,
                "size": len(payload),
            })
            _log.info("PJ finalisee : %s (%s)", target, category)
        except Exception as e:
            _log.error("Move %s -> %s : %s", src, target, e)

    # Cleanup si dossier _pending vide
    try:
        if not os.listdir(pending_dir):
            os.rmdir(pending_dir)
    except Exception:
        pass
    return saved


def discard_pending(pending_dir: str) -> bool:
    """Supprime un dossier _pending (rejet de la permission)."""
    if not pending_dir or not os.path.isdir(pending_dir):
        return False
    try:
        for fname in os.listdir(pending_dir):
            try:
                os.remove(os.path.join(pending_dir, fname))
            except Exception:
                pass
        os.rmdir(pending_dir)
        return True
    except Exception as e:
        _log.error("Discard %s : %s", pending_dir, e)
        return False
