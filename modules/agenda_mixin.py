"""Méthodes agenda/calendrier pour AssistantApp."""
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


class AgendaMixin:
    def load_agenda(self):
        if os.path.exists(AGENDA_FILE):
            try:
                with open(AGENDA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return []
        return []

    def save_agenda(self):
        # Snapshot + ecriture en arriere-plan pour ne pas bloquer l'UI
        # quand on ajoute / supprime un evenement.
        snapshot = list(self.agenda_events)
        def _write():
            try:
                with open(AGENDA_FILE, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"Erreur sauvegarde agenda : {e}")
        threading.Thread(target=_write, daemon=True).start()

    def _start_notification_thread(self):
        """Lance un thread de surveillance des rappels agenda + rapport journalier."""
        self._notif_sent = set()
        self._last_report_date = None
        self.daily_report_hour = 18
        self._last_auto_fetch = 0  # timestamp du dernier auto-fetch des mails

        def _checker():
            while True:
                time.sleep(60)
                now = datetime.now()
                today = date.today()

                # ============ AUTO-FETCH DES MAILS ============
                # Declenche un fetch en arriere-plan toutes les 5 minutes,
                # meme si l'inbox n'est pas ouverte. Le triage auto s'execute
                # ensuite automatiquement a la fin du fetch.
                try:
                    if (time.time() - self._last_auto_fetch) >= 300:
                        if (getattr(self, "user_email", None)
                                and getattr(self, "user_app_pass", None)
                                and hasattr(self, "_fetch_emails_logic")):
                            self._last_auto_fetch = time.time()
                            threading.Thread(
                                target=self._fetch_emails_logic,
                                daemon=True).start()
                except Exception as _e:
                    print(f"Erreur auto-fetch mail : {_e}")
                # Alerts agenda
                for ev in self.agenda_events:
                    try:
                        ev_date = ev.get("date", "")
                        ev_time = ev.get("time", "")
                        if not ev_date or not ev_time:
                            continue
                        if ev_date != today.strftime("%Y-%m-%d"):
                            continue
                        h, m = map(int, ev_time.split(":"))
                        ev_dt = datetime.combine(today, datetime.min.time().replace(hour=h, minute=m))
                        diff = (ev_dt - now).total_seconds()
                        eid = ev.get("id")
                        if 0 < diff <= 900 and eid not in self._notif_sent:
                            self._notif_sent.add(eid)
                            title = ev.get("title", "Événement")
                            self.root.after(0, lambda t=title: self._show_toast(f"📅 Dans 15 min : {t}"))
                    except:
                        pass
                # Rapport journalier automatique
                if now.hour == self.daily_report_hour and now.minute < 2:
                    if self._last_report_date != today:
                        self._last_report_date = today
                        threading.Thread(target=self._send_daily_report, daemon=True).start()
                # Resume matinal inbox (H. morning digest, OPT-IN, par mail)
                try:
                    digest_hour = int(
                        getattr(self, "optim_settings", {}).get("digest_hour", 8))
                    digest_on = getattr(self, "optim_settings", {}).get(
                        "morning_digest", False)
                    if (digest_on and now.hour == digest_hour
                            and now.minute < 2
                            and getattr(self, "_last_digest_date", None) != today):
                        self._last_digest_date = today
                        threading.Thread(target=self._send_morning_digest,
                                         daemon=True).start()
                except Exception:
                    pass

                # Briefing matinal complet (DEFAULT ON, dans le chat)
                # Independant du digest mail. Idempotent (1x/jour).
                try:
                    brief_hour = int(getattr(self, "briefing_hour", 8))
                    if (now.hour == brief_hour and now.minute < 2
                            and hasattr(self, "show_morning_briefing")
                            and getattr(self, "_briefing_shown_date", None) != today.isoformat()):
                        # Le show_morning_briefing gere lui-meme le flag _briefing_shown_date.
                        self.show_morning_briefing(force=False, open_chat=True)
                except Exception as _e:
                    print(f"Erreur briefing scheduler : {_e}")

        threading.Thread(target=_checker, daemon=True).start()

    def _process_scheduled_mail(self, text):
        """Détecte et enregistre un mail programmé dans l'agenda."""
        if "SCHEDULED_MAIL_START" not in text or "SCHEDULED_MAIL_END" not in text:
            return
        try:
            block_m = re.search(r"SCHEDULED_MAIL_START(.*?)SCHEDULED_MAIL_END", text, re.DOTALL)
            block = block_m.group(1) if block_m else text

            to_m    = re.search(r"TO\s*:\s*([\w\.-]+@[\w\.-]+\.\w+)", block)
            sub_m   = re.search(r"SUBJECT\s*:\s*(.+)", block)
            date_m  = re.search(r"SEND_DATE\s*:\s*(\d{4}-\d{2}-\d{2})", block)
            time_m  = re.search(r"SEND_TIME\s*:\s*(\d{1,2}[:hH]\d{2})", block)
            body_m  = re.search(r"BODY\s*:(.*?)$", block, re.DOTALL)

            dest    = to_m.group(1).strip()   if to_m   else None
            sujet   = sub_m.group(1).strip().strip('"').strip("'") if sub_m else "Mail programmé"
            date_s  = date_m.group(1).strip() if date_m else date.today().strftime("%Y-%m-%d")

            time_s = "09:00"
            if time_m:
                time_norm = time_m.group(1).replace("h", ":").replace("H", ":")
                parts = time_norm.split(":")
                if len(parts) == 2:
                    time_s = f"{int(parts[0]):02d}:{parts[1][:2]}"

            corps   = body_m.group(1).strip() if body_m else ""

            if dest:
                note = f"MAIL_PROG|TO:{dest}|SUB:{sujet}|BODY:{corps[:200]}"
                self.add_agenda_event(f"Mail programmé : {sujet[:30]}", date_s, time_s, note)
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM", f"📨 Mail programmé pour le {date_s} à {time_s} → {dest}"))
        except Exception as e:
            print(f"Erreur mail programmé : {e}")

    def add_agenda_event(self, title, date_str, time_str="", note="", source_mail_uid=None):
        """Ajoute un événement et rafraîchit l'affichage si l'agenda est ouvert.

        Si source_mail_uid est fourni, il est stocke sur l'evenement pour
        permettre l'auto-suppression du mail IMAP une fois le RDV passe.
        """
        event = {
            "id": int(time.time() * 1000),
            "title": title,
            "date": date_str,
            "time": time_str,
            "note": note
        }
        if source_mail_uid:
            event["source_mail_uid"] = str(source_mail_uid)
        self.agenda_events.append(event)
        self.save_agenda()
        if self.mode == "agenda":
            self.root.after(0, self._render_agenda_calendar)
        return event

    def add_agenda_event_with_reminder(self, title, date_str, time_str="", note="", source_mail_uid=None):
        """Ajoute l'evenement principal ET un rappel informel.

        Regle du rappel :
        - Si l'evenement est dans plus de 24h : rappel la veille a 18:00.
        - Si l'evenement est dans moins de 24h mais > 2h : rappel 2h avant.
        - Sinon : pas de rappel (inutile, on y est deja).
        """
        main = self.add_agenda_event(title, date_str, time_str, note, source_mail_uid=source_mail_uid)
        try:
            if not date_str:
                return main
            # On essaie de calculer le moment du rappel
            ev_time = time_str if time_str else "09:00"
            try:
                ev_dt = datetime.strptime(f"{date_str} {ev_time}", "%Y-%m-%d %H:%M")
            except Exception:
                ev_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=9)

            now = datetime.now()
            delta_hours = (ev_dt - now).total_seconds() / 3600.0
            if delta_hours > 24:
                # Rappel la veille a 18:00
                rappel_dt = ev_dt - timedelta(days=1)
                rappel_dt = rappel_dt.replace(hour=18, minute=0)
            elif delta_hours > 2:
                # Rappel 2h avant
                rappel_dt = ev_dt - timedelta(hours=2)
            else:
                return main

            # Petite pause pour eviter qu'id du rappel == id principal
            time.sleep(0.002)
            self.add_agenda_event(
                f"Rappel : {title[:50]}",
                rappel_dt.strftime("%Y-%m-%d"),
                rappel_dt.strftime("%H:%M"),
                f"Rappel informel avant l'evenement du {date_str} {ev_time}"
            )
        except Exception as e:
            print(f"Erreur ajout rappel : {e}")
        return main

    def delete_agenda_event(self, event_id):
        self.agenda_events = [e for e in self.agenda_events if e.get("id") != event_id]
        self.save_agenda()
        self._render_agenda_calendar()
        if hasattr(self, "_agenda_selected_date"):
            self._agenda_select_day(self._agenda_selected_date)

    def get_events_for_date(self, d):
        date_str = d.strftime("%Y-%m-%d")
        return [e for e in self.agenda_events if e.get("date") == date_str]

    def _agenda_prev_month(self):
        d = self.agenda_current_month
        if d.month == 1:
            self.agenda_current_month = d.replace(year=d.year - 1, month=12)
        else:
            self.agenda_current_month = d.replace(month=d.month - 1)
        self._render_agenda_calendar()

    def _agenda_next_month(self):
        d = self.agenda_current_month
        if d.month == 12:
            self.agenda_current_month = d.replace(year=d.year + 1, month=1)
        else:
            self.agenda_current_month = d.replace(month=d.month + 1)
        self._render_agenda_calendar()

    def _render_agenda_calendar(self):
        # Mettre à jour le label du mois
        mois_fr = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                   "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
        d = self.agenda_current_month
        self.lbl_agenda_month.configure(text=f"{mois_fr[d.month - 1]} {d.year}")

        # Effacer la grille précédente
        for w in self.cal_frame.winfo_children():
            w.destroy()

        # En-têtes jours
        jours = ["LU", "MA", "ME", "JE", "VE", "SA", "DI"]
        for col, j in enumerate(jours):
            ctk.CTkLabel(self.cal_frame, text=j, width=52, height=26,
                         font=("Futura", 9, "bold"),
                         text_color="#4a4a6a").grid(row=0, column=col, padx=2, pady=(0, 6))

        # Remplir le calendrier
        cal = calendar.monthcalendar(d.year, d.month)
        today = date.today()

        for row_i, week in enumerate(cal):
            for col_i, day_num in enumerate(week):
                if day_num == 0:
                    ctk.CTkLabel(self.cal_frame, text="", width=52, height=40).grid(
                        row=row_i + 1, column=col_i, padx=2, pady=2)
                    continue

                day_date = date(d.year, d.month, day_num)
                events = self.get_events_for_date(day_date)
                has_events = len(events) > 0
                n_events = len(events)

                is_today = (day_date == today)
                is_weekend = col_i >= 5

                # Palette selon état
                if is_today:
                    bg = couleur_agenda
                    txt_color = "#0a0010"
                    brd = "#e0c0ff"
                    brd_w = 2
                elif has_events:
                    bg = "#1a0f35"
                    txt_color = "#e0c0ff"
                    brd = "#5a3a9e"
                    brd_w = 1
                elif is_weekend:
                    bg = "#0b0b14"
                    txt_color = "#3a3a5c"
                    brd = "#15152a"
                    brd_w = 1
                else:
                    bg = "#0d0d18"
                    txt_color = "#9a9ac0"
                    brd = "#1a1a2e"
                    brd_w = 1

                cell = ctk.CTkButton(
                    self.cal_frame,
                    text=str(day_num),
                    width=52, height=40,
                    fg_color=bg,
                    hover_color="#2d1f4e",
                    border_color=brd,
                    border_width=brd_w,
                    corner_radius=10,
                    font=("Futura", 11, "bold"),
                    text_color=txt_color,
                    command=lambda dd=day_date: self._agenda_select_day(dd)
                )
                cell.grid(row=row_i + 1, column=col_i, padx=2, pady=2)

                # Points indicateurs d'événement (1, 2 ou 3 points selon nb)
                if has_events and not is_today:
                    n_dots = min(n_events, 3)
                    dot_str = " ".join(["•"] * n_dots)
                    dot = ctk.CTkLabel(self.cal_frame, text=dot_str, width=52, height=8,
                                       font=("Arial", 9, "bold"), text_color=couleur_agenda,
                                       fg_color="transparent")
                    dot.grid(row=row_i + 1, column=col_i, padx=2, pady=(0, 3), sticky="s")

    def _agenda_select_day(self, selected_date):
        """Affiche une timeline horaire du jour sélectionné."""
        for w in self.agenda_detail_frame.winfo_children():
            w.destroy()

        jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        mois_fr  = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun", "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
        label_date = f"{jours_fr[selected_date.weekday()]} {selected_date.day} {mois_fr[selected_date.month - 1]} {selected_date.year}"

        # En-tête du jour
        hdr = ctk.CTkFrame(self.agenda_detail_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(hdr, text=label_date,
                     font=("Futura", 11, "bold"), text_color=couleur_agenda).pack(side="left")
        # Bouton ajout rapide depuis ici
        ctk.CTkButton(hdr, text="+ Ajouter",
                      width=70, height=24,
                      fg_color="#1a0f35", hover_color=couleur_agenda,
                      text_color=couleur_agenda,
                      font=("Futura", 8, "bold"), corner_radius=12,
                      command=self._agenda_quick_add).pack(side="right")

        events = self.get_events_for_date(selected_date)
        # Indexer par heure pour la timeline
        events_by_hour = {}
        no_time_events = []
        for ev in events:
            t = ev.get("time", "").strip()
            if t and ":" in t:
                try:
                    h = int(t.split(":")[0])
                    events_by_hour.setdefault(h, []).append(ev)
                except:
                    no_time_events.append(ev)
            else:
                no_time_events.append(ev)

        # Plage horaire : 07h → 22h
        now = datetime.now()
        is_today = (selected_date == date.today())

        for hour in range(7, 23):
            hour_events = events_by_hour.get(hour, [])
            is_current_hour = is_today and (now.hour == hour)

            # Conteneur de la ligne horaire
            row = ctk.CTkFrame(self.agenda_detail_frame, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=0)

            # Label heure
            h_color = "#c084fc" if is_current_hour else ("#3a3a5c" if not hour_events else "#7a5aaa")
            h_weight = "bold" if is_current_hour or hour_events else "normal"
            ctk.CTkLabel(row,
                text=f"{hour:02d}h",
                font=("Consolas", 9, h_weight),
                text_color=h_color,
                width=36, anchor="e").pack(side="left")

            # Ligne séparateur
            line_color = "#c084fc" if is_current_hour else ("#2a2a3e" if not hour_events else "#3d2a6e")
            lw = 2 if is_current_hour else 1
            tk.Frame(row, bg=line_color, height=lw).pack(side="left", fill="x",
                                                          expand=True if not hour_events else False,
                                                          pady=9, padx=(4, 0),
                                                          ipadx=0)

            # Événements de ce créneau
            if hour_events:
                ev_col = ctk.CTkFrame(row, fg_color="transparent")
                ev_col.pack(side="left", fill="x", expand=True, padx=(4, 0))
                for ev in hour_events:
                    ev_card = ctk.CTkFrame(ev_col,
                        fg_color="#1e1040", corner_radius=8,
                        border_width=1, border_color="#4a2a8e")
                    ev_card.pack(fill="x", pady=2)

                    inner = ctk.CTkFrame(ev_card, fg_color="transparent")
                    inner.pack(fill="x", padx=8, pady=4)

                    t_label = ev.get("time", "")
                    time_lbl = ctk.CTkLabel(inner,
                        text=t_label,
                        font=("Consolas", 9, "bold"),
                        text_color=couleur_agenda,
                        width=36)
                    time_lbl.pack(side="left")

                    title_lbl = ctk.CTkLabel(inner,
                        text=ev.get("title", "?"),
                        font=("Segoe UI", 10, "bold"),
                        text_color="#dde4ff")
                    title_lbl.pack(side="left", padx=(6, 0))

                    note_lbl = None
                    if ev.get("note"):
                        note_txt = ev["note"]
                        if not note_txt.startswith("MAIL_PROG"):
                            note_lbl = ctk.CTkLabel(inner,
                                text=note_txt[:35],
                                font=("Segoe UI", 8),
                                text_color="#555577")
                            note_lbl.pack(side="left", padx=6)

                    eid = ev.get("id")
                    ev_title = ev.get("title", "?")
                    ctk.CTkButton(inner, text="✕",
                        width=22, height=22,
                        fg_color="transparent", hover_color="#330022",
                        text_color="#442244", corner_radius=11,
                        command=lambda e_id=eid, t=ev_title: self._confirm_delete_event(e_id, t)).pack(side="right")

                    # Click sur la carte entiere = demande de suppression
                    def _bind_click(widget, e_id=eid, t=ev_title):
                        widget.bind("<Button-1>",
                            lambda e, i=e_id, tt=t: self._confirm_delete_event(i, tt))
                    for w in (ev_card, inner, time_lbl, title_lbl):
                        _bind_click(w)
                    if note_lbl is not None:
                        _bind_click(note_lbl)

        # Événements sans heure en bas
        if no_time_events:
            ctk.CTkLabel(self.agenda_detail_frame,
                text="Sans heure précise",
                font=("Futura", 8), text_color="#2a2a44").pack(anchor="w", padx=10, pady=(6, 2))
            for ev in no_time_events:
                ev_row = ctk.CTkFrame(self.agenda_detail_frame,
                    fg_color="#111122", corner_radius=8)
                ev_row.pack(fill="x", padx=8, pady=2)
                nt_lbl = ctk.CTkLabel(ev_row,
                    text=ev.get("title", "?"),
                    font=("Segoe UI", 10, "bold"),
                    text_color="#aaaacc")
                nt_lbl.pack(side="left", padx=10, pady=5)
                eid = ev.get("id")
                ev_title = ev.get("title", "?")
                ctk.CTkButton(ev_row, text="✕",
                    width=22, height=22,
                    fg_color="transparent", hover_color="#330000",
                    text_color="#442222", corner_radius=11,
                    command=lambda e_id=eid, t=ev_title: self._confirm_delete_event(e_id, t)).pack(side="right", padx=6, pady=3)
                # Click sur la ligne entiere = demande de suppression
                for w in (ev_row, nt_lbl):
                    w.bind("<Button-1>",
                        lambda e, i=eid, t=ev_title: self._confirm_delete_event(i, t))

        if not events:
            ctk.CTkLabel(self.agenda_detail_frame,
                text="Journée libre  ✦",
                font=("Segoe UI", 10), text_color="#1e1e3a").pack(pady=4)

        self._agenda_selected_date = selected_date

    def _confirm_delete_event(self, event_id, title):
        """Petite popup de confirmation avant suppression d'un evenement."""
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        pw, ph = 320, 140
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - pw) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - ph) // 2
        except Exception:
            x, y = 300, 200
        popup.geometry(f"{pw}x{ph}+{x}+{y}")
        popup.configure(bg="#0a0a18")
        popup.configure(highlightbackground=couleur_agenda, highlightthickness=1)

        tk.Label(popup, text="Supprimer cet evenement ?",
                 font=("Segoe UI", 11, "bold"),
                 bg="#0a0a18", fg=couleur_agenda).pack(pady=(14, 4))
        tk.Label(popup, text=title[:60],
                 font=("Segoe UI", 10),
                 bg="#0a0a18", fg="#dde4ff",
                 wraplength=280).pack(pady=(0, 10))

        btn_row = tk.Frame(popup, bg="#0a0a18")
        btn_row.pack(pady=4)

        def do_delete():
            popup.destroy()
            self.delete_agenda_event(event_id)
            try:
                self._show_toast(f"Evenement supprime : {title[:30]}")
            except Exception:
                pass

        ctk.CTkButton(btn_row, text="Supprimer", width=100, height=28,
                      fg_color="#1a0010", hover_color="#ff4466",
                      text_color="#ff4466",
                      corner_radius=14, font=("Futura", 10, "bold"),
                      command=do_delete).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Annuler", width=90, height=28,
                      fg_color="transparent", hover_color="#1a1a2e",
                      text_color="#7a7aaa",
                      corner_radius=14, font=("Futura", 10),
                      command=popup.destroy).pack(side="left", padx=6)

    def _agenda_quick_add(self):
        """Ajoute rapidement un événement au jour sélectionné ou à aujourd'hui."""
        title = self.agenda_quick_entry.get().strip()
        if not title:
            return
        target_date = getattr(self, "_agenda_selected_date", date.today())
        self.add_agenda_event(title, target_date.strftime("%Y-%m-%d"))
        self.agenda_quick_entry.delete(0, "end")
        self._agenda_select_day(target_date)

    def _process_agenda_command(self, text):
        """Détecte et exécute une commande d'ajout d'événement agenda."""
        if "AGENDA_ADD_START" not in text or "AGENDA_ADD_END" not in text:
            return

        try:
            # On isole d'abord le bloc pour éviter d'attraper TITLE: d'un autre contexte
            block_m = re.search(r"AGENDA_ADD_START(.*?)AGENDA_ADD_END", text, re.DOTALL)
            block = block_m.group(1) if block_m else text

            title_m = re.search(r"TITLE\s*:\s*(.+)", block)
            date_m  = re.search(r"DATE\s*:\s*(\d{4}-\d{2}-\d{2})", block)
            time_m  = re.search(r"TIME\s*:\s*(\d{1,2}[:hH]\d{2})", block)
            note_m  = re.search(r"NOTE\s*:\s*(.+)", block)

            def _clean(s):
                # Enlève guillemets, backticks, espaces en trop
                return s.strip().strip('"').strip("'").strip("`").strip()

            title = _clean(title_m.group(1)) if title_m else "Événement"
            date_str = date_m.group(1).strip() if date_m else date.today().strftime("%Y-%m-%d")

            # Normalisation de l'heure : accepte "14h30" aussi bien que "14:30"
            time_raw = time_m.group(1).strip() if time_m else ""
            time_str = ""
            if time_raw:
                time_norm = time_raw.replace("h", ":").replace("H", ":")
                # Forcer 2 chiffres pour l'heure
                parts = time_norm.split(":")
                if len(parts) == 2:
                    time_str = f"{int(parts[0]):02d}:{parts[1][:2]}"

            note = _clean(note_m.group(1)) if note_m else ""

            # Si l'heure est manquante, laisser l'IA proposer un creneau libre
            # en fonction des evenements deja presents a cette date.
            if not time_str:
                try:
                    time_str = self._pick_free_slot_for_date(date_str) or ""
                except Exception:
                    time_str = ""

            if getattr(self, "lacher_prise_mode", False):
                # Mode Lacher Prise : on ajoute directement + rappel informel
                self.add_agenda_event_with_reminder(title, date_str, time_str, note)
                self.root.after(0, lambda: self.add_message(
                    "SYSTEM", f"📅 Événement + rappel : {title} — {date_str} {time_str}".rstrip()))
            else:
                # Mode normal : passer par la boite Permissions
                try:
                    self._create_agenda_permission_request(
                        title, date_str, time_str, note, source="chat IA")
                    self.root.after(0, lambda: self.add_message(
                        "SYSTEM", f"📅 En attente dans Permissions (icone eclair du hub) : {title}"))
                except Exception:
                    # Fallback : ajout direct si la permission echoue
                    self.add_agenda_event_with_reminder(title, date_str, time_str, note)
        except Exception as e:
            print(f"Erreur agenda command : {e}")

    def _pick_free_slot_for_date(self, date_str):
        """Demande a Mistral de proposer un creneau libre pour cette date
        en fonction des evenements existants. Retourne 'HH:MM' ou None."""
        try:
            existing = [e for e in self.agenda_events if e.get("date") == date_str]
            if existing:
                occup = ", ".join(
                    f"{e.get('time', '??:??')} {e.get('title', '')[:30]}"
                    for e in existing if e.get("time"))
            else:
                occup = "(aucun)"

            prompt = (
                f"Tu dois proposer un creneau horaire libre le {date_str} "
                f"pour un nouveau rendez-vous. Evenements deja planifies ce jour : "
                f"{occup}. Propose une heure entre 09:00 et 18:00 qui laisse "
                f"au moins 45 min de marge avant et apres les autres evenements. "
                f"Reponds UNIQUEMENT au format HH:MM, rien d'autre."
            )
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {API_KEY}"}
            payload = {
                "model": MODELE,
                "messages": [
                    {"role": "system", "content": "Tu es un assistant planning. Reponds avec une unique heure HH:MM."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 10,
            }
            resp = self._post_with_retry(URL, headers, payload, timeout=(5, 30))
            if resp.status_code != 200:
                return None
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            m = re.search(r"([01]?\d|2[0-3]):([0-5]\d)", raw)
            if not m:
                return None
            hh = int(m.group(1))
            if hh < 9 or hh > 18:
                return None
            return f"{hh:02d}:{m.group(2)}"
        except Exception as e:
            print(f"Erreur pick_free_slot : {e}")
            return None
