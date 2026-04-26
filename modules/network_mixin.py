"""Module réseau."""
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


class NetworkMixin:
    def _network_refresh_loop(self):
        while True:
            try:
                self._network_users = database.get_all_users_info()
                if self.mode == "reseau":
                    self.root.after(0, self._render_reseau)
            except:
                pass
            time.sleep(10)

    def _render_reseau(self):
        if not hasattr(self, "reseau_canvas"):
            return
        c = self.reseau_canvas
        c.delete("all")
        users_info = database.get_all_users_info()
        cx, cy = 220, 130
        radius = 90
        nb = max(len(users_info), 1)
        pulse = (math.sin(time.time() * 2) + 1) / 2

        for i, (uname, info) in enumerate(users_info.items()):
            angle = math.radians(i * (360 / nb) - 90)
            ux = cx + radius * math.cos(angle)
            uy = cy + radius * math.sin(angle)
            is_me = (uname == USERNAME)
            is_online = info.get("is_online", False)
            lvl = info["permissions"][0] if info["permissions"] else 1
            lvl_colors = {0: couleur_gold, 1: couleur_accent, 2: couleur_permissions}
            col = lvl_colors.get(lvl, couleur_accent) if is_online else "#333333"

            c.create_line(cx, cy, ux, uy, fill="#1a1a00" if not is_online else "#332200", width=1)
            if is_online:
                halo = 20 + pulse * 7
                c.create_oval(ux-halo, uy-halo, ux+halo, uy+halo, outline=col, width=1)
            r = 16 if is_me else 12
            c.create_oval(ux-r, uy-r, ux+r, uy+r, fill="#0a0a00", outline=col, width=2 if is_me else 1)
            c.create_text(ux, uy+r+10, text=uname[:8], fill=col, font=("Futura", 7, "bold"))
            role = info.get("role", "")
            if role:
                c.create_text(ux, uy+r+20, text=role[:8], fill="#554400", font=("Arial", 6))

        # Centre = session actuelle
        ps = 28 + pulse * 9
        c.create_oval(cx-ps, cy-ps, cx+ps, cy+ps, outline=couleur_reseau, width=1)
        c.create_oval(cx-24, cy-24, cx+24, cy+24, fill="#0d0d00", outline=couleur_reseau, width=2)
        c.create_text(cx, cy-4, text=USERNAME[:8], fill=couleur_reseau, font=("Futura", 8, "bold"))
        c.create_text(cx, cy+8, text="SESSION", fill="#665500", font=("Arial", 6))

