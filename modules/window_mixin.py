"""Gestion fenêtre."""
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


class WindowMixin:
    def _save_report_hour(self):
        try:
            h = int(self.report_hour_var.get())
            if 0 <= h <= 23:
                self.daily_report_hour = h
                self._show_toast(f"Rapport journalier réglé à {h:02d}h00")
                # Met a jour la tache Windows Task Scheduler si elle existe
                # (permet l'envoi meme si l'application est fermee / PC veille).
                try:
                    self._register_windows_scheduled_report(h)
                except Exception as e:
                    print(f"Note : tache Windows non mise a jour : {e}")
        except:
            pass

    def _register_windows_scheduled_report(self, hour):
        """Enregistre (ou remplace) la tache Windows 'AssistantDailyReport'
        qui execute daily_report.bat chaque jour a l'heure voulue.

        Ne demande PAS les droits admin : la tache s'execute sous le compte
        utilisateur courant. Pour que le PC s'allume automatiquement,
        l'utilisateur doit cocher 'Reveiller l'ordinateur' dans les
        proprietes de la tache (cf. register_daily_report.bat).
        """
        import os as _os
        import subprocess as _sp
        base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        bat_path = _os.path.join(base, "daily_report.bat")
        if not _os.path.exists(bat_path):
            return False
        hh = f"{int(hour):02d}:00"
        cmd = [
            "schtasks", "/Create",
            "/SC", "DAILY",
            "/TN", "AssistantDailyReport",
            "/TR", f'"{bat_path}"',
            "/ST", hh,
            "/F",
        ]
        try:
            # CREATE_NO_WINDOW = 0x08000000 pour ne pas ouvrir de fenetre
            _sp.run(cmd, creationflags=0x08000000, timeout=10, check=False)
            return True
        except Exception:
            return False

    def _toggle_lacher_prise(self):
        self.lacher_prise_mode = not self.lacher_prise_mode
        if self.lacher_prise_mode:
            self.lacher_btn.configure(text="ACTIF ✦", fg_color="#3a0050", text_color="#ff44ff")
            self._show_toast("Mode Lâcher Prise ACTIVÉ — L'IA agit en autonomie totale.")
        else:
            self.lacher_btn.configure(text="INACTIF", fg_color="#1a0000", text_color=couleur_permissions)
            self._show_toast("Mode Lâcher Prise DÉSACTIVÉ.")

    def _hide_all_frames(self):
        for w in [self.chat_frame, self.mail_frame, self.options_frame,
                  self.admin_frame, self.bubble_canvas, self.hub_canvas,
                  self.inbox_frame, self.agenda_frame,
                  getattr(self, 'reseau_frame', None),
                  getattr(self, 'permissions_frame', None),
                  getattr(self, 'finance_frame', None),
                  getattr(self, 'finance_hub_canvas', None),
                  getattr(self, 'principal_hub_canvas', None)]:
            if w is None: continue
            try:
                w.pack_forget()
            except Exception:
                pass

    def toggle_chat(self):
        if self.mode == "chat":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.hub_open = False
            self.chat_frame.pack(fill="both", expand=True)
            self.root.geometry("450x650")
            self.root.deiconify()
            self.mode = "chat"
            self.entry.focus_set()

    def toggle_bubble(self):
        if self.mode == "bubble":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.hub_open = False
            self.bubble_canvas.pack()
            self.root.geometry(f"{self.bubble_win_size}x{self.bubble_win_size}")
            self.root.deiconify()
            self.mode = "bubble"

    def toggle_inbox(self):
        if self.mode == "inbox":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            # On ajoute un petit padding (padx/pady) pour que les arrondis soient parfaits
            self.inbox_frame.pack(fill="both", expand=True, padx=2, pady=2)
            
            w, h = 470, 620
            x = (self.sw // 2) - (w // 2)
            y = (self.sh // 2) - (h // 2)
            self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
            self.root.deiconify()
            self.mode = "inbox"
            self.refresh_inbox()

    def toggle_agenda(self):
        if self.mode == "agenda":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.agenda_frame.pack(fill="both", expand=True)
            w, h = 430, 620
            x = (self.sw // 2) - (w // 2)
            y = (self.sh // 2) - (h // 2)
            self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
            self.root.deiconify()
            self.mode = "agenda"
            self._agenda_selected_date = date.today()
            self._render_agenda_calendar()
            self._agenda_select_day(date.today())

    def toggle_hub(self):
        if self.mode == "hub":
            self.hub_open = False
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.hub_open = False
            self.hub_dots_progress = [0.0] * 8
            self.hub_canvas.pack(fill="both", expand=True)
            self.root.geometry(f"{self.hub_size}x{self.hub_size}")
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.mode = "hub"
            self._refresh_pending_cache()
            self.root.after(50, self._hub_logo_loop)

    def toggle_options(self):
        if self.mode == "options":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.hub_open = False
            self.options_frame.pack(fill="both", expand=True)
            self.root.geometry("380x600")
            self.root.deiconify()
            self.mode = "options"


    def toggle_admin(self):
        if USER_LEVEL > 0:
            return
        if self.mode == "admin":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.hub_open = False
            self.admin_frame.pack(fill="both", expand=True)
            self.root.geometry("540x660")
            self.root.deiconify()
            self.mode = "admin"
            self.refresh_admin_logs(self.admin_user_list.get())

    def toggle_mail(self):
        if self.mode == "mail":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.hub_open = False
            self.mail_status.configure(text="", text_color="#00ffcc")
            self.mail_attachment_path = None
            self.lbl_file_status.configure(text="Aucun fichier", text_color="#555")
            self.root.geometry("400x650")
            self.mail_frame.pack(fill="both", expand=True)
            self.root.deiconify()
            self.mode = "mail"

    def auto_return_hub(self):
        if self.mode == "mail":
            self.toggle_hub()

    def logout(self):
        database.set_user_online(USERNAME, False)
        subprocess.Popen([sys.executable, "Login.pyw"])
        self.root.quit()
        sys.exit()

    def toggle_reseau(self):
        if USER_LEVEL > 0:
            self._show_toast("Accès Réseau réservé au Père (niveau 0).")
            return
        if self.mode == "reseau":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.reseau_frame.pack(fill="both", expand=True)
            w, h = 480, 600
            self.root.geometry(f"{w}x{h}+{(self.sw-w)//2}+{(self.sh-h)//2}")
            self.root.deiconify()
            self.mode = "reseau"
            self._render_reseau()

    def toggle_permissions(self):
        if self.mode == "permissions":
            self.root.withdraw(); self.mode = "hidden"
        else:
            self._hide_all_frames()
            self.permissions_frame.pack(fill="both", expand=True)
            w, h = 460, 600
            self.root.geometry(f"{w}x{h}+{(self.sw-w)//2}+{(self.sh-h)//2}")
            self.root.deiconify()
            self.mode = "permissions"
            self._refresh_permissions()

    def hub_click_event(self, event):
        cx, cy = self.hub_size // 2, self.hub_size // 2
        dist = math.sqrt((event.x - cx)**2 + (event.y - cy)**2)

        if dist < 60:
            self.hub_active_animation = True
            self.root.after(150, lambda: setattr(self, 'hub_active_animation', False))
            self.hub_open = not self.hub_open
            self._refresh_pending_cache()
            self.update_hub_animation()
            return

        if self.hub_open:
            nb = 8
            radius = 150
            for i in range(nb):
                angle = math.radians(i * (360 / nb) - 90)
                ox = cx + radius * math.cos(angle)
                oy = cy + radius * math.sin(angle)
                if math.sqrt((event.x - ox)**2 + (event.y - oy)**2) < 40:
                    if   i == 0: self.toggle_chat()
                    elif i == 1: self.toggle_options()
                    elif i == 2:
                        if USER_LEVEL == 0: self.toggle_admin()
                        else: self.toggle_permissions()
                    elif i == 3: self.toggle_principal_hub()  # retour hub d'entree
                    elif i == 4: self.toggle_inbox()
                    elif i == 5: self.toggle_agenda()
                    elif i == 6: self.toggle_reseau()
                    elif i == 7: self.toggle_permissions()    # PERMS (mail compose passe en bouton chat)
                    break

    def update_speed(self, val):
        self.bubble_speed = int(val)
        self.speed_x = self.bubble_speed if self.speed_x > 0 else -self.bubble_speed
        self.speed_y = self.bubble_speed if self.speed_y > 0 else -self.bubble_speed

    def click_window(self, event):
        self._offsetx, self._offsety = event.x, event.y

    def drag_window(self, event):
        if self.mode != "hidden":
            x = self.root.winfo_pointerx() - self._offsetx
            y = self.root.winfo_pointery() - self._offsety
            self.root.geometry(f"+{x}+{y}")

