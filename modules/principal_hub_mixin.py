"""Hub d'entree (principal) : 3 branches — MAIL, FINANCE, LOGOUT.

C'est le hub qui s'ouvre AU LANCEMENT de l'Assistant. Il sert de point
d'entree minimaliste : trois choix, pas plus.

- MAIL    -> ouvre l'ancien hub (chat, inbox, agenda, options, ...)
- FINANCE -> ouvre le hub finance
- LOGOUT  -> deconnecte et relance l'ecran de login

Architecture : un canvas de la meme taille que les autres hubs, avec 3
segments disposes a 120 d'intervalle (haut, bas-droit, bas-gauche). Animation
identique aux autres hubs.
"""
from __future__ import annotations
import time
import math
import tkinter as tk

from modules.config import (
    couleur_gold, couleur_mail_pale, couleur_log_pale,
    HUB_ANIM_SPEED,
)


# 3 branches du hub d'entree.
# Format : (label, couleur, action)  — angles calcules dans le draw.
PRINCIPAL_SEGMENTS = [
    ("MAIL",    couleur_mail_pale, "mail"),    # 0 — angle -90 (haut)
    ("FINANCE", couleur_gold,      "finance"), # 1 — angle  30 (bas-droit)
    ("LOGOUT",  couleur_log_pale,  "logout"),  # 2 — angle 150 (bas-gauche)
]


class PrincipalHubMixin:

    def _build_principal_hub(self):
        """Cree le canvas du hub d'entree."""
        self.principal_hub_canvas = tk.Canvas(
            self.root, width=self.hub_size, height=self.hub_size,
            bg="#010101", highlightthickness=0)
        self.principal_hub_canvas.bind(
            "<Button-1>", self.principal_hub_click_event)
        self.principal_hub_open = False
        self.principal_hub_dots_progress = [0.0] * 3
        self.principal_hub_rotation = 0.0

    # ---------------------------------------------------------------- toggle

    def toggle_principal_hub(self):
        """Ouvre / ferme le hub d'entree."""
        if self.mode == "principal_hub":
            self.root.withdraw()
            self.mode = "hidden"
            return
        self._hide_all_frames()
        self.principal_hub_open = False
        self.principal_hub_dots_progress = [0.0] * 3
        self.principal_hub_canvas.pack(fill="both", expand=True)
        self.root.geometry(f"{self.hub_size}x{self.hub_size}")
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.mode = "principal_hub"
        self.root.after(50, self._principal_hub_logo_loop)

    def _principal_hub_logo_loop(self):
        """Logo central pulsant : un triangle (3 branches)."""
        if self.mode != "principal_hub":
            return
        pulse = (math.sin(time.time() * 5) + 1) / 2
        cx, cy = self.hub_size // 2, self.hub_size // 2
        self.principal_hub_canvas.delete("logo")
        # Cercle de fond
        glow = int(180 + 60 * pulse)
        self.principal_hub_canvas.create_oval(
            cx - 50, cy - 50, cx + 50, cy + 50,
            fill="#0a0a14",
            outline=f"#{glow:02x}{glow:02x}{glow:02x}",
            width=2, tags="logo")
        # Triangle central pulsant (3 sommets, 1 par branche)
        size = 22 + 4 * pulse
        pts = []
        for i in range(3):
            ang = math.radians(i * 120 - 90)
            pts.extend([cx + size * math.cos(ang),
                        cy + size * math.sin(ang)])
        self.principal_hub_canvas.create_polygon(
            pts, fill="", outline=couleur_gold, width=2, tags="logo")
        self.principal_hub_canvas.create_text(
            cx, cy, text="◆", fill=couleur_gold,
            font=("Futura", int(20 + 4 * pulse), "bold"), tags="logo")
        self.root.after(30, self._principal_hub_logo_loop)

    # ---------------------------------------------------------------- click

    def principal_hub_click_event(self, event):
        cx, cy = self.hub_size // 2, self.hub_size // 2
        dist = math.sqrt((event.x - cx) ** 2 + (event.y - cy) ** 2)
        if dist < 60:
            # Click central : ouvre / ferme les branches
            self.principal_hub_open = not self.principal_hub_open
            self._update_principal_hub_animation()
            return
        if self.principal_hub_open:
            radius = 150
            for i, (_, _, action) in enumerate(PRINCIPAL_SEGMENTS):
                ang = math.radians(i * 120 - 90)
                ox = cx + radius * math.cos(ang)
                oy = cy + radius * math.sin(ang)
                if math.sqrt((event.x - ox) ** 2 + (event.y - oy) ** 2) < 50:
                    self._principal_dispatch(action)
                    return

    def _principal_dispatch(self, action: str):
        if action == "mail":
            # Ferme le hub d'entree et ouvre le hub mail (ancien hub)
            self.toggle_hub()
        elif action == "finance":
            self.toggle_finance_hub()
        elif action == "logout":
            self.logout()

    # -------------------------------------------------------------- drawing

    def _update_principal_hub_animation(self):
        target = 1.0 if self.principal_hub_open else 0.0
        changed = False
        for i in range(3):
            if i > 0:
                prev = self.principal_hub_dots_progress[i - 1]
                if self.principal_hub_open and prev < 0.2:
                    continue
                if (not self.principal_hub_open) and prev > 0.8:
                    continue
            cur = self.principal_hub_dots_progress[i]
            if cur < target:
                self.principal_hub_dots_progress[i] = min(
                    target, cur + HUB_ANIM_SPEED)
                changed = True
            elif cur > target:
                self.principal_hub_dots_progress[i] = max(
                    target, cur - HUB_ANIM_SPEED)
                changed = True
        self._draw_principal_hub_elements()
        still = self.principal_hub_open and self.mode == "principal_hub"
        if changed or still:
            self.root.after(40, self._update_principal_hub_animation)

    def _draw_principal_hub_elements(self):
        self.principal_hub_canvas.delete("menu")
        cx, cy = self.hub_size // 2, self.hub_size // 2
        self.principal_hub_rotation += 1.5
        # Cercle radar exterieur
        self.principal_hub_canvas.create_oval(
            cx - 175, cy - 175, cx + 175, cy + 175,
            outline="#1a1a2e", width=1, dash=(2, 10), tags="menu")
        # Arc d'accent (gold) qui tourne
        self.principal_hub_canvas.create_arc(
            cx - 160, cy - 160, cx + 160, cy + 160,
            start=-self.principal_hub_rotation, extent=80,
            outline=couleur_gold, width=2, style="arc", tags="menu")
        # 2e arc plus fin, plus rapide en sens inverse
        self.principal_hub_canvas.create_arc(
            cx - 168, cy - 168, cx + 168, cy + 168,
            start=self.principal_hub_rotation * 0.7, extent=40,
            outline="#ffffff", width=1, style="arc", tags="menu")

        radius_max = 150
        for i in range(3):
            prog = self.principal_hub_dots_progress[i]
            if prog <= 0:
                continue
            label, col, _ = PRINCIPAL_SEGMENTS[i]
            ang = math.radians(i * 120 - 90)
            cur_r = prog * radius_max
            x = cx + cur_r * math.cos(ang)
            y = cy + cur_r * math.sin(ang)
            # Ligne centre -> orbe
            self.principal_hub_canvas.create_line(
                cx, cy, x, y, fill="#1a1a2a", width=1, tags="menu")
            # Particule de flux qui remonte la ligne
            flow_pos = (time.time() * 1.5 + i * 0.3) % 1.0
            fx = cx + (cur_r * flow_pos) * math.cos(ang)
            fy = cy + (cur_r * flow_pos) * math.sin(ang)
            self.principal_hub_canvas.create_oval(
                fx - 1.5, fy - 1.5, fx + 1.5, fy + 1.5,
                fill=col, outline="", tags="menu")
            # Orbe principal — ici on agrandit pour seulement 3 branches
            opt_size = 38 * prog
            self.principal_hub_canvas.create_oval(
                x - opt_size, y - opt_size, x + opt_size, y + opt_size,
                fill="#050510", outline=col, width=3, tags="menu")
            # Halo doux
            for r_off, alpha in ((opt_size + 6, 0.5), (opt_size + 12, 0.25)):
                self.principal_hub_canvas.create_oval(
                    x - r_off, y - r_off, x + r_off, y + r_off,
                    outline=col, width=1, tags="menu")
            if prog > 0.8:
                self.principal_hub_canvas.create_text(
                    x, y - 5, text=label, fill=col,
                    font=("Verdana", 9, "bold"), tags="menu")
                self.principal_hub_canvas.create_text(
                    x, y + 14, text="●", fill=col,
                    font=("Arial", 7), tags="menu")
