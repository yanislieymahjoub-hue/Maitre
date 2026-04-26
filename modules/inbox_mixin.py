"""Affichage/filtrage de la boîte de réception."""
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


class InboxMixin:
    def refresh_inbox(self):
        """Lance la récupération des mails dans un thread séparé."""
        self._inbox_mails = []
        self._inbox_active_filter = "TOUT"
        # Réinitialiser les boutons filtre
        for lbl, (btn, col) in self._filter_buttons.items():
            btn.configure(fg_color="#1a1a35" if lbl == "TOUT" else "transparent")

        for widget in self.inbox_scroll.winfo_children():
            widget.destroy()

        loader_frame = ctk.CTkFrame(self.inbox_scroll, fg_color="transparent")
        loader_frame.pack(fill="x", pady=30)
        ctk.CTkLabel(loader_frame, text="⬡", font=("Arial", 28), text_color=couleur_accent).pack()
        ctk.CTkLabel(loader_frame, text="Connexion au serveur neural...",
                     font=("Segoe UI", 12), text_color="#333").pack(pady=8)

        threading.Thread(target=self._fetch_emails_logic, daemon=True).start()

    def _apply_inbox_filter(self, filter_label):
        """Active un filtre et redessine les cartes."""
        self._inbox_active_filter = filter_label
        # On revient a la pagination initiale a chaque changement d'onglet
        self._inbox_render_limit = 80
        # Highlight du bouton actif
        for lbl, (btn, col) in self._filter_buttons.items():
            btn.configure(fg_color="#1a1a35" if lbl == filter_label else "transparent")
        self._render_inbox_cards()

    def _render_inbox_cards(self):
        """(Re)dessine les cartes selon le filtre actif, triées par urgence."""
        for w in self.inbox_scroll.winfo_children():
            w.destroy()

        urgency_order = {"haute": 0, "normale": 1, "faible": 2}
        filter_map = {"TOUT": None, "URGENT": "haute", "NORMAL": "normale", "INFO": "faible"}
        target = filter_map.get(self._inbox_active_filter)

        mails = list(self._inbox_mails)
        # E. Dedup (meme sender+subject) - UI only
        try:
            mails = self.dedupe_mails_for_render(mails)
        except Exception:
            pass
        # I. Focus mode - filtre additionnel
        try:
            mails = self.apply_focus_filter(mails)
        except Exception:
            pass
        # Tri : urgents d'abord, puis normaux, puis faibles
        mails_sorted = sorted(mails, key=lambda m: urgency_order.get(m.get("urgency", "normale"), 1))

        # Feedback visuel du bouton focus
        try:
            if getattr(self, "focus_btn", None):
                active = getattr(self, "_focus_mode", False)
                self.focus_btn.configure(
                    text_color="#22b9ff" if active else "#999",
                    fg_color="#003a55" if active else "#1a1a35")
        except Exception:
            pass

        # --- Virtualisation legere : on plafonne le nombre de cartes ---
        # Au-dela, on n'instancie pas les widgets (tkinter rame avec >200
        # cartes). L'utilisateur peut etendre via le bouton "Afficher plus".
        BATCH = getattr(self, "_inbox_render_limit", 80)
        eligible = [m for m in mails_sorted
                    if target is None or m.get("urgency") == target]
        total_eligible = len(eligible)
        to_render = eligible[:BATCH]
        leftover = total_eligible - len(to_render)

        shown = 0
        for m in to_render:
            self._add_mail_card(
                m["sender"], m["subject"], m["summary"],
                m["urgency"], m["action"], m["date_str"],
                m.get("attach_summaries"), m.get("warning"),
                m.get("mail_type", "inconnu"), m.get("agenda_suggestion"),
                m.get("uid"), m.get("reply_info"),
                m.get("dupe_count", 0),
            )
            shown += 1

        if leftover > 0:
            try:
                def _expand():
                    # On augmente la limite par paliers de 80
                    self._inbox_render_limit = BATCH + 80
                    self._render_inbox_cards()
                ctk.CTkButton(
                    self.inbox_scroll,
                    text=f"+ Afficher {min(leftover, 80)} mail(s) de plus  "
                         f"(reste {leftover})",
                    fg_color="#16162a", hover_color="#1f1f3a",
                    text_color="#88d8ff", height=30,
                    command=_expand,
                ).pack(fill="x", padx=10, pady=8)
            except Exception:
                pass

        if shown == 0:
            ctk.CTkLabel(self.inbox_scroll,
                text="Aucun mail dans cette catégorie.",
                font=("Segoe UI", 11), text_color="#333").pack(pady=30)

    def _show_inbox_empty(self):
        for w in self.inbox_scroll.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.inbox_scroll, text="Aucun mail trouvé.",
                     font=("Segoe UI", 12), text_color="#333").pack(pady=30)

    def _show_inbox_error(self, err):
        for w in self.inbox_scroll.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.inbox_scroll, text=f"⚠ Erreur : {err[:80]}",
                     font=("Segoe UI", 11), text_color="#ff4444", wraplength=380).pack(pady=20)

    def _add_mail_card(self, sender, subject, summary, urgency="normale", action="", date_str="", attach_summaries=None, warning=None, mail_type="inconnu", agenda_suggestion=None, uid=None, reply_info=None, dupe_count=0):
        urgency_colors = {
            "haute":   "#ff4060",
            "normale": "#22b9ff",
            "faible":  "#5a5a8a",
        }
        type_icons = {
            "cv": "📄", "facture": "💳", "relance": "📣", "phishing": "☠",
            "arnaque": "☠", "spam": "🗑", "newsletter": "📰", "invitation": "🎟",
            "contractuel": "📋", "juridique": "⚖", "support": "🛠",
            "professionnel": "💼", "personnel": "💬", "inconnu": "❓",
            "self": "🪞",  # mail envoye par l'Assistant (rapport, notif)
        }
        urg_color = urgency_colors.get(urgency, "#22b9ff")
        type_icon = type_icons.get(mail_type, "✉")

        # Bordure colorée selon contexte
        card_border = "#ff2244" if warning else ("#2a2a45" if urgency != "haute" else "#4a2030")

        # === CARD CONTAINER ===
        card = ctk.CTkFrame(self.inbox_scroll, fg_color="#0c0c1c",
                            corner_radius=18, border_width=1,
                            border_color=card_border)
        card.pack(padx=10, pady=7, fill="x")

        # Barre latérale colorée selon urgence / warning
        bar_color = "#ff2244" if warning else urg_color
        accent_bar = tk.Frame(card, bg=bar_color, width=4)
        accent_bar.pack(side="left", fill="y", padx=(0, 0))

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True, padx=14, pady=12)

        # === LIGNE HAUTE : TYPE BADGE + SENDER + DATE ===
        top_row = ctk.CTkFrame(content, fg_color="transparent")
        top_row.pack(fill="x")

        # Badge type
        type_badge = ctk.CTkLabel(top_row,
            text=f"{type_icon} {mail_type.upper()}",
            font=("Futura", 7, "bold"),
            text_color=urg_color,
            fg_color="#0f0f22",
            corner_radius=6,
            padx=6, pady=2)
        type_badge.pack(side="left")

        sender_clean = re.sub(r'<.*?>', '', sender).strip()[:32]
        ctk.CTkLabel(top_row,
            text=f"  {sender_clean}",
            font=("Segoe UI", 9), text_color="#3a3a5c").pack(side="left")

        # Date formatée
        if date_str:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str)
                date_nice = dt.strftime("%d/%m %H:%M")
            except:
                date_nice = date_str[:10]
            ctk.CTkLabel(top_row,
                text=date_nice,
                font=("Consolas", 8), text_color="#2a2a44").pack(side="right")

        if attach_summaries:
            ctk.CTkLabel(top_row,
                text=f"  📎{len(attach_summaries)}",
                font=("Segoe UI", 9, "bold"), text_color="#ffb366").pack(side="right")

        # Badge duplicates (dedup UI)
        if dupe_count and dupe_count > 0:
            ctk.CTkLabel(top_row,
                text=f"  +{dupe_count}",
                font=("Futura", 8, "bold"),
                text_color="#ff88aa",
                fg_color="#3a0020",
                corner_radius=5,
                padx=5, pady=1).pack(side="right", padx=(4, 0))

        # Mark-as-opened sur clic du body
        def _track_click(_e=None, u=uid, s=sender):
            try:
                self.mark_mail_opened(u, s)
            except Exception:
                pass
        try:
            card.bind("<Button-1>", _track_click)
            content.bind("<Button-1>", _track_click)
        except Exception:
            pass

        # === WARNING BLOCK (arnaque/phishing) ===
        if warning:
            warn_frame = ctk.CTkFrame(content, fg_color="#1a0010", corner_radius=8, border_width=1, border_color="#ff2244")
            warn_frame.pack(fill="x", pady=(8, 0))
            ctk.CTkLabel(warn_frame,
                text=f"⚠  {warning}",
                font=("Futura", 9, "bold"),
                text_color="#ff4060",
                wraplength=370, justify="left").pack(padx=12, pady=8, anchor="w")

        # === SUJET ===
        tk.Frame(content, bg="#1c1c35", height=1).pack(fill="x", pady=(8, 5))
        ctk.CTkLabel(content,
            text=subject[:65],
            font=("Futura", 11, "bold"),
            text_color="#dde4ff", anchor="w").pack(fill="x")

        # === RÉSUMÉ ===
        ctk.CTkLabel(content,
            text=summary,
            font=("Segoe UI", 10),
            text_color="#7a7aaa",
            wraplength=360, anchor="w", justify="left").pack(fill="x", pady=(3, 0))

        # === BADGE "REPONSE AU RDV" (si applicable) ===
        if reply_info:
            reply_frame = ctk.CTkFrame(
                content, fg_color="#0a2a1a", corner_radius=8,
                border_width=1, border_color="#22b966")
            reply_frame.pack(fill="x", pady=(8, 0))
            ctk.CTkLabel(reply_frame,
                text=f"✓  {reply_info}",
                font=("Futura", 9, "bold"),
                text_color="#44ff88",
                wraplength=360, justify="left").pack(padx=10, pady=6, anchor="w")

        # === PIÈCES JOINTES ===
        if attach_summaries:
            tk.Frame(content, bg="#1c1c35", height=1).pack(fill="x", pady=(8, 4))
            for att in attach_summaries:
                att_row = ctk.CTkFrame(content, fg_color="#0d0d25", corner_radius=8)
                att_row.pack(fill="x", pady=2)
                ctk.CTkLabel(att_row,
                    text=f"📎 {att.get('nom', 'Fichier')[:42]}",
                    font=("Consolas", 9, "bold"), text_color="#ffb366").pack(anchor="w", padx=10, pady=2)
                if att.get("resume"):
                    ctk.CTkLabel(att_row,
                        text=att["resume"],
                        font=("Segoe UI", 9), text_color="#555577",
                        wraplength=340, justify="left").pack(anchor="w", padx=10, pady=(0, 5))

        # === CONSEILS ===
        if action:
            action_frame = ctk.CTkFrame(content, fg_color="#0d0d20", corner_radius=10)
            action_frame.pack(fill="x", pady=(10, 0))

            if isinstance(action, list):
                points = []
                for pt in action:
                    clean = str(pt).strip().replace("→","").replace("->","").replace("*","").rstrip(".,")
                    if clean: points.append(f"› {clean}")
                formatted = "\n".join(points)
            else:
                formatted = f"› {str(action).replace('→','').replace('->','')}"

            ctk.CTkLabel(action_frame,
                text=formatted,
                font=("Segoe UI", 9, "bold"),
                text_color="#22b9ff",
                anchor="w", justify="left", wraplength=355).pack(padx=14, pady=10, fill="x")

        # === BOUTON AGENDA (si suggestion) ===
        if agenda_suggestion and not warning:
            def add_from_inbox(sug=agenda_suggestion):
                title = sug.get("title", "Événement mail")
                note = sug.get("note", "")
                # Nouveau format : date (YYYY-MM-DD) et time (HH:MM) séparés.
                # On gère l'ancien format date_hint pour compatibilité.
                date_target = sug.get("date") or ""
                time_target = sug.get("time") or ""
                if not date_target:
                    import re as _re
                    hint = sug.get("date_hint", "")
                    date_target = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                    try:
                        found = _re.search(r"(\d{4}-\d{2}-\d{2})", hint)
                        if found: date_target = found.group(1)
                    except: pass
                # Heure manquante : on tente un creneau libre via l'IA
                if not time_target and hasattr(self, "_pick_free_slot_for_date"):
                    try:
                        picked = self._pick_free_slot_for_date(date_target)
                        if picked: time_target = picked
                    except Exception:
                        pass
                self.add_agenda_event(title, date_target, time_target,
                                      f"Ajoute depuis inbox. {note}")
                msg = f"Ajoute : {title[:30]} ({date_target} {time_target})".rstrip()
                self.root.after(0, lambda m=msg: self._show_toast(m))

            btn_row = ctk.CTkFrame(content, fg_color="transparent")
            btn_row.pack(fill="x", pady=(8, 0))
            ctk.CTkButton(btn_row,
                text=f"📅  Ajouter à l'agenda — {agenda_suggestion.get('title', '')[:30]}",
                font=("Futura", 9, "bold"),
                height=30, corner_radius=15,
                fg_color="#1a0f35", hover_color="#2d1f60",
                text_color=couleur_agenda, border_width=1, border_color=couleur_agenda,
                command=add_from_inbox).pack(side="left")

        # Boutons Archive / Supprimer (en bas de chaque carte)
        action_row = ctk.CTkFrame(content, fg_color="transparent")
        action_row.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(action_row, text="🗄 Archiver", width=90, height=26,
                      fg_color="transparent", hover_color="#0a2a1a",
                      text_color="#224422", corner_radius=13, font=("Futura", 8),
                      command=lambda: (card.destroy(),
                          database.add_experience_entry(USERNAME, "mail_classified",
                          f"Archive: {subject}", "success", 1.0))).pack(side="right", padx=(4, 0))
        def _do_delete_mail():
            # Supprime l'entree de la liste en memoire
            try:
                self._inbox_mails = [mm for mm in self._inbox_mails
                                     if mm.get("uid") != uid]
            except Exception:
                pass
            # Detruit la carte immediatement
            try:
                card.destroy()
            except Exception:
                pass
            # Suppression serveur en arriere-plan (IMAP)
            if uid:
                threading.Thread(
                    target=self.delete_mail_on_server,
                    args=(uid, subject),
                    daemon=True).start()
            else:
                # Pas d'UID : au moins logger le geste
                try:
                    database.add_experience_entry(
                        USERNAME, "mail_classified",
                        f"Supprime (local) : {subject}", "success", 1.0)
                except Exception:
                    pass

        ctk.CTkButton(action_row, text="🗑 Supprimer", width=95, height=26,
                      fg_color="transparent", hover_color="#2a0a0a",
                      text_color="#553333", corner_radius=13, font=("Futura", 8),
                      command=_do_delete_mail).pack(side="right")
