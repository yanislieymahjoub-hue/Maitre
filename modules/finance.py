"""Backend finance : factures, banque, fiscal, relances, compliance.

Stockage : SQLite (finance.db a la racine du projet) — plus solide qu'un
JSON pour les requetes par date / statut / montant.

Fonctionnalites :
- Modele Invoice (entrante / sortante) avec extraction LLM des champs cles
- Modele BankMovement (import CSV)
- Validation SIRET (Luhn 14 chiffres) + IBAN (mod-97)
- Detection de doublons (hash sender+date+montant)
- Calculs fiscaux par regime (auto-entrepreneur BIC/BNC/lib, SAS/SARL,
  BNC reel)
- Generation de relances client par LLM
- Rapprochement facture <-> mouvement bancaire (par montant + date)

Tout est conçu pour fonctionner offline (fallback) si Mistral / Ollama
n'est pas dispo : extension-based heuristics, regex, etc.
"""
from __future__ import annotations
import os
import re
import csv
import json
import sqlite3
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from typing import Optional

from modules.logger import get_logger

_log = get_logger("finance")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE_DIR, "finance.db")


# ===========================================================================
# Modele de donnees
# ===========================================================================

INVOICE_DIRECTIONS = ("in", "out")  # in = fournisseur ; out = client
INVOICE_STATUSES = (
    "pending",      # en attente de paiement
    "paid",         # payee
    "overdue",      # en retard
    "disputed",     # contestee
    "cancelled",    # annulee
)


@dataclass
class Invoice:
    """Representation d'une facture (entrante OU sortante)."""
    id: Optional[int] = None
    direction: str = "in"                # "in" ou "out"
    party: str = ""                      # nom du fournisseur OU du client
    party_email: str = ""
    party_siret: str = ""
    party_iban: str = ""
    invoice_number: str = ""
    date_emission: str = ""              # YYYY-MM-DD
    date_echeance: str = ""              # YYYY-MM-DD
    montant_ht: float = 0.0
    taux_tva: float = 20.0
    montant_tva: float = 0.0
    montant_ttc: float = 0.0
    currency: str = "EUR"
    statut: str = "pending"
    mode_paiement: str = ""              # "virement", "cb", "especes", ...
    notes: str = ""
    source: str = "manual"               # "mail", "manual", "import"
    source_mail_uid: str = ""
    file_path: str = ""                  # chemin vers le PDF source
    fingerprint: str = ""                # hash anti-doublon
    relance_count: int = 0
    last_relance_date: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class BankMovement:
    """Mouvement bancaire (issu d'un import CSV)."""
    id: Optional[int] = None
    date: str = ""           # YYYY-MM-DD
    libelle: str = ""
    montant: float = 0.0     # positif = credit ; negatif = debit
    sens: str = "credit"     # "credit" ou "debit"
    matched_invoice_id: Optional[int] = None
    source_csv: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ===========================================================================
# DB SQLite
# ===========================================================================

def _conn():
    """Renvoie une connexion SQLite (avec foreign_keys ON)."""
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA foreign_keys = ON")
    c.row_factory = sqlite3.Row
    return c


def _init_db():
    """Cree les tables si absentes. Idempotent."""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT NOT NULL,
            party TEXT,
            party_email TEXT,
            party_siret TEXT,
            party_iban TEXT,
            invoice_number TEXT,
            date_emission TEXT,
            date_echeance TEXT,
            montant_ht REAL DEFAULT 0,
            taux_tva REAL DEFAULT 20,
            montant_tva REAL DEFAULT 0,
            montant_ttc REAL DEFAULT 0,
            currency TEXT DEFAULT 'EUR',
            statut TEXT DEFAULT 'pending',
            mode_paiement TEXT,
            notes TEXT,
            source TEXT,
            source_mail_uid TEXT,
            file_path TEXT,
            fingerprint TEXT UNIQUE,
            relance_count INTEGER DEFAULT 0,
            last_relance_date TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_inv_direction ON invoices(direction);
        CREATE INDEX IF NOT EXISTS idx_inv_statut ON invoices(statut);
        CREATE INDEX IF NOT EXISTS idx_inv_party ON invoices(party);
        CREATE INDEX IF NOT EXISTS idx_inv_date ON invoices(date_emission);

        CREATE TABLE IF NOT EXISTS bank_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            libelle TEXT,
            montant REAL,
            sens TEXT,
            matched_invoice_id INTEGER,
            source_csv TEXT,
            created_at TEXT,
            FOREIGN KEY(matched_invoice_id) REFERENCES invoices(id)
                ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bk_date ON bank_movements(date);
        CREATE INDEX IF NOT EXISTS idx_bk_match ON bank_movements(matched_invoice_id);
        """)


_init_db()


def _row_to_invoice(row) -> Invoice:
    if row is None:
        return None
    d = dict(row)
    return Invoice(**d)


def _row_to_movement(row) -> BankMovement:
    if row is None:
        return None
    return BankMovement(**dict(row))


def _fingerprint_invoice(inv: Invoice) -> str:
    """Hash anti-doublon : direction + party + date_emission + montant_ttc."""
    raw = f"{inv.direction}|{(inv.party or '').lower().strip()}|" \
          f"{inv.date_emission}|{round(inv.montant_ttc, 2)}|" \
          f"{(inv.invoice_number or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def add_invoice(inv: Invoice) -> Optional[int]:
    """Ajoute une facture. Renvoie l'id ou None si doublon."""
    if not inv.fingerprint:
        inv.fingerprint = _fingerprint_invoice(inv)
    inv.updated_at = datetime.now().isoformat()
    try:
        with _conn() as c:
            cur = c.execute(
                """INSERT INTO invoices
                   (direction, party, party_email, party_siret, party_iban,
                    invoice_number, date_emission, date_echeance,
                    montant_ht, taux_tva, montant_tva, montant_ttc,
                    currency, statut, mode_paiement, notes, source,
                    source_mail_uid, file_path, fingerprint,
                    relance_count, last_relance_date,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?)""",
                (inv.direction, inv.party, inv.party_email, inv.party_siret,
                 inv.party_iban, inv.invoice_number, inv.date_emission,
                 inv.date_echeance, inv.montant_ht, inv.taux_tva,
                 inv.montant_tva, inv.montant_ttc, inv.currency, inv.statut,
                 inv.mode_paiement, inv.notes, inv.source,
                 inv.source_mail_uid, inv.file_path, inv.fingerprint,
                 inv.relance_count, inv.last_relance_date,
                 inv.created_at, inv.updated_at))
            return cur.lastrowid
    except sqlite3.IntegrityError:
        _log.info("Facture en doublon (fingerprint=%s)", inv.fingerprint)
        return None
    except Exception as e:
        _log.error("add_invoice : %s", e)
        return None


def update_invoice(invoice_id: int, **fields) -> bool:
    """Mise a jour partielle d'une facture."""
    if not fields:
        return True
    fields["updated_at"] = datetime.now().isoformat()
    keys = list(fields.keys())
    sets = ", ".join(f"{k}=?" for k in keys)
    vals = [fields[k] for k in keys] + [invoice_id]
    try:
        with _conn() as c:
            c.execute(f"UPDATE invoices SET {sets} WHERE id=?", vals)
        return True
    except Exception as e:
        _log.error("update_invoice : %s", e)
        return False


def get_invoice(invoice_id: int) -> Optional[Invoice]:
    with _conn() as c:
        row = c.execute("SELECT * FROM invoices WHERE id=?",
                        (invoice_id,)).fetchone()
        return _row_to_invoice(row)


def list_invoices(direction: str = None, statut: str = None,
                  limit: int = 200) -> list:
    q = "SELECT * FROM invoices WHERE 1=1"
    args = []
    if direction:
        q += " AND direction=?"
        args.append(direction)
    if statut:
        q += " AND statut=?"
        args.append(statut)
    q += " ORDER BY date_emission DESC, id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
        return [_row_to_invoice(r) for r in rows]


def list_overdue(reference_date: str = None) -> list:
    """Renvoie les factures sortantes (clients) impayees dont l'echeance
    est depassee."""
    if not reference_date:
        reference_date = date.today().isoformat()
    with _conn() as c:
        rows = c.execute(
            """SELECT * FROM invoices
               WHERE direction='out' AND statut IN ('pending','overdue')
                 AND date_echeance <> '' AND date_echeance < ?
               ORDER BY date_echeance ASC""",
            (reference_date,)).fetchall()
        return [_row_to_invoice(r) for r in rows]


def delete_invoice(invoice_id: int) -> bool:
    try:
        with _conn() as c:
            c.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))
        return True
    except Exception as e:
        _log.error("delete_invoice : %s", e)
        return False


# --- Mouvements bancaires --------------------------------------------------

def add_movement(mv: BankMovement) -> Optional[int]:
    try:
        with _conn() as c:
            cur = c.execute(
                """INSERT INTO bank_movements
                   (date, libelle, montant, sens, matched_invoice_id,
                    source_csv, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (mv.date, mv.libelle, mv.montant, mv.sens,
                 mv.matched_invoice_id, mv.source_csv, mv.created_at))
            return cur.lastrowid
    except Exception as e:
        _log.error("add_movement : %s", e)
        return None


def list_movements(limit: int = 200, unmatched_only: bool = False) -> list:
    q = "SELECT * FROM bank_movements WHERE 1=1"
    if unmatched_only:
        q += " AND matched_invoice_id IS NULL"
    q += " ORDER BY date DESC LIMIT ?"
    with _conn() as c:
        rows = c.execute(q, (limit,)).fetchall()
        return [_row_to_movement(r) for r in rows]


# ===========================================================================
# Compliance : SIRET, IBAN, doublons
# ===========================================================================

def validate_siret(siret: str) -> bool:
    """Verifie un SIRET (14 chiffres, algorithme de Luhn)."""
    if not siret:
        return False
    digits = re.sub(r"\D", "", siret)
    if len(digits) != 14:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def validate_iban(iban: str) -> bool:
    """Verifie un IBAN par l'algorithme mod-97 (norme ISO 13616)."""
    if not iban:
        return False
    iban = iban.replace(" ", "").upper()
    if len(iban) < 15 or len(iban) > 34:
        return False
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]+$", iban):
        return False
    # Deplace les 4 premiers caracteres a la fin
    rearranged = iban[4:] + iban[:4]
    # Convertit chaque lettre en nombre (A=10, B=11, ..., Z=35)
    numeric = ""
    for ch in rearranged:
        if ch.isalpha():
            numeric += str(ord(ch) - 55)
        else:
            numeric += ch
    try:
        return int(numeric) % 97 == 1
    except Exception:
        return False


def detect_anomaly(inv: Invoice, history: list = None) -> list:
    """Renvoie une liste de drapeaux d'anomalie detectes sur une facture.

    Codes possibles :
    - 'SIRET_INVALID'
    - 'IBAN_INVALID'
    - 'NO_SIRET'
    - 'NO_IBAN'
    - 'AMOUNT_OUTLIER'  (montant tres different de l'historique du tiers)
    - 'NEW_PARTY'       (premier mail de ce tiers)
    - 'TVA_MISMATCH'    (TTC != HT + TVA)
    - 'FUTURE_DATE'     (date d'emission dans le futur)
    """
    flags = []
    if inv.party_siret:
        if not validate_siret(inv.party_siret):
            flags.append("SIRET_INVALID")
    else:
        flags.append("NO_SIRET")
    if inv.party_iban:
        if not validate_iban(inv.party_iban):
            flags.append("IBAN_INVALID")
    else:
        if inv.direction == "in":
            # Pour une facture fournisseur on attend un IBAN de paiement
            flags.append("NO_IBAN")
    # Coherence HT/TVA/TTC (tolerance 0.05 EUR pour les arrondis)
    expected_ttc = inv.montant_ht * (1 + (inv.taux_tva or 0) / 100.0)
    if inv.montant_ttc and abs(expected_ttc - inv.montant_ttc) > 0.05:
        flags.append("TVA_MISMATCH")
    # Date dans le futur
    try:
        if inv.date_emission:
            d = datetime.strptime(inv.date_emission, "%Y-%m-%d").date()
            if d > date.today() + timedelta(days=1):
                flags.append("FUTURE_DATE")
    except Exception:
        pass
    # Anomalies historiques (si on a l'historique du tiers)
    if history is not None:
        same_party = [h for h in history
                      if (h.party or "").lower() == (inv.party or "").lower()]
        if not same_party:
            flags.append("NEW_PARTY")
        elif inv.montant_ttc > 0:
            avg = sum(h.montant_ttc for h in same_party) / len(same_party)
            if avg > 0 and inv.montant_ttc > avg * 3:
                flags.append("AMOUNT_OUTLIER")
    return flags


# ===========================================================================
# Extraction LLM des champs d'une facture
# ===========================================================================

def extract_invoice_fields(raw_text: str, sender: str = "",
                           subject: str = "") -> dict:
    """A partir du texte brut d'une facture (PDF / mail), demande au LLM
    d'extraire fournisseur, numero, date, echeance, HT, TVA, TTC, IBAN,
    SIRET. Renvoie un dict (champs vides en cas d'echec)."""
    out = {
        "party": "", "party_email": "", "party_siret": "", "party_iban": "",
        "invoice_number": "", "date_emission": "", "date_echeance": "",
        "montant_ht": 0.0, "taux_tva": 20.0,
        "montant_tva": 0.0, "montant_ttc": 0.0, "currency": "EUR",
    }
    if not raw_text:
        return out
    # 1) Heuristiques regex (gratuites, robustes)
    out.update(_regex_extract(raw_text))
    # 2) Affinement LLM si dispo (n'ecrase pas les champs deja remplis)
    try:
        from modules.config import URL, API_KEY, MODELE
        import requests as _rq
        prompt = (
            "Extrais les champs suivants d'une facture (JSON strict, "
            "rien d'autre) : party (nom du fournisseur ou du client), "
            "invoice_number, date_emission (YYYY-MM-DD), date_echeance "
            "(YYYY-MM-DD), montant_ht (float), taux_tva (float, 20 par "
            "defaut), montant_tva (float), montant_ttc (float), "
            "currency (EUR/USD/...), party_siret (14 chiffres si "
            "present), party_iban (sans espaces).\n\n"
            f"Sujet du mail : {subject}\nExpediteur : {sender}\n\n"
            f"Texte de la facture :\n{raw_text[:4000]}"
        )
        headers = {"Authorization": f"Bearer {API_KEY}",
                   "Content-Type": "application/json"}
        body = {
            "model": MODELE, "temperature": 0.0,
            "messages": [
                {"role": "system",
                 "content": "Tu extrais des champs de facture en JSON strict."},
                {"role": "user", "content": prompt},
            ],
        }
        r = _rq.post(URL, headers=headers, json=body, timeout=20)
        if r.status_code == 200:
            txt = r.json()["choices"][0]["message"]["content"]
            data = _parse_json_loose(txt)
            if isinstance(data, dict):
                # Ne remplace que si l'heuristique a laisse vide
                for k, v in data.items():
                    if k not in out:
                        continue
                    cur = out.get(k)
                    if (cur in ("", 0, 0.0, None)) and v not in ("", None):
                        out[k] = _coerce(k, v)
    except Exception as e:
        _log.info("Extraction LLM facture indispo : %s", e)
    # Recalcule TVA / TTC si manquants
    if out["montant_ht"] and not out["montant_tva"]:
        out["montant_tva"] = round(out["montant_ht"] * out["taux_tva"] / 100.0, 2)
    if out["montant_ht"] and not out["montant_ttc"]:
        out["montant_ttc"] = round(out["montant_ht"] + out["montant_tva"], 2)
    return out


_RE_AMOUNT = re.compile(r"(\d{1,6}(?:[ .,]\d{3})*(?:[.,]\d{2}))")
_RE_HT = re.compile(r"(?:montant\s+)?(?:total\s+)?(?:ht|hors\s*taxes?)\s*[:=]?\s*"
                    + _RE_AMOUNT.pattern, re.IGNORECASE)
_RE_TTC = re.compile(r"(?:montant\s+)?(?:total\s+)?ttc\s*[:=]?\s*"
                     + _RE_AMOUNT.pattern, re.IGNORECASE)
_RE_TVA = re.compile(r"tva\s*(?:\(\s*\d+(?:[.,]\d+)?\s*%\s*\))?\s*[:=]?\s*"
                     + _RE_AMOUNT.pattern, re.IGNORECASE)
_RE_TVA_RATE = re.compile(r"tva\s*\(?\s*(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_RE_INV_NUM = re.compile(r"(?:facture|invoice|n[°o\s]*)\s*[:#]?\s*([A-Z0-9\-/_]{3,30})",
                         re.IGNORECASE)
_RE_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")
_RE_DATE_ISO = re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b")
_RE_SIRET = re.compile(r"\bSIRET\s*[:=]?\s*(\d[\d\s]{12,16}\d)\b", re.IGNORECASE)
_RE_IBAN = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9 ]{11,30})\b")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _parse_amount(s: str) -> float:
    """'1 234,56' / '1.234,56' / '1234.56' -> 1234.56"""
    if not s:
        return 0.0
    cleaned = s.replace(" ", "").replace(" ", "")
    # Si comma + dot tous deux presents : le dernier separateur est decimal
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except Exception:
        return 0.0


def _normalize_date(s: str) -> str:
    """JJ/MM/AAAA ou AAAA-MM-JJ -> AAAA-MM-JJ."""
    if not s:
        return ""
    m = _RE_DATE_ISO.search(s)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except Exception:
            pass
    m = _RE_DATE.search(s)
    if m:
        d, mo, y = m.groups()
        if len(y) == 2:
            y = "20" + y
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def _regex_extract(text: str) -> dict:
    """Premiere passe d'extraction par regex (gratuite)."""
    out = {}
    m = _RE_HT.search(text)
    if m:
        out["montant_ht"] = _parse_amount(m.group(1))
    m = _RE_TTC.search(text)
    if m:
        out["montant_ttc"] = _parse_amount(m.group(1))
    m = _RE_TVA.search(text)
    if m:
        out["montant_tva"] = _parse_amount(m.group(1))
    m = _RE_TVA_RATE.search(text)
    if m:
        out["taux_tva"] = _parse_amount(m.group(1))
    m = _RE_INV_NUM.search(text)
    if m:
        out["invoice_number"] = m.group(1).strip()
    m = _RE_SIRET.search(text)
    if m:
        out["party_siret"] = re.sub(r"\D", "", m.group(1))
    m = _RE_IBAN.search(text)
    if m:
        out["party_iban"] = m.group(1).replace(" ", "").upper()
    m = _RE_EMAIL.search(text)
    if m:
        out["party_email"] = m.group(0).lower()
    # Date d'emission : on prend la premiere date trouvee
    d_iso = _normalize_date(text)
    if d_iso:
        out["date_emission"] = d_iso
    return out


def _parse_json_loose(text: str):
    """Trouve le premier objet JSON dans une reponse LLM bruyante."""
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _coerce(key: str, val):
    if key in ("montant_ht", "montant_tva", "montant_ttc", "taux_tva"):
        try:
            return float(val) if not isinstance(val, (int, float)) \
                else float(val)
        except Exception:
            return 0.0
    if key in ("date_emission", "date_echeance"):
        if isinstance(val, str):
            return _normalize_date(val) or val
    if key == "party_iban":
        return str(val).replace(" ", "").upper()
    if key == "party_siret":
        return re.sub(r"\D", "", str(val))
    return val if isinstance(val, str) else str(val or "")


# ===========================================================================
# Calculs fiscaux par regime
# ===========================================================================

# Taux URSSAF auto-entrepreneur (vigueur 2024 indicative — ajustable plus tard)
_URSSAF_RATES = {
    "auto_entrepreneur_bic": 0.123,    # vente de marchandises
    "auto_entrepreneur_bnc": 0.212,    # prestations de services BIC
    "auto_entrepreneur_lib": 0.232,    # liberal (CIPAV)
}

# Abattements forfaitaires micro
_ABATTEMENT = {
    "auto_entrepreneur_bic": 0.71,
    "auto_entrepreneur_bnc": 0.50,
    "auto_entrepreneur_lib": 0.34,
}


def fiscal_summary(regime: str, year: int = None,
                   tva_franchise: bool = False) -> dict:
    """Renvoie un dashboard fiscal pour l'annee donnee.

    Champs renvoyes :
        ca_ht, ca_ttc, achats_ht, achats_ttc,
        tva_collectee, tva_deductible, tva_a_reverser,
        urssaf_estime, ir_estime, is_estime,
        benefice_brut, regime
    """
    if year is None:
        year = date.today().year
    start = f"{year}-01-01"
    end = f"{year + 1}-01-01"
    with _conn() as c:
        rows_out = c.execute(
            """SELECT COALESCE(SUM(montant_ht), 0) AS ht,
                      COALESCE(SUM(montant_ttc), 0) AS ttc,
                      COALESCE(SUM(montant_tva), 0) AS tva
               FROM invoices
               WHERE direction='out'
                 AND date_emission >= ? AND date_emission < ?""",
            (start, end)).fetchone()
        rows_in = c.execute(
            """SELECT COALESCE(SUM(montant_ht), 0) AS ht,
                      COALESCE(SUM(montant_ttc), 0) AS ttc,
                      COALESCE(SUM(montant_tva), 0) AS tva
               FROM invoices
               WHERE direction='in'
                 AND date_emission >= ? AND date_emission < ?""",
            (start, end)).fetchone()

    ca_ht  = float(rows_out["ht"] or 0)
    ca_ttc = float(rows_out["ttc"] or 0)
    tva_collectee = float(rows_out["tva"] or 0)
    achats_ht  = float(rows_in["ht"] or 0)
    achats_ttc = float(rows_in["ttc"] or 0)
    tva_deductible = float(rows_in["tva"] or 0)

    # TVA a reverser
    if tva_franchise:
        tva_a_reverser = 0.0
    else:
        tva_a_reverser = max(0.0, tva_collectee - tva_deductible)

    # URSSAF / IR / IS selon regime
    urssaf = 0.0
    ir = 0.0
    is_ = 0.0
    benefice = 0.0
    base_ca = ca_ttc if tva_franchise else ca_ht

    if regime in _URSSAF_RATES:
        # Auto-entrepreneur : URSSAF = % du CA encaisse (approx CA HT/TTC)
        urssaf = round(base_ca * _URSSAF_RATES[regime], 2)
        # IR estimatif : abattement forfaitaire * CA, puis tranches
        abat = _ABATTEMENT[regime]
        revenu_imposable = base_ca * (1 - abat)
        # Approximation tranche IR 11 % puis 30 % (simplifie)
        if revenu_imposable < 11497:
            ir = 0.0
        elif revenu_imposable < 29315:
            ir = (revenu_imposable - 11497) * 0.11
        else:
            ir = (29315 - 11497) * 0.11 + (revenu_imposable - 29315) * 0.30
        ir = round(ir, 2)
        benefice = round(revenu_imposable - urssaf, 2)
    elif regime in ("sas", "sarl"):
        # IS : 15 % jusqu'a 42 500 €, 25 % au-dessus
        benefice = ca_ht - achats_ht
        if benefice <= 0:
            is_ = 0.0
        elif benefice <= 42500:
            is_ = round(benefice * 0.15, 2)
        else:
            is_ = round(42500 * 0.15 + (benefice - 42500) * 0.25, 2)
    elif regime == "bnc_reel":
        # Declaration controlee : benefice = CA - charges (approx achats)
        benefice = ca_ht - achats_ht
        # Tranche IR comme micro mais sans abattement
        if benefice < 11497:
            ir = 0.0
        elif benefice < 29315:
            ir = (benefice - 11497) * 0.11
        else:
            ir = (29315 - 11497) * 0.11 + (benefice - 29315) * 0.30
        ir = round(ir, 2)
    # else "none" : pas de calcul

    return {
        "regime": regime,
        "year": year,
        "ca_ht": round(ca_ht, 2),
        "ca_ttc": round(ca_ttc, 2),
        "achats_ht": round(achats_ht, 2),
        "achats_ttc": round(achats_ttc, 2),
        "tva_collectee": round(tva_collectee, 2),
        "tva_deductible": round(tva_deductible, 2),
        "tva_a_reverser": round(tva_a_reverser, 2),
        "urssaf_estime": urssaf,
        "ir_estime": ir,
        "is_estime": is_,
        "benefice_estime": round(benefice, 2),
        "tva_franchise": bool(tva_franchise),
    }


# ===========================================================================
# Import banque (CSV multi-formats)
# ===========================================================================

# Tentative auto-detection des colonnes par mots-cles courants.
_DATE_KEYS = ("date", "date opération", "date operation", "date valeur",
              "date d'opération", "date_operation", "transaction date")
_LIB_KEYS  = ("libellé", "libelle", "label", "description", "intitulé",
              "intitule", "détail", "detail", "wording")
_DEB_KEYS  = ("débit", "debit", "withdrawal")
_CRED_KEYS = ("crédit", "credit", "deposit")
_AMT_KEYS  = ("montant", "amount", "value", "somme")


def import_bank_csv(path: str) -> int:
    """Importe un CSV de releve bancaire. Auto-detecte le delimiteur et
    les colonnes (date, libelle, montant ou debit/credit). Renvoie le
    nombre de mouvements ajoutes."""
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            sample = f.read(4096)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            class _D(csv.Dialect):
                delimiter = ";"
                quotechar = '"'
                lineterminator = "\n"
                quoting = csv.QUOTE_MINIMAL
                doublequote = True
                skipinitialspace = True
            dialect = _D
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f, dialect=dialect)
            rows = list(reader)
    except Exception as e:
        _log.error("import_bank_csv read : %s", e)
        return 0

    if not rows:
        return 0

    # Resolve column mapping (case-insensitive, accents tolerants)
    headers = {h.strip().lower(): h for h in rows[0].keys() if h}

    def find(keys):
        for k in keys:
            if k in headers:
                return headers[k]
        # match partiel
        for hk, hv in headers.items():
            for k in keys:
                if k in hk:
                    return hv
        return None

    col_date = find(_DATE_KEYS)
    col_lib  = find(_LIB_KEYS)
    col_amt  = find(_AMT_KEYS)
    col_deb  = find(_DEB_KEYS)
    col_cred = find(_CRED_KEYS)

    if not col_date or not col_lib:
        _log.warning("CSV banque : colonnes date/libelle introuvables (%s)", headers)
        return 0

    added = 0
    src_name = os.path.basename(path)
    for row in rows:
        try:
            d_iso = _normalize_date(row.get(col_date, "") or "")
            if not d_iso:
                continue
            lib = (row.get(col_lib, "") or "").strip()
            mont = 0.0
            sens = "credit"
            if col_amt and row.get(col_amt):
                mont = _parse_amount(row.get(col_amt))
                sens = "debit" if mont < 0 else "credit"
            else:
                deb = _parse_amount(row.get(col_deb, "")) if col_deb else 0.0
                cred = _parse_amount(row.get(col_cred, "")) if col_cred else 0.0
                if deb:
                    mont, sens = -abs(deb), "debit"
                elif cred:
                    mont, sens = abs(cred), "credit"
                else:
                    continue
            mv = BankMovement(date=d_iso, libelle=lib[:200],
                              montant=round(mont, 2), sens=sens,
                              source_csv=src_name)
            if add_movement(mv):
                added += 1
        except Exception as e:
            _log.info("Ligne ignoree : %s", e)
    _log.info("import_bank_csv : %d mouvements ajoutes (%s)", added, src_name)
    # Tente un rapprochement automatique
    try:
        reconcile()
    except Exception as e:
        _log.warning("Reconciliation : %s", e)
    return added


def reconcile(amount_tolerance: float = 0.01,
              day_tolerance: int = 7) -> int:
    """Tente d'apparier les mouvements bancaires non rapproches avec les
    factures pending. Critere : meme montant (au centime pres) et date
    proche (+/- N jours autour de l'echeance ou de l'emission).

    Renvoie le nombre de matches realises.
    """
    matches = 0
    with _conn() as c:
        movs = c.execute(
            "SELECT * FROM bank_movements WHERE matched_invoice_id IS NULL "
            "ORDER BY date DESC").fetchall()
        invs = c.execute(
            "SELECT * FROM invoices WHERE statut='pending' "
            "ORDER BY date_emission DESC").fetchall()

    inv_list = [_row_to_invoice(r) for r in invs]
    for mv_row in movs:
        mv = _row_to_movement(mv_row)
        # Une facture sortante (client) est payee par un CREDIT entrant.
        # Une facture entrante (fournisseur) est payee par un DEBIT sortant.
        for inv in inv_list:
            if inv.statut != "pending":
                continue
            if inv.direction == "out" and mv.sens != "credit":
                continue
            if inv.direction == "in" and mv.sens != "debit":
                continue
            if abs(abs(mv.montant) - inv.montant_ttc) > amount_tolerance:
                continue
            try:
                ref_date = inv.date_echeance or inv.date_emission
                d1 = datetime.strptime(ref_date, "%Y-%m-%d").date()
                d2 = datetime.strptime(mv.date, "%Y-%m-%d").date()
                if abs((d2 - d1).days) > day_tolerance:
                    continue
            except Exception:
                continue
            # Match !
            with _conn() as c:
                c.execute("UPDATE bank_movements SET matched_invoice_id=? "
                          "WHERE id=?", (inv.id, mv.id))
                c.execute("UPDATE invoices SET statut='paid', "
                          "updated_at=? WHERE id=?",
                          (datetime.now().isoformat(), inv.id))
            inv.statut = "paid"  # local
            matches += 1
            break
    if matches:
        _log.info("Rapprochement : %d facture(s) marquee(s) payee(s)", matches)
    return matches


# ===========================================================================
# Generation de relances client
# ===========================================================================

def _relance_template(inv: Invoice, niveau: int) -> tuple:
    """Fallback offline : sujet + corps de relance selon le niveau."""
    party = inv.party or "Cher client"
    num = inv.invoice_number or "(sans numero)"
    montant = f"{inv.montant_ttc:.2f} {inv.currency}"
    echeance = inv.date_echeance or "(non precisee)"
    if niveau == 1:
        subj = f"Rappel : facture {num} en attente de reglement"
        body = (f"Bonjour,\n\n"
                f"Sauf erreur de notre part, le reglement de la facture "
                f"n° {num} d'un montant de {montant} (echeance : {echeance}) "
                f"ne nous est pas encore parvenu.\n\n"
                f"Pourriez-vous proceder au reglement dans les meilleurs "
                f"delais ? Si le paiement a deja ete effectue, merci de "
                f"considerer ce mail comme nul et non avenu.\n\n"
                f"Cordialement.")
    elif niveau == 2:
        subj = f"Relance : facture {num} impayee"
        body = (f"Bonjour {party},\n\n"
                f"Malgre un premier rappel reste sans suite, la facture "
                f"n° {num} d'un montant de {montant} demeure impayee.\n\n"
                f"Merci de proceder au reglement sous huitaine. A defaut, "
                f"nous serons contraints d'engager une procedure de "
                f"recouvrement.\n\n"
                f"Restant a votre disposition pour toute question.\n\n"
                f"Cordialement.")
    else:
        subj = f"Mise en demeure : facture {num}"
        body = (f"Madame, Monsieur,\n\n"
                f"Sans reglement de votre part de la facture n° {num} "
                f"(montant : {montant}) sous 8 jours, nous nous reservons "
                f"le droit d'engager toutes voies de recouvrement, y "
                f"compris contentieuses, sans nouvel avis.\n\n"
                f"Veuillez agreer, Madame, Monsieur, l'expression de nos "
                f"salutations distinguees.")
    return subj, body


def generate_relance(invoice_id: int) -> Optional[dict]:
    """Genere un mail de relance pour une facture client. Renvoie un dict
    {to, subject, body, niveau} ou None si la facture n'existe pas."""
    inv = get_invoice(invoice_id)
    if inv is None or inv.direction != "out":
        return None
    niveau = (inv.relance_count or 0) + 1
    if niveau > 3:
        niveau = 3
    subj, body = _relance_template(inv, niveau)
    # Affinement LLM (poli/ferme, personnalise) si dispo
    try:
        from modules.config import URL, API_KEY, MODELE
        import requests as _rq
        prompt = (
            f"Tu rediges une relance de niveau {niveau}/3 (1=courtoise, "
            f"2=ferme, 3=mise en demeure) pour une facture impayee.\n"
            f"Client : {inv.party}\n"
            f"Facture : {inv.invoice_number}\n"
            f"Montant : {inv.montant_ttc:.2f} {inv.currency}\n"
            f"Date emission : {inv.date_emission}\n"
            f"Echeance : {inv.date_echeance}\n"
            f"Renvoie UNIQUEMENT un JSON {{\"subject\": ..., \"body\": ...}} "
            f"avec un mail prêt a envoyer (en français, signature: "
            f"\"Cordialement\")."
        )
        headers = {"Authorization": f"Bearer {API_KEY}",
                   "Content-Type": "application/json"}
        bodyj = {"model": MODELE, "temperature": 0.3,
                 "messages": [
                     {"role": "system",
                      "content": "Tu rediges des relances commerciales en francais."},
                     {"role": "user", "content": prompt}]}
        r = _rq.post(URL, headers=headers, json=bodyj, timeout=20)
        if r.status_code == 200:
            txt = r.json()["choices"][0]["message"]["content"]
            data = _parse_json_loose(txt)
            if isinstance(data, dict):
                subj = data.get("subject") or subj
                body = data.get("body") or body
    except Exception as e:
        _log.info("Relance LLM indispo : %s", e)
    return {
        "to": inv.party_email,
        "subject": subj,
        "body": body,
        "niveau": niveau,
        "invoice_id": invoice_id,
    }


def mark_relance_sent(invoice_id: int):
    """Incremente le compteur de relances + met a jour la date."""
    inv = get_invoice(invoice_id)
    if inv is None:
        return
    update_invoice(invoice_id,
                   relance_count=(inv.relance_count or 0) + 1,
                   last_relance_date=date.today().isoformat())


# ===========================================================================
# Lecture de fichier facture + extraction directe
# ===========================================================================

def _read_file_text(path: str) -> str:
    """Lit le texte d'un PDF, docx, txt... (limite 8000 char)."""
    if not path or not os.path.isfile(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            try:
                import fitz
                with fitz.open(path) as doc:
                    text = ""
                    for page in doc:
                        text += page.get_text()
                        if len(text) > 8000:
                            break
                return text[:8000]
            except Exception as e:
                _log.info("PDF lecture %s : %s", path, e)
                return ""
        if ext == ".docx":
            try:
                import docx
                d = docx.Document(path)
                return "\n".join(p.text for p in d.paragraphs)[:8000]
            except Exception as e:
                _log.info("DOCX lecture %s : %s", path, e)
                return ""
        if ext in (".txt", ".md", ".csv", ".log"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:8000]
    except Exception as e:
        _log.warning("Read file %s : %s", path, e)
    return ""


def extract_from_file(path: str, sender_email: str = "",
                      subject: str = "", user_email: str = "",
                      source_mail_uid: str = "") -> Optional[Invoice]:
    """Lit un PDF/docx/texte et extrait une Invoice complete.

    La direction est devinee :
      - 'out' (facture client) si sender_email == user_email
      - 'in'  (facture fournisseur) sinon
    """
    text = _read_file_text(path)
    if not text:
        return None
    fields = extract_invoice_fields(text, sender=sender_email,
                                    subject=subject)
    direction = "in"
    if (sender_email and user_email and
            sender_email.lower() == user_email.lower()):
        direction = "out"
    if not fields.get("party_email"):
        fields["party_email"] = sender_email or ""
    inv = Invoice(direction=direction, source="mail", file_path=path,
                  source_mail_uid=source_mail_uid or "", **fields)
    inv.fingerprint = _fingerprint_invoice(inv)
    return inv


# ===========================================================================
# Helpers UI : agregations rapides
# ===========================================================================

def quick_stats() -> dict:
    """Stats compactes pour le dashboard du hub finance."""
    today = date.today().isoformat()
    with _conn() as c:
        n_in_pending = c.execute(
            "SELECT COUNT(*) FROM invoices WHERE direction='in' "
            "AND statut='pending'").fetchone()[0]
        n_out_pending = c.execute(
            "SELECT COUNT(*) FROM invoices WHERE direction='out' "
            "AND statut='pending'").fetchone()[0]
        n_overdue = c.execute(
            "SELECT COUNT(*) FROM invoices WHERE direction='out' "
            "AND statut IN ('pending','overdue') "
            "AND date_echeance <> '' AND date_echeance < ?",
            (today,)).fetchone()[0]
        sum_overdue = c.execute(
            "SELECT COALESCE(SUM(montant_ttc), 0) FROM invoices "
            "WHERE direction='out' "
            "AND statut IN ('pending','overdue') "
            "AND date_echeance <> '' AND date_echeance < ?",
            (today,)).fetchone()[0]
        n_unmatched = c.execute(
            "SELECT COUNT(*) FROM bank_movements "
            "WHERE matched_invoice_id IS NULL").fetchone()[0]
    return {
        "fournisseurs_a_payer": n_in_pending,
        "clients_a_encaisser": n_out_pending,
        "factures_en_retard": n_overdue,
        "montant_en_retard": round(float(sum_overdue or 0), 2),
        "mouvements_non_rapproches": n_unmatched,
    }
