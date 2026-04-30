"""Rendu canvas, animations, toast."""
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


class AnimationsMixin:
    def _show_toast(self, message):
        """Affiche une notification toast temporaire en haut de l'écran."""
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.attributes("-alpha", 0.0)  # démarre invisible pour fade-in
        tw, th = 400, 62
        tx = (self.sw // 2) - (tw // 2)
        ty = 40
        toast.geometry(f"{tw}x{th}+{tx}+{ty}")
        toast.configure(bg="#0a0a18")

        # Barre d'accent latérale
        accent = tk.Frame(toast, bg="#c084fc", width=4)
        accent.pack(side="left", fill="y")

        # Contenu
        content = tk.Frame(toast, bg="#0a0a18")
        content.pack(side="left", fill="both", expand=True, padx=14, pady=8)

        # Label principal
        tk.Label(content, text=message,
                 font=("Segoe UI", 10, "bold"),
                 bg="#0a0a18", fg="#dde4ff",
                 wraplength=340, justify="left", anchor="w").pack(fill="both", expand=True)

        # Bordure lumineuse
        toast.configure(highlightbackground="#2d1f4e", highlightthickness=1)

        # Fade-in progressif
        def _fade_in(alpha=0.0):
            alpha = min(alpha + 0.12, 0.96)
            toast.attributes("-alpha", alpha)
            if alpha < 0.96:
                toast.after(16, lambda: _fade_in(alpha))
        _fade_in()

        # Fade-out puis destroy
        def _fade_out(alpha=0.96):
            alpha = max(alpha - 0.08, 0.0)
            toast.attributes("-alpha", alpha)
            if alpha > 0:
                toast.after(16, lambda: _fade_out(alpha))
            else:
                toast.destroy()

        toast.after(3200, _fade_out)

    def start_notification_blink(self):
        self.blink_end_time = time.time() + 3.0

    def draw_logo(self, canvas, x, y, size, pulse=0, with_bg=False, color="#22b9ff"):
        canvas.delete("logo")
        scale = 0.85 if self.hub_active_animation and canvas == self.hub_canvas else 1.0
        s = size * scale

        if with_bg:
            # Fond circulaire avec dégradé simulé (deux cercles concentriques)
            canvas.create_oval(x-s-22, y-s-22, x+s+22, y+s+22,
                               fill="#040408", outline="#0c0c18", tags="logo")
            canvas.create_oval(x-s-14, y-s-14, x+s+14, y+s+14,
                               fill="", outline="#1a1a2a", width=1, tags="logo")

        render_color = color if self.logo_alpha > 0.5 else "#222233"

        # Triangle principal (un seul)
        points = [x, y-s, x+s, y+s, x-s, y+s]
        canvas.create_polygon(points, outline=render_color, fill="", width=2, tags="logo")

        # Point central
        canvas.create_oval(x-2, y-1, x+2, y+3, fill=render_color, outline="", tags="logo")

        if canvas == self.hub_canvas:
            opening_prog = self.hub_dots_progress[0]
            if opening_prog == 0:
                # Un seul cercle pulsant autour du triangle (rebond)
                p_size = s + (pulse * 50)
                canvas.create_oval(x-p_size, y-p_size, x+p_size, y+p_size,
                                   outline="#ffffff", width=1, tags="logo")
            elif opening_prog < 1.0:
                shock_size = s + 40 + (opening_prog * 120)
                canvas.create_oval(x-shock_size, y-shock_size, x+shock_size, y+shock_size,
                                   outline="#442222", width=1, dash=(5, 5), tags="logo")

    def _hub_logo_loop(self):
        """Logo central du hub mail : meme rendu que le hub principal
        (halos pulsants + disque + hexagone tournant + Y blanc + tick
        marks + diamant), mais avec un fond NOIR au lieu d'indigo."""
        if self.mode != "hub":
            return
        cx, cy = self.hub_size // 2, self.hub_size // 2
        spin = self.hub_rotation
        self.hub_canvas.delete("hublogo")

        # UN SEUL halo pulsant noir autour du disque central (bounce 3 Hz)
        t = time.time()
        pulse = 8 + 5 * math.sin(t * 3)  # bounce entre +3 et +13 px du rayon
        Rc = 54  # rayon central (108/2)
        self.hub_canvas.create_oval(
            cx - Rc - pulse, cy - Rc - pulse,
            cx + Rc + pulse, cy + Rc + pulse,
            fill="", outline="#1a1a1a", width=1, tags="hublogo")

        # Disque central : UN seul cercle noir uniforme (pas de gradient).
        self.hub_canvas.create_oval(
            cx - Rc, cy - Rc, cx + Rc, cy + Rc,
            fill="#000000", outline="", tags="hublogo")

        # Logo Y dedans (même geometrie que principal_hub)
        ry = 28.88
        # Hexagone tournant (rotation spin*0.4 + 30deg)
        hex_pts = []
        for i in range(6):
            a = math.radians(i * 60 + spin * 0.4 + 30)
            hex_pts.extend([cx + ry * 1.22 * math.cos(a),
                            cy + ry * 1.22 * math.sin(a)])
        self.hub_canvas.create_polygon(
            hex_pts, fill="", outline="#666666", width=1, tags="hublogo")
        # Cercle interieur
        circle_r = ry * 0.96
        self.hub_canvas.create_oval(
            cx - circle_r, cy - circle_r,
            cx + circle_r, cy + circle_r,
            fill="", outline="#444444", width=1, tags="hublogo")
        # 12 tick marks (4 cardinaux plus epais)
        for i in range(12):
            a = math.radians(i * 30)
            inner = 0.82 if i % 3 == 0 else 0.88
            x1 = cx + ry * inner * math.cos(a)
            y1 = cy + ry * inner * math.sin(a)
            x2 = cx + ry * 0.96 * math.cos(a)
            y2 = cy + ry * 0.96 * math.sin(a)
            self.hub_canvas.create_line(
                x1, y1, x2, y2, fill="#888888",
                width=2 if i % 3 == 0 else 1, tags="hublogo")
        # Y blanc geometrique (2 branches + tige + diamant)
        self.hub_canvas.create_line(
            cx - ry * 0.45, cy - ry * 0.52, cx, cy - ry * 0.02,
            fill="#ffffff", width=2, capstyle="round", tags="hublogo")
        self.hub_canvas.create_line(
            cx + ry * 0.45, cy - ry * 0.52, cx, cy - ry * 0.02,
            fill="#ffffff", width=2, capstyle="round", tags="hublogo")
        self.hub_canvas.create_line(
            cx, cy - ry * 0.02, cx, cy + ry * 0.46,
            fill="#ffffff", width=2, capstyle="round", tags="hublogo")
        # Diamant au croisement
        self.hub_canvas.create_polygon(
            cx, cy - ry * 0.14,
            cx + ry * 0.08, cy - ry * 0.02,
            cx, cy + ry * 0.1,
            cx - ry * 0.08, cy - ry * 0.02,
            fill="#888888", outline="", tags="hublogo")
        # 3 dots aux sommets pairs de l'hexagone
        for i in (0, 2, 4):
            a = math.radians(i * 60 + spin * 0.4 + 30)
            px = cx + ry * 1.22 * math.cos(a)
            py = cy + ry * 1.22 * math.sin(a)
            self.hub_canvas.create_oval(
                px - 1.4, py - 1.4, px + 1.4, py + 1.4,
                fill="#aaaaaa", outline="", tags="hublogo")

        self.root.after(30, self._hub_logo_loop)

    def _refresh_pending_cache(self):
        """Rafraîchit le cache du nombre de permissions en attente (évite appel DB à chaque frame)."""
        try:
            self._pending_count_cache = len(database.get_pending_requests(
                None if USER_LEVEL == 0 else USERNAME))
        except:
            self._pending_count_cache = 0
        if self.mode == "hub":
            self.root.after(5000, self._refresh_pending_cache)

    def update_hub_animation(self, gen: int = 0):
        # Gen-tracking : si une generation plus recente a demarre OU si on
        # a quitte le hub, on s'auto-termine. Empeche les loops fantomes
        # qui causent dedoublement / textes superposes.
        if gen != getattr(self, "_hub_loop_gen", 0):
            return
        if self.mode != "hub":
            return

        target = 1.0 if self.hub_open else 0.0
        changed = False

        for i in range(8):
            if i > 0:
                prev = self.hub_dots_progress[i-1]
                if self.hub_open and prev < 0.2: continue
                if not self.hub_open and prev > 0.8: continue

            if self.hub_dots_progress[i] < target:
                self.hub_dots_progress[i] = min(target, self.hub_dots_progress[i] + HUB_ANIM_SPEED)
                changed = True
            elif self.hub_dots_progress[i] > target:
                self.hub_dots_progress[i] = max(target, self.hub_dots_progress[i] - HUB_ANIM_SPEED)
                changed = True

        # Dessiner
        self.draw_hub_elements()

        # Re-schedule en propageant la meme generation.
        self.root.after(40, lambda g=gen: self.update_hub_animation(g))

    def draw_hub_elements(self):
        self.hub_canvas.delete("menu")
        cx, cy = self.hub_size // 2, self.hub_size // 2

        self.hub_rotation += 1.5
        # Le cercle hachure exterieur est supprime, mais on garde les 2 arcs
        # qui tournent comme accent (radar style).
        self.hub_canvas.create_arc(cx-155, cy-155, cx+155, cy+155,
                                   start=-self.hub_rotation, extent=60,
                                   outline="#ffffff", width=2, style="arc", tags="menu")
        self.hub_canvas.create_arc(cx-162, cy-162, cx+162, cy+162,
                                   start=self.hub_rotation * 0.6, extent=30,
                                   outline="#22b9ff", width=1, style="arc", tags="menu")

        if USER_LEVEL == 0:
            admin_color = couleur_gold
        elif USER_LEVEL == 2:
            admin_color = couleur_permissions
        else:
            admin_color = "#555555"
        reseau_color = couleur_reseau if USER_LEVEL == 0 else "#222222"
        config_options = [
            ("CHAT",    couleur_chat_pale),    # 0
            ("OPTION",  couleur_option_pale),  # 1
            ("ADMIN",   admin_color),          # 2
            ("RETOUR",  couleur_log_pale),     # 3  (retour vers hub d'entree)
            ("INBOX",   couleur_inbox),        # 4
            ("AGENDA",  couleur_agenda),       # 5
            ("RÉSEAU",  reseau_color),         # 6
            ("PERMS",   couleur_permissions),  # 7  (compose mail = bouton dans chat)
        ]

        nb = len(config_options)
        radius_max = 150
        # pending_count mis en cache (rafraîchi via _refresh_pending_cache)
        pending_count = getattr(self, "_pending_count_cache", 0)
        # Pulse global pour halos
        glow_pulse = (math.sin(time.time() * 2.2) + 1) / 2  # 0..1

        for i in range(nb):
            prog = self.hub_dots_progress[i]
            if prog <= 0: continue

            nom, col = config_options[i]
            angle = math.radians(i * (360 / nb) - 90)
            cur_r = prog * radius_max
            x = cx + cur_r * math.cos(angle)
            y = cy + cur_r * math.sin(angle)

            # Ligne centre → orbe
            self.hub_canvas.create_line(cx, cy, x, y, fill="#1a1a2a", width=1, tags="menu")

            # Particule qui remonte le long de la ligne (effet flux)
            flow_pos = (time.time() * 1.5 + i * 0.2) % 1.0
            fx = cx + (cur_r * flow_pos) * math.cos(angle)
            fy = cy + (cur_r * flow_pos) * math.sin(angle)
            self.hub_canvas.create_oval(fx-1.5, fy-1.5, fx+1.5, fy+1.5,
                                        fill=col, outline="", tags="menu")

            opt_size = 28 * prog

            # Orbe principal (fond + bordure)
            self.hub_canvas.create_oval(x-opt_size, y-opt_size, x+opt_size, y+opt_size,
                                        fill="#050510", outline=col, width=2, tags="menu")

            # Texte uniquement quand l'orbe est ouvert à >80%
            if prog > 0.8:
                self.hub_canvas.create_text(x, y-5, text=nom, fill=col,
                                            font=("Verdana", 7, "bold"), tags="menu")
                if nom == "PERMS" and pending_count > 0:
                    self.hub_canvas.create_oval(x+18, y-22, x+30, y-10,
                                                fill=couleur_permissions, outline="", tags="menu")
                    self.hub_canvas.create_text(x+24, y-16, text=str(pending_count),
                                                fill="#000", font=("Arial", 7, "bold"), tags="menu")
                else:
                    self.hub_canvas.create_text(x, y+12, text="●", fill=col,
                                                font=("Arial", 6), tags="menu")

    def animate(self):
        now = time.time()
        self.pulse_val = (math.sin(now * 5) + 1) / 2

        if self.mode == "bubble":
            self.pos_x += self.speed_x
            self.pos_y += self.speed_y
            if self.pos_x <= 0 or self.pos_x >= self.sw - self.bubble_win_size:
                self.speed_x *= -1
