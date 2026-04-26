"""Gestion des emails (SMTP, IMAP, pièces jointes)."""
import os
import sys
import json
import time
import threading
import re
import base64
import math
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
import mimetypes
import smtplib
import imaplib
import email
import subprocess
import requests
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.header import decode_header
from datetime import datetime, date, timedelta
import database
from modules.config import (
    USERNAME, USER_PERMS, USER_LEVEL, USER_ROLE, USER_BIZ,
    MEMORY_FILE, AGENDA_FILE,
    SYSTEM_INSTRUCTION, INBOX_SYSTEM_PROMPT,
    API_KEY, URL, MODELE, AI_TEMPERATURE,
    couleur_chat_pale, couleur_bubble_pale, couleur_hub_pale,
    couleur_option_pale, couleur_mail_pale, couleur_log_pale,
    couleur_accent, couleur_inbox, couleur_agenda, couleur_reseau,
    couleur_permissions, couleur_gold,
    HUB_ANIM_SPEED,
)


class EmailMixin:
    # ==========================================
    # Connexion IMAP persistante (pool partage)
    # ==========================================
    def _get_imap_pool(self):
        """Retourne (et lazy-cree) le pool IMAP partage."""
        pool = getattr(self, "_imap_pool_inst", None)
        if pool is not None:
            # Detecte un changement de credentials
            if pool.email == self.user_email and pool.password == self.user_app_pass:
                return pool
            try:
                pool.close()
            except Exception:
                pass
        from modules.imap_pool import ImapPool
        host, _, _, _ = self._get_email_servers()
        pool = ImapPool(self.user_email, self.user_app_pass, host=host)
        self._imap_pool_inst = pool
        return pool

    def _imap_session(self):
        """Context manager : renvoie une connexion IMAP live (via le pool).

        A utiliser ainsi :
            with self._imap_session() as mail:
                mail.select("INBOX")
                ...
        NE PAS appeler mail.close() ni mail.logout() : la connexion est reutilisee.
        """
        return self._get_imap_pool().connection()

    # ==========================================
    # Detection multi-provider (Gmail / Outlook)
    # ==========================================
    def _get_email_servers(self):
        """Retourne (imap_host, smtp_host, smtp_port, is_gmail) selon le
        domaine de l'email utilisateur. Defaut = Gmail."""
        addr = (self.user_email or "").lower()
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        # Outlook / Hotmail / Live / MSN
        if domain in ("outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr",
                      "live.com", "live.fr", "msn.com", "office365.com"):
            return ("outlook.office365.com", "smtp-mail.outlook.com", 587, False)
        # Yahoo
        if domain in ("yahoo.com", "yahoo.fr", "ymail.com"):
            return ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 587, False)
        # Defaut : Gmail
        return ("imap.gmail.com", "smtp.gmail.com", 587, True)

    def _get_attachment_data(self, path):
        """Lit le contenu du fichier (Texte ou PDF) pour l'IA."""
        ext = os.path.splitext(path)[1].lower()

        if ext == '.pdf':
            try:
                import fitz
                text = ""
                with fitz.open(path) as doc:
                    for page in doc:
                        text += page.get_text()
                return f"\n\n--- CONTENU DU PDF '{os.path.basename(path)}' ---\n{text}\n--- FIN ---"
            except ImportError:
                return "\n[Erreur : PyMuPDF n'est pas installé. Tapez 'pip install pymupdf' dans le terminal]"
            except Exception as e:
                return f"\n[Erreur lecture PDF : {e}]"

        if ext == '.docx':
            try:
                import docx
                doc = docx.Document(path)
                text = "\n".join([p.text for p in doc.paragraphs])
                return f"\n\n--- CONTENU DU DOCX '{os.path.basename(path)}' ---\n{text}\n--- FIN ---"
            except:
                return "\n[Erreur lecture DOCX]"

        text_extensions = ['.txt', '.py', '.js', '.html', '.css', '.json', '.log', '.md', '.csv']
        if ext in text_extensions:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                return f"\n\n--- CONTENU DU FICHIER '{os.path.basename(path)}' ---\n{content}\n--- FIN ---"
            except Exception as e:
                return f"\n[Erreur lecture texte : {e}]"

        return ""

    def _extract_attachment_text(self, part):
        """Extrait le texte d'une pièce jointe (PDF, DOCX, TXT, etc.)."""
        filename = part.get_filename() or ""
        if not filename:
            return None, None

        # Décodage du nom de fichier si encodé
        decoded = decode_header(filename)[0]
        if isinstance(decoded[0], bytes):
            filename = decoded[0].decode(decoded[1] or "utf-8", errors="ignore")

        ext = os.path.splitext(filename)[1].lower()
        payload = part.get_payload(decode=True)
        if not payload:
            return filename, None

        text = None

        # --- PDF ---
        if ext == ".pdf":
            try:
                import fitz
                import io
                with fitz.open(stream=payload, filetype="pdf") as doc:
                    text = ""
                    for page in doc:
                        text += page.get_text()
                text = text[:3000]  # Limiter pour l'IA
            except ImportError:
                text = "[PyMuPDF non installé - pip install pymupdf]"
            except Exception as e:
                text = f"[Erreur lecture PDF : {e}]"

        # --- DOCX ---
        elif ext == ".docx":
            try:
                import docx
                import io
                doc = docx.Document(io.BytesIO(payload))
                text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
                text = text[:3000]
            except ImportError:
                text = "[python-docx non installé - pip install python-docx]"
            except Exception as e:
                text = f"[Erreur lecture DOCX : {e}]"

        # --- Texte brut / code ---
        elif ext in [".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".js"]:
            try:
                text = payload.decode("utf-8", errors="ignore")[:3000]
            except Exception:
                text = None

        return filename, text

    def delete_mail_on_server(self, uid, subject=""):
        """Supprime definitivement un mail du serveur Gmail (via IMAP).

        Strategie :
        - Pour Gmail : on ajoute le label '\\Trash' (deplace dans la Corbeille),
          puis on expunge. C'est la methode la plus fiable pour Gmail.
        - Fallback : on marque simplement le flag \\Deleted puis on expunge.
        """
        if not uid:
            return False
        if not self.user_email or not self.user_app_pass:
            self.root.after(0, lambda: self.add_message(
                "SYSTEM", "❌ Email non configure : impossible de supprimer."))
            return False
        try:
            _, _, _, is_gmail = self._get_email_servers()
            with self._imap_session() as mail:
                mail.select("inbox")
                uid_b = uid.encode() if isinstance(uid, str) else uid

                ok = False
                # Methode Gmail : label Trash (deplace dans la Corbeille Gmail)
                if is_gmail:
                    try:
                        typ, _ = mail.uid("STORE", uid_b, "+X-GM-LABELS", "\\Trash")
                        if typ == "OK":
                            ok = True
                    except Exception:
                        ok = False
                else:
                    # Outlook / autre : copie dans le dossier Corbeille/Deleted
                    # puis flag Deleted. On essaie plusieurs noms de dossier.
                    trash_candidates = [
                        "Deleted Items", "Deleted", "Trash",
                        "[Gmail]/Trash", "Corbeille", "Elements supprimes",
                    ]
                    for folder in trash_candidates:
                        try:
                            typ, _ = mail.uid("COPY", uid_b, folder)
                            if typ == "OK":
                                ok = True
                                break
                        except Exception:
                            continue

                # Fallback IMAP standard : flag \\Deleted
                if not ok:
                    try:
                        typ, _ = mail.uid("STORE", uid_b, "+FLAGS", "(\\Deleted)")
                        if typ == "OK":
                            ok = True
                    except Exception:
                        ok = False

                try:
                    mail.expunge()
                except Exception:
                    pass
                # Pas de close()/logout() : la connexion est partagee dans le pool.

            if ok:
                try:
                    database.add_experience_entry(
                        USERNAME, "mail_deleted",
                        f"Mail supprime du serveur : {subject[:60]}",
                        "success", 1.0)
                except Exception:
                    pass
                self.root.after(0, lambda s=subject: self._show_toast(
                    f"🗑 Mail supprime : {s[:40]}"))
                return True
            else:
                self.root.after(0, lambda s=subject: self.add_message(
                    "SYSTEM", f"❌ Echec suppression mail : {s[:40]}"))
                return False
        except Exception as e:
            err = str(e)[:80]
            self.root.after(0, lambda m=err: self.add_message(
                "SYSTEM", f"❌ Erreur suppression mail : {m}"))
            return False

    # ===================================================================
    # CACHE DES RESUMES DE MAIL (UID -> mail_data)
    # ===================================================================
    def _mail_cache_path(self):
        return f"mail_summaries_{USERNAME}.json"

    def _load_mail_cache(self):
        try:
            p = self._mail_cache_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            print(f"Erreur load mail cache : {e}")
        return {}

    def _save_mail_cache(self, cache):
        try:
            # Cap 5000 mails pour ne pas exploser le fichier
            if len(cache) > 5000:
                keys = list(cache.keys())
                for k in keys[:-5000]:
                    cache.pop(k, None)
            with open(self._mail_cache_path(), "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            print(f"Erreur save mail cache : {e}")

    def _parse_mail_message(self, msg):
        """Extrait subject/sender/date/body/attachments d'un email.Message."""
        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding or "utf-8", errors="ignore")
        sender = msg.get("From", "Inconnu")
        date_str = msg.get("Date", "")
        body = ""
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition", ""))
                content_type = part.get_content_type()
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    if not body:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="ignore")
                elif "attachment" in content_disposition or part.get_filename():
                    fname, ftext = self._extract_attachment_text(part)
                    if fname:
                        attachments.append((fname, ftext))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="ignore")
        return sender, subject or "(sans sujet)", date_str, body, attachments

    def _dispatch_attachment_save(self, mimetmsg, uid_str, sender, subject,
                                  date_str, attachments):
        """Selon le mode (Lacher Prise ou non), sauve les PJ ou cree une
        permission distinctive.

        Tout est lance dans un thread daemon : aucune latence sur le pipeline
        de triage. Si le module attachment_extractor n'est pas importable,
        on log et on ignore (jamais bloquant).
        """
        atts_snapshot = [fn for fn, _ in attachments]
        sender_l = sender or ""
        subject_l = subject or ""
        date_l = date_str or ""
        uid_l = str(uid_str)
        msg_ref = mimetmsg

        def _worker():
            try:
                from modules.attachment_extractor import (
                    save_attachments, save_pending,
                )
            except Exception as e:
                print(f"[PJ] module attachment_extractor indisponible : {e}")
                return
            try:
                if getattr(self, "lacher_prise_mode", False):
                    saved = save_attachments(msg_ref, sender_l, subject_l, date_l)
                    if saved:
                        try:
                            n = len(saved)
                            self.root.after(0, lambda nn=n: self._show_toast(
                                f"📎 {nn} piece(s) jointe(s) sauvegardee(s)"))
                        except Exception:
                            pass
                        # Chaine facture : pour chaque PJ classee 'Factures',
                        # extrait les champs et l'ajoute en DB finance.
                        try:
                            if hasattr(self, "_chain_invoice_from_attachments"):
                                self._chain_invoice_from_attachments(
                                    saved, sender=sender_l,
                                    subject=subject_l, uid=uid_l)
                        except Exception as e:
                            print(f"[finance] chain LP : {e}")
                else:
                    pending = save_pending(msg_ref, uid_l, sender_l, subject_l, date_l)
                    if pending:
                        fnames = atts_snapshot
                        head = ", ".join(fnames[:3])
                        if len(fnames) > 3:
                            head += "..."
                        try:
                            database.add_permission_request(
                                USERNAME, "attachment_extract",
                                f"📎 Sauvegarder {len(fnames)} PJ — {sender_l[:40]}",
                                f"Mail : {subject_l[:80]}\nFichiers : {head}",
                                {
                                    "pending_dir": pending,
                                    "sender": sender_l,
                                    "subject": subject_l,
                                    "date_str": date_l,
                                    "filenames": fnames,
                                    "highlight": True,
                                },
                            )
                            try:
                                self.root.after(0, lambda: self._show_toast(
                                    "📎 Pieces jointes en attente de validation"))
                            except Exception:
                                pass
                        except Exception as e:
                            print(f"[PJ] add_permission_request a echoue : {e}")
            except Exception as e:
                print(f"[PJ] worker exception : {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _fetch_emails_logic(self):
        try:
            # L'auto-fetch peut declencher cette methode avant que l'UI inbox
            # soit construite. On initialise les attributs necessaires.
            if not hasattr(self, "_inbox_mails"):
                self._inbox_mails = []
            if not hasattr(self, "_inbox_active_filter"):
                self._inbox_active_filter = "TOUT"

            # Si pas d'email configure, on sort silencieusement
            if not getattr(self, "user_email", None) or not getattr(self, "user_app_pass", None):
                return

            # Charge le cache des resumes deja calcules
            cache = self._load_mail_cache()

            imap_host, _, _, _ = self._get_email_servers()
            mail = imaplib.IMAP4_SSL(imap_host)
            mail.login(self.user_email, self.user_app_pass)
            mail.select("inbox")

            # UID SEARCH pour avoir des identifiants stables
            status, messages = mail.uid("search", None, "ALL")
            mail_ids = messages[0].split()
            # Cap eleve a 2000 mails pour couvrir beaucoup plus de boites
            MAX_FETCH = 2000
            if len(mail_ids) > MAX_FETCH:
                last_ids = mail_ids[-MAX_FETCH:]
            else:
                last_ids = mail_ids

            # Detection des mails NON LUS
            unseen_uids = set()
            try:
                st, un_msgs = mail.uid("search", None, "UNSEEN")
                if st == "OK" and un_msgs and un_msgs[0]:
                    for u in un_msgs[0].split():
                        try:
                            unseen_uids.add(u.decode() if isinstance(u, bytes) else str(u))
                        except Exception:
                            pass
            except Exception as _e:
                print(f"Erreur UID UNSEEN : {_e}")

            # Convertit en strings
            uid_strs = []
            for m_id in reversed(last_ids):
                try:
                    uid_strs.append(m_id.decode() if isinstance(m_id, bytes) else str(m_id))
                except Exception:
                    uid_strs.append(str(m_id))

            # Separation UIDs deja en cache / UIDs nouveaux a analyser
            cached_uids = [u for u in uid_strs if u in cache]
            new_uids = [u for u in uid_strs if u not in cache]

            # --- RENDU IMMEDIAT DES MAILS EN CACHE ---
            # L'utilisateur voit instantanement tous les mails deja analyses.
            # Pendant ce temps, les NOUVEAUX mails seront fetches et analyses
            # en arriere-plan, puis ajoutes au rendu.
            # NB : les mails envoyes par soi-meme (rapports quotidiens,
            # auto-notifications) sont AFFICHES normalement mais ont un
            # mail_type="self" pour que le triage et l'analyse IA les ignorent.
            cached_now = []
            for u in cached_uids:
                md = dict(cache[u])
                md["seen"] = u not in unseen_uids
                md["body"] = ""
                cached_now.append(md)
            self._inbox_mails = cached_now

            if hasattr(self, "inbox_scroll"):
                # Efface le loader et affiche les mails en cache
                self.root.after(0, lambda: [w.destroy() for w in self.inbox_scroll.winfo_children()])
                self.root.after(0, self._render_inbox_cards)
                # Affiche un compteur de progression pour les nouveaux
                if new_uids:
                    try:
                        self.root.after(0, lambda n=len(new_uids), c=len(cached_uids):
                            self._show_toast(f"📬 {c} mail(s) affichés, {n} nouveaux en analyse..."))
                    except Exception:
                        pass

            # --- 1) Mails NOUVEAUX : fetch IMAP + AI parallelise ---
            new_results = []  # liste de mail_data complets
            if new_uids:
                # Fetch IMAP body pour chaque nouveau UID
                fetched_new = []
                for uid_str in new_uids:
                    try:
                        m_id_bytes = uid_str.encode() if isinstance(uid_str, str) else uid_str
                        res, msg_data = mail.uid("fetch", m_id_bytes, "(BODY.PEEK[])")
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                mimetmsg = email.message_from_bytes(response_part[1])
                                sender, subject, date_str, body, attachments = \
                                    self._parse_mail_message(mimetmsg)
                                # Short-circuit anti-boucle : les mails
                                # auto-envoyes (rapports, notifications...)
                                # sont AFFICHES dans l'inbox mais on ne
                                # gaspille pas d'appel IA dessus et le triage
                                # les ignorera.
                                _snd = (sender or "").lower()
                                _me = (self.user_email or "").lower()
                                if _me and _me in _snd:
                                    md_self = {
                                        "uid": uid_str, "sender": sender,
                                        "subject": subject,
                                        "summary": "Auto-envoye (rapport / notification).",
                                        "urgency": "low", "action": "ignore",
                                        "date_str": date_str,
                                        "attach_summaries": [],
                                        "warning": "", "mail_type": "self",
                                        "agenda_suggestion": None,
                                        "reply_info": None,
                                        "seen": uid_str not in unseen_uids,
                                        "body": (body or "")[:500],
                                    }
                                    # Ajout immediat au rendu
                                    self._inbox_mails.append(md_self)
                                    # Cache sans body (leger)
                                    cache_entry = {k: v for k, v in md_self.items() if k != "body"}
                                    cache[uid_str] = cache_entry
                                    try:
                                        if hasattr(self, "inbox_scroll"):
                                            self.root.after(0, self._render_inbox_cards)
                                    except Exception:
                                        pass
                                    break
                                fetched_new.append((uid_str, sender, subject, body, date_str, attachments))
                                # --- Extraction PJ : auto en mode lacher prise,
                                # sinon demande de permission distinctive.
                                if attachments:
                                    self._dispatch_attachment_save(
                                        mimetmsg, uid_str, sender, subject,
                                        date_str, attachments,
                                    )
                                break
                    except Exception as e:
                        print(f"Erreur fetch UID {uid_str} : {e}")

                # AI summarize en parallele doux (2 workers + throttle)
                # pour ne pas saturer Mistral (429 rate limit).
                import random as _rnd
                # Semaphore interne pour limiter les requetes simultanees
                # meme si plusieurs threads arrivent en meme temps
                _ai_sem = threading.Semaphore(2)

                # Pre-filtre local : evite un appel Mistral sur les mails
                # evidents (newsletter / spam / notifs automatiques).
                try:
                    from modules.mail_prefilter import prefilter as _prefilter
                except Exception:
                    _prefilter = None

                def _summarize_one(item):
                    uid, sender, subject, body, date_str, attachments = item
                    try:
                        # --- Pass 1 : pre-classification locale gratuite ---
                        pre = None
                        if _prefilter is not None and not attachments:
                            try:
                                pre = _prefilter(sender, subject, body)
                            except Exception:
                                pre = None

                        if pre is not None:
                            # On evite totalement Mistral pour ce mail.
                            summary = pre.get("resume", "")
                            urgency_fr = pre.get("urgence", "faible")
                            # Mapping FR -> champ interne
                            urgency = urgency_fr
                            action = pre.get("action", [])
                            attach_summaries = []
                            warning = pre.get("warning")
                            mail_type = pre.get("type", "inconnu")
                            agenda_sug = pre.get("agenda_suggestion")
                        else:
                            # --- Pass 2 : appel Mistral classique ---
                            with _ai_sem:
                                # Petit delai aleatoire pour desynchroniser les
                                # workers et eviter les bursts simultanes
                                time.sleep(_rnd.uniform(0.2, 0.6))
                                summary, urgency, action, attach_summaries, warning, mail_type, agenda_sug = \
                                    self._ai_summarize_mail(sender, subject, body, attachments)

                        return {
                            "uid": uid,
                            "sender": sender, "subject": subject, "summary": summary,
                            "urgency": urgency, "action": action, "date_str": date_str,
                            "attach_summaries": attach_summaries, "warning": warning,
                            "mail_type": mail_type, "agenda_suggestion": agenda_sug,
                            "reply_info": None,
                            "seen": uid not in unseen_uids,
                            "body": body[:2000],
                        }
                    except Exception as e:
                        print(f"Erreur summarize {uid} : {e}")
                        return None

                # Stagger la soumission pour repartir la charge dans le temps
                with ThreadPoolExecutor(max_workers=2) as ex:
                    futures = []
                    for it in fetched_new:
                        futures.append(ex.submit(_summarize_one, it))
                        time.sleep(0.15)  # petit decalage a la soumission
                    for fut in as_completed(futures):
                        md = fut.result()
                        if md:
                            new_results.append(md)
                            # Cache sans le body (allege le fichier)
                            cache_entry = {k: v for k, v in md.items() if k != "body"}
                            cache[md["uid"]] = cache_entry
                            # Rendu progressif : on met a jour l'UI au fur
                            # et a mesure que les mails sont analyses,
                            # plutot que tout d'un coup a la fin.
                            try:
                                self._inbox_mails.append(md)
                                if hasattr(self, "inbox_scroll"):
                                    self.root.after(0, self._render_inbox_cards)
                            except Exception:
                                pass

            mail.close()
            mail.logout()

            # Les mails en cache sont dans self._inbox_mails (rendu plus haut),
            # et les nouveaux ont ete ajoutes un par un dans le rendu progressif.
            # Sauvegarde du cache mis a jour.
            self._save_mail_cache(cache)

            if not self._inbox_mails:
                if hasattr(self, "inbox_scroll"):
                    self.root.after(0, lambda: self._show_inbox_empty())
                return

            # Toast final uniquement (le re-rendu a deja ete fait au fil de l'eau)
            if hasattr(self, "inbox_scroll") and new_results:
                try:
                    self.root.after(0, lambda n=len(new_results):
                        self._show_toast(f"✨ {n} nouveau(x) mail(s) analyse(s)."))
                except Exception:
                    pass

            # Detection RDV / reponses en attente sur les mails NOUVEAUX
            # (on ne relance pas ca sur les caches pour economiser les appels)
            for md in new_results:
                try:
                    rep = self._check_pending_place_reply(md["sender"], md["subject"], md.get("body", ""))
                    if rep and rep.get("matched"):
                        md["reply_info"] = rep.get("info_details") or "Reponse traitee."
                        continue
                except Exception as _e:
                    print(f"Erreur check pending place : {_e}")
                try:
                    self._auto_handle_mail_agenda(
                        md["sender"], md["subject"], md.get("agenda_suggestion"),
                        md.get("warning"), source_uid=md.get("uid"))
                except Exception as _e:
                    print(f"Erreur auto-agenda mail : {_e}")

            # Purge automatique des mails dont le RDV est deja passe
            try:
                self._purge_past_rdv_mails()
            except Exception as _e:
                print(f"Erreur purge RDV passes : {_e}")

            # Optimisations inbox : spam aging, archivage, unsubscribe,
            # categorisation serveur, accuses de reception
            try:
                self._run_inbox_optimizations(self._inbox_mails)
            except Exception as _e:
                print(f"Erreur optim inbox : {_e}")

            # Triage automatique : actif en PERMANENCE.
            # - Lacher Prise ON  : l'IA execute les actions directement
            # - Lacher Prise OFF : l'IA cree des requetes de permission en
            #   attente, validees manuellement dans le module Permissions.
            # Tous les mails non deja triages sont traites (lus ou non).
            try:
                self._auto_triage_mails(self._inbox_mails)
            except Exception as _e:
                print(f"Erreur triage mails : {_e}")

        except Exception as e:
            if hasattr(self, "inbox_scroll"):
                self.root.after(0, lambda: self._show_inbox_error(str(e)))
            else:
                print(f"Erreur fetch mail auto (UI fermee) : {e}")

    def _ai_summarize_mail(self, sender, subject, body, attachments=None):
        clean_body = body[:2500].replace('\n', ' ')
        attach_section = ""
        if attachments:
            for fname, ftext in attachments:
                if ftext:
                    attach_section += f"\n\nPIÈCE JOINTE '{fname}':\n{ftext[:2500]}"

        prompt = f"""Analyse ce mail reçu par {USERNAME} :

DE : {sender}
SUJET : {subject}
DATE DE RÉCEPTION : {datetime.now().strftime('%d/%m/%Y')}
CONTENU :
{clean_body}{attach_section}

Réponds UNIQUEMENT en JSON valide selon le format demandé. Aucun texte hors du JSON."""

        try:
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": INBOX_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.05
            }
            response = self._post_with_retry(URL, headers, payload, timeout=30)
            if response.status_code != 200:
                raise RuntimeError(f"API {response.status_code}")
            raw = response.json()['choices'][0]['message']['content'].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()

            data = json.loads(raw)
            actions = data.get("action", [])
            if isinstance(actions, str): actions = [actions]

            return (
                data.get("resume", ""),
                data.get("urgence", "normale"),
                actions,
                data.get("pieces_jointes", []),
                data.get("warning", None),
                data.get("type", "inconnu"),
                data.get("agenda_suggestion", None)
            )

        except Exception as e:
            return "Erreur analyse.", "faible", ["Lire le mail manuellement."], [], None, "inconnu", None

    def _auto_handle_mail_agenda(self, sender, subject, agenda_sug, warning, source_uid=None):
        """Traite automatiquement un rendez-vous detecte dans un mail.

        - Si warning (arnaque/phishing) : on ignore.
        - Si pas de date : on ignore (l'utilisateur peut toujours cliquer sur
          le bouton 'Ajouter a l'agenda' dans la carte du mail).
        - Si pas d'heure : on demande a l'IA de choisir un creneau libre.
        - Lacher Prise ON : ajout direct.
        - Lacher Prise OFF : demande de permission dans le module Permissions.
        """
        if not agenda_sug or warning:
            return
        if not isinstance(agenda_sug, dict):
            return
        date_str = agenda_sug.get("date")
        if not date_str:
            return  # date vague, on laisse l'utilisateur cliquer manuellement

        # Deduplication : un meme mail (sender + subject + date) ne genere
        # qu'une seule entree, meme quand l'inbox est rafraichie plusieurs fois.
        if not hasattr(self, "_auto_agenda_seen"):
            self._auto_agenda_seen = set()
        dedup_key = f"{(sender or '').strip().lower()}|{(subject or '').strip().lower()}|{date_str}"
        if dedup_key in self._auto_agenda_seen:
            return
        self._auto_agenda_seen.add(dedup_key)

        title = agenda_sug.get("title") or f"Rendez-vous : {subject[:40]}"
        time_str = agenda_sug.get("time") or ""
        note = agenda_sug.get("note") or ""
        place = (agenda_sug.get("place") or "").strip()
        motif = (agenda_sug.get("motif") or "").strip()
        sender_clean = re.sub(r"<.*?>", "", sender or "").strip()[:40]
        source = f"mail de {sender_clean}" if sender_clean else "mail"

        # Extraction de l'adresse email de l'expediteur (pour repondre)
        sender_email = None
        try:
            m_email = re.search(r"[\w\.\-\+]+@[\w\.\-]+\.\w+", sender or "")
            if m_email:
                sender_email = m_email.group(0)
        except Exception:
            sender_email = None

        # Securite supplementaire : si un evenement quasi-identique existe
        # deja dans l'agenda (meme date + meme titre), on ne le recree pas.
        try:
            already = any(
                (ev.get("date") == date_str and
                 ev.get("title", "").strip().lower() == title.strip().lower())
                for ev in self.agenda_events
            )
            if already:
                return
        except Exception:
            pass

        # Etat du lieu, du motif et de l'heure
        place_low = place.lower()
        place_known = bool(place) and place_low not in ("null", "none", "inconnu", "a definir", "à définir")
        is_remote = place_low in ("visio", "online", "distanciel", "en ligne", "zoom", "meet", "teams", "skype")
        motif_low = motif.lower()
        motif_known = bool(motif) and motif_low not in ("null", "none", "inconnu", "a definir", "à définir")
        time_known = bool(time_str) and time_str.lower() not in ("null", "none", "inconnu")

        # Le lieu est manquant seulement si ce n'est pas distanciel
        needs_place = (not place_known) and (not is_remote)
        needs_motif = not motif_known
        needs_time = not time_known

        # --- CAS 0 : creneau deja occupe dans l'agenda ---
        # Si l'heure est connue et un evenement existe deja sur le meme
        # creneau (meme date + meme heure), on repond automatiquement pour
        # refuser le RDV et on supprime le mail entrant du serveur.
        try:
            if time_known and self._is_slot_busy(date_str, time_str):
                if sender_email:
                    refus_subject = f"Re: {subject}" if subject else "Indisponibilite pour ce creneau"
                    refus_body = (
                        f"Bonjour,\n\n"
                        f"Je vous remercie pour votre proposition de rendez-vous "
                        f"le {date_str} a {time_str}. Malheureusement, je ne suis "
                        f"pas disponible a ce creneau (deja pris).\n\n"
                        f"Pourriez-vous me proposer un autre horaire ? Je vous "
                        f"repondrai des que possible.\n\n"
                        f"Cordialement."
                    )
                    # Envoi reel du mail de refus (thread daemon pour ne pas bloquer)
                    threading.Thread(
                        target=self.execute_auto_mail,
                        args=(sender_email, refus_subject, refus_body),
                        daemon=True,
                    ).start()
                # Suppression reelle du mail entrant sur le serveur IMAP
                if source_uid:
                    threading.Thread(
                        target=self.delete_mail_on_server,
                        args=(source_uid, f"RDV refuse (creneau occupe) : {subject}"),
                        daemon=True,
                    ).start()
                try:
                    self.root.after(0, lambda t=title, d=date_str, h=time_str:
                        self.add_message(
                            "SYSTEM",
                            f"⛔ '{t}' refuse : creneau {d} {h} deja pris. Mail supprime, reponse envoyee."))
                except Exception:
                    pass
                try:
                    database.add_experience_entry(
                        USERNAME, "mail_rdv_refus",
                        f"Refus RDV {date_str} {time_str} : {title[:50]}",
                        "success", 1.0)
                except Exception:
                    pass
                return
        except Exception as e:
            print(f"Erreur detection creneau occupe : {e}")

        # --- CAS 1 : une info manque et on peut la demander par mail ---
        # En mode Lacher Prise : on valide UNIQUEMENT quand lieu, motif ET
        # heure sont tous connus. Sinon on met le RDV en attente et on demande
        # par mail les infos manquantes.
        if (needs_place or needs_motif or needs_time) and sender_email:
            try:
                missing = []
                if needs_place: missing.append("place")
                if needs_motif: missing.append("motif")
                if needs_time:  missing.append("time")
                pending = {
                    "sender_email": sender_email.lower(),
                    "title": title,
                    "date": date_str,
                    "time": time_str if time_known else "",
                    "note": note,
                    "source": source,
                    "subject": subject,
                    "place": place if place_known else "",
                    "motif": motif if motif_known else "",
                    "missing": missing,
                    "source_mail_uid": str(source_uid) if source_uid else "",
                }
                if not hasattr(self, "_pending_place_events"):
                    self._pending_place_events = []
                # Deduplication dans la file d'attente
                same = [p for p in self._pending_place_events
                        if p["sender_email"] == pending["sender_email"]
                        and p["date"] == pending["date"]
                        and p["title"].strip().lower() == pending["title"].strip().lower()]
                if not same:
                    self._pending_place_events.append(pending)
                # Envoi du mail de demande (direct ou permission)
                self._auto_ask_meeting_info(
                    sender_email, subject, title, date_str, time_str, missing)
                miss_fr = " et ".join(
                    {"place": "lieu", "motif": "motif", "time": "heure"}[m] for m in missing)
                self.root.after(0, lambda t=title, mf=miss_fr: self.add_message(
                    "SYSTEM", f"⏳ RDV '{t}' en attente : {mf} demande par mail. Valide des reception."))
            except Exception as e:
                print(f"Erreur demande info par mail : {e}")
            return  # IMPORTANT : on ne cree pas encore l'evenement

        # --- CAS 2 : infos suffisantes (ou pas d'email pour demander) ---
        full_note = f"{source}."
        if motif_known:
            full_note += f" Motif : {motif}."
        if place_known:
            full_note += f" Lieu : {place}."
        if time_known:
            full_note += f" Heure : {time_str}."
        if note:
            full_note += f" {note}"

        if getattr(self, "lacher_prise_mode", False):
            try:
                self.add_agenda_event_with_reminder(title, date_str, time_str, full_note,
                                                     source_mail_uid=source_uid)
                self.root.after(0, lambda t=title, d=date_str, h=time_str:
                                self._show_toast(f"📅 {t} ajoute ({d} {h})".rstrip()))
            except Exception as e:
                print(f"Erreur ajout direct agenda : {e}")
        else:
            try:
                perm_note = note
                if motif_known: perm_note += f" Motif : {motif}."
                if place_known: perm_note += f" Lieu : {place}."
                self._create_agenda_permission_request(
                    title, date_str, time_str, perm_note.strip(),
                    source=source, source_mail_uid=source_uid)
                self.root.after(0, lambda t=title: self.add_message(
                    "SYSTEM", f"📅 Rendez-vous detecte ('{t}') en attente dans Permissions (⚡)."))
            except Exception as e:
                print(f"Erreur demande permission agenda : {e}")

    def _check_pending_place_reply(self, sender, subject, body):
        """Inspecte un mail entrant : s'il repond a une demande de lieu/motif/
        heure pour un RDV en attente, extrait les infos manquantes et, si tout
        est recu ET coherent, valide l'evenement.

        Retourne un dict {'matched': bool, 'info_details': str} pour permettre
        a l'UI d'afficher un badge "reponse traitee" sur la carte du mail.
        """
        result = {"matched": False, "info_details": ""}
        if not getattr(self, "_pending_place_events", None):
            return result
        try:
            m_email = re.search(r"[\w\.\-\+]+@[\w\.\-]+\.\w+", sender or "")
        except Exception:
            m_email = None
        if not m_email:
            return result
        sender_email = m_email.group(0).lower()

        candidates = [p for p in self._pending_place_events
                      if p.get("sender_email") == sender_email]
        if not candidates:
            return result
        result["matched"] = True

        for pending in list(candidates):
            title = pending["title"]
            date_str = pending["date"]
            time_str = pending.get("time", "") or ""
            note = pending.get("note", "") or ""
            source = pending.get("source", "mail")
            missing = list(pending.get("missing", []))
            place = pending.get("place", "") or ""
            motif = pending.get("motif", "") or ""

            # On extrait ce qui manque, uniquement ce qui manque
            if "place" in missing:
                try:
                    extracted_place = self._ai_extract_place(body)
                except Exception:
                    extracted_place = None
                if extracted_place:
                    extracted_place = extracted_place.strip().strip('"').strip("'")[:120]
                    if extracted_place and extracted_place.lower() not in ("null", "none", "inconnu"):
                        place = extracted_place
                        missing.remove("place")
            if "time" in missing:
                try:
                    extracted_time = self._ai_extract_time(body)
                except Exception:
                    extracted_time = None
                if extracted_time:
                    extracted_time = extracted_time.strip().strip('"').strip("'")[:10]
                    # Validation format HH:MM
                    if re.match(r"^\d{1,2}:\d{2}$", extracted_time):
                        hh, mm = extracted_time.split(":")
                        try:
                            hhi = int(hh); mmi = int(mm)
                            if 0 <= hhi <= 23 and 0 <= mmi <= 59:
                                time_str = f"{hhi:02d}:{mmi:02d}"
                                missing.remove("time")
                        except Exception:
                            pass
            if "motif" in missing:
                try:
                    extracted_motif = self._ai_extract_motif(body, title, pending.get("subject", ""))
                except Exception:
                    extracted_motif = None
                if extracted_motif:
                    extracted_motif = extracted_motif.strip().strip('"').strip("'")[:200]
                    if extracted_motif and extracted_motif.lower() not in ("null", "none", "inconnu"):
                        # Verifier la coherence du motif
                        coherent = False
                        try:
                            coherent = self._ai_is_motif_coherent(
                                extracted_motif, title, pending.get("subject", ""), note)
                        except Exception:
                            coherent = True  # en cas d'echec reseau, on accepte
                        if coherent:
                            motif = extracted_motif
                            missing.remove("motif")
                        else:
                            self.root.after(0, lambda t=title, m=extracted_motif: self.add_message(
                                "SYSTEM", f"⚠ Motif recu pour '{t}' juge incoherent : '{m[:60]}'. RDV toujours en attente."))

            # Mise a jour du pending dans la file
            pending["place"] = place
            pending["motif"] = motif
            pending["missing"] = missing

            # Si il reste des infos manquantes, on ne valide pas encore
            if missing:
                continue

            # Tout est la : validation (toutes les infos recues)
            # On construit une note complete qui sera affichee dans l'agenda
            full_note = f"{source}. "
            if motif:
                full_note += f"Motif : {motif}. "
            if place:
                full_note += f"Lieu : {place}. "
            if time_str:
                full_note += f"Heure : {time_str}. "
            if note:
                full_note += f"{note}"
            full_note = full_note.strip()

            perm_note = ""
            if motif: perm_note += f"Motif : {motif}. "
            if place: perm_note += f"Lieu : {place}. "
            if time_str: perm_note += f"Heure : {time_str}. "
            if note: perm_note += f"{note}"
            perm_note = perm_note.strip()

            try:
                src_uid = pending.get("source_mail_uid") or None
                if getattr(self, "lacher_prise_mode", False):
                    self.add_agenda_event_with_reminder(
                        title, date_str, time_str, full_note,
                        source_mail_uid=src_uid)
                    self.root.after(0, lambda t=title, p=place, mo=motif, h=time_str, d=date_str: self.add_message(
                        "SYSTEM",
                        f"✅ Infos completes pour '{t}' (date : {d}, heure : {h or '-'}, "
                        f"lieu : {p or '-'}, motif : {mo or '-'}). Ajoute a l'agenda."))
                else:
                    self._create_agenda_permission_request(
                        title, date_str, time_str, perm_note, source=source,
                        source_mail_uid=src_uid)
                    self.root.after(0, lambda t=title: self.add_message(
                        "SYSTEM", f"✅ Infos completes pour '{t}'. RDV en attente dans Permissions (⚡)."))
            except Exception as e:
                print(f"Erreur validation pending : {e}")

            # Retirer ce pending de la file
            try:
                self._pending_place_events.remove(pending)
            except ValueError:
                pass

        # Construction du detail a afficher sur la carte du mail
        try:
            parts = []
            for p in candidates:
                miss = p.get("missing", [])
                got = []
                if "place" not in miss and p.get("place"): got.append(f"lieu : {p['place']}")
                if "motif" not in miss and p.get("motif"): got.append(f"motif : {p['motif']}")
                if "time" not in miss and p.get("time"):   got.append(f"heure : {p['time']}")
                still = [{"place":"lieu","motif":"motif","time":"heure"}.get(x,x) for x in miss]
                if miss:
                    parts.append(f"Reponse au RDV '{p['title'][:35]}' ({', '.join(got) or 'partiel'}). En attente : {', '.join(still)}.")
                else:
                    parts.append(f"Reponse au RDV '{p['title'][:35]}' ({', '.join(got)}). RDV valide.")
            result["info_details"] = " | ".join(parts)
        except Exception:
            pass
        return result

    def _ai_extract_motif(self, body, title, original_subject):
        """Extrait le motif du rdv depuis un mail de reponse."""
        clean = (body or "")[:2000].replace('\n', ' ')
        if not clean.strip():
            return None
        prompt = (
            f"Un rendez-vous intitule '{title}' (sujet du mail : '{original_subject}') "
            "doit avoir un motif (but concret). Extrait le motif du mail suivant. "
            "Reponds avec une phrase courte (max 15 mots), ou 'NONE' si aucun "
            "motif clair n'est mentionne. Aucun prefixe, aucune ponctuation finale.\n\n"
            f"MAIL :\n{clean}"
        )
        try:
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": "Tu extrais des motifs de rendez-vous. Reponse tres courte."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 80,
            }
            r = self._post_with_retry(URL, headers, payload, timeout=(5, 20))
            if r.status_code != 200:
                return None
            txt = r.json()["choices"][0]["message"]["content"].strip()
            if not txt or txt.upper() == "NONE":
                return None
            return txt.splitlines()[0].strip()
        except Exception as e:
            print(f"Erreur ai_extract_motif : {e}")
            return None

    def _ai_is_motif_coherent(self, motif, title, subject, note):
        """Juge si le motif propose est coherent avec le contexte du RDV.
        Retourne True si coherent, False sinon.
        """
        prompt = (
            f"Contexte d'un rendez-vous :\n"
            f"- Titre : {title}\n"
            f"- Sujet du mail d'origine : {subject}\n"
            f"- Note : {note}\n"
            f"- Motif propose (extrait d'une reponse mail) : {motif}\n\n"
            "Ce motif est-il coherent et professionnel pour ce rendez-vous ? "
            "Reponds UNIQUEMENT par 'OUI' ou 'NON'. Considere NON uniquement "
            "si le motif est manifestement spam, injurieux, absurde ou sans "
            "rapport avec le rendez-vous. Dans le doute, reponds OUI."
        )
        try:
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": "Tu juges la coherence de motifs de rendez-vous. Reponds OUI ou NON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 10,
            }
            r = self._post_with_retry(URL, headers, payload, timeout=(5, 20))
            if r.status_code != 200:
                return True
            txt = r.json()["choices"][0]["message"]["content"].strip().upper()
            return "OUI" in txt
        except Exception as e:
            print(f"Erreur ai_is_motif_coherent : {e}")
            return True

    def _ai_extract_place(self, body):
        """Demande a Mistral d'extraire le lieu d'un rendez-vous dans un mail.
        Retourne une chaine (adresse / salle / visio) ou None si introuvable.
        """
        clean = (body or "")[:2000].replace('\n', ' ')
        if not clean.strip():
            return None
        prompt = (
            "Tu recois le corps d'un mail qui est suppose indiquer le lieu d'un "
            "rendez-vous. Extrais UNIQUEMENT le lieu (adresse complete, salle, "
            "ville, ou 'visio' si c'est en ligne). Si aucun lieu clair n'est "
            "mentionne, reponds exactement 'NONE'. Reponds avec le lieu seul, "
            "sans phrase, sans ponctuation finale.\n\n"
            f"MAIL :\n{clean}"
        )
        try:
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": "Tu extrais des lieux de rendez-vous. Reponse tres courte."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 60,
            }
            r = self._post_with_retry(URL, headers, payload, timeout=(5, 20))
            if r.status_code != 200:
                return None
            txt = r.json()["choices"][0]["message"]["content"].strip()
            if not txt or txt.upper() == "NONE":
                return None
            return txt.splitlines()[0].strip()
        except Exception as e:
            print(f"Erreur ai_extract_place : {e}")
            return None

    def _ai_extract_time(self, body):
        """Extrait l'heure (HH:MM) d'un rendez-vous depuis un mail de reponse.
        Retourne une chaine au format HH:MM ou None si introuvable.
        """
        clean = (body or "")[:2000].replace('\n', ' ')
        if not clean.strip():
            return None
        # Tentative rapide via regex (avant d'appeler l'IA)
        try:
            m = re.search(r"\b([01]?\d|2[0-3])\s*[h:]\s*([0-5]\d)\b", clean)
            if m:
                return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
            m = re.search(r"\b([01]?\d|2[0-3])\s*h(?!\w)", clean, re.IGNORECASE)
            if m:
                return f"{int(m.group(1)):02d}:00"
        except Exception:
            pass
        prompt = (
            "Tu recois le corps d'un mail qui est suppose indiquer l'heure d'un "
            "rendez-vous. Extrais UNIQUEMENT l'heure, au format HH:MM (24h). "
            "Si aucune heure claire n'est mentionnee, reponds exactement 'NONE'. "
            "Reponds avec l'heure seule, sans phrase, sans ponctuation finale.\n\n"
            f"MAIL :\n{clean}"
        )
        try:
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": "Tu extrais des heures de rendez-vous au format HH:MM. Reponse tres courte."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 20,
            }
            r = self._post_with_retry(URL, headers, payload, timeout=(5, 20))
            if r.status_code != 200:
                return None
            txt = r.json()["choices"][0]["message"]["content"].strip()
            if not txt or txt.upper() == "NONE":
                return None
            return txt.splitlines()[0].strip()
        except Exception as e:
            print(f"Erreur ai_extract_time : {e}")
            return None

    def _auto_ask_meeting_info(self, dest_email, original_subject, title, date_str, time_str, missing):
        """Envoie (ou demande permission d'envoyer) un mail poli pour demander
        les infos manquantes d'un RDV : lieu et/ou motif.

        - missing : liste contenant 'place', 'motif' ou les deux.
        - Lacher Prise ON : envoi direct.
        - Lacher Prise OFF : cree une permission mail_send.
        """
        if not dest_email or not missing:
            return

        # Deduplication : ne pas renvoyer si on a deja demande les memes infos
        if not hasattr(self, "_auto_info_asked"):
            self._auto_info_asked = set()
        dedup = f"{dest_email.lower()}|{date_str}|{title.strip().lower()}|{'-'.join(sorted(missing))}"
        if dedup in self._auto_info_asked:
            return
        self._auto_info_asked.add(dedup)

        sujet = f"Re: {original_subject}" if original_subject else f"Precision : {title}"
        if sujet.lower().startswith("re: re:"):
            sujet = sujet[4:]

        # Construction de la phrase de demande selon ce qui manque
        ask_parts = []
        if "place" in missing:
            ask_parts.append("le lieu precis (adresse complete ou lien visio)")
        if "motif" in missing:
            ask_parts.append("le motif / objectif de ce rendez-vous")
        if "time" in missing:
            ask_parts.append("l'heure exacte (format HH:MM)")
        ask_phrase = " ainsi que ".join(ask_parts)

        heure_txt = f" a {time_str}" if time_str and "time" not in missing else ""
        date_txt = date_str or "la date indiquee"
        corps = (
            "Bonjour,\n\n"
            f"Merci pour votre message concernant notre rendez-vous du {date_txt}{heure_txt}.\n"
            f"Pourriez-vous me preciser {ask_phrase} ?\n\n"
            "Cela me permettra de preparer cette rencontre au mieux.\n\n"
            "Bien cordialement,"
        )

        # Libelle lisible pour les logs/notifs
        lib_fr = " et ".join({"place": "lieu", "motif": "motif", "time": "heure"}[m] for m in missing)

        if getattr(self, "lacher_prise_mode", False):
            try:
                threading.Thread(
                    target=self.execute_auto_mail,
                    args=(dest_email, sujet, corps),
                    daemon=True
                ).start()
                self.root.after(0, lambda d=dest_email, l=lib_fr: self.add_message(
                    "SYSTEM", f"✉ Demande de {l} envoyee automatiquement a {d}."))
            except Exception as e:
                print(f"Erreur envoi direct demande info : {e}")
        else:
            try:
                import database
                database.add_permission_request(
                    USERNAME, "mail_send",
                    f"Demander {lib_fr} du rdv a {dest_email}",
                    f"Sujet : {sujet}\nDate : {date_txt}{heure_txt}",
                    {"to": dest_email, "subject": sujet, "body": corps}
                )
                self.root.after(0, lambda d=dest_email, l=lib_fr: self.add_message(
                    "SYSTEM", f"✉ Mail de demande de {l} en attente dans Permissions (⚡) pour {d}."))
            except Exception as e:
                print(f"Erreur permission demande info : {e}")

    def _prefill_mail_form(self, dest, sujet, corps):
        self.mail_to.delete(0, "end")
        self.mail_to.insert(0, dest)
        self.mail_subject.delete(0, "end")
        self.mail_subject.insert(0, sujet)
        self.mail_body.delete("1.0", "end")
        self.mail_body.insert("1.0", corps)

    def _open_mail_prefilled(self, raw_text):
        """Lacher Prise OFF : extrait le mail de la reponse IA, pre-remplit
        le formulaire et ouvre l'interface Mail pour que l'utilisateur relise
        et clique lui-meme sur Envoyer."""
        try:
            block_m = re.search(r"COMMAND_START(.*?)COMMAND_END", raw_text, re.DOTALL)
            block = block_m.group(1) if block_m else raw_text
            to_m   = re.search(r"TO\s*:\s*([\w\.\-]+@[\w\.\-]+\.\w+)", block)
            sub_m  = re.search(r"SUBJECT\s*:\s*(.+)", block)
            body_m = re.search(r"BODY\s*:(.*?)$", block, re.DOTALL)
            dest  = to_m.group(1).strip()   if to_m   else ""
            sujet = sub_m.group(1).strip().strip('"').strip("'") if sub_m else ""
            corps = body_m.group(1).strip() if body_m else ""
            if not dest:
                return
            def _do():
                self._prefill_mail_form(dest, sujet, corps)
                if self.mode != "mail":
                    self.toggle_mail()
                self.add_message(
                    "SYSTEM",
                    f"Mail pre-rempli pour {dest}. Relisez puis cliquez sur Envoyer."
                )
            self.root.after(0, _do)
        except Exception as e:
            print(f"Erreur ouverture mail pre-rempli : {e}")

    def execute_auto_mail(self, dest, sujet, corps, attachment_path=None):
        if not self.user_email or not self.user_app_pass:
            self.root.after(0, lambda: self.add_message("SYSTEM", "❌ Erreur : Gmail non configuré dans la base."))
            return

        try:
            msg = MIMEMultipart()
            msg['From']    = self.user_email
            msg['To']      = dest
            msg['Subject'] = sujet
            msg.attach(MIMEText(corps, 'plain'))

            if attachment_path and os.path.exists(attachment_path):
                filename = os.path.basename(attachment_path)
                ctype, _ = mimetypes.guess_type(attachment_path)
                main, sub = (ctype or "application/octet-stream").split('/', 1)
                with open(attachment_path, "rb") as f:
                    part = MIMEBase(main, sub)
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment', filename=filename)
                msg.attach(part)

            _, smtp_host, smtp_port, _ = self._get_email_servers()
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(self.user_email, self.user_app_pass)
            server.send_message(msg)
            server.quit()

            database.add_experience_entry(USERNAME, "mail_sent", f"Auto-envoi à {dest} : {sujet}", "success", 1.0)
            self.root.after(0, lambda: self.add_message("SYSTEM", f"✅ Mail envoyé avec succès à {dest} !"))

        except Exception as e:
            err = str(e)[:60]
            self.root.after(0, lambda m=err: self.add_message("SYSTEM", f"❌ Échec de l'envoi : {m}"))

    def attach_file_logic(self):
        path = filedialog.askopenfilename()
        if path:
            self.mail_attachment_path = path
            self.lbl_file_status.configure(text=f"Joint : {os.path.basename(path)}", text_color="#44ff88")

    def send_mail_logic(self):
        dest  = self.mail_to.get().strip()
        sujet = self.mail_subject.get().strip()
        corps = self.mail_body.get("1.0", "end").strip()

        if not dest:
            self.mail_status.configure(text="DESTINATAIRE MANQUANT", text_color="#ff4444")
            return
        if not self.user_email or not self.user_app_pass:
            self.mail_status.configure(text="EMAIL NON CONFIGURÉ", text_color="#ff4444")
            return

        def thread_mail():
            try:
                self.root.after(0, lambda: self.mail_status.configure(text="ENVOI EN COURS...", text_color="#00ffcc"))

                msg = MIMEMultipart()
                msg['From']    = self.user_email
                msg['To']      = dest
                msg['Subject'] = sujet
                msg.attach(MIMEText(corps, 'plain'))

                if self.mail_attachment_path and os.path.exists(self.mail_attachment_path):
                    filename = os.path.basename(self.mail_attachment_path)
                    ctype, _ = mimetypes.guess_type(self.mail_attachment_path)
                    main, sub = (ctype or "application/octet-stream").split('/', 1)
                    with open(self.mail_attachment_path, "rb") as f:
                        part = MIMEBase(main, sub)
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    
                    part.add_header('Content-Disposition', 'attachment', filename=filename)
                    msg.attach(part)

                _, smtp_host, smtp_port, _ = self._get_email_servers()
                server = smtplib.SMTP(smtp_host, smtp_port)
                server.starttls()
                server.login(self.user_email, self.user_app_pass)
                server.send_message(msg)
                server.quit()

                self.root.after(0, self.on_mail_success)

            except Exception as e:
                err = str(e)[:40]
                self.root.after(0, lambda m=err: self.mail_status.configure(text=f"ERREUR : {m}", text_color="#ff4444"))

        threading.Thread(target=thread_mail, daemon=True).start()

    def on_mail_success(self):
        self.mail_status.configure(text="MESSAGE ENVOYE", text_color="#44ff88")
        self.mail_to.delete(0, "end")
        self.mail_subject.delete(0, "end")
        self.mail_body.delete("1.0", "end")
        self.mail_attachment_path = None
        self.lbl_file_status.configure(text="Aucun fichier", text_color="#555")
        self.root.after(2000, self.auto_return_hub)

    # ======================================================
    #     PURGE AUTOMATIQUE DES MAILS LIES A UN RDV PASSE
    # ======================================================
    def _purge_past_rdv_mails(self):
        """Supprime du serveur les mails dont le RDV associe est deja passe.

        Parcourt l'agenda : pour chaque evenement avec source_mail_uid dont la
        date/heure est passee (et qui n'a pas deja ete purge), on supprime le
        mail source du serveur IMAP (en thread), puis on marque l'evenement
        comme purge (flag 'source_mail_purged') pour ne pas refaire le boulot.
        """
        try:
            if not getattr(self, "agenda_events", None):
                return
            if not self.user_email or not self.user_app_pass:
                return
            now = datetime.now()
            to_purge = []
            for ev in list(self.agenda_events):
                uid = ev.get("source_mail_uid")
                if not uid:
                    continue
                if ev.get("source_mail_purged"):
                    continue
                d = ev.get("date") or ""
                t = ev.get("time") or "23:59"
                try:
                    ev_dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
                except Exception:
                    try:
                        ev_dt = datetime.strptime(d, "%Y-%m-%d").replace(hour=23, minute=59)
                    except Exception:
                        continue
                if ev_dt < now:
                    to_purge.append(ev)

            if not to_purge:
                return

            def _worker(events):
                for ev in events:
                    uid = ev.get("source_mail_uid")
                    title = ev.get("title", "")
                    try:
                        ok = self.delete_mail_on_server(uid, f"RDV termine : {title}")
                    except Exception as e:
                        print(f"Erreur purge mail RDV : {e}")
                        ok = False
                    # Qu'il ait reussi ou pas, on marque pour ne pas reessayer
                    # en boucle. En cas d'echec serveur, le flag evite un spam
                    # d'erreurs a chaque refresh.
                    ev["source_mail_purged"] = True
                try:
                    self.save_agenda()
                except Exception:
                    pass

            threading.Thread(target=_worker, args=(to_purge,), daemon=True).start()
        except Exception as e:
            print(f"Erreur _purge_past_rdv_mails : {e}")

    def _is_slot_busy(self, date_str, time_str, window_min=30):
        """Retourne True si l'agenda contient deja un evenement pour ce creneau.

        La comparaison se fait sur la meme date et une fenetre de +/- window_min
        minutes autour de time_str (HH:MM). En cas d'heure mal formatee cote
        agenda, on considere un conflit seulement en cas de correspondance
        exacte de la chaine.
        """
        try:
            if not date_str or not time_str:
                return False
            try:
                ref = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            except Exception:
                return False
            for ev in getattr(self, "agenda_events", []) or []:
                if ev.get("date") != date_str:
                    continue
                ev_time = (ev.get("time") or "").strip()
                if not ev_time:
                    continue
                if ev_time == time_str:
                    return True
                try:
                    ev_dt = datetime.strptime(f"{date_str} {ev_time}", "%Y-%m-%d %H:%M")
                    delta = abs((ev_dt - ref).total_seconds()) / 60.0
                    if delta <= window_min:
                        return True
                except Exception:
                    continue
            return False
        except Exception as e:
            print(f"Erreur _is_slot_busy : {e}")
            return False

    # ===================================================================
    # TRIAGE AUTO (actif dans les deux modes)
    # ===================================================================
    def _triaged_store_path(self):
        """Chemin du fichier qui memorise les UIDs deja triages."""
        return f"triaged_mails_{USERNAME}.json"

    def _load_triaged_uids(self):
        try:
            path = self._triaged_store_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data)
        except Exception as e:
            print(f"Erreur load triaged : {e}")
        return set()

    def _save_triaged_uids(self, uid_set):
        try:
            path = self._triaged_store_path()
            # Cap 5000 pour ne pas faire exploser le fichier
            lst = list(uid_set)[-5000:]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(lst, f)
        except Exception as e:
            print(f"Erreur save triaged : {e}")

    def _auto_triage_mails(self, mails):
        """Triage automatique de TOUS les mails non encore triages.

        Comportement :
        - Lacher Prise ON  : chaque action (forward au fils, suppression,
          archivage) est executee directement par l'IA, et tracee dans le
          module Permissions comme auto-approuvee.
        - Lacher Prise OFF : chaque action cree une requete de permission
          EN ATTENTE dans le module Permissions. L'utilisateur valide
          manuellement via le bouton ✔ Accepter.

        Les mails deja triages lors d'un run precedent sont ignores (via
        un fichier JSON qui memorise les UIDs deja vus).
        """
        if not mails:
            return

        # Charge la memoire des UIDs deja traites
        if not hasattr(self, "_triaged_uids"):
            self._triaged_uids = self._load_triaged_uids()
        triaged = self._triaged_uids

        # Filtre : uniquement les mails jamais triages
        # + on exclut systematiquement les mails provenant de soi-meme
        # (rapports quotidiens, auto-notifications) pour ne pas boucler.
        my_email = (self.user_email or "").lower()
        pending = []
        for m in mails:
            uid = m.get("uid")
            if not uid or uid in triaged:
                continue
            snd = (m.get("sender") or "").lower()
            if my_email and my_email in snd:
                # marque comme triage pour ne plus revenir dessus
                triaged.add(uid)
                continue
            if (m.get("mail_type") or "").lower() == "self":
                triaged.add(uid)
                continue
            pending.append(m)
        if not pending:
            # On sauvegarde quand meme les UIDs auto-exclus
            try:
                self._save_triaged_uids(triaged)
            except Exception:
                pass
            return

        # Recupere la liste des fils (utilisateurs niveau >= 1, sauf moi)
        try:
            all_users = database.get_all_users_info()
        except Exception as e:
            print(f"Erreur get_all_users_info : {e}")
            return
        sons = {}
        for u, info in all_users.items():
            if u == USERNAME:
                continue
            lvl = info.get("permissions", [1])[0]
            if lvl == 0:
                continue
            if not info.get("has_email"):
                continue
            sons[u] = info

        # Meme si aucun fils, on peut encore trier (delete/archive/keep)
        # donc on ne return pas ici.

        # Lance le worker dans un thread pour ne pas bloquer l'UI
        threading.Thread(
            target=self._triage_unread_worker,
            args=(pending, sons),
            daemon=True,
        ).start()

    def _triage_unread_worker(self, unread_mails, sons):
        """Worker du triage : appelle l'IA par mail puis execute l'action."""
        # Formatage de la liste des fils pour le prompt
        sons_desc_lines = []
        for u, info in sons.items():
            biz = info.get("business_role") or "polyvalent"
            role = info.get("role") or ""
            sons_desc_lines.append(f"- {u} (metier: {biz}{', role: ' + role if role else ''})")
        sons_desc = "\n".join(sons_desc_lines)
        son_names = list(sons.keys())

        lp_mode = bool(getattr(self, "lacher_prise_mode", False))
        done = 0
        for m in unread_mails:
            uid = m.get("uid")
            sender = m.get("sender", "")
            subject = m.get("subject", "")
            body = (m.get("body") or "")[:2000]
            summary = m.get("summary", "")
            mtype = m.get("mail_type", "inconnu")

            decision = self._ai_triage_decision(
                sender, subject, body, summary, mtype, sons_desc, son_names)
            if not decision:
                continue

            action = (decision.get("action") or "keep").lower()
            target = decision.get("target_fils")
            short = decision.get("summary") or summary
            reason = decision.get("reason") or ""

            # ================= FORWARD AU FILS =================
            if target and target in sons:
                son_email = None
                try:
                    cfg = database.get_email_config(target)
                    son_email = (cfg or {}).get("email")
                except Exception:
                    son_email = None
                if son_email:
                    fwd_subject = f"[Transmis par Assistant] {subject[:70]}"
                    fwd_body = (
                        f"Bonjour {target},\n\n"
                        f"Un mail pour toi, resume par l'Assistant :\n\n"
                        f"De : {sender}\n"
                        f"Sujet : {subject}\n"
                        f"Type : {mtype}\n\n"
                        f"Resume :\n{short}\n\n"
                        f"Motif du routage : {reason}\n\n"
                        f"-- Assistant"
                    )
                    data = {"to": son_email, "subject": fwd_subject,
                            "body": fwd_body, "son": target,
                            "source_uid": uid}
                    try:
                        rid = database.add_permission_request(
                            USERNAME, "mail_forward_son",
                            f"Transmis a {target} : {subject[:50]}",
                            f"Resume a envoyer a {son_email} (motif : {reason[:80]})",
                            data)
                        if lp_mode:
                            # Lacher Prise ON : auto-approuve + envoi direct
                            database.resolve_permission_request(rid, "approved")
                            try:
                                self.execute_auto_mail(son_email, fwd_subject, fwd_body)
                                # Log specifique pour le compte rendu quotidien
                                try:
                                    database.add_experience_entry(
                                        USERNAME, "mail_forward_son",
                                        f"Transmis a {target} ({son_email}) : {subject[:50]}",
                                        "success", 1.0)
                                except Exception:
                                    pass
                            except Exception as e:
                                print(f"Erreur envoi mail fils : {e}")
                        # Lacher Prise OFF : requete PENDING, attend validation
                    except Exception as e:
                        print(f"Erreur permission forward : {e}")

            # ================= ACTION SUR LE MAIL D'ORIGINE =================
            if action == "delete" and uid:
                try:
                    rid = database.add_permission_request(
                        USERNAME, "mail_delete_triage",
                        f"Suppr. triage : {subject[:50]}",
                        f"Motif : {reason[:80]}",
                        {"uid": uid, "subject": subject})
                    if lp_mode:
                        database.resolve_permission_request(rid, "approved")
                        try:
                            self.delete_mail_on_server(uid, subject)
                        except Exception as e:
                            print(f"Erreur suppr triage : {e}")
                except Exception:
                    pass
            elif action == "archive" and uid:
                try:
                    rid = database.add_permission_request(
                        USERNAME, "mail_archive_triage",
                        f"Archive triage : {subject[:50]}",
                        f"Transmis a {target or '?'} - motif : {reason[:60]}",
                        {"uid": uid, "subject": subject})
                    if lp_mode:
                        database.resolve_permission_request(rid, "approved")
                        try:
                            self.archive_mail_on_server(uid, subject)
                        except Exception as e:
                            print(f"Erreur archive triage : {e}")
                except Exception:
                    pass
            # action == "keep" : on laisse le mail dans l'inbox sans requete

            # Memorise cet UID pour ne pas retraiter au prochain refresh
            try:
                if uid:
                    self._triaged_uids.add(uid)
            except Exception:
                pass
            done += 1

        # Persiste la memoire des UIDs traites
        try:
            self._save_triaged_uids(self._triaged_uids)
        except Exception:
            pass

        if done:
            mode_label = "auto" if lp_mode else "permissions en attente"
            try:
                self.root.after(0, lambda n=done, lbl=mode_label: self.add_message(
                    "SYSTEM",
                    f"🗂 Triage : {n} mail(s) traites ({lbl})."))
            except Exception:
                pass

    def _ai_triage_decision(self, sender, subject, body, summary, mtype,
                             sons_desc, son_names):
        """Interroge l'IA pour obtenir la decision de triage d'un mail.

        Retourne un dict {target_fils, summary, action, reason} ou None.
        """
        try:
            prompt = (
                f"Tu triages les mails non lus de {USERNAME} (patron).\n\n"
                f"MAIL :\n"
                f"De : {sender}\n"
                f"Sujet : {subject}\n"
                f"Type detecte : {mtype}\n"
                f"Resume existant : {summary}\n"
                f"Extrait corps :\n{body[:1500]}\n\n"
                f"FILS DISPONIBLES :\n{sons_desc}\n\n"
                f"Decide :\n"
                f"1) target_fils : le nom exact du fils le plus pertinent pour "
                f"gerer ce mail (doit etre dans la liste), ou null si aucun ne "
                f"correspond.\n"
                f"2) action : 'delete' (spam/pub/inutile), 'archive' "
                f"(informatif, deja transmis au fils), ou 'keep' (important, "
                f"doit rester dans l'inbox du patron).\n"
                f"3) summary : resume ultra-concis (1-2 phrases max) du mail "
                f"pour le fils.\n"
                f"4) reason : pourquoi ce routage/action en une phrase.\n\n"
                f"REGLES :\n"
                f"- Si mail personnel/familial : action='keep', target_fils=null.\n"
                f"- Si spam/pub/newsletter : action='delete'.\n"
                f"- Si mail metier : target_fils=nom du fils adapte, "
                f"action='archive'.\n"
                f"- Si urgent ou confidentiel : action='keep'.\n\n"
                f"Reponds UNIQUEMENT en JSON strict :\n"
                f'{{"target_fils": "...", "action": "...", "summary": "...", "reason": "..."}}'
            )
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": "Tu retournes UNIQUEMENT un JSON strict."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 300,
            }
            resp = self._post_with_retry(URL, headers, payload, timeout=(5, 30))
            if resp.status_code != 200:
                return None
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(0))
            # Normalisation : le target doit etre un fils valide
            t = data.get("target_fils")
            if t and t not in son_names:
                data["target_fils"] = None
            act = (data.get("action") or "").lower()
            if act not in ("delete", "archive", "keep"):
                data["action"] = "keep"
            return data
        except Exception as e:
            print(f"Erreur _ai_triage_decision : {e}")
            return None

    # ==========================================================
    # COMMANDE CHAT : NETTOYAGE DE L'INBOX (suppression/archivage)
    # ==========================================================
    # NOTE : version aussi presente dans modules/email_commands_mixin.py.
    # EmailCommandsMixin est listee AVANT EmailMixin dans la MRO de
    # AssistantApp, donc c'est la version "Commands" qui est utilisee.
    # On garde celle-ci pour ne rien casser ; futur cleanup possible.
    def _process_inbox_action_command(self, raw_text):
        """Parse et execute un bloc INBOX_ACTION_START/END genere par l'IA.

        Format attendu :
            INBOX_ACTION_START
            ACTION: delete|archive
            TYPE: spam|newsletter|phishing|old|relance|facture|tous|personnalise
            SENDER: (optionnel)
            KEYWORD: (optionnel)
            OLDER_DAYS: (optionnel)
            LIMIT: (optionnel)
            INBOX_ACTION_END

        Comportement :
        - Lacher Prise ON  : execute directement les suppressions/archivages.
        - Lacher Prise OFF : cree une requete de permission en attente.
        """
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

            # Source de verite : la liste des mails deja analyses dans l'UI.
            # Si l'inbox n'a pas encore ete fetchee, on force un fetch bloquant
            # rapide (sans UI) pour avoir qqch a nettoyer.
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

            # Filtrage
            now = datetime.now()
            type_groups = {
                "spam":       {"spam", "phishing", "arnaque"},
                "phishing":   {"phishing", "arnaque"},
                "newsletter": {"newsletter"},
                "relance":    {"relance"},
                "facture":    {"facture"},
                "old":        set(),  # gere via OLDER_DAYS seul
                "tous":       None,   # None = pas de filtre type
                "personnalise": None,
            }
            wanted_types = type_groups.get(mtype, {"spam"})

            def _parse_date(md):
                try:
                    d = md.get("date_str") or ""
                    # Tentative parsing RFC 2822
                    from email.utils import parsedate_to_datetime
                    return parsedate_to_datetime(d)
                except Exception:
                    return None

            matched = []
            for m in mails:
                t = (m.get("mail_type") or "inconnu").lower()
                if wanted_types is not None and t not in wanted_types:
                    # "old" seul ignore le type et se base uniquement sur l'age
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

            # Description humaine
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
            # On cree UNE seule requete qui contient la liste des UIDs,
            # plutot qu'une par mail (sinon on spamme Permissions).
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
        correspondants dans le chat (via un message SYSTEM).

        Format attendu :
            MAIL_SEARCH_START
            SENDER: ...
            KEYWORD: ...
            TYPE: ...
            WITHIN_DAYS: ...
            LIMIT: ...
            MAIL_SEARCH_END

        Les mails retournes proviennent de self._inbox_mails (deja analyses).
        Si l'inbox est vide, on declenche un fetch court avant.
        """
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

            # Assure qu'on a une liste a chercher
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

            # On exclut les self-mails des recherches
            mails = [m for m in mails
                     if (m.get("mail_type") or "").lower() != "self"]

            now = datetime.now()
            from email.utils import parsedate_to_datetime

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

            # Tri : plus recents d'abord
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

            # Construction d'un message SYSTEM lisible + injecte aussi dans
            # l'historique de chat pour que l'IA puisse rebondir
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

            # Injection dans l'historique pour permettre une relance naturelle
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
