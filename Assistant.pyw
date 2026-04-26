"""Assistant IA — point d'entrée principal (refactorisé en mixins)."""
import os
import sys
import json
import time
import threading
import ctypes
import requests
import tkinter as tk
import customtkinter as ctk
import keyboard
import database
from datetime import datetime, date

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# --- .env (secrets portables, prioritaire avant tout autre import) ---
try:
    from modules.dotenv_loader import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Logging centralise (tournant, logs/assistant.log) ---
from modules.logger import setup_logging, get_logger
setup_logging()
_log = get_logger("Assistant")
_log.info("Demarrage de l'Assistant")

# --- Backup auto des donnees critiques (non bloquant, max 7 zips) ---
try:
    from modules.backup import make_backup_async
    make_backup_async()
except Exception as _e:
    _log.warning("Backup auto indisponible : %s", _e)

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
from modules.agenda_mixin import AgendaMixin
from modules.ui_frames_mixin import UIFramesMixin
from modules.email_mixin import EmailMixin
from modules.email_commands_mixin import EmailCommandsMixin
from modules.inbox_mixin import InboxMixin
from modules.inbox_optim_mixin import InboxOptimMixin
from modules.ai_chat_mixin import AIChatMixin
from modules.admin_mixin import AdminMixin
from modules.network_mixin import NetworkMixin
from modules.permissions_mixin import PermissionsMixin
from modules.animations_mixin import AnimationsMixin
from modules.window_mixin import WindowMixin
from modules.finance_mixin import FinanceMixin
from modules.principal_hub_mixin import PrincipalHubMixin


class AssistantApp(
    AgendaMixin,
    UIFramesMixin,
    EmailCommandsMixin,   # AVANT EmailMixin : prioritaire sur les methodes _process_*
    EmailMixin,
    InboxMixin,
    InboxOptimMixin,
    AIChatMixin,
    AdminMixin,
    NetworkMixin,
    PermissionsMixin,
    FinanceMixin,
    PrincipalHubMixin,
    AnimationsMixin,
    WindowMixin,
):
    def __init__(self):
        # --- Config email ---
        config = database.get_email_config(USERNAME)
        if config:
            self.user_email    = config["email"]
            self.user_app_pass = config["app_password"]
        else:
            self.user_email    = None
            self.user_app_pass = None

        # --- État ---
        self.mode                 = "hidden"
        self.history              = self.load_memory()
        self.blink_end_time       = 0
        self.logo_alpha           = 1.0
        self.hub_open             = False
        self.hub_dots_progress    = [0.0] * 8
        self.hub_active_animation = False
        self.hub_rotation         = 0
        self.chat_attachment_path = None
        self.mail_attachment_path = None
        self.session              = requests.Session()
        # Inbox — stockage des mails chargés pour le filtrage
        self._inbox_mails         = []   # liste de dicts {sender, subject, summary, urgency, action, date_str, attach_summaries, warning, mail_type, agenda_suggestion}
        self._inbox_active_filter = "TOUT"  # filtre actif

        # Mode Lâcher Prise
        self.lacher_prise_mode    = False

        # Reporting journalier
        self.daily_report_hour    = 18
        self._last_report_date    = None
        self._last_digest_date    = None

        # Optimisations inbox (tout activé par defaut)
        self.init_inbox_optim()

        # Réseau
        self._network_users       = {}

        # Agenda
        self.agenda_events        = self.load_agenda()
        self.agenda_current_month = date.today().replace(day=1)
        self._start_notification_thread()
        threading.Thread(target=self._network_refresh_loop, daemon=True).start()

        # --- Fenêtre principale ---
        self.root = ctk.CTk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "#010101")
        self.root.configure(fg_color="#010101")

        # --- Dimensions écran ---
        user32 = ctypes.windll.user32
        self.sw, self.sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

        self.bubble_speed    = 4
        self.pos_x           = self.sw // 2
        self.pos_y           = self.sh // 2
        self.speed_x         = self.bubble_speed
        self.speed_y         = self.bubble_speed
        self.bubble_win_size = 140
        self.hub_size        = 450
        self.pulse_val       = 0
        self._offsetx        = 0
        self._offsety        = 0

        self.root.bind("<Button-1>", self.click_window)
        self.root.bind("<B1-Motion>", self.drag_window)

        # --- Création des interfaces ---
        self._build_chat_frame()
        self._build_mail_frame()
        self._build_options_frame()
        self._build_admin_frame()
        self._build_inbox_frame()
        self._build_agenda_frame()
        self._build_reseau_frame()
        self._build_permissions_frame()
        # Hub finance + frame partage des 7 vues
        self._build_finance_frame()

        # --- Canvas ---
        self.bubble_canvas = tk.Canvas(self.root, width=self.bubble_win_size, height=self.bubble_win_size,
                                       bg="#010101", highlightthickness=0)
        self.hub_canvas = tk.Canvas(self.root, width=self.hub_size, height=self.hub_size,
                                    bg="#010101", highlightthickness=0)
        self.hub_canvas.bind("<Button-1>", self.hub_click_event)
        # Hub finance (canvas distinct, meme taille que le hub principal)
        self._build_finance_hub()
        # Hub d'entree (3 branches : MAIL / FINANCE / LOGOUT)
        self._build_principal_hub()

        # Wizard fiscal au 1er lancement (non bloquant)
        try:
            self.root.after(2000, self.maybe_show_fiscal_wizard)
        except Exception:
            pass

        # --- Raccourcis clavier ---
        keyboard.add_hotkey('ctrl+alt+g', self.toggle_chat)
        keyboard.add_hotkey('ctrl+alt+b', self.toggle_bubble)
        # ctrl+alt+h ouvre le hub d'entree (et non plus le hub mail
        # directement). Depuis le hub d'entree, l'utilisateur choisit MAIL,
        # FINANCE ou LOGOUT.
        keyboard.add_hotkey('ctrl+alt+h', self.toggle_principal_hub)
        keyboard.add_hotkey('ctrl+alt+o', self.toggle_options)
        keyboard.add_hotkey('ctrl+alt+a', self.toggle_admin)
        keyboard.add_hotkey('ctrl+alt+m', self.toggle_mail)
        keyboard.add_hotkey('ctrl+alt+p', self.toggle_permissions)
        keyboard.add_hotkey('ctrl+alt+r', self.toggle_reseau)

        # --- Démarrage ---
        # On ouvre directement le hub d'entree (3 branches) au lancement.
        self.root.withdraw()
        self.animate()
        # Affiche le hub d'entree juste apres le 1er tick d'anim.
        self.root.after(100, self.toggle_principal_hub)



if __name__ == "__main__":
    app = AssistantApp()
    app.root.mainloop()
