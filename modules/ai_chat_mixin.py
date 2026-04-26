"""Chat IA Mistral, mémoire, parsing commandes."""
import os
import sys
import json
import time
import threading
import re
import base64
import math
import calendar
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


class AIChatMixin:
    def _post_with_retry(self, url, headers, payload, timeout=(5, 60),
                        max_retries=6, session=None):
        """POST avec retry exponentiel sur 429 (rate-limit) et 5xx.

        Respecte le header Retry-After si present. Ajoute un jitter
        aleatoire pour eviter que plusieurs threads parallel retapent
        en meme temps apres un 429 ("thundering herd"). Renvoie la
        response finale (meme si elle est encore en erreur apres les
        retries) pour que l'appelant puisse logger le status_code.
        """
        import random
        poster = session.post if session is not None else requests.post
        backoff = 2.0  # secondes
        last_resp = None
        for attempt in range(max_retries + 1):
            try:
                resp = poster(url, headers=headers, json=payload, timeout=timeout)
            except requests.exceptions.RequestException as e:
                # Erreur reseau : on reessaye sauf au dernier tour
                if attempt == max_retries:
                    raise
                time.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 60)
                continue

            last_resp = resp
            # Succes ou erreur cliente non-429 : on retourne direct
            if resp.status_code < 500 and resp.status_code != 429:
                return resp

            # 429 ou 5xx : on attend puis on reessaye
            if attempt == max_retries:
                return resp

            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff
            # Jitter pour eviter que tous les workers retapent
            # en meme temps apres le meme 429
            wait = wait + random.uniform(0, min(wait * 0.5, 5.0))
            # Feedback utilisateur sur le premier retry
            if attempt == 0 and resp.status_code == 429:
                try:
                    self.root.after(0, lambda w=wait: self.add_message(
                        "SYSTEM",
                        f"⏳ Mistral sature (429). Nouvelle tentative dans {int(w)}s..."
                    ))
                except Exception:
                    pass
            time.sleep(wait)
            backoff = min(backoff * 2, 60)
        return last_resp

    def send_logic(self):
        text = self.entry.get().strip()
        if not text:
            return

        # Commandes navigation
        if text.lower() in ["/agenda", "agenda", "ouvre agenda", "ouvrir agenda"]:
            self.entry.delete(0, "end")
            self.toggle_agenda()
            return
        if text.lower() in ["/permissions", "permissions", "/perms", "perms"]:
            self.entry.delete(0, "end")
            self.toggle_permissions()
            return

        full_content = text
        if self.chat_attachment_path:
            full_content += f" [FILE_ATTACHED: {os.path.basename(self.chat_attachment_path)}]"

        self.add_message("USER", text)
        self.entry.delete(0, "end")
        self.history.append({"role": "user", "content": full_content})

        if re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text):
            self._switch_to_mail_mode_from_chat()

        threading.Thread(target=self.requete_directe, daemon=True).start()

    def _switch_to_mail_mode_from_chat(self):
        pass

    def requete_directe(self):
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}"
            }

            last_msg_text = self.history[-1]["content"] if self.history else ""
            file_text_content = ""
            image_b64 = None

            if self.chat_attachment_path and os.path.exists(self.chat_attachment_path):
                ext = os.path.splitext(self.chat_attachment_path)[1].lower()
                if ext in ['.png', '.jpg', '.jpeg', '.webp']:
                    try:
                        with open(self.chat_attachment_path, "rb") as f:
                            image_b64 = base64.b64encode(f.read()).decode('utf-8')
                    except Exception as e:
                        print(f"Erreur image: {e}")
                file_text_content = self._get_attachment_data(self.chat_attachment_path)

            full_user_prompt = f"{last_msg_text}{file_text_content}"

            today_str = datetime.now().strftime("%A %d %B %Y")
            system_with_date = SYSTEM_INSTRUCTION.replace("{TODAY}", today_str)
            messages = [{"role": "system", "content": system_with_date}]
            for h in self.history[:-1]:
                messages.append(h)

            # IMPORTANT : Mistral/pixtral renvoie un 400 si on lui passe un
            # content sous forme de liste sans image. On envoie un string pur
            # quand pas d'image, et une liste uniquement en presence d'image.
            if image_b64:
                user_content = [
                    {"type": "text", "text": full_user_prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ]
            else:
                user_content = full_user_prompt

            messages.append({"role": "user", "content": user_content})

            payload = {
                "model": MODELE,
                "messages": messages,
                "temperature": AI_TEMPERATURE,
                "max_tokens": 1500
            }

            response = self._post_with_retry(URL, headers, payload,
                                             timeout=(5, 60),
                                             session=self.session)

            if response.status_code == 200:
                raw = response.json()['choices'][0]['message']['content']

                # === ORDRE CRITIQUE : parser les commandes sur la réponse BRUTE ===
                self._process_agenda_command(raw)
                self._process_scheduled_mail(raw)
                # Nettoyage inbox (suppression/archivage en lot sur demande)
                try:
                    self._process_inbox_action_command(raw)
                except Exception as _e:
                    print(f"Erreur inbox_action_command : {_e}")
                # Recherche / lecture de mails (affiche les resultats en SYSTEM)
                try:
                    self._process_mail_search_command(raw)
                except Exception as _e:
                    print(f"Erreur mail_search_command : {_e}")
                if self.lacher_prise_mode:
                    # Mode Lacher Prise : execution directe sans confirmation
                    mail_detected = self.process_ai_commands(raw)
                else:
                    # Mode normal : creer une demande de permission dans la
                    # boite Permissions. L'utilisateur ouvrira Permissions (⚡)
                    # et cliquera "Accepter" pour que le mail soit envoye.
                    if "COMMAND_START" in raw and "COMMAND_END" in raw:
                        self._create_mail_permission_request(raw)
                        mail_detected = False
                    else:
                        mail_detected = self.process_ai_commands(raw)

                # Nettoyage pour l'affichage et la mémoire
                txt = self.clean_text(raw)

                self.history.append({"role": "assistant", "content": txt})
                self.save_memory()
                database.add_experience_entry(USERNAME, "chat_response",
                                              f"Q: {self.history[-2].get('content','')[:60] if len(self.history)>=2 else ''}",
                                              "success", 1.0)

                self.root.after(0, lambda: self.add_message("MISTRAL", txt))
                self.root.after(0, self.start_notification_blink)

                self.chat_attachment_path = None
            else:
                code = response.status_code
                if code == 429:
                    msg = "⚠️ Mistral sature (429). Attends un peu avant de renvoyer."
                elif code == 401:
                    msg = "⚠️ Clé API invalide (401)."
                elif code >= 500:
                    msg = f"⚠️ Serveur Mistral indisponible ({code}). Reessaye plus tard."
                else:
                    msg = f"⚠️ Erreur API {code}"
                self.root.after(0, lambda m=msg: self.add_message("SYSTEM", m))

        except Exception as e:
            print(f"Erreur critique : {e}")
            self.root.after(0, lambda: self.add_message("SYSTEM", "⚠️ Impossible de lire le fichier ou l'IA est indisponible."))

    def process_ai_commands(self, text):
        if "COMMAND_START" not in text or "COMMAND_END" not in text:
            return False

        try:
            block_m = re.search(r"COMMAND_START(.*?)COMMAND_END", text, re.DOTALL)
            block = block_m.group(1) if block_m else text

            to_match   = re.search(r"TO\s*:\s*([\w\.-]+@[\w\.-]+\.\w+)", block)
            sub_match  = re.search(r"SUBJECT\s*:\s*(.+)", block)
            body_match = re.search(r"BODY\s*:(.*?)$", block, re.DOTALL)

            dest  = to_match.group(1).strip()   if to_match   else None
            sujet = sub_match.group(1).strip().strip('"').strip("'") if sub_match else "Message de l'Assistant"
            corps = body_match.group(1).strip() if body_match else None

            if dest and corps:
                self.root.after(0, lambda: self.add_message("SYSTEM", f"🚀 Envoi automatique du mail à {dest}..."))
                threading.Thread(target=self.execute_auto_mail,
                             args=(dest, sujet, corps, self.chat_attachment_path),
                             daemon=True).start()
                self.chat_attachment_path = None
                return True
            return False
        except Exception as e:
            print(f"Erreur d'analyse commande IA : {e}")
            return False

    def chat_attach_file(self):
        path = filedialog.askopenfilename()
        if path:
            self.chat_attachment_path = path
            self.add_message("SYSTEM", f"📎 Fichier prêt : {os.path.basename(path)}")

    def clean_text(self, text):
        text = re.sub(r"AGENDA_ADD_START.*?AGENDA_ADD_END", "", text, flags=re.DOTALL)
        text = re.sub(r"SCHEDULED_MAIL_START.*?SCHEDULED_MAIL_END", "", text, flags=re.DOTALL)
        text = re.sub(r"COMMAND_START.*?COMMAND_END", "", text, flags=re.DOTALL)
        text = re.sub(r"INBOX_ACTION_START.*?INBOX_ACTION_END", "", text, flags=re.DOTALL)
        text = re.sub(r"MAIL_SEARCH_START.*?MAIL_SEARCH_END", "", text, flags=re.DOTALL)
        return text.replace("*", "").strip()

    def add_message(self, sender, text):
        display_text = re.sub(r"COMMAND_START.*?COMMAND_END", "", text, flags=re.DOTALL).strip()
        display_text = re.sub(r"AGENDA_ADD_START.*?AGENDA_ADD_END", "", display_text, flags=re.DOTALL).strip()
        display_text = re.sub(r"SCHEDULED_MAIL_START.*?SCHEDULED_MAIL_END", "", display_text, flags=re.DOTALL).strip()
        display_text = re.sub(r"INBOX_ACTION_START.*?INBOX_ACTION_END", "", display_text, flags=re.DOTALL).strip()
        display_text = re.sub(r"MAIL_SEARCH_START.*?MAIL_SEARCH_END", "", display_text, flags=re.DOTALL).strip()
        if not display_text and sender == "MISTRAL":
            return None

        # Configuration visuelle par type d'expéditeur
        if sender == "USER":
            side     = "e"
            bg       = "#0b5fd4"
            border   = "#2d7dff"
            fg       = "#ffffff"
            max_w    = 290
        elif sender == "MISTRAL":
            side     = "w"
            bg       = "#141425"
            border   = "#22b9ff"
            fg       = "#dde4ff"
            max_w    = 300
        elif sender == "BRIEFING":
            # Style distinct or pour le briefing matinal (cf. hub d'entree)
            side     = "w"
            bg       = "#1a1400"
            border   = "#ffd700"
            fg       = "#ffe27a"
            max_w    = 340
        else:  # SYSTEM
            side     = "w"
            bg       = "#1a1025"
            border   = "#c084fc"
            fg       = "#c9b8e6"
            max_w    = 290

        msg_box = ctk.CTkFrame(
            self.msg_area,
            fg_color=bg,
            corner_radius=16,
            border_width=1,
            border_color=border,
        )
        msg_box.pack(padx=14, pady=6, anchor=side)

        lbl = ctk.CTkLabel(
            msg_box, text=display_text,
            wraplength=max_w, justify="left",
            font=("Segoe UI", 11),
            text_color=fg,
        )
        lbl.pack(padx=14, pady=10)

        try:
            self.msg_area._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass
        return msg_box

    def show_morning_briefing(self, force: bool = False, open_chat: bool = True):
        """Genere et affiche le briefing matinal dans le chat.

        - force=False : n'affiche qu'une fois par jour (idempotent)
        - open_chat=True : ouvre le chat si pas deja ouvert
        Le calcul tourne dans un thread daemon pour ne pas bloquer l'UI.
        """
        from datetime import date as _date
        today_iso = _date.today().isoformat()
        if not force and getattr(self, "_briefing_shown_date", None) == today_iso:
            return
        self._briefing_shown_date = today_iso

        def _worker():
            try:
                from modules.morning_briefing import generate_briefing
                br = generate_briefing(USERNAME)
            except Exception as e:
                print(f"Erreur generate_briefing : {e}")
                return
            text = br.get("raw_text", "")
            if not text:
                return
            # Ouvre le chat si demande, puis pousse le message en mode BRIEFING
            def _push():
                try:
                    if open_chat and self.mode != "chat":
                        self.toggle_chat()
                    self.add_message("BRIEFING", text)
                except Exception as e:
                    print(f"Erreur push briefing : {e}")
            try:
                self.root.after(0, _push)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def smart_reset(self):
        self.history = []
        self.save_memory()
        for widget in self.msg_area.winfo_children():
            widget.destroy()
        self.add_message("SYSTEM", f"Mémoire effacée pour {USERNAME}.")

    def load_memory(self):
        # Lecture chiffree transparente : si la lib cryptography est dispo,
        # le fichier est chiffre (Fernet/AES). Fallback automatique au format
        # JSON-en-clair pour les anciennes installs.
        if not os.path.exists(MEMORY_FILE):
            return []
        try:
            from modules.crypto import read_text as _read_enc
            raw = _read_enc(MEMORY_FILE, USERNAME)
            if not raw:
                return []
            return json.loads(raw)
        except Exception:
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []

    def save_memory(self):
        # Snapshot + ecriture en arriere-plan : l'UI n'est jamais bloquee
        # par l'I/O disque, meme si memory_<user>.json grossit.
        snapshot = list(self.history[-30:])
        def _write():
            try:
                from modules.crypto import write_text as _write_enc
                payload = json.dumps(snapshot, indent=4, ensure_ascii=False)
                _write_enc(MEMORY_FILE, payload, USERNAME)
            except Exception as e:
                # Fallback JSON-en-clair si le module crypto pose probleme
                try:
                    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                        json.dump(snapshot, f, indent=4, ensure_ascii=False)
                except Exception as e2:
                    print(f"Erreur sauvegarde memoire : {e} / {e2}")
        threading.Thread(target=_write, daemon=True).start()
