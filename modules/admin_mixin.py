"""Panneau admin."""
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


class AdminMixin:
    def _build_admin_user_buttons(self):
        for w in self.admin_user_btns_frame.winfo_children():
            w.destroy()
        users_info = database.get_all_users_info()
        for username, info in users_info.items():
            level = info["permissions"][0] if info["permissions"] else 1
            is_online = info.get("is_online", False)
            role = info.get("role", "")
            dot_color = "#44ff88" if is_online else "#333344"
            lvl_colors = {0: couleur_gold, 1: couleur_accent, 2: "#ff6b35"}
            btn_color = lvl_colors.get(level, couleur_accent)
            row = ctk.CTkFrame(self.admin_user_btns_frame, fg_color="#0d0d0d",
                               corner_radius=12, border_width=1, border_color=btn_color)
            row.pack(fill="x", pady=3)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=5)
            ctk.CTkLabel(inner, text="●", font=("Arial", 10),
                         text_color=dot_color, width=14).pack(side="left")
            lbl_name = f"{username}  [{role}]" if role else username
            ctk.CTkLabel(inner, text=lbl_name, font=("Futura", 11, "bold"),
                         text_color=btn_color).pack(side="left", padx=6)
            lvl_labels = {0: "PÈRE", 1: "FILS", 2: "SUPER"}
            ctk.CTkLabel(inner, text=lvl_labels.get(level, "?"),
                         font=("Futura", 8), text_color="#555544").pack(side="left")
            ctk.CTkButton(inner, text="Hub ↗", width=60, height=24,
                          fg_color="#1a1a00", hover_color=btn_color,
                          text_color=btn_color,
                          corner_radius=12, font=("Futura", 9),
                          command=lambda u=username: self.refresh_admin_logs(u)).pack(side="right")
            ctk.CTkButton(inner, text="Niv.", width=44, height=24,
                          fg_color="#111100", hover_color="#333300",
                          text_color="#888800", corner_radius=12, font=("Futura", 8),
                          command=lambda u=username, l=level: self._admin_toggle_level(u, l)).pack(side="right", padx=4)

    def _admin_toggle_level(self, username, current_level):
        new_level = (current_level + 1) % 3
        database.update_user_permission(username, new_level)
        self._build_admin_user_buttons()
        self._show_toast(f"Niveau de {username} → {new_level}")

    def _admin_create_user(self):
        popup = ctk.CTkToplevel(self.root)
        popup.title("Créer utilisateur")
        popup.geometry("320x360")
        popup.configure(fg_color="#0a0a0a")
        popup.attributes("-topmost", True)
        popup.grab_set()
        ctk.CTkLabel(popup, text="NOUVEAU COMPTE", font=("Futura", 14, "bold"),
                     text_color=couleur_gold).pack(pady=20)
        e_user = ctk.CTkEntry(popup, placeholder_text="Identifiant", width=260, height=40, corner_radius=20)
        e_user.pack(pady=6)
        e_pass = ctk.CTkEntry(popup, placeholder_text="Mot de passe", width=260, height=40, corner_radius=20, show="●")
        e_pass.pack(pady=6)
        roles = ["", "Finance", "RH", "Manager", "Technique", "Commercial", "Direction", "Support"]
        role_var = tk.StringVar(value="")
        ctk.CTkOptionMenu(popup, values=roles, variable=role_var, width=260,
                          fg_color="#111122", button_color="#002244").pack(pady=6)
        lbl_s = ctk.CTkLabel(popup, text="", font=("Segoe UI", 10), text_color="#ff4444")
        lbl_s.pack()
        def do_create():
            u = e_user.get().strip(); p = e_pass.get().strip(); r = role_var.get().strip()
            if not u or not p:
                lbl_s.configure(text="Champs vides"); return
            if database.create_user(u, p, permissions=[1], role=r):
                popup.destroy(); self._build_admin_user_buttons()
                self._show_toast(f"Compte '{u}' créé.")
            else:
                lbl_s.configure(text="Déjà existant")
        ctk.CTkButton(popup, text="CRÉER", fg_color="#1a1a00", hover_color=couleur_gold,
                      text_color=couleur_gold, width=260, height=42, corner_radius=21,
                      font=("Futura", 12, "bold"), command=do_create).pack(pady=16)

    def refresh_admin_logs(self, selected_user):
        self.admin_log_area.delete("1.0", "end")
        self.admin_log_area.insert("end", f"══ HUB DE {selected_user.upper()} ══\n\n")
        # Mémoire chat
        target_file = f"memory_{selected_user}.json"
        if os.path.exists(target_file):
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.admin_log_area.insert("end", "── HISTORIQUE CHAT ──\n")
                for msg in data[-10:]:
                    role = msg.get('role', '???').upper()
                    txt  = msg.get('content', '')[:120]
                    self.admin_log_area.insert("end", f"[{role}] : {txt}\n\n")
            except Exception:
                self.admin_log_area.insert("end", "Erreur lecture mémoire.\n")
        else:
            self.admin_log_area.insert("end", f"Pas d'historique pour {selected_user}.\n")
        # Agenda
        agenda_file = f"agenda_{selected_user}.json"
        if os.path.exists(agenda_file):
            try:
                with open(agenda_file, "r", encoding="utf-8") as f:
                    agenda_data = json.load(f)
                self.admin_log_area.insert("end", f"\n── AGENDA ({selected_user}) ──\n")
                today_str = date.today().strftime("%Y-%m-%d")
                upcoming = [ev for ev in agenda_data if ev.get("date","") >= today_str]
                for ev in sorted(upcoming, key=lambda e: e.get("date",""))[:10]:
                    self.admin_log_area.insert("end",
                        f"📅 {ev.get('date','')} {ev.get('time','')} — {ev.get('title','?')}\n")
                if not upcoming:
                    self.admin_log_area.insert("end", "Aucun événement à venir.\n")
            except:
                pass
        # Expérience log
        log = database.load_experience_log()
        user_log = [e for e in log if e.get("username") == selected_user][-10:]
        if user_log:
            self.admin_log_area.insert("end", f"\n── ACTIONS RÉCENTES ──\n")
            for e in user_log:
                self.admin_log_area.insert("end",
                    f"[{e.get('action_type','?')}] {e.get('details',''[:60])}\n")

    def _build_daily_report_body(self, entries, today_str):
        """Construit un compte rendu COMPACT de la journee.

        Quelques lignes qui resument les actions, plutot qu'une liste
        exhaustive. Le detail reste consultable dans l'UI / la base
        d'experience. Format :

            COMPTE RENDU — 24/04/2026
            📥 15 mails analyses
            ✉  3 mails envoyes
            🗑 7 mails supprimes
            📅 2 RDV ajoutes
            ⚡ 4 permissions traitees

            Faits marquants :
            • ...
            • ...
        """
        # Compteurs par grande categorie
        counters = {
            "mails_recus":     0,
            "mails_envoyes":   0,
            "mails_supprimes": 0,
            "mails_archives":  0,
            "rdv":             0,
            "permissions":     0,
            "refus_rdv":       0,
            "forward_fils":    0,
        }
        action_map = {
            "mail_classified":       "mails_recus",
            "mail_received":         "mails_recus",
            "mail_sent":             "mails_envoyes",
            "mail_deleted":          "mails_supprimes",
            "mail_archived":         "mails_archives",
            "mail_categorized":      "mails_recus",
            "mail_unsubscribed":     "mails_supprimes",
            "mail_rdv_refus":        "refus_rdv",
            "mail_forward_son":      "forward_fils",
            "mail_forward_sent":     "forward_fils",
            "agenda_added":          "rdv",
            "agenda_modified":       "rdv",
            "agenda_deleted":        "rdv",
            "permission_granted":    "permissions",
            "permission_denied":     "permissions",
        }

        # Faits marquants : on garde les actions "notables"
        notable_types = {
            "mail_rdv_refus", "mail_forward_son", "agenda_added",
            "permission_granted", "permission_denied",
        }
        notable = []

        for e in entries:
            at = e.get("action_type", "")
            cat = action_map.get(at)
            if cat and cat in counters:
                counters[cat] += 1
            if at in notable_types and len(notable) < 5:
                ts = e.get("timestamp", "")
                try:
                    hh = datetime.fromisoformat(ts).strftime("%H:%M")
                except Exception:
                    hh = "--:--"
                detail = (e.get("details") or "").strip()
                if len(detail) > 90:
                    detail = detail[:87] + "..."
                notable.append(f"  • {hh} — {detail}")

        date_hum = datetime.strptime(today_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        lines = []
        lines.append(f"COMPTE RENDU — {date_hum}")
        lines.append("")

        labels = [
            ("mails_recus",     "📥 {n} mail(s) analyse(s)"),
            ("mails_envoyes",   "✉  {n} mail(s) envoye(s)"),
            ("mails_supprimes", "🗑 {n} mail(s) supprime(s)"),
            ("mails_archives",  "📦 {n} mail(s) archive(s)"),
            ("forward_fils",    "📨 {n} mail(s) transmis aux fils"),
            ("refus_rdv",       "⛔ {n} RDV refuse(s) (creneau occupe)"),
            ("rdv",             "📅 {n} RDV ajoute(s) a l'agenda"),
            ("permissions",     "⚡ {n} permission(s) traitee(s)"),
        ]
        total = 0
        for key, fmt in labels:
            n = counters.get(key, 0)
            if n > 0:
                lines.append(fmt.format(n=n))
                total += n

        if total == 0:
            lines.append("Aucune action automatique aujourd'hui.")
        else:
            if notable:
                lines.append("")
                lines.append("Faits marquants :")
                lines.extend(notable)

        lines.append("")
        lines.append(f"— Assistant de {USERNAME}")
        return "\n".join(lines)

    def _send_daily_report(self):
        if not self.user_email or not self.user_app_pass:
            return
        log = database.load_experience_log()
        today_str = date.today().strftime("%Y-%m-%d")
        if USER_LEVEL == 0:
            entries = [e for e in log if e.get("timestamp","").startswith(today_str)]
        else:
            entries = [e for e in log if e.get("timestamp","").startswith(today_str)
                       and e.get("username") == USERNAME]

        body = self._build_daily_report_body(entries, today_str)

        try:
            msg = MIMEMultipart()
            msg['From'] = self.user_email
            msg['To'] = self.user_email
            msg['Subject'] = f"Compte rendu journalier — {datetime.now().strftime('%d/%m/%Y')}"
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            # Multi-provider : on passe par le helper d'EmailMixin si dispo
            try:
                _, smtp_host, smtp_port, _ = self._get_email_servers()
            except Exception:
                smtp_host, smtp_port = "smtp.gmail.com", 587
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(self.user_email, self.user_app_pass)
            server.send_message(msg)
            server.quit()
            try:
                database.add_experience_entry(
                    USERNAME, "report_sent",
                    f"Compte rendu journalier envoye a {self.user_email}",
                    "success", 1.0)
            except Exception:
                pass
        except Exception as e:
            print(f"Erreur rapport : {e}")
