"""Construction des frames UI."""
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


class UIFramesMixin:
    def _build_agenda_frame(self):
        self.agenda_frame = ctk.CTkFrame(
            self.root, fg_color="#0a0a0a",
            border_color=couleur_agenda, border_width=2, corner_radius=30
        )
        self.agenda_frame.bind("<Button-1>", self.click_window)

        # --- En-tête ---
        header = ctk.CTkFrame(self.agenda_frame, fg_color="transparent")
        header.pack(pady=(20, 5), fill="x", padx=20)

        ctk.CTkLabel(header, text="📅 NEURAL AGENDA",
                     font=("Futura", 18, "bold"), text_color=couleur_agenda).pack(side="left")
        ctk.CTkButton(header, text="RETOUR HUB", fg_color="transparent",
                      text_color="#555", width=90, command=self.toggle_hub).pack(side="right")

        # --- Navigation mois ---
        nav = ctk.CTkFrame(self.agenda_frame, fg_color="transparent")
        nav.pack(fill="x", padx=20, pady=5)

        ctk.CTkButton(nav, text="◀", width=35, height=30,
                      fg_color="#1a1a1a", hover_color=couleur_agenda,
                      command=self._agenda_prev_month).pack(side="left")

        self.lbl_agenda_month = ctk.CTkLabel(nav, text="", font=("Futura", 13, "bold"),
                                              text_color=couleur_agenda, width=200)
        self.lbl_agenda_month.pack(side="left", expand=True)

        ctk.CTkButton(nav, text="▶", width=35, height=30,
                      fg_color="#1a1a1a", hover_color=couleur_agenda,
                      command=self._agenda_next_month).pack(side="right")

        # --- Grille calendrier ---
        self.cal_frame = ctk.CTkFrame(self.agenda_frame, fg_color="transparent")
        self.cal_frame.pack(padx=15, pady=5, fill="both")

        # --- Détail événements du jour sélectionné (timeline) ---
        self.agenda_detail_frame = ctk.CTkScrollableFrame(
            self.agenda_frame, fg_color="#0c0c1a", corner_radius=12, height=220
        )
        self.agenda_detail_frame.pack(padx=15, pady=5, fill="x")

        # --- Bouton ajout rapide ---
        add_row = ctk.CTkFrame(self.agenda_frame, fg_color="transparent")
        add_row.pack(fill="x", padx=15, pady=(5, 15))

        self.agenda_quick_entry = ctk.CTkEntry(
            add_row, placeholder_text="Titre de l'événement...",
            fg_color="#1a1a1a", height=38, corner_radius=18
        )
        self.agenda_quick_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(add_row, text="+ Ajouter", width=80, height=38,
                      fg_color=couleur_agenda, hover_color="#a855f7", corner_radius=18,
                      text_color="#000", font=("Futura", 11, "bold"),
                      command=self._agenda_quick_add).pack(side="right")

    def _build_chat_frame(self):
        self.chat_frame = ctk.CTkFrame(self.root, fg_color="#08081a",
                                       border_color=couleur_accent, border_width=2, corner_radius=30)

        header = ctk.CTkFrame(self.chat_frame, fg_color="transparent")
        header.pack(pady=(18, 4), fill="x")

        self.header_logo_canvas = tk.Canvas(header, width=40, height=40,
                                            bg="#08081a", highlightthickness=0)
        self.header_logo_canvas.pack(side="left", padx=(25, 12))
        self.header_logo_canvas.bind("<Button-1>", lambda e: self.toggle_hub())

        # Titre + sous-titre
        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.pack(side="left")
        ctk.CTkLabel(title_col, text="Neural Assistant",
                     font=("Futura", 14, "bold"),
                     text_color=couleur_accent).pack(anchor="w")
        role_display = f"● {USER_ROLE}" if USER_ROLE else "● online"
        ctk.CTkLabel(title_col, text=role_display,
                     font=("Futura", 8),
                     text_color="#22b9ff").pack(anchor="w")

        # Bouton briefing du jour (force=True pour ouvrir a la demande)
        ctk.CTkButton(
            header, text="☀", width=40, height=32,
            fg_color="#0f0f22", hover_color="#ffd700",
            text_color="#ffd700",
            corner_radius=16, font=("Arial", 14, "bold"),
            command=lambda: self.show_morning_briefing(
                force=True, open_chat=False)).pack(side="right", padx=(0, 8))

        ctk.CTkButton(header, text="⟲", width=40, height=32,
                      fg_color="#0f0f22", hover_color=couleur_accent,
                      text_color=couleur_accent,
                      corner_radius=16, font=("Arial", 14, "bold"),
                      command=self.smart_reset).pack(side="right", padx=25)

        # Séparateur lumineux
        tk.Frame(self.chat_frame, bg="#1a1a2e", height=1).pack(fill="x", padx=20, pady=(6, 4))

        self.msg_area = ctk.CTkScrollableFrame(
            self.chat_frame, fg_color="transparent", corner_radius=0,
            scrollbar_button_color="#1a1a2e",
            scrollbar_button_hover_color=couleur_accent)
        self.msg_area.pack(padx=10, pady=5, fill="both", expand=True)

        input_container = ctk.CTkFrame(self.chat_frame, fg_color="transparent")
        input_container.pack(fill="x", padx=20, pady=(8, 22))

        ctk.CTkButton(input_container, text="📎", width=46, height=46,
                      fg_color="#0f0f22", hover_color="#1a1a3a",
                      text_color=couleur_accent,
                      corner_radius=23, font=("Arial", 14),
                      command=self.chat_attach_file).pack(side="left", padx=(0, 6))

        # Accès rapide au mode Mail depuis le chat (remplace l'orbite MAIL du hub)
        ctk.CTkButton(input_container, text="✉", width=46, height=46,
                      fg_color="#0f0f22", hover_color="#1a1a3a",
                      text_color=couleur_mail_pale,
                      corner_radius=23, font=("Arial", 14),
                      command=self.toggle_mail).pack(side="left", padx=(0, 10))

        self.entry = ctk.CTkEntry(input_container,
                                  placeholder_text="Parler à Assistant ou /agenda",
                                  fg_color="#0f0f22",
                                  border_color="#1a1a3a", border_width=1,
                                  text_color="#dde4ff",
                                  height=46, corner_radius=23,
                                  font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.send_logic())

    def _build_mail_frame(self):
        self.mail_frame = ctk.CTkFrame(self.root, width=400, height=650, fg_color="#08081a",
                                       border_color="#00ffcc", border_width=2, corner_radius=30)
        self.mail_frame.pack_propagate(False)

        # Header avec sous-titre
        header_col = ctk.CTkFrame(self.mail_frame, fg_color="transparent")
        header_col.pack(pady=(30, 4))
        ctk.CTkLabel(header_col, text="✉  NEURAL MAILING",
                     font=("Futura", 18, "bold"), text_color="#00ffcc").pack()
        ctk.CTkLabel(header_col, text="Transmission sécurisée",
                     font=("Futura", 8), text_color="#2a5a55").pack()

        # Séparateur
        tk.Frame(self.mail_frame, bg="#0f2a2a", height=1).pack(fill="x", padx=30, pady=(8, 14))

        self.mail_to = ctk.CTkEntry(self.mail_frame, placeholder_text="DESTINATAIRE",
                                    width=340, height=42, corner_radius=21,
                                    fg_color="#0f0f1f", border_color="#1a3a3a", border_width=1)
        self.mail_to.pack(pady=6)

        self.mail_subject = ctk.CTkEntry(self.mail_frame, placeholder_text="SUJET",
                                         width=340, height=42, corner_radius=21,
                                         fg_color="#0f0f1f", border_color="#1a3a3a", border_width=1)
        self.mail_subject.pack(pady=6)

        ctk.CTkButton(self.mail_frame, text="📎  PIÈCE JOINTE",
                      width=160, height=32, corner_radius=16,
                      fg_color="#0f1f1f", hover_color="#1a3a3a",
                      text_color="#00ffcc", font=("Futura", 10, "bold"),
                      command=self.attach_file_logic).pack(pady=(10, 4))

        self.lbl_file_status = ctk.CTkLabel(self.mail_frame, text="Aucun fichier",
                                            font=("Segoe UI", 9), text_color="#2a5a55")
        self.lbl_file_status.pack()

        self.mail_body = ctk.CTkTextbox(self.mail_frame, width=340, height=180,
                                        corner_radius=16,
                                        fg_color="#0f0f1f", border_color="#1a3a3a", border_width=1)
        self.mail_body.pack(pady=12)

        self.mail_status = ctk.CTkLabel(self.mail_frame, text="", font=("Segoe UI", 10, "bold"))
        self.mail_status.pack(pady=4)

        ctk.CTkButton(self.mail_frame, text="▸  TRANSMETTRE",
                      fg_color="#00a888", hover_color="#00ffcc",
                      text_color="#001a15",
                      width=340, height=46, corner_radius=23,
                      font=("Futura", 12, "bold"),
                      command=self.send_mail_logic).pack(pady=(8, 6))
        ctk.CTkButton(self.mail_frame, text="← RETOUR HUB", fg_color="transparent",
                      hover_color="#0f1f1f",
                      text_color="#4a7a7a", font=("Futura", 9),
                      command=self.toggle_hub).pack(pady=(2, 20))

        self.mail_frame.bind("<Button-1>", self.click_window)

    def _build_admin_frame(self):
        self.admin_frame = ctk.CTkFrame(self.root, fg_color="#0a0a0a",
                                        border_color=couleur_gold, border_width=2, corner_radius=30)
        hdr = ctk.CTkFrame(self.admin_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 8))
        ctk.CTkLabel(hdr, text="⬡  CENTRE ADMIN PÈRE",
                     font=("Futura", 16, "bold"), text_color=couleur_gold).pack(side="left")
        ctk.CTkButton(hdr, text="← HUB", width=60, height=28,
                      fg_color="transparent", hover_color="#1a1a00",
                      text_color=couleur_gold, font=("Futura", 9),
                      command=self.toggle_hub).pack(side="right")
        tk.Frame(self.admin_frame, bg="#2a2000", height=1).pack(fill="x", padx=20, pady=(0, 10))

        # Zone de défilement utilisateurs — boutons cliquables
        users_outer = ctk.CTkScrollableFrame(self.admin_frame, fg_color="transparent", height=120)
        users_outer.pack(fill="x", padx=20, pady=4)
        self.admin_user_btns_frame = users_outer
        self._build_admin_user_buttons()

        # Log area
        self.admin_log_area = ctk.CTkTextbox(
            self.admin_frame, fg_color="#060606",
            text_color="#44ff88", border_color="#1a1a00",
            border_width=1, corner_radius=12)
        self.admin_log_area.pack(pady=6, padx=20, fill="both", expand=True)

        footer = ctk.CTkFrame(self.admin_frame, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(4, 16))
        ctk.CTkButton(footer, text="+ Créer utilisateur", width=140, height=32,
                      fg_color="#1a1a00", hover_color=couleur_gold,
                      text_color=couleur_gold,
                      corner_radius=16, font=("Futura", 10),
                      command=self._admin_create_user).pack(side="left")
        ctk.CTkButton(footer, text="↻ Actualiser", width=100, height=32,
                      fg_color="#1a1a00", hover_color="#333300",
                      text_color="#aaa800", corner_radius=16, font=("Futura", 10),
                      command=self._build_admin_user_buttons).pack(side="right")
        self.admin_frame.bind("<Button-1>", self.click_window)

    def _build_options_frame(self):
        self.options_frame = ctk.CTkFrame(self.root, fg_color="#0a0a12",
                                          border_color=couleur_option_pale, border_width=2, corner_radius=30)
        ctk.CTkLabel(self.options_frame, text="PARAMÈTRES",
                     font=("Futura", 16, "bold"), text_color=couleur_option_pale).pack(pady=(24, 8))

        ctk.CTkLabel(self.options_frame, text="Vitesse de la bulle",
                     font=("Segoe UI", 12), text_color="#cc7777").pack(pady=(12, 4))
        self.speed_slider = ctk.CTkSlider(self.options_frame, from_=1, to=20, command=self.update_speed,
                                          progress_color=couleur_option_pale, button_color=couleur_option_pale)
        self.speed_slider.set(self.bubble_speed)
        self.speed_slider.pack(pady=8, padx=40, fill="x")

        tk.Frame(self.options_frame, bg="#2a1a1a", height=1).pack(fill="x", padx=30, pady=14)

        # Rapport journalier
        ctk.CTkLabel(self.options_frame, text="Heure rapport journalier",
                     font=("Segoe UI", 11), text_color="#cc7777").pack(pady=(4, 4))
        hour_row = ctk.CTkFrame(self.options_frame, fg_color="transparent")
        hour_row.pack()
        self.report_hour_var = tk.StringVar(value=str(self.daily_report_hour))
        ctk.CTkEntry(hour_row, textvariable=self.report_hour_var,
                     width=60, height=34, corner_radius=17,
                     fg_color="#111122", font=("Consolas", 12)).pack(side="left", padx=8)
        ctk.CTkLabel(hour_row, text="h00", font=("Futura", 12),
                     text_color="#cc7777").pack(side="left")
        ctk.CTkButton(hour_row, text="✔", width=34, height=34,
                      fg_color="#1a0000", hover_color=couleur_option_pale,
                      corner_radius=17, font=("Arial", 12),
                      command=self._save_report_hour).pack(side="left", padx=8)

        tk.Frame(self.options_frame, bg="#2a1a1a", height=1).pack(fill="x", padx=30, pady=14)

        # Mode Lâcher Prise
        ctk.CTkLabel(self.options_frame, text="Mode Lâcher Prise",
                     font=("Segoe UI", 11), text_color="#cc7777").pack(pady=(4, 6))
        self.lacher_btn = ctk.CTkButton(
            self.options_frame,
            text="INACTIF",
            fg_color="#1a0000", hover_color=couleur_permissions,
            text_color=couleur_permissions,
            width=200, height=38, corner_radius=19,
            font=("Futura", 12, "bold"),
            command=self._toggle_lacher_prise)
        self.lacher_btn.pack(pady=6)
        ctk.CTkLabel(self.options_frame,
                     text="L'IA agit sans demander de permission.",
                     font=("Segoe UI", 9), text_color="#443333").pack()

        # NB : le bouton PERMISSIONS a ete deplace sur le hub mail (slot 7)
        # avec son compteur de demandes en attente. Plus besoin ici.

        ctk.CTkButton(self.options_frame, text="RETOUR HUB",
                      fg_color="#1a1a1a", hover_color=couleur_accent,
                      command=self.toggle_hub, corner_radius=18, height=36).pack(pady=24)
        self.options_frame.bind("<Button-1>", self.click_window)

    def _build_inbox_frame(self):
        self.inbox_frame = ctk.CTkFrame(self.root, fg_color="#07070f",
                                         border_color="#1a1a35", border_width=1, corner_radius=28)
        self.inbox_frame.bind("<Button-1>", self.click_window)

        # === HEADER PREMIUM ===
        header_bg = ctk.CTkFrame(self.inbox_frame, fg_color="#0d0d1f", corner_radius=0, height=70)
        header_bg.pack(fill="x")
        header_bg.pack_propagate(False)

        header_inner = ctk.CTkFrame(header_bg, fg_color="transparent")
        header_inner.pack(fill="both", expand=True, padx=20, pady=12)

        # Titre avec accent
        title_col = ctk.CTkFrame(header_inner, fg_color="transparent")
        title_col.pack(side="left")
        ctk.CTkLabel(title_col,
            text="INBOX",
            font=("Futura", 20, "bold"),
            text_color="#dde4ff").pack(side="left")
        ctk.CTkLabel(title_col,
            text="  ✦ NEURAL",
            font=("Futura", 10),
            text_color="#22b9ff").pack(side="left", pady=(4, 0))

        # Boutons header
        btn_frame = ctk.CTkFrame(header_inner, fg_color="transparent")
        btn_frame.pack(side="right")

        ctk.CTkButton(btn_frame,
            text="↻",
            width=38, height=38,
            fg_color="#1a1a35", hover_color="#22b9ff",
            text_color="#22b9ff",
            font=("Arial", 16),
            corner_radius=19,
            command=self.refresh_inbox).pack(side="right", padx=(5, 0))

        self.focus_btn = ctk.CTkButton(btn_frame,
            text="◎",
            width=38, height=38,
            fg_color="#1a1a35", hover_color="#22b9ff",
            text_color="#999",
            font=("Arial", 16),
            corner_radius=19,
            command=self.toggle_focus_mode)
        self.focus_btn.pack(side="right", padx=(5, 0))

        ctk.CTkButton(btn_frame,
            text="🧹",
            width=38, height=38,
            fg_color="#1a1a35", hover_color="#ff4060",
            text_color="#ff88aa",
            font=("Arial", 14),
            corner_radius=19,
            command=self.bulk_delete_spam).pack(side="right", padx=(5, 0))

        ctk.CTkButton(btn_frame,
            text="HUB",
            width=52, height=38,
            fg_color="transparent", hover_color="#1a1a35",
            text_color="#333355",
            font=("Futura", 9, "bold"),
            corner_radius=19,
            command=self.toggle_hub).pack(side="right")

        # Séparateur lumineux
        sep = tk.Frame(self.inbox_frame, bg="#1e1e3a", height=1)
        sep.pack(fill="x")

        # Filtre rapide (urgence)
        filter_row = ctk.CTkFrame(self.inbox_frame, fg_color="#0a0a18", height=40)
        filter_row.pack(fill="x")
        filter_row.pack_propagate(False)

        filter_inner = ctk.CTkFrame(filter_row, fg_color="transparent")
        filter_inner.pack(expand=True, fill="both", padx=15, pady=6)

        ctk.CTkLabel(filter_inner,
            text="FILTRER :",
            font=("Futura", 8), text_color="#2a2a44").pack(side="left")

        self._filter_buttons = {}
        for label, color in [("TOUT", "#dde4ff"), ("URGENT", "#ff4060"), ("NORMAL", "#22b9ff"), ("INFO", "#444466")]:
            btn = ctk.CTkButton(filter_inner,
                text=label,
                width=60, height=26,
                fg_color="#1a1a35" if label == "TOUT" else "transparent",
                hover_color="#1a1a35",
                text_color=color,
                font=("Futura", 8, "bold"),
                corner_radius=13,
                command=lambda l=label: self._apply_inbox_filter(l))
            btn.pack(side="left", padx=3)
            self._filter_buttons[label] = (btn, color)

        # === ZONE SCROLL PRINCIPALE ===
        self.inbox_scroll = ctk.CTkScrollableFrame(
            self.inbox_frame,
            fg_color="transparent",
            scrollbar_button_color="#1a1a35",
            scrollbar_button_hover_color="#22b9ff"
        )
        self.inbox_scroll.pack(padx=8, pady=5, fill="both", expand=True)

        # === FOOTER ===
        footer = ctk.CTkFrame(self.inbox_frame, fg_color="#0a0a18", height=42)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        ctk.CTkLabel(footer,
            text="Neural Assistant  ·  Inbox",
            font=("Futura", 8), text_color="#1e1e3a").pack(expand=True)

    def _build_reseau_frame(self):
        self.reseau_frame = ctk.CTkFrame(self.root, fg_color="#080808",
                                          border_color=couleur_reseau, border_width=2, corner_radius=30)
        hdr = ctk.CTkFrame(self.reseau_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(18, 8))
        ctk.CTkLabel(hdr, text="⬡  RÉSEAU NEURAL",
                     font=("Futura", 16, "bold"), text_color=couleur_reseau).pack(side="left")
        ctk.CTkButton(hdr, text="← HUB", width=60, height=28,
                      fg_color="transparent", hover_color="#1a1400",
                      text_color=couleur_reseau, font=("Futura", 9),
                      command=self.toggle_hub).pack(side="right")
        tk.Frame(self.reseau_frame, bg="#1a1400", height=1).pack(fill="x", padx=20, pady=(0, 8))

        # Canvas réseau interactif
        self.reseau_canvas = tk.Canvas(self.reseau_frame, width=440, height=260,
                                       bg="#050508", highlightthickness=0)
        self.reseau_canvas.pack(padx=16, pady=8)

        # Config réseau
        cfg = ctk.CTkScrollableFrame(self.reseau_frame, fg_color="#0a0a00",
                                      corner_radius=12, height=200)
        cfg.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(cfg, text="Configuration globale du réseau",
                     font=("Futura", 11, "bold"), text_color=couleur_reseau).pack(anchor="w", padx=12, pady=(8, 4))

        row2 = ctk.CTkFrame(cfg, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=4)
        ctk.CTkButton(row2, text="Envoyer rapport maintenant", height=32, corner_radius=16,
                      fg_color="#1a1400", hover_color=couleur_reseau, text_color=couleur_reseau,
                      font=("Futura", 10),
                      command=lambda: threading.Thread(target=self._send_daily_report, daemon=True).start()
                      ).pack(side="left")

        ctk.CTkLabel(cfg, text="Utilisateurs du réseau :",
                     font=("Futura", 10, "bold"), text_color="#887700").pack(anchor="w", padx=12, pady=(10, 4))
        users_info = database.get_all_users_info()
        for uname, info in users_info.items():
            lvl = info["permissions"][0] if info["permissions"] else 1
            online = info.get("is_online", False)
            role = info.get("role", "")
            dot_col = "#44ff88" if online else "#333322"
            row = ctk.CTkFrame(cfg, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(row, text="●" if online else "○", font=("Arial", 11),
                         text_color=dot_col, width=14).pack(side="left")
            lbl = f"{uname}  [{role}]  (Niv.{lvl})" if role else f"{uname}  (Niv.{lvl})"
            ctk.CTkLabel(row, text=lbl, font=("Segoe UI", 10), text_color="#888833").pack(side="left", padx=6)

        self.reseau_frame.bind("<Button-1>", self.click_window)

    def _build_permissions_frame(self):
        self.permissions_frame = ctk.CTkFrame(self.root, fg_color="#080810",
                                               border_color=couleur_permissions, border_width=2, corner_radius=30)
        hdr = ctk.CTkFrame(self.permissions_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(18, 8))
        ctk.CTkLabel(hdr, text="⚡  PERMISSIONS",
                     font=("Futura", 16, "bold"), text_color=couleur_permissions).pack(side="left")
        ctk.CTkButton(hdr, text="← HUB", width=60, height=28,
                      fg_color="transparent", hover_color="#1a0000",
                      text_color=couleur_permissions, font=("Futura", 9),
                      command=self.toggle_hub).pack(side="right")
        tk.Frame(self.permissions_frame, bg="#1a0000", height=1).pack(fill="x", padx=20, pady=(0, 8))

        # Bouton Magique — Tout Accepter
        ctk.CTkButton(self.permissions_frame, text="⚡  TOUT ACCEPTER",
                      fg_color="#330015", hover_color=couleur_permissions,
                      text_color=couleur_permissions,
                      width=380, height=48, corner_radius=24,
                      font=("Futura", 14, "bold"),
                      command=self._accept_all_permissions).pack(padx=20, pady=8)
        tk.Frame(self.permissions_frame, bg="#1a0000", height=1).pack(fill="x", padx=20, pady=4)

        self.permissions_scroll = ctk.CTkScrollableFrame(
            self.permissions_frame, fg_color="transparent",
            scrollbar_button_color="#1a0010",
            scrollbar_button_hover_color=couleur_permissions)
        self.permissions_scroll.pack(fill="both", expand=True, padx=8, pady=4)

        footer = ctk.CTkFrame(self.permissions_frame, fg_color="#080810", height=40)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        ctk.CTkButton(footer, text="↻ Actualiser", width=100, height=28,
                      fg_color="transparent", hover_color="#1a0010",
                      text_color="#553333", font=("Futura", 9),
                      command=self._refresh_permissions).pack(side="right", padx=12, pady=6)
        self.permissions_frame.bind("<Button-1>", self.click_window)

