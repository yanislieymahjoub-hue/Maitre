"""Optimisations inbox : spam aging, archivage > 30j, desabonnement auto,
dedup, categorisation serveur, focus mode, resume matinal, anti accuses.

Toutes les actions qui touchent le serveur IMAP passent par le module
Permissions (database.add_permission_request). Quand Lacher Prise est ON,
la requete est creee ET auto-approuvee immediatement (l'action s'execute),
mais elle reste tracee dans l'historique des permissions pour que
l'utilisateur voit tout ce qui a ete fait.
"""
import os
import re
import json
import time
import threading
import imaplib
import smtplib
import requests
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import database
from modules.config import USERNAME


MAIL_TRACKING_FILE = f"mail_tracking_{USERNAME}.json"

# Types de mail -> dossier serveur
CATEGORY_FOLDERS = {
    "facture":       "Assistant/Factures",
    "relance":       "Assistant/Factures",
    "professionnel": "Assistant/Pro",
    "personnel":     "Assistant/Perso",
    "newsletter":    "Assistant/Newsletters",
    "spam":          "Assistant/Spam",
    "invitation":    "Assistant/Pro",
    "contractuel":   "Assistant/Juridique",
    "juridique":     "Assistant/Juridique",
    "cv":            "Assistant/Pro",
    "support":       "Assistant/Support",
}

FOCUS_TYPES = {"professionnel", "facture", "relance", "juridique",
               "contractuel", "cv"}

# Motifs de sujets consideres comme accuses de reception
RECEIPT_RE = re.compile(
    r"^(vu|read|delivered|lu|accus[eé]\s+de\s+r[eé]ception|read\s+receipt|"
    r"delivery\s+status|non[- ]?remis|undeliverable|undelivered)\s*[:>-]",
    re.IGNORECASE,
)

# Parametres par defaut (tout activé — modifiable via self.optim_settings)
DEFAULT_OPTIM_SETTINGS = {
    "auto_spam_aging":      True,   # A
    "bulk_spam_button":     True,   # B
    "auto_unsubscribe":     True,   # C
    "auto_archive_old":     True,   # D
    "dedupe_view":          True,   # E
    "server_categorize":    True,   # F
    "delete_receipts":      True,   # G
    "morning_digest":       False,  # H - desactive par defaut : on prefere
                                    # le compte rendu quotidien consolide (le soir)
                                    # plutot qu'un mail du matin en plus.
    "focus_mode":           False,  # I (UI toggle)
    # Seuils
    "spam_aging_days":      3,
    "archive_age_days":     30,
    "unsubscribe_threshold":5,   # nb de newsletters sans engagement
    "digest_hour":          8,
}


class InboxOptimMixin:
    # =============================================================
    #     INITIALISATION
    # =============================================================
    def init_inbox_optim(self):
        """Appelee depuis Assistant.__init__ apres la config email."""
        self.optim_settings = dict(DEFAULT_OPTIM_SETTINGS)
        self._mail_tracking = self._load_mail_tracking()
        self._focus_mode = False
        # Anti-boucle : on ne repousse pas une meme permission 100 fois
        self._optim_pending_keys = set()

    def _load_mail_tracking(self):
        try:
            if os.path.exists(MAIL_TRACKING_FILE):
                with open(MAIL_TRACKING_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "mails" not in data:  data["mails"] = {}
                if "senders" not in data: data["senders"] = {}
                return data
        except Exception:
            pass
        return {"mails": {}, "senders": {}}

    def _save_mail_tracking(self):
        try:
            with open(MAIL_TRACKING_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mail_tracking, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Erreur save mail tracking : {e}")

    def _track_mail(self, mail_data):
        """Enregistre first_seen et type d'un mail dans le fichier de suivi."""
        try:
            uid = str(mail_data.get("uid") or "")
            if not uid:
                return
            store = self._mail_tracking.setdefault("mails", {})
            entry = store.get(uid)
            if not entry:
                entry = {
                    "first_seen": datetime.now().isoformat(timespec="seconds"),
                    "type": mail_data.get("mail_type", "inconnu"),
                    "sender": mail_data.get("sender", "")[:120],
                    "subject": mail_data.get("subject", "")[:120],
                    "opened": False,
                }
                store[uid] = entry
            else:
                # Met a jour le type (l'IA peut reclasser)
                entry["type"] = mail_data.get("mail_type", entry.get("type"))
            # Compteur de mails par expediteur (pour unsubscribe auto)
            sender_email = self._extract_sender_email(mail_data.get("sender", ""))
            if sender_email:
                s = self._mail_tracking.setdefault("senders", {}).setdefault(
                    sender_email,
                    {"count": 0, "opened": 0, "last_subject": ""})
                s["count"] = s.get("count", 0) + 1
                s["last_subject"] = mail_data.get("subject", "")[:100]
        except Exception as e:
            print(f"Erreur track mail : {e}")

    def _extract_sender_email(self, sender):
        try:
            m = re.search(r"[\w\.\-\+]+@[\w\.\-]+\.\w+", sender or "")
            return m.group(0).lower() if m else None
        except Exception:
            return None

    def mark_mail_opened(self, uid, sender=""):
        """A appeler quand l'utilisateur ouvre / interagit avec un mail."""
        if not uid:
            return
        uid = str(uid)
        store = self._mail_tracking.setdefault("mails", {})
        entry = store.setdefault(uid, {})
        entry["opened"] = True
        se = self._extract_sender_email(sender or entry.get("sender", ""))
        if se:
            s = self._mail_tracking.setdefault("senders", {}).setdefault(
                se, {"count": 1, "opened": 0, "last_subject": ""})
            s["opened"] = s.get("opened", 0) + 1
        self._save_mail_tracking()

    # =============================================================
    #     ORCHESTRATEUR : appele apres chaque fetch
    # =============================================================
    def _run_inbox_optimizations(self, mails):
        """Lance toutes les optimisations activees sur la liste de mails
        fraichement chargee.

        Les mails envoyes par soi-meme (mail_type='self' : rapports
        quotidiens, auto-notifications) sont exclus de toutes les
        optimisations automatiques pour ne pas creer de boucle.
        """
        try:
            # Exclusion systematique des mails 'self' de toutes les
            # optimisations (archivage, suppression, categorisation...)
            mails = [m for m in (mails or [])
                     if (m.get("mail_type") or "").lower() != "self"]

            # Tracking de base
            for m in mails:
                self._track_mail(m)
            self._save_mail_tracking()

            # G. Supprimer les accuses de reception
            if self.optim_settings.get("delete_receipts"):
                self._scan_receipts(mails)
            # A. Spams vieillissants
            if self.optim_settings.get("auto_spam_aging"):
                self._scan_spam_aging(mails)
            # D. Archivage > 30j
            if self.optim_settings.get("auto_archive_old"):
                self._scan_archive_old(mails)
            # C. Desabonnement newsletters ignorees
            if self.optim_settings.get("auto_unsubscribe"):
                self._scan_unsubscribe()
            # F. Categorisation serveur
            if self.optim_settings.get("server_categorize"):
                self._scan_categorize(mails)
        except Exception as e:
            print(f"Erreur _run_inbox_optimizations : {e}")

    # =============================================================
    #     A. SPAM AGING  -> suppression apres N jours
    # =============================================================
    def _scan_spam_aging(self, mails):
        days = int(self.optim_settings.get("spam_aging_days", 3))
        limit = datetime.now() - timedelta(days=days)
        for m in mails:
            if (m.get("mail_type") or "").lower() != "spam":
                continue
            uid = str(m.get("uid") or "")
            if not uid:
                continue
            entry = self._mail_tracking.get("mails", {}).get(uid, {})
            fs = entry.get("first_seen")
            if not fs:
                continue
            try:
                fs_dt = datetime.fromisoformat(fs)
            except Exception:
                continue
            if fs_dt > limit:
                continue
            if entry.get("opened"):
                continue
            key = f"spam_delete:{uid}"
            if key in self._optim_pending_keys:
                continue
            self._optim_pending_keys.add(key)
            self._request_action(
                "mail_delete_spam",
                title=f"Supprimer spam ancien : {m.get('subject','')[:40]}",
                description=f"Spam non ouvert depuis {days}j. Expediteur : {m.get('sender','')[:60]}",
                action_data={"uid": uid, "subject": m.get("subject",""),
                             "sender": m.get("sender","")},
            )

    # =============================================================
    #     B. VIDER TOUS LES SPAMS  (bouton UI)
    # =============================================================
    def bulk_delete_spam(self):
        """Collecte tous les UIDs des mails visibles de type spam et cree
        une requete de suppression en masse.
        """
        try:
            mails = [m for m in getattr(self, "_inbox_mails", [])
                     if (m.get("mail_type") or "").lower() == "spam"
                     and m.get("uid")]
            if not mails:
                self._show_toast("Aucun spam a vider.")
                return
            uids = [str(m["uid"]) for m in mails]
            subjects = [m.get("subject", "")[:40] for m in mails[:5]]
            sample = " | ".join(subjects)
            self._request_action(
                "mail_bulk_delete_spam",
                title=f"Vider {len(uids)} spam(s)",
                description=f"Suppression en masse. Exemples : {sample}",
                action_data={"uids": uids},
            )
        except Exception as e:
            print(f"Erreur bulk_delete_spam : {e}")

    def execute_bulk_delete_spam(self, uids):
        """Executee apres approbation (ou directement en Lacher Prise)."""
        def _worker():
            ok = 0
            for u in uids:
                try:
                    if self.delete_mail_on_server(u, "Spam (vidage)"):
                        ok += 1
                except Exception:
                    pass
            self.root.after(0, lambda: self._show_toast(
                f"🗑 {ok}/{len(uids)} spam(s) supprime(s)"))
        threading.Thread(target=_worker, daemon=True).start()

    # =============================================================
    #     C. DESABONNEMENT AUTO DES NEWSLETTERS IGNOREES
    # =============================================================
    def _scan_unsubscribe(self):
        threshold = int(self.optim_settings.get("unsubscribe_threshold", 5))
        senders = self._mail_tracking.get("senders", {})
        for sender_email, stats in list(senders.items()):
            count = stats.get("count", 0)
            opened = stats.get("opened", 0)
            if count < threshold or opened > 0:
                continue
            if stats.get("unsubscribed"):
                continue
            key = f"unsub:{sender_email}"
            if key in self._optim_pending_keys:
                continue
            self._optim_pending_keys.add(key)
            self._request_action(
                "mail_unsubscribe",
                title=f"Desabonner : {sender_email}",
                description=f"{count} mails recus, aucun ouvert. Dernier sujet : {stats.get('last_subject','')[:60]}",
                action_data={"sender_email": sender_email},
            )

    def execute_unsubscribe(self, sender_email):
        """Cherche le dernier mail de ce sender, extrait le lien unsubscribe
        ou l'adresse List-Unsubscribe, et effectue l'action."""
        def _worker():
            try:
                imap_host, _, _, _ = self._get_email_servers()
                mail = imaplib.IMAP4_SSL(imap_host)
                mail.login(self.user_email, self.user_app_pass)
                mail.select("inbox")
                typ, data = mail.uid("search", None, f'(FROM "{sender_email}")')
                if typ != "OK" or not data or not data[0]:
                    self.root.after(0, lambda: self._show_toast(
                        f"Desabo : aucun mail trouve pour {sender_email[:30]}"))
                    mail.logout()
                    return
                uids = data[0].split()
                if not uids:
                    mail.logout()
                    return
                last_uid = uids[-1]
                typ, msg_data = mail.uid("fetch", last_uid, "(RFC822)")
                if typ != "OK":
                    mail.logout()
                    return
                import email as _email
                raw = msg_data[0][1]
                msg = _email.message_from_bytes(raw)
                # 1) Entete List-Unsubscribe (preferable)
                list_unsub = msg.get("List-Unsubscribe", "")
                did = False
                # mailto: -> envoyer email "unsubscribe"
                m_mail = re.search(r"mailto:([^>,\s]+)", list_unsub)
                if m_mail:
                    try:
                        dest = m_mail.group(1).strip()
                        self._send_unsubscribe_email(dest)
                        did = True
                    except Exception:
                        did = False
                # http(s): -> GET
                if not did:
                    m_url = re.search(r"<(https?://[^>]+)>", list_unsub)
                    if m_url:
                        try:
                            requests.get(m_url.group(1), timeout=8)
                            did = True
                        except Exception:
                            did = False
                # 2) Fallback : chercher un lien "unsubscribe" dans le body
                if not did:
                    body = self._extract_body_text(msg)
                    m_url = re.search(
                        r"https?://[^\s\"'<>]*(unsubscribe|desabonnement|opt[-_]?out)[^\s\"'<>]*",
                        body, re.IGNORECASE)
                    if m_url:
                        try:
                            requests.get(m_url.group(0), timeout=8)
                            did = True
                        except Exception:
                            did = False
                mail.logout()

                # Marquer le sender comme desabonne peu importe, pour ne pas
                # retenter indefiniment
                s = self._mail_tracking.setdefault("senders", {}).setdefault(
                    sender_email, {})
                s["unsubscribed"] = True
                s["unsubscribed_at"] = datetime.now().isoformat(timespec="seconds")
                self._save_mail_tracking()

                self.root.after(0, lambda: self._show_toast(
                    f"✦ Desabo lance : {sender_email[:30]} ({'OK' if did else 'tente'})"))
                try:
                    database.add_experience_entry(
                        USERNAME, "mail_unsubscribe",
                        f"Desabonnement tente : {sender_email}",
                        "success" if did else "partial", 1.0)
                except Exception:
                    pass
            except Exception as e:
                print(f"Erreur unsubscribe : {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def _send_unsubscribe_email(self, dest):
        _, smtp_host, smtp_port, _ = self._get_email_servers()
        msg = MIMEMultipart()
        msg["From"] = self.user_email
        msg["To"] = dest
        msg["Subject"] = "Unsubscribe"
        msg.attach(MIMEText("Please unsubscribe me from this mailing list.",
                            "plain", "utf-8"))
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
        server.login(self.user_email, self.user_app_pass)
        server.send_message(msg)
        server.quit()

    def _extract_body_text(self, msg):
        out = []
        try:
            if msg.is_multipart():
                for p in msg.walk():
                    ct = p.get_content_type()
                    if ct in ("text/plain", "text/html"):
                        try:
                            payload = p.get_payload(decode=True)
                            if payload:
                                out.append(payload.decode(
                                    p.get_content_charset() or "utf-8",
                                    errors="replace"))
                        except Exception:
                            pass
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    out.append(payload.decode(
                        msg.get_content_charset() or "utf-8",
                        errors="replace"))
        except Exception:
            pass
        return " ".join(out)[:8000]

    # =============================================================
    #     D. ARCHIVAGE AUTOMATIQUE > N JOURS
    # =============================================================
    def _scan_archive_old(self, mails):
        days = int(self.optim_settings.get("archive_age_days", 30))
        limit = datetime.now() - timedelta(days=days)
        for m in mails:
            uid = str(m.get("uid") or "")
            if not uid:
                continue
            t = (m.get("mail_type") or "").lower()
            if t in ("facture", "relance", "juridique", "contractuel"):
                # Jamais archiver automatiquement ce qui peut etre critique
                continue
            urg = (m.get("urgency") or "").lower()
            if urg == "haute":
                continue
            entry = self._mail_tracking.get("mails", {}).get(uid, {})
            fs = entry.get("first_seen")
            if not fs:
                continue
            try:
                fs_dt = datetime.fromisoformat(fs)
            except Exception:
                continue
            if fs_dt > limit:
                continue
            if entry.get("archived"):
                continue
            key = f"archive:{uid}"
            if key in self._optim_pending_keys:
                continue
            self._optim_pending_keys.add(key)
            self._request_action(
                "mail_archive",
                title=f"Archiver : {m.get('subject','')[:40]}",
                description=f"Mail > {days}j, lu ou non critique.",
                action_data={"uid": uid, "subject": m.get("subject","")},
            )

    def archive_mail_on_server(self, uid, subject=""):
        """Deplace un mail dans le dossier Archive du serveur."""
        if not uid:
            return False
        try:
            imap_host, _, _, _ = self._get_email_servers()
            mail = imaplib.IMAP4_SSL(imap_host)
            mail.login(self.user_email, self.user_app_pass)
            mail.select("inbox")
            uid_b = uid.encode() if isinstance(uid, str) else uid
            ok = False
            for folder in ("Archive", "[Gmail]/All Mail", "Archives"):
                try:
                    # Cree le dossier si besoin (silencieux si existe)
                    try: mail.create(folder)
                    except Exception: pass
                    typ, _ = mail.uid("COPY", uid_b, folder)
                    if typ == "OK":
                        ok = True
                        break
                except Exception:
                    continue
            if ok:
                try:
                    mail.uid("STORE", uid_b, "+FLAGS", "(\\Deleted)")
                    mail.expunge()
                except Exception:
                    pass
            mail.logout()
            if ok:
                store = self._mail_tracking.setdefault("mails", {}).setdefault(
                    str(uid), {})
                store["archived"] = True
                self._save_mail_tracking()
                database.add_experience_entry(
                    USERNAME, "mail_archived",
                    f"Mail archive : {subject[:60]}", "success", 1.0)
                self.root.after(0, lambda s=subject: self._show_toast(
                    f"📦 Archive : {s[:40]}"))
            return ok
        except Exception as e:
            print(f"Erreur archive : {e}")
            return False

    # =============================================================
    #     E. DEDUP VIEW (UI uniquement, pas de permission)
    # =============================================================
    def dedupe_mails_for_render(self, mails):
        """Retourne une liste de mails dedupliquee pour l'affichage inbox.

        Regle : meme sender normalisé + meme subject normalisé dans les
        dernieres 48h -> on garde le plus recent et on ajoute un
        compteur "+N" sur la carte via la cle 'dupe_count'.
        """
        if not self.optim_settings.get("dedupe_view"):
            return mails
        try:
            groups = {}
            for m in mails:
                sender = (m.get("sender","").strip().lower())
                subj = re.sub(r"^(re|fw|fwd|tr)\s*:\s*", "",
                              (m.get("subject","").strip().lower()))
                key = f"{sender}|{subj}"
                groups.setdefault(key, []).append(m)
            out = []
            for key, items in groups.items():
                if len(items) <= 1:
                    out.extend(items)
                    continue
                # Garde le plus recent (basé sur position dans la liste,
                # la plus recente arrive en dernier a la reception)
                items[-1]["dupe_count"] = len(items) - 1
                out.append(items[-1])
            return out
        except Exception:
            return mails

    # =============================================================
    #     F. CATEGORISATION SERVEUR
    # =============================================================
    def _scan_categorize(self, mails):
        for m in mails:
            uid = str(m.get("uid") or "")
            t = (m.get("mail_type") or "").lower()
            folder = CATEGORY_FOLDERS.get(t)
            if not uid or not folder:
                continue
            entry = self._mail_tracking.get("mails", {}).get(uid, {})
            if entry.get("categorized"):
                continue
            key = f"cat:{uid}"
            if key in self._optim_pending_keys:
                continue
            self._optim_pending_keys.add(key)
            self._request_action(
                "mail_categorize",
                title=f"Ranger dans {folder} : {m.get('subject','')[:40]}",
                description=f"Type detecte : {t}. Le mail sera deplace sur le serveur.",
                action_data={"uid": uid, "folder": folder,
                             "subject": m.get("subject","")},
            )

    def categorize_mail_on_server(self, uid, folder, subject=""):
        if not uid or not folder:
            return False
        try:
            imap_host, _, _, _ = self._get_email_servers()
            mail = imaplib.IMAP4_SSL(imap_host)
            mail.login(self.user_email, self.user_app_pass)
            mail.select("inbox")
            # cree la hierarchie si besoin
            try: mail.create(folder)
            except Exception: pass
            uid_b = uid.encode() if isinstance(uid, str) else uid
            ok = False
            try:
                typ, _ = mail.uid("COPY", uid_b, folder)
                if typ == "OK":
                    ok = True
            except Exception:
                ok = False
            if ok:
                try:
                    mail.uid("STORE", uid_b, "+FLAGS", "(\\Deleted)")
                    mail.expunge()
                except Exception:
                    pass
            mail.logout()
            if ok:
                store = self._mail_tracking.setdefault("mails", {}).setdefault(
                    str(uid), {})
                store["categorized"] = True
                store["folder"] = folder
                self._save_mail_tracking()
                database.add_experience_entry(
                    USERNAME, "mail_categorized",
                    f"Mail range dans {folder} : {subject[:50]}",
                    "success", 1.0)
                self.root.after(0, lambda f=folder, s=subject: self._show_toast(
                    f"🗂 {f} : {s[:30]}"))
            return ok
        except Exception as e:
            print(f"Erreur categorize : {e}")
            return False

    # =============================================================
    #     G. SUPPRESSION DES ACCUSES DE RECEPTION
    # =============================================================
    def _scan_receipts(self, mails):
        for m in mails:
            subj = m.get("subject","") or ""
            uid = str(m.get("uid") or "")
            if not uid:
                continue
            # signal fort : pattern dans le sujet
            if not RECEIPT_RE.match(subj.strip()):
                # Ou sender postmaster/mailer-daemon
                se = self._extract_sender_email(m.get("sender","")) or ""
                if not any(tag in se for tag in
                           ("postmaster", "mailer-daemon", "mail-daemon",
                            "mailerdaemon", "noreply-receipts")):
                    continue
            key = f"receipt:{uid}"
            if key in self._optim_pending_keys:
                continue
            self._optim_pending_keys.add(key)
            self._request_action(
                "mail_delete_receipt",
                title=f"Supprimer accuse : {subj[:40]}",
                description=f"Accuse de reception / rapport de livraison detecte.",
                action_data={"uid": uid, "subject": subj},
            )

    # =============================================================
    #     H. RESUME MATINAL (digest)
    # =============================================================
    def _send_morning_digest(self):
        """Envoie un resume matinal de l'etat actuel de l'inbox."""
        if not self.user_email or not self.user_app_pass:
            return
        try:
            mails = list(getattr(self, "_inbox_mails", []))
            by_type = {}
            for m in mails:
                t = (m.get("mail_type") or "inconnu").lower()
                by_type.setdefault(t, []).append(m)

            today = datetime.now().strftime("%d/%m/%Y")
            lines = [f"RESUME INBOX MATINAL — {today}",
                     f"Total : {len(mails)} mail(s) en inbox",
                     ""]
            priority = ["facture", "relance", "juridique", "contractuel",
                        "professionnel", "cv", "invitation", "support",
                        "personnel", "newsletter", "spam", "inconnu"]
            labels = {
                "facture":"💳 Factures", "relance":"📣 Relances",
                "juridique":"⚖ Juridique", "contractuel":"📋 Contractuel",
                "professionnel":"💼 Pro", "cv":"📄 CV",
                "invitation":"🎟 Invitations", "support":"🛠 Support",
                "personnel":"💬 Perso", "newsletter":"📰 Newsletters",
                "spam":"🗑 Spam", "inconnu":"❓ Autres",
            }
            for t in priority:
                items = by_type.get(t, [])
                if not items:
                    continue
                lines.append(f"── {labels.get(t,t)} ({len(items)}) ──")
                for m in items[:8]:
                    sender = (m.get("sender","")[:40] or "?").strip()
                    subj = (m.get("subject","")[:60] or "").strip()
                    lines.append(f"  • {sender} — {subj}")
                if len(items) > 8:
                    lines.append(f"  ... et {len(items)-8} de plus")
                lines.append("")
            lines.append("── Fin du resume ──")
            body = "\n".join(lines)

            _, smtp_host, smtp_port, _ = self._get_email_servers()
            msg = MIMEMultipart()
            msg["From"] = self.user_email
            msg["To"]   = self.user_email
            msg["Subject"] = f"Resume inbox matinal — {today}"
            msg.attach(MIMEText(body, "plain", "utf-8"))
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()
            server.login(self.user_email, self.user_app_pass)
            server.send_message(msg)
            server.quit()
            try:
                database.add_experience_entry(
                    USERNAME, "morning_digest_sent",
                    f"Resume inbox matinal envoye ({len(mails)} mails)",
                    "success", 1.0)
            except Exception:
                pass
        except Exception as e:
            print(f"Erreur morning digest : {e}")

    # =============================================================
    #     I. FOCUS MODE (UI)
    # =============================================================
    def toggle_focus_mode(self):
        self._focus_mode = not self._focus_mode
        self.optim_settings["focus_mode"] = self._focus_mode
        self._show_toast(
            f"Mode Focus {'ACTIVE' if self._focus_mode else 'DESACTIVE'}")
        try:
            self._render_inbox_cards()
        except Exception:
            pass

    def apply_focus_filter(self, mails):
        if not self._focus_mode:
            return mails
        out = []
        for m in mails:
            t = (m.get("mail_type") or "").lower()
            urg = (m.get("urgency") or "").lower()
            if urg == "haute" or t in FOCUS_TYPES:
                out.append(m)
        return out

    # =============================================================
    #     PIPE COMMUNE : CREATION DE PERMISSION + AUTO-APPROVAL SI
    #     LACHER PRISE
    # =============================================================
    def _request_action(self, req_type, title, description, action_data):
        """Cree une permission_request. En Lacher Prise ON, l'auto-approuve
        et execute immediatement. Sinon attend validation utilisateur.
        """
        try:
            req_id = database.add_permission_request(
                USERNAME, req_type, title, description, action_data)
            if getattr(self, "lacher_prise_mode", False):
                # Auto-approuvee : on execute tout de suite
                resolved = database.resolve_permission_request(req_id, "approved")
                if resolved:
                    try:
                        self._execute_permission_action(resolved)
                    except Exception as e:
                        print(f"Erreur auto-exec {req_type} : {e}")
            else:
                try:
                    self.root.after(0, lambda: self._show_toast(
                        f"⚡ Permission : {title[:40]}"))
                except Exception:
                    pass
        except Exception as e:
            print(f"Erreur _request_action {req_type} : {e}")
