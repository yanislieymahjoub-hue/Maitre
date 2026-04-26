"""Commandes chat sur la boite mail.

Extrait de email_mixin.py pour separer la logique IMAP/SMTP (envoi, fetch,
delete bas niveau) des protocoles textuels declenches par le chat IA :

- INBOX_ACTION_START / INBOX_ACTION_END : suppression / archivage en lot
- MAIL_SEARCH_START / MAIL_SEARCH_END   : recherche / lecture

Methodes attendues sur self (fournies par EmailMixin et autres) :
- self._inbox_mails (list[dict])
- self.lacher_prise_mode (bool)
- self.user_email, self.user_app_pass
- self.delete_mail_on_server(uid, subject)
- self.archive_mail_on_server(uid, subject)
- self._fetch_emails_logic()
- self.add_message("SYSTEM", text)
- self.history (list[dict]) + self.save_memory()
- self.root.after(...)
"""
from __future__ import annotations
import re
import threading
from datetime import datetime
from email.utils import parsedate_to_datetime

import database
from modules.config import USERNAME


class EmailCommandsMixin:
    # ==========================================================
    # COMMANDE CHAT : NETTOYAGE INBOX (suppression / archivage en lot)
    # ==========================================================
    def _process_inbox_action_command(self, raw_text):
        """Parse et execute un bloc INBOX_ACTION_START/END genere par l'IA."""
        if "INBOX_ACTION_START" not in raw_text or "INBOX_ACTION_END" not in raw_text:
            return False
        try:
            block_m = re.search(r"INBOX_ACTION_START(.*?)INBOX_ACTION_END",
                                raw_text, re.DOTALL)
            if not block_m:
                return False
            block = block_m.group(1)

            def _field(name):
                m = re.search(rf"{name}\s*:\s*(.+)", block)
                return m.group(1).strip() if m else ""

            action = (_field("ACTION") or "delete").lower()
            if action not in ("delete", "archive"):
                action = "delete"
            mtype  = (_field("TYPE") or "spam").lower()
            sender = _field("SENDER").lower()
            keyword= _field("KEYWORD").lower()
            try:
                older_days = int(_field("OLDER_DAYS") or "0")
            except Exception:
                older_days = 0
            try:
                limit = int(_field("LIMIT") or "0")
            except Exception:
                limit = 0

            mails = list(getattr(self, "_inbox_mails", []) or [])
            if not mails and self.user_email and self.user_app_pass:
                try:
                    self._fetch_emails_logic()
                    mails = list(getattr(self, "_inbox_mails", []) or [])
                except Exception as _e:
                    print(f"Erreur fetch avant nettoyage : {_e}")

            if not mails:
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM",
                    "⚠ Aucun mail dans l'inbox analysee — rien a nettoyer."))
                return True

            now = datetime.now()
            type_groups = {
                "spam":       {"spam", "phishing", "arnaque"},
                "phishing":   {"phishing", "arnaque"},
                "newsletter": {"newsletter"},
                "relance":    {"relance"},
                "facture":    {"facture"},
                "old":        set(),
                "tous":       None,
                "personnalise": None,
            }
            wanted_types = type_groups.get(mtype, {"spam"})

            def _parse_date(md):
                try:
                    d = md.get("date_str") or ""
                    return parsedate_to_datetime(d)
                except Exception:
                    return None

            matched = []
            for m in mails:
                t = (m.get("mail_type") or "inconnu").lower()
                if wanted_types is not None and t not in wanted_types:
                    if mtype != "old":
                        continue
                if sender:
                    snd = (m.get("sender") or "").lower()
                    if sender not in snd:
                        continue
                if keyword:
                    subj = (m.get("subject") or "").lower()
                    if keyword not in subj:
                        continue
                if older_days > 0:
                    dt = _parse_date(m)
                    if not dt:
                        continue
                    try:
                        age_days = (now - dt.replace(tzinfo=None)).days
                    except Exception:
                        age_days = 0
                    if age_days < older_days:
                        continue
                if not m.get("uid"):
                    continue
                matched.append(m)

            if limit > 0:
                matched = matched[:limit]

            if not matched:
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM",
                    f"🔎 Aucun mail correspondant a {mtype}"
                    + (f" (sender={sender})" if sender else "")
                    + (f" (keyword={keyword})" if keyword else "")
                    + (f" (>{older_days}j)" if older_days else "")
                    + "."))
                return True

            parts = [mtype]
            if sender:    parts.append(f"sender~{sender}")
            if keyword:   parts.append(f"keyword~{keyword}")
            if older_days:parts.append(f">{older_days}j")
            desc_filter = ", ".join(parts)
            act_verb = "Supprimer" if action == "delete" else "Archiver"

            # --- LACHER PRISE ON : execution directe ---
            if getattr(self, "lacher_prise_mode", False):
                def _bulk_worker(items, act):
                    ok_count = 0
                    for m in items:
                        uid = m.get("uid")
                        subj = m.get("subject", "")
                        try:
                            if act == "delete":
                                if self.delete_mail_on_server(uid, subj):
                                    ok_count += 1
                            else:
                                if self.archive_mail_on_server(uid, subj):
                                    ok_count += 1
                        except Exception as e:
                            print(f"Erreur bulk {act} : {e}")
                    try:
                        self.root.after(0, lambda n=ok_count, a=act_verb.lower():
                            self.add_message(
                                "SYSTEM",
                                f"✅ {n} mail(s) {a}s ({desc_filter})."))
                    except Exception:
                        pass
                self.root.after(0, lambda n=len(matched), v=act_verb.lower():
                    self.add_message(
                        "SYSTEM",
                        f"🧹 {v.capitalize()} {n} mail(s) — {desc_filter}..."))
                threading.Thread(target=_bulk_worker,
                                 args=(matched, action),
                                 daemon=True).start()
                return True

            # --- LACHER PRISE OFF : permission unique groupee ---
            try:
                uids = [m.get("uid") for m in matched if m.get("uid")]
                subjects_preview = "\n".join(
                    f"  • {(m.get('sender') or '?')[:30]} — {(m.get('subject') or '')[:60]}"
                    for m in matched[:8])
                if len(matched) > 8:
                    subjects_preview += f"\n  ... et {len(matched)-8} de plus"
                kind = "mail_bulk_delete_spam" if action == "delete" else "mail_bulk_archive"
                database.add_permission_request(
                    USERNAME, kind,
                    f"{act_verb} {len(matched)} mail(s) ({desc_filter})",
                    subjects_preview,
                    {"uids": uids, "filter": desc_filter, "action": action})
                self.root.after(0, lambda n=len(matched), v=act_verb.lower():
                    self.add_message(
                        "SYSTEM",
                        f"⚡ {n} mail(s) a {v} — ouvre Permissions pour valider."))
            except Exception as e:
                print(f"Erreur permission bulk clean : {e}")
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM", "❌ Impossible de creer la permission de nettoyage."))
            return True
        except Exception as e:
            print(f"Erreur _process_inbox_action_command : {e}")
            return False

    # ==========================================================
    # COMMANDE CHAT : RECHERCHE / LECTURE DE MAILS
    # ==========================================================
    def _process_mail_search_command(self, raw_text):
        """Parse un bloc MAIL_SEARCH_START/END et affiche les mails
        correspondants dans le chat (via un message SYSTEM)."""
        if "MAIL_SEARCH_START" not in raw_text or "MAIL_SEARCH_END" not in raw_text:
            return False
        try:
            block_m = re.search(r"MAIL_SEARCH_START(.*?)MAIL_SEARCH_END",
                                raw_text, re.DOTALL)
            if not block_m:
                return False
            block = block_m.group(1)

            def _field(name):
                m = re.search(rf"{name}\s*:\s*(.+)", block)
                return m.group(1).strip() if m else ""

            sender  = _field("SENDER").lower()
            keyword = _field("KEYWORD").lower()
            mtype   = _field("TYPE").lower()
            try:
                within_days = int(_field("WITHIN_DAYS") or "0")
            except Exception:
                within_days = 0
            try:
                limit = int(_field("LIMIT") or "5")
            except Exception:
                limit = 5
            limit = max(1, min(limit, 20))

            if not (sender or keyword or mtype):
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM",
                    "🔎 Recherche ignoree : aucun critere fourni."))
                return True

            mails = list(getattr(self, "_inbox_mails", []) or [])
            if not mails and self.user_email and self.user_app_pass:
                try:
                    self._fetch_emails_logic()
                    mails = list(getattr(self, "_inbox_mails", []) or [])
                except Exception as _e:
                    print(f"Erreur fetch avant search : {_e}")

            if not mails:
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM", "🔎 Aucun mail dans la boite pour le moment."))
                return True

            mails = [m for m in mails
                     if (m.get("mail_type") or "").lower() != "self"]

            now = datetime.now()

            def _age_days(md):
                try:
                    d = md.get("date_str") or ""
                    dt = parsedate_to_datetime(d)
                    return (now - dt.replace(tzinfo=None)).days
                except Exception:
                    return 99999

            matched = []
            for m in mails:
                if sender:
                    snd = (m.get("sender") or "").lower()
                    if sender not in snd:
                        continue
                if keyword:
                    subj = (m.get("subject") or "").lower()
                    bod  = (m.get("body") or "").lower()
                    if keyword not in subj and keyword not in bod:
                        continue
                if mtype:
                    t = (m.get("mail_type") or "").lower()
                    if mtype not in t:
                        continue
                if within_days > 0:
                    if _age_days(m) > within_days:
                        continue
                matched.append(m)

            matched.sort(key=_age_days)
            matched = matched[:limit]

            if not matched:
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM",
                    f"🔎 Aucun mail ne correspond"
                    + (f" (sender~{sender})" if sender else "")
                    + (f" (keyword~{keyword})" if keyword else "")
                    + (f" (type~{mtype})" if mtype else "")
                    + (f" (<{within_days}j)" if within_days else "") + "."))
                return True

            lines = [f"🔎 {len(matched)} mail(s) trouve(s) :"]
            for i, m in enumerate(matched, 1):
                snd = (m.get("sender") or "?")[:40]
                sub = (m.get("subject") or "")[:70]
                sm  = (m.get("summary") or "").strip()
                if len(sm) > 120:
                    sm = sm[:117] + "..."
                d = m.get("date_str", "")
                lines.append(f"{i}. {snd}  —  {sub}")
                if d:
                    lines.append(f"    📅 {d}")
                if sm:
                    lines.append(f"    📝 {sm}")
            msg = "\n".join(lines)

            try:
                self.history.append({"role": "system", "content": msg})
                self.save_memory()
            except Exception:
                pass

            self.root.after(0, lambda m=msg: self.add_message("SYSTEM", m))
            return True
        except Exception as e:
            print(f"Erreur _process_mail_search_command : {e}")
            return False
