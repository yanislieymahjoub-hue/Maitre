"""Script autonome d'envoi du compte rendu journalier.

Lance par Windows Task Scheduler, meme si l'application principale n'est
pas ouverte (session fermee, PC en veille, etc.). Avec les options
/WAKE et /ST, le PC peut meme etre reveille a l'heure programmee.

Usage :
    python daily_report_standalone.py [username]

Si aucun nom d'utilisateur n'est fourni, on utilise le 1er utilisateur
trouve dans users_db.json (celui qui a des credentials email).
"""
import os
import sys
import json
import smtplib
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# On ajoute le dossier du script au PYTHONPATH pour pouvoir importer
# database.py (meme logique que l'app principale).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    import database
except Exception as e:
    print(f"Impossible d'importer database.py : {e}")
    sys.exit(1)


def _get_servers_for(email_addr):
    """Retourne (imap_host, smtp_host, smtp_port) selon le domaine."""
    addr = (email_addr or "").lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    if domain in ("outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr",
                  "live.com", "live.fr", "msn.com", "office365.com"):
        return ("outlook.office365.com", "smtp-mail.outlook.com", 587)
    if domain in ("yahoo.com", "yahoo.fr", "ymail.com"):
        return ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 587)
    return ("imap.gmail.com", "smtp.gmail.com", 587)


def _pick_username():
    """Choisit l'utilisateur dont le compte mail est configure."""
    if len(sys.argv) >= 2:
        return sys.argv[1]
    try:
        users = database._load_db()
    except Exception:
        return None
    for u, data in users.items():
        if data.get("email_config"):
            return u
    return None


def _build_report(entries, username, today_str):
    cats = {
        "mails_recus":    ("📥 Mails recus & classes", []),
        "mails_envoyes":  ("✉ Mails envoyes", []),
        "mails_supprimes":("🗑 Mails supprimes", []),
        "rdv":            ("📅 Rendez-vous", []),
        "permissions":    ("⚡ Permissions traitees", []),
        "autres":         ("• Autres actions", []),
    }
    action_map = {
        "mail_classified": "mails_recus",
        "mail_received":   "mails_recus",
        "mail_sent":       "mails_envoyes",
        "mail_deleted":    "mails_supprimes",
        "agenda_added":    "rdv",
        "agenda_modified": "rdv",
        "agenda_deleted":  "rdv",
        "agenda_reminder": "rdv",
        "permission_granted": "permissions",
        "permission_denied":  "permissions",
    }
    for e in entries:
        cats[action_map.get(e.get("action_type", ""), "autres")][1].append(e)

    date_hum = datetime.strptime(today_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    lines = [
        f"COMPTE RENDU — {date_hum}",
        f"Utilisateur : {username}",
        f"Total d'actions : {len(entries)}",
        "",
    ]
    any_section = False
    for key, (label, items) in cats.items():
        if not items:
            continue
        any_section = True
        lines.append(f"── {label} ({len(items)}) ──")
        for e in items:
            ts = e.get("timestamp", "")
            try:
                hh = datetime.fromisoformat(ts).strftime("%H:%M")
            except Exception:
                hh = "--:--"
            detail = (e.get("details") or "").strip()
            if len(detail) > 140:
                detail = detail[:137] + "..."
            lines.append(f"  • {hh} — {detail}")
        lines.append("")
    if not any_section:
        lines.append("Aucune action enregistree aujourd'hui.")
    lines.append("── Fin du rapport ──")
    return "\n".join(lines)


def main():
    username = _pick_username()
    if not username:
        print("Aucun utilisateur avec un compte mail configure.")
        return 1

    cfg = database.get_email_config(username)
    if not cfg or not cfg.get("email") or not cfg.get("app_password"):
        print(f"Pas de credentials email pour {username}.")
        return 1
    email_addr = cfg["email"]
    app_pass   = cfg["app_password"]

    log = database.load_experience_log()
    today_str = date.today().strftime("%Y-%m-%d")
    entries = [e for e in log
               if e.get("timestamp", "").startswith(today_str)
               and e.get("username") == username]

    body = _build_report(entries, username, today_str)

    _, smtp_host, smtp_port = _get_servers_for(email_addr)
    try:
        msg = MIMEMultipart()
        msg["From"] = email_addr
        msg["To"]   = email_addr
        msg["Subject"] = f"Compte rendu journalier — {datetime.now().strftime('%d/%m/%Y')}"
        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
        server.login(email_addr, app_pass)
        server.send_message(msg)
        server.quit()

        try:
            database.add_experience_entry(
                username, "report_sent",
                f"Compte rendu journalier envoye (standalone) a {email_addr}",
                "success", 1.0)
        except Exception:
            pass
        print(f"Rapport envoye a {email_addr}.")
        return 0
    except Exception as e:
        print(f"Echec envoi rapport : {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
