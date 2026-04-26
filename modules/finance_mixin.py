"""Hub finance (2e hub radial) + UI des 7 modules finance.

Architecture :
- Un nouveau canvas radial `finance_hub_canvas` avec 8 segments :
  ENTRANTES, SORTANTES, BANQUE, FISCAL, RELANCES, COMPLIANCE, REGIME, RETOUR.
- Un frame `finance_frame` partage entre les 7 vues, qui se contente de
  reconfigurer son contenu selon la vue active (`_finance_view`).
- Wizard de regime fiscal au 1er lancement (si aucun regime defini).

Toutes les actions passent par la boite Permissions (sauf en mode Lacher
Prise ou elles sont auto). Cf. permissions_mixin pour le dispatch.
"""
from __future__ import annotations
import os
import time
import math
import threading
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from datetime import datetime, date

import database
from modules.config import (
    USERNAME, USER_LEVEL, couleur_gold, couleur_accent,
    couleur_inbox, couleur_agenda, couleur_permissions,
    couleur_chat_pale, couleur_mail_pale, couleur_option_pale,
    couleur_log_pale, HUB_ANIM_SPEED,
)
from modules import finance


# Segments du hub finance : (label, couleur, vue cible)
# 9 segments dispos a 40 d'intervalle.
FINANCE_SEGMENTS = [
    ("ENTRANTES",  "#ff8a3d",        "in"),         # 0 — fact. fournisseurs
    ("SORTANTES",  couleur_inbox,    "out"),        # 1 — fact. clients
    ("BANQUE",     couleur_accent,   "bank"),       # 2 — rapprochement
    ("FISCAL",     couleur_gold,     "fiscal"),     # 3 — dashboard fiscal
    ("RELANCES",   couleur_permissions, "relances"),# 4 — impayes
    ("ACTIONS",    "#3aff7a",        "actions"),    # 5 — trading / portefeuille
    ("COMPLIANCE", "#a3e635",        "compliance"), # 6 — SIRET/IBAN/anomalies
    ("REGIME",     couleur_agenda,   "regime"),     # 7 — config fiscale
    ("RETOUR",     couleur_log_pale, "back"),       # 8 — retour hub d'entree
]


class FinanceMixin:
    # ============================================================
    # Construction des widgets (appele depuis Assistant.__init__)
    # ============================================================

    def _build_finance_hub(self):
        """Cree le canvas du hub finance (similaire au hub principal)."""
        self.finance_hub_canvas = tk.Canvas(
            self.root, width=self.hub_size, height=self.hub_size,
            bg="#010101", highlightthickness=0)
        self.finance_hub_canvas.bind("<Button-1>", self.finance_hub_click_event)
        self.finance_hub_open = False
        # Progression d'apparition des orbes : un slot par segment
        self.finance_hub_dots_progress = [0.0] * len(FINANCE_SEGMENTS)
        self.finance_hub_rotation = 0.0
        self._finance_view = None  # vue active dans le frame finance

    def _build_finance_frame(self):
        """Frame mere du module finance (vues partagees)."""
        self.finance_frame = ctk.CTkFrame(self.root, fg_color="#000000",
                                          corner_radius=0)
        # Header (titre + bouton retour vers le hub finance)
        head = ctk.CTkFrame(self.finance_frame, fg_color="#0a0a14",
                            corner_radius=0)
        head.pack(fill="x")
        self.finance_title = ctk.CTkLabel(
            head, text="FINANCE", font=("Futura", 16, "bold"),
            text_color=couleur_gold)
        self.finance_title.pack(side="left", padx=14, pady=10)
        ctk.CTkButton(head, text="↩ Hub", width=60, height=26,
                      fg_color="transparent", hover_color="#1a1a2a",
                      text_color=couleur_log_pale,
                      command=self._return_to_finance_hub).pack(
            side="right", padx=10)

        # Body : conteneur scrollable reutilise par chaque vue
        self.finance_body = ctk.CTkScrollableFrame(
            self.finance_frame, fg_color="#000000")
        self.finance_body.pack(fill="both", expand=True, padx=8, pady=6)

    # ============================================================
    # Hub finance : navigation
    # ============================================================

    def toggle_finance_hub(self):
        """Ouvre ou ferme le hub finance."""
        if self.mode == "finance_hub":
            self.root.withdraw()
            self.mode = "hidden"
            return
        self._hide_all_frames()
        self.hub_open = False
        self.finance_hub_open = False
        self.finance_hub_dots_progress = [0.0] * len(FINANCE_SEGMENTS)
        self.finance_hub_canvas.pack(fill="both", expand=True)
        self.root.geometry(f"{self.hub_size}x{self.hub_size}")
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.mode = "finance_hub"
        self.root.after(50, self._finance_hub_logo_loop)

    def _return_to_finance_hub(self):
        """Depuis un frame finance, retour au hub finance."""
        self._hide_all_frames()
        self.toggle_finance_hub()

    def _finance_hub_logo_loop(self):
        if self.mode != "finance_hub":
            return
        pulse = (math.sin(time.time() * 5) + 1) / 2
        # Logo central : un € pulsant (placeholder simple)
        cx, cy = self.hub_size // 2, self.hub_size // 2
        self.finance_hub_canvas.delete("logo")
        glow = int(140 + 80 * pulse)
        self.finance_hub_canvas.create_oval(
            cx - 50, cy - 50, cx + 50, cy + 50,
            fill="#0a0a14",
            outline=f"#{glow:02x}{int(glow*0.85):02x}00",
            width=2, tags="logo")
        self.finance_hub_canvas.create_text(
            cx, cy, text="€", fill=couleur_gold,
            font=("Futura", int(34 + 6 * pulse), "bold"), tags="logo")
        self.root.after(30, self._finance_hub_logo_loop)

    def finance_hub_click_event(self, event):
        cx, cy = self.hub_size // 2, self.hub_size // 2
        dist = math.sqrt((event.x - cx) ** 2 + (event.y - cy) ** 2)
        if dist < 60:
            # Click central : ouvre/ferme le rayonnement
            self.finance_hub_open = not self.finance_hub_open
            self._update_finance_hub_animation()
            return
        if self.finance_hub_open:
            nb = len(FINANCE_SEGMENTS)
            radius = 150
            # Avec 9 segments, on rapproche un peu le seuil de detection
            hit_radius = 36 if nb >= 9 else 40
            for i in range(nb):
                angle = math.radians(i * (360 / nb) - 90)
                ox = cx + radius * math.cos(angle)
                oy = cy + radius * math.sin(angle)
                if math.sqrt((event.x - ox) ** 2 + (event.y - oy) ** 2) < hit_radius:
                    _, _, view = FINANCE_SEGMENTS[i]
                    if view == "back":
                        # Retour vers le hub d'entree (3 branches)
                        self.toggle_principal_hub()
                    else:
                        self._open_finance_view(view)
                    return

    def _update_finance_hub_animation(self):
        target = 1.0 if self.finance_hub_open else 0.0
        changed = False
        for i in range(len(FINANCE_SEGMENTS)):
            if i > 0:
                prev = self.finance_hub_dots_progress[i - 1]
                if self.finance_hub_open and prev < 0.2:
                    continue
                if (not self.finance_hub_open) and prev > 0.8:
                    continue
            cur = self.finance_hub_dots_progress[i]
            if cur < target:
                self.finance_hub_dots_progress[i] = min(
                    target, cur + HUB_ANIM_SPEED)
                changed = True
            elif cur > target:
                self.finance_hub_dots_progress[i] = max(
                    target, cur - HUB_ANIM_SPEED)
                changed = True
        self._draw_finance_hub_elements()
        still = self.finance_hub_open and self.mode == "finance_hub"
        if changed or still:
            self.root.after(40, self._update_finance_hub_animation)

    def _draw_finance_hub_elements(self):
        self.finance_hub_canvas.delete("menu")
        cx, cy = self.hub_size // 2, self.hub_size // 2
        self.finance_hub_rotation += 1.5
        # Cercle radar exterieur
        self.finance_hub_canvas.create_oval(
            cx - 170, cy - 170, cx + 170, cy + 170,
            outline="#1f1a0a", width=1, dash=(2, 10), tags="menu")
        self.finance_hub_canvas.create_arc(
            cx - 155, cy - 155, cx + 155, cy + 155,
            start=-self.finance_hub_rotation, extent=60,
            outline=couleur_gold, width=2, style="arc", tags="menu")

        radius_max = 150
        nb = len(FINANCE_SEGMENTS)
        # Avec 9 orbes, on les retrecit legerement pour qu'elles ne se touchent pas
        orb_factor = 24 if nb >= 9 else 28
        for i in range(nb):
            prog = self.finance_hub_dots_progress[i]
            if prog <= 0:
                continue
            label, col, _ = FINANCE_SEGMENTS[i]
            angle = math.radians(i * (360 / nb) - 90)
            cur_r = prog * radius_max
            x = cx + cur_r * math.cos(angle)
            y = cy + cur_r * math.sin(angle)
            self.finance_hub_canvas.create_line(
                cx, cy, x, y, fill="#1a1a2a", width=1, tags="menu")
            opt_size = orb_factor * prog
            self.finance_hub_canvas.create_oval(
                x - opt_size, y - opt_size, x + opt_size, y + opt_size,
                fill="#080806", outline=col, width=2, tags="menu")
            if prog > 0.8:
                self.finance_hub_canvas.create_text(
                    x, y - 5, text=label, fill=col,
                    font=("Verdana", 7, "bold"), tags="menu")
                self.finance_hub_canvas.create_text(
                    x, y + 12, text="●", fill=col,
                    font=("Arial", 6), tags="menu")

    # ============================================================
    # Vues du frame finance
    # ============================================================

    def _open_finance_view(self, view: str):
        self._hide_all_frames()
        self._finance_view = view
        self.finance_frame.pack(fill="both", expand=True)
        self.root.geometry("520x680")
        self.root.deiconify()
        self.mode = "finance"
        # Vide le body
        for w in self.finance_body.winfo_children():
            w.destroy()
        # Dispatch selon la vue
        if view == "in":
            self.finance_title.configure(text="📥 FACTURES FOURNISSEURS")
            self._render_finance_invoices("in")
        elif view == "out":
            self.finance_title.configure(text="📤 FACTURES CLIENTS")
            self._render_finance_invoices("out")
        elif view == "bank":
            self.finance_title.configure(text="🏦 BANQUE & RAPPROCHEMENT")
            self._render_finance_bank()
        elif view == "fiscal":
            self.finance_title.configure(text="💸 DASHBOARD FISCAL")
            self._render_finance_fiscal()
        elif view == "relances":
            self.finance_title.configure(text="🔔 RELANCES IMPAYÉS")
            self._render_finance_relances()
        elif view == "compliance":
            self.finance_title.configure(text="🛡️ COMPLIANCE")
            self._render_finance_compliance()
        elif view == "regime":
            self.finance_title.configure(text="⚙️ RÉGIME FISCAL")
            self._render_finance_regime()
        elif view == "actions":
            self.finance_title.configure(text="📈 ACTIONS / TRADING")
            self._render_finance_actions()

    # --- Vue Factures (entrantes / sortantes) -------------------------------

    def _render_finance_invoices(self, direction: str):
        invs = finance.list_invoices(direction=direction, limit=200)
        if not invs:
            ctk.CTkLabel(
                self.finance_body,
                text=("Aucune facture fournisseur." if direction == "in"
                      else "Aucune facture client."),
                text_color="#666", font=("Segoe UI", 11)).pack(pady=30)
            return
        for inv in invs:
            self._invoice_card(inv)

    def _invoice_card(self, inv):
        col = "#1a1a2a"
        if inv.statut == "paid":
            border = "#3aff7a"
        elif inv.statut == "overdue":
            border = couleur_permissions
        else:
            border = couleur_gold

        card = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                            corner_radius=10, border_width=1,
                            border_color=border)
        card.pack(fill="x", padx=4, pady=4)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(top, text=f"{inv.party or '(inconnu)'} — {inv.invoice_number or '#?'}",
                     font=("Futura", 11, "bold"),
                     text_color=couleur_gold).pack(side="left")
        ctk.CTkLabel(top, text=f"{inv.montant_ttc:.2f} {inv.currency}",
                     font=("Futura", 11, "bold"),
                     text_color="#cccccc").pack(side="right")
        info = (f"Émise : {inv.date_emission or '?'}    "
                f"Échéance : {inv.date_echeance or '?'}    "
                f"Statut : {inv.statut}")
        ctk.CTkLabel(card, text=info, font=("Segoe UI", 9),
                     text_color="#888").pack(anchor="w", padx=10, pady=(0, 4))
        # Anomalies
        flags = finance.detect_anomaly(inv)
        if flags:
            ctk.CTkLabel(
                card, text="⚠ " + " ".join(flags),
                font=("Segoe UI", 8, "bold"),
                text_color=couleur_permissions).pack(
                anchor="w", padx=10, pady=(0, 4))
        # Boutons
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(0, 8))
        if inv.statut != "paid":
            ctk.CTkButton(
                btns, text="✓ Marquer payée", width=110, height=24,
                fg_color="transparent", hover_color="#0a4020",
                text_color="#3aff7a",
                command=lambda i=inv.id: self._finance_mark_paid(i)).pack(
                side="left", padx=2)
        if inv.direction == "out" and inv.statut != "paid":
            ctk.CTkButton(
                btns, text="🔔 Relancer", width=90, height=24,
                fg_color="transparent",
                hover_color="#3a0020",
                text_color=couleur_permissions,
                command=lambda i=inv.id: self._finance_request_relance(i)).pack(
                side="left", padx=2)
        ctk.CTkButton(
            btns, text="✕", width=24, height=24,
            fg_color="transparent", hover_color="#3a0000",
            text_color="#aa3333",
            command=lambda i=inv.id: self._finance_delete_invoice(i)).pack(
            side="right", padx=2)

    def _finance_mark_paid(self, invoice_id: int):
        finance.update_invoice(invoice_id, statut="paid")
        self._show_toast("✓ Facture marquée payée")
        self._open_finance_view(self._finance_view)

    def _finance_delete_invoice(self, invoice_id: int):
        finance.delete_invoice(invoice_id)
        self._show_toast("✕ Facture supprimée")
        self._open_finance_view(self._finance_view)

    def _finance_request_relance(self, invoice_id: int):
        """Cree une permission de relance (ou envoi auto en lacher prise)."""
        mail = finance.generate_relance(invoice_id)
        if not mail or not mail.get("to"):
            self._show_toast("Pas d'email client : impossible de relancer")
            return
        if getattr(self, "lacher_prise_mode", False):
            try:
                threading.Thread(
                    target=self.execute_auto_mail,
                    args=(mail["to"], mail["subject"], mail["body"]),
                    daemon=True).start()
                finance.mark_relance_sent(invoice_id)
                self._show_toast("🔔 Relance envoyée")
            except Exception as e:
                print(f"Erreur relance auto : {e}")
        else:
            database.add_permission_request(
                USERNAME, "finance_relance_send",
                f"🔔 Relance niv. {mail['niveau']} — {mail['to']}",
                f"Facture : {mail['subject']}",
                {
                    "to": mail["to"], "subject": mail["subject"],
                    "body": mail["body"], "invoice_id": invoice_id,
                    "highlight_finance": True,
                })
            self._show_toast("Relance en attente dans Permissions")

    # --- Vue Banque ---------------------------------------------------------

    def _render_finance_bank(self):
        # Bouton import CSV
        bar = ctk.CTkFrame(self.finance_body, fg_color="transparent")
        bar.pack(fill="x", pady=(4, 8))
        ctk.CTkButton(bar, text="+ Importer un relevé CSV",
                      fg_color=couleur_accent, hover_color="#1a90c0",
                      command=self._finance_import_csv).pack(
            side="left", padx=4)
        ctk.CTkButton(bar, text="↻ Rapprocher",
                      fg_color="transparent", border_width=1,
                      border_color=couleur_accent,
                      text_color=couleur_accent,
                      command=self._finance_reconcile).pack(
            side="left", padx=4)

        # Stats
        stats = finance.quick_stats()
        st = ctk.CTkLabel(
            self.finance_body,
            text=(f"Mouvements non rapprochés : "
                  f"{stats['mouvements_non_rapproches']}"),
            font=("Segoe UI", 10), text_color="#aaa")
        st.pack(anchor="w", padx=4, pady=(0, 6))

        movs = finance.list_movements(limit=100)
        if not movs:
            ctk.CTkLabel(self.finance_body,
                         text="Aucun mouvement importé.",
                         text_color="#666").pack(pady=20)
            return
        for mv in movs:
            self._movement_card(mv)

    def _movement_card(self, mv):
        border = "#3aff7a" if mv.matched_invoice_id else "#444"
        card = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                            corner_radius=8, border_width=1,
                            border_color=border)
        card.pack(fill="x", padx=4, pady=2)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=4)
        sign = "+" if mv.sens == "credit" else "−"
        col = "#3aff7a" if mv.sens == "credit" else "#ff6b6b"
        ctk.CTkLabel(top, text=mv.date, font=("Segoe UI", 9),
                     text_color="#888").pack(side="left")
        ctk.CTkLabel(top, text=f"{sign}{abs(mv.montant):.2f} €",
                     font=("Futura", 11, "bold"),
                     text_color=col).pack(side="right")
        ctk.CTkLabel(card, text=mv.libelle[:80],
                     font=("Segoe UI", 9),
                     text_color="#aaa", anchor="w", justify="left").pack(
            anchor="w", padx=10, pady=(0, 4))
        if mv.matched_invoice_id:
            ctk.CTkLabel(card, text=f"↔ Facture #{mv.matched_invoice_id}",
                         font=("Segoe UI", 8),
                         text_color="#3aff7a").pack(anchor="w", padx=10,
                                                    pady=(0, 4))

    def _finance_import_csv(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("Tous", "*.*")],
            title="Releve bancaire (CSV)")
        if not path:
            return
        if getattr(self, "lacher_prise_mode", False):
            n = finance.import_bank_csv(path)
            self._show_toast(f"🏦 {n} mouvement(s) importe(s)")
            self._open_finance_view("bank")
        else:
            database.add_permission_request(
                USERNAME, "finance_bank_import",
                f"🏦 Importer relevé : {os.path.basename(path)}",
                "Le fichier sera lu et les mouvements ajoutés en DB.",
                {"path": path, "highlight_finance": True})
            self._show_toast("Import en attente dans Permissions")

    def _finance_reconcile(self):
        n = finance.reconcile()
        self._show_toast(f"↻ {n} facture(s) rapprochée(s)")
        self._open_finance_view("bank")

    # --- Vue Fiscal ---------------------------------------------------------

    def _render_finance_fiscal(self):
        cfg = database.get_fiscal_config(USERNAME)
        regime = cfg.get("regime") or "none"
        franchise = cfg.get("tva_franchise", False)
        s = finance.fiscal_summary(regime, tva_franchise=franchise)

        if regime == "none":
            ctk.CTkLabel(
                self.finance_body,
                text=("Aucun régime fiscal configuré.\n"
                      "Va dans RÉGIME pour le définir."),
                font=("Segoe UI", 11),
                text_color=couleur_permissions, justify="center").pack(
                pady=20)
            return

        # Bandeau régime
        ctk.CTkLabel(self.finance_body,
                     text=f"Régime : {regime}    Année : {s['year']}",
                     font=("Futura", 11, "bold"),
                     text_color=couleur_gold).pack(pady=(4, 10))

        # Cartes chiffrees
        def _kpi(label, value, color):
            f = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                             corner_radius=10, border_width=1,
                             border_color=color)
            f.pack(fill="x", padx=4, pady=3)
            ctk.CTkLabel(f, text=label, font=("Segoe UI", 9),
                         text_color="#888").pack(anchor="w", padx=12,
                                                  pady=(8, 0))
            ctk.CTkLabel(f, text=value, font=("Futura", 16, "bold"),
                         text_color=color).pack(anchor="e", padx=12,
                                                pady=(0, 8))

        _kpi("Chiffre d'affaires HT", f"{s['ca_ht']:.2f} €", couleur_inbox)
        _kpi("Achats HT", f"{s['achats_ht']:.2f} €", couleur_chat_pale)
        if not franchise:
            _kpi("TVA collectée", f"{s['tva_collectee']:.2f} €", "#aaa")
            _kpi("TVA déductible", f"{s['tva_deductible']:.2f} €", "#aaa")
            _kpi("TVA à reverser", f"{s['tva_a_reverser']:.2f} €",
                 couleur_gold)
        else:
            _kpi("TVA", "Franchise (non assujetti)", "#aaa")
        if regime in ("auto_entrepreneur_bic", "auto_entrepreneur_bnc",
                      "auto_entrepreneur_lib"):
            _kpi("URSSAF estimée", f"{s['urssaf_estime']:.2f} €",
                 couleur_permissions)
            _kpi("IR estimé", f"{s['ir_estime']:.2f} €",
                 couleur_permissions)
        elif regime in ("sas", "sarl"):
            _kpi("Bénéfice estimé", f"{s['benefice_estime']:.2f} €",
                 couleur_inbox)
            _kpi("IS estimé", f"{s['is_estime']:.2f} €",
                 couleur_permissions)
        elif regime == "bnc_reel":
            _kpi("Bénéfice estimé", f"{s['benefice_estime']:.2f} €",
                 couleur_inbox)
            _kpi("IR estimé", f"{s['ir_estime']:.2f} €",
                 couleur_permissions)

    # --- Vue Relances -------------------------------------------------------

    def _render_finance_relances(self):
        overdue = finance.list_overdue()
        ctk.CTkLabel(
            self.finance_body,
            text=f"{len(overdue)} facture(s) client en retard",
            font=("Futura", 11, "bold"),
            text_color=couleur_permissions).pack(pady=(4, 10))
        if not overdue:
            ctk.CTkLabel(self.finance_body,
                         text="Tout est à jour ✦",
                         text_color="#3aff7a").pack(pady=20)
            return
        for inv in overdue:
            self._invoice_card(inv)

    # --- Vue Compliance -----------------------------------------------------

    def _render_finance_compliance(self):
        all_inv = finance.list_invoices(limit=500)
        flagged = []
        for inv in all_inv:
            f = finance.detect_anomaly(inv, history=all_inv)
            if f:
                flagged.append((inv, f))
        ctk.CTkLabel(
            self.finance_body,
            text=f"{len(flagged)} facture(s) avec anomalies",
            font=("Futura", 11, "bold"),
            text_color=couleur_permissions).pack(pady=(4, 10))
        if not flagged:
            ctk.CTkLabel(self.finance_body,
                         text="Aucune anomalie détectée.",
                         text_color="#3aff7a").pack(pady=20)
            return
        for inv, flags in flagged:
            card = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                                corner_radius=10, border_width=1,
                                border_color=couleur_permissions)
            card.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(
                card, text=f"{inv.party or '?'} — {inv.invoice_number or '?'}",
                font=("Futura", 11, "bold"),
                text_color=couleur_gold).pack(anchor="w", padx=10,
                                              pady=(8, 2))
            ctk.CTkLabel(
                card, text=" | ".join(flags),
                font=("Segoe UI", 9, "bold"),
                text_color=couleur_permissions).pack(anchor="w", padx=10,
                                                     pady=(0, 8))

    # --- Vue Regime fiscal --------------------------------------------------

    def _render_finance_regime(self):
        cfg = database.get_fiscal_config(USERNAME)
        regime_cur = cfg.get("regime") or "(non defini)"
        ctk.CTkLabel(
            self.finance_body,
            text=f"Régime actuel : {regime_cur}",
            font=("Futura", 12, "bold"),
            text_color=couleur_gold).pack(pady=(8, 12))

        # Boutons de selection des regimes
        regimes = [
            ("auto_entrepreneur_bic", "Auto-entrepreneur — Vente / Commerce (BIC)"),
            ("auto_entrepreneur_bnc", "Auto-entrepreneur — Prestations services (BNC)"),
            ("auto_entrepreneur_lib", "Auto-entrepreneur — Libéral (CIPAV)"),
            ("sas",                   "Société — SAS / SASU (IS)"),
            ("sarl",                  "Société — SARL / EURL (IS)"),
            ("bnc_reel",              "Profession libérale (BNC réel)"),
            ("none",                  "Pas de calcul fiscal"),
        ]
        for code, label in regimes:
            b = ctk.CTkButton(
                self.finance_body, text=label,
                fg_color=("#1a4020" if code == cfg.get("regime")
                          else "transparent"),
                hover_color="#1a4020",
                text_color=("#3aff7a" if code == cfg.get("regime")
                            else couleur_gold),
                border_width=1, border_color=couleur_gold, anchor="w",
                command=lambda c=code: self._finance_set_regime(c))
            b.pack(fill="x", padx=4, pady=2)

        # TVA franchise (toggle)
        franchise_var = tk.BooleanVar(
            value=bool(cfg.get("tva_franchise", False)))

        def _toggle_franchise():
            database.set_fiscal_config(
                USERNAME, tva_franchise=franchise_var.get())
            self._show_toast("Configuration TVA mise à jour")

        ctk.CTkCheckBox(
            self.finance_body,
            text="Bénéficie de la franchise en base de TVA",
            variable=franchise_var, command=_toggle_franchise,
            text_color="#cccccc",
            border_color=couleur_gold,
            fg_color=couleur_gold).pack(anchor="w", padx=8, pady=(12, 4))

        # SIRET / IBAN / Raison sociale
        for key, label in (("raison_sociale", "Raison sociale"),
                           ("siret", "SIRET"),
                           ("iban", "IBAN")):
            row = ctk.CTkFrame(self.finance_body, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(row, text=label + " :",
                         text_color="#888", width=110, anchor="w").pack(
                side="left")
            ent = ctk.CTkEntry(row, width=300)
            ent.insert(0, cfg.get(key, ""))
            ent.pack(side="left", padx=4)
            ctk.CTkButton(
                row, text="✓", width=30, fg_color=couleur_gold,
                text_color="#000",
                command=lambda k=key, e=ent: self._finance_save_field(
                    k, e.get())).pack(side="left", padx=2)

    def _finance_set_regime(self, code: str):
        database.set_fiscal_config(USERNAME, regime=code)
        self._show_toast(f"Régime mis à jour : {code}")
        self._open_finance_view("regime")

    def _finance_save_field(self, key: str, val: str):
        kwargs = {key: val}
        database.set_fiscal_config(USERNAME, **kwargs)
        self._show_toast(f"{key} enregistré")
        # Validation pour SIRET / IBAN — feedback immediat
        if key == "siret" and val and not finance.validate_siret(val):
            self._show_toast("⚠ SIRET invalide (Luhn)")
        elif key == "iban" and val and not finance.validate_iban(val):
            self._show_toast("⚠ IBAN invalide (mod-97)")

    # ============================================================
    # Vue Actions / Trading
    # ============================================================

    def _render_finance_actions(self):
        """Vue ACTIONS : compte broker, positions, watchlist, ordre rapide.

        Tous les ordres passent par permissions_mixin (sauf Lacher Prise).
        """
        from modules import actions as _act

        # Bandeau warning si en live trading
        cfg = database.get_broker_config(USERNAME) or {}
        broker_code = cfg.get("broker", "none")
        if broker_code == "alpaca_live":
            warn = ctk.CTkFrame(self.finance_body, fg_color="#3a0000",
                                corner_radius=8)
            warn.pack(fill="x", padx=4, pady=(2, 8))
            ctk.CTkLabel(warn, text=("⚠ LIVE TRADING — argent reel. "
                                     "Chaque ordre engage tes finances."),
                         font=("Futura", 10, "bold"),
                         text_color="#ffaaaa").pack(padx=10, pady=8)

        # Snapshot
        try:
            snap = _act.portfolio_snapshot(USERNAME)
        except Exception as e:
            ctk.CTkLabel(self.finance_body,
                         text=f"Erreur portfolio : {e}",
                         text_color=couleur_permissions).pack(pady=20)
            snap = None

        if not snap or not snap.get("is_connected"):
            self._render_actions_setup_panel()
            return

        # En-tete : equity / cash / day PL
        acc = snap["account"]
        head = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                            corner_radius=10, border_width=1,
                            border_color="#3aff7a")
        head.pack(fill="x", padx=4, pady=(0, 8))
        mode_lbl = "PAPER" if snap.get("is_paper") else "LIVE"
        title = (f"{snap.get('broker_name','?').upper()} — {mode_lbl}")
        ctk.CTkLabel(head, text=title, font=("Futura", 10, "bold"),
                     text_color="#3aff7a").pack(anchor="w", padx=10,
                                                pady=(8, 0))
        ctk.CTkLabel(
            head, text=(f"Equity : {acc.get('equity',0):.2f} {acc.get('currency','USD')}    "
                        f"Cash : {acc.get('cash',0):.2f}    "
                        f"PL jour : {snap.get('day_pl',0):+.2f}"),
            font=("Segoe UI", 10), text_color="#cccccc").pack(
            anchor="w", padx=10, pady=(0, 8))

        # Boutons d'action
        bar = ctk.CTkFrame(self.finance_body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(bar, text="+ Passer un ordre",
                      fg_color="#1a4020", hover_color="#3aff7a",
                      text_color="#3aff7a",
                      command=self._actions_open_order_form).pack(
            side="left", padx=4)
        ctk.CTkButton(bar, text="↻ Rafraichir",
                      fg_color="transparent", border_width=1,
                      border_color=couleur_accent,
                      text_color=couleur_accent,
                      command=lambda: self._open_finance_view("actions")).pack(
            side="left", padx=4)
        ctk.CTkButton(bar, text="⚙ Broker",
                      fg_color="transparent", border_width=1,
                      border_color=couleur_gold,
                      text_color=couleur_gold,
                      command=self._actions_open_broker_setup).pack(
            side="left", padx=4)

        # --- Positions ---
        positions = snap.get("positions") or []
        ctk.CTkLabel(self.finance_body,
                     text=f"POSITIONS ({len(positions)})",
                     font=("Futura", 10, "bold"),
                     text_color="#3aff7a").pack(anchor="w", padx=4,
                                                 pady=(8, 4))
        if not positions:
            ctk.CTkLabel(self.finance_body,
                         text="Aucune position en cours.",
                         text_color="#666").pack(pady=4)
        for p in positions:
            self._actions_position_card(p)

        # --- Watchlist ---
        wl = _act.list_watchlist()
        ctk.CTkLabel(self.finance_body,
                     text=f"WATCHLIST ({len(wl)})",
                     font=("Futura", 10, "bold"),
                     text_color=couleur_accent).pack(anchor="w", padx=4,
                                                     pady=(12, 4))
        wl_bar = ctk.CTkFrame(self.finance_body, fg_color="transparent")
        wl_bar.pack(fill="x")
        wl_entry = ctk.CTkEntry(wl_bar, placeholder_text="Symbole (AAPL, BTCUSDT...)",
                                width=180)
        wl_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(wl_bar, text="+", width=30,
                      fg_color=couleur_accent, text_color="#000",
                      command=lambda: (
                          _act.add_to_watchlist(wl_entry.get().strip()),
                          self._open_finance_view("actions"))).pack(side="left")
        for w in wl:
            self._actions_watchlist_card(w)

        # --- Historique ordres (5 derniers) ---
        orders = _act.list_orders(limit=5)
        if orders:
            ctk.CTkLabel(self.finance_body, text="ORDRES RECENTS",
                         font=("Futura", 10, "bold"),
                         text_color="#cccccc").pack(anchor="w", padx=4,
                                                     pady=(12, 4))
            for o in orders:
                self._actions_order_card(o)

    def _render_actions_setup_panel(self):
        """Affiche un panneau d'aide quand aucun broker n'est configure."""
        ctk.CTkLabel(
            self.finance_body,
            text="Aucun broker connecte",
            font=("Futura", 13, "bold"),
            text_color=couleur_permissions).pack(pady=(20, 6))
        ctk.CTkLabel(
            self.finance_body,
            text=(
                "Pour passer des ordres reels ou en simulation, "
                "configure un broker.\n\n"
                "Recommande pour debuter : Alpaca Paper (gratuit, "
                "simulation realiste, US stocks).\n"
                "Crypto : Binance.\n\n"
                "Les cles API sont stockees dans le keyring ou en variables\n"
                "d'environnement (jamais dans users_db.json)."),
            justify="left", text_color="#aaa",
            font=("Segoe UI", 10),
            wraplength=460).pack(padx=12, pady=8)
        ctk.CTkButton(
            self.finance_body, text="⚙ Configurer le broker",
            fg_color=couleur_gold, text_color="#000",
            command=self._actions_open_broker_setup).pack(pady=12)

    def _actions_position_card(self, p):
        col = "#3aff7a" if p.unrealized_pl >= 0 else "#ff6b6b"
        sign = "+" if p.unrealized_pl >= 0 else ""
        card = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                            corner_radius=8, border_width=1,
                            border_color=col)
        card.pack(fill="x", padx=4, pady=2)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(top, text=p.symbol, font=("Futura", 11, "bold"),
                     text_color="#cccccc").pack(side="left")
        ctk.CTkLabel(top, text=f"{p.qty:g} @ {p.avg_price:.2f}",
                     font=("Segoe UI", 9),
                     text_color="#888").pack(side="left", padx=10)
        ctk.CTkLabel(
            top, text=f"{sign}{p.unrealized_pl:.2f} {p.currency} "
                     f"({sign}{p.unrealized_pl_pct:.2f}%)",
            font=("Futura", 11, "bold"),
            text_color=col).pack(side="right")
        # Boutons fermer la position
        bot = ctk.CTkFrame(card, fg_color="transparent")
        bot.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(
            bot, text="Fermer position", width=120, height=24,
            fg_color="transparent", border_width=1,
            border_color=couleur_permissions,
            text_color=couleur_permissions,
            command=lambda sym=p.symbol, q=p.qty:
                self._actions_request_order(sym, q, "sell")).pack(
            side="left")

    def _actions_watchlist_card(self, w):
        from modules import actions as _act
        sym = w.get("symbol", "")
        try:
            broker = _act.get_broker(USERNAME)
            quote = broker.get_quote(sym)
        except Exception:
            quote = 0.0
        card = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                            corner_radius=8, border_width=1,
                            border_color="#1a1a2a")
        card.pack(fill="x", padx=4, pady=2)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(row, text=sym, font=("Futura", 11, "bold"),
                     text_color=couleur_accent).pack(side="left")
        ctk.CTkLabel(row, text=f"{quote:.2f}" if quote else "—",
                     font=("Segoe UI", 10),
                     text_color="#cccccc").pack(side="left", padx=12)
        ctk.CTkButton(
            row, text="Acheter", width=70, height=22,
            fg_color="#1a4020", hover_color="#3aff7a",
            text_color="#3aff7a",
            command=lambda s=sym: self._actions_open_order_form(symbol=s)).pack(
            side="right", padx=2)
        ctk.CTkButton(
            row, text="✕", width=22, height=22,
            fg_color="transparent", text_color="#aa3333",
            command=lambda s=sym: (_act.remove_from_watchlist(s),
                                   self._open_finance_view("actions"))).pack(
            side="right", padx=2)

    def _actions_order_card(self, o):
        col_status = {"filled": "#3aff7a", "submitted": couleur_accent,
                      "pending": couleur_gold, "rejected": couleur_permissions,
                      "cancelled": "#888888"}.get(o.status, "#888")
        card = ctk.CTkFrame(self.finance_body, fg_color="#0a0a14",
                            corner_radius=8, border_width=1,
                            border_color=col_status)
        card.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(
            card,
            text=(f"{o.side.upper()} {o.qty:g} {o.symbol} — "
                  f"{o.status} ({o.requested_at[:19]})"),
            font=("Segoe UI", 9), text_color="#cccccc").pack(
            anchor="w", padx=10, pady=4)
        if o.notes:
            ctk.CTkLabel(card, text=o.notes, font=("Segoe UI", 8),
                         text_color="#aa3333").pack(anchor="w", padx=10,
                                                     pady=(0, 4))

    def _actions_open_order_form(self, symbol: str = ""):
        """Ouvre une popup tk pour saisir un ordre. Cree une perm
        actions_buy/sell ou execute direct en lacher prise."""
        win = ctk.CTkToplevel(self.root)
        win.title("Nouvel ordre")
        win.geometry("360x340")
        win.attributes("-topmost", True)
        win.configure(fg_color="#0a0a14")
        ctk.CTkLabel(win, text="📈  Nouvel ordre",
                     font=("Futura", 14, "bold"),
                     text_color="#3aff7a").pack(pady=10)

        # Symbole
        sym_e = ctk.CTkEntry(win, placeholder_text="Symbole (AAPL, BTCUSDT...)",
                             width=300)
        if symbol:
            sym_e.insert(0, symbol)
        sym_e.pack(pady=6)
        # Cote
        side_var = tk.StringVar(value="buy")
        side_row = ctk.CTkFrame(win, fg_color="transparent")
        side_row.pack(pady=4)
        ctk.CTkRadioButton(side_row, text="ACHAT",
                           variable=side_var, value="buy",
                           text_color="#3aff7a").pack(side="left", padx=8)
        ctk.CTkRadioButton(side_row, text="VENTE",
                           variable=side_var, value="sell",
                           text_color=couleur_permissions).pack(
            side="left", padx=8)
        # Quantite
        qty_e = ctk.CTkEntry(win, placeholder_text="Quantite (ex: 1, 0.05...)",
                             width=300)
        qty_e.pack(pady=6)
        # Type
        type_var = tk.StringVar(value="market")
        type_row = ctk.CTkFrame(win, fg_color="transparent")
        type_row.pack(pady=4)
        ctk.CTkRadioButton(type_row, text="MARKET",
                           variable=type_var,
                           value="market").pack(side="left", padx=8)
        ctk.CTkRadioButton(type_row, text="LIMIT",
                           variable=type_var,
                           value="limit").pack(side="left", padx=8)
        limit_e = ctk.CTkEntry(win, placeholder_text="Prix limite (LIMIT seul.)",
                               width=300)
        limit_e.pack(pady=6)

        def _do_submit():
            sym = sym_e.get().strip().upper()
            try:
                qty = float(qty_e.get().strip().replace(",", "."))
            except Exception:
                return
            if not sym or qty <= 0:
                return
            limit_price = 0.0
            if type_var.get() == "limit":
                try:
                    limit_price = float(limit_e.get().strip().replace(",", "."))
                except Exception:
                    return
            self._actions_request_order(sym, qty, side_var.get(),
                                        order_type=type_var.get(),
                                        limit_price=limit_price)
            win.destroy()

        ctk.CTkButton(win, text="Envoyer", height=36,
                      fg_color="#1a4020", text_color="#3aff7a",
                      command=_do_submit).pack(pady=10)

    def _actions_request_order(self, symbol: str, qty: float, side: str,
                               order_type: str = "market",
                               limit_price: float = 0.0):
        """Selon le mode, execute direct ou cree une permission distinctive."""
        from modules import actions as _act
        # Estimation prix (pour le descriptif de la perm)
        try:
            broker = _act.get_broker(USERNAME)
            est_price = (limit_price if order_type == "limit"
                         else broker.get_quote(symbol))
        except Exception:
            est_price = 0.0
        notional = qty * est_price if est_price else 0.0
        cur = "USD" if (broker and broker.name == "alpaca") else "USDT"

        if getattr(self, "lacher_prise_mode", False):
            def _do_exec():
                try:
                    res = _act.execute_order(
                        USERNAME, symbol, qty, side,
                        order_type=order_type, limit_price=limit_price)
                    msg = (f"✓ {side.upper()} {qty:g} {symbol}"
                           if res.get("ok")
                           else f"✕ {res.get('error','?')[:80]}")
                    self.root.after(0, lambda m=msg: self._show_toast(m))
                except Exception as e:
                    print(f"Erreur execute_order : {e}")
            threading.Thread(target=_do_exec, daemon=True).start()
        else:
            perm_type = ("actions_buy" if side == "buy"
                         else "actions_sell")
            title = (f"📈 {side.upper()} {qty:g} {symbol} "
                     f"(~{notional:.0f} {cur})")
            desc = (f"Type : {order_type}"
                    + (f" @ {limit_price}" if limit_price else "")
                    + f"    Prix estime : {est_price:.2f} {cur}")
            database.add_permission_request(
                USERNAME, perm_type, title, desc,
                {
                    "symbol": symbol, "qty": qty, "side": side,
                    "order_type": order_type, "limit_price": limit_price,
                    "highlight_finance": True,
                })
            self._show_toast("Ordre en attente dans Permissions")

    def _actions_open_broker_setup(self):
        """Popup pour configurer le broker (choix + cles API + safety)."""
        win = ctk.CTkToplevel(self.root)
        win.title("Configuration broker")
        win.geometry("420x520")
        win.attributes("-topmost", True)
        win.configure(fg_color="#0a0a14")
        ctk.CTkLabel(win, text="⚙ Configuration broker",
                     font=("Futura", 14, "bold"),
                     text_color=couleur_gold).pack(pady=10)

        cfg = database.get_broker_config(USERNAME) or {}
        broker_var = tk.StringVar(value=cfg.get("broker", "none"))

        ctk.CTkLabel(win, text="Broker :", text_color="#aaa").pack(
            anchor="w", padx=20, pady=(6, 0))
        for code, label in [
            ("none",          "Aucun (lecture seule via yfinance)"),
            ("alpaca_paper",  "Alpaca PAPER (gratuit, simulation)"),
            ("alpaca_live",   "Alpaca LIVE (vrai argent ⚠)"),
            ("binance",       "Binance (crypto)"),
        ]:
            ctk.CTkRadioButton(win, text=label, variable=broker_var,
                               value=code,
                               text_color="#cccccc").pack(
                anchor="w", padx=30, pady=2)

        ctk.CTkLabel(win, text="Cles API : a mettre dans .env ou keyring.",
                     font=("Segoe UI", 9),
                     text_color="#888").pack(padx=20, pady=(10, 2))
        ctk.CTkLabel(
            win,
            text=("ASSISTANT_ALPACA_KEY / _SECRET\n"
                  "ASSISTANT_BINANCE_KEY / _SECRET\n"
                  "(Voir .env.example)"),
            font=("Consolas", 8),
            text_color="#666",
            justify="left").pack(padx=20)

        # Safety rails
        ctk.CTkLabel(win, text="\nSafety rails (Lacher Prise) :",
                     text_color="#aaa").pack(anchor="w", padx=20)
        max_eur_e = ctk.CTkEntry(win, placeholder_text="Montant max / ordre (€)",
                                 width=300)
        if cfg.get("max_order_eur"):
            max_eur_e.insert(0, str(cfg["max_order_eur"]))
        max_eur_e.pack(pady=4)
        max_d_e = ctk.CTkEntry(win, placeholder_text="Nb max d'ordres / jour",
                               width=300)
        if cfg.get("max_orders_day"):
            max_d_e.insert(0, str(cfg["max_orders_day"]))
        max_d_e.pack(pady=4)

        def _save():
            try:
                me = float(max_eur_e.get()) if max_eur_e.get().strip() else 0.0
            except Exception:
                me = 0.0
            try:
                md = int(max_d_e.get()) if max_d_e.get().strip() else 0
            except Exception:
                md = 0
            database.set_broker_config(USERNAME,
                                       broker=broker_var.get(),
                                       max_order_eur=me,
                                       max_orders_day=md)
            self._show_toast("Broker enregistre")
            win.destroy()
            self._open_finance_view("actions")

        ctk.CTkButton(win, text="Enregistrer", height=36,
                      fg_color=couleur_gold, text_color="#000",
                      command=_save).pack(pady=14)

    # ============================================================
    # Wizard de premier lancement (regime fiscal)
    # ============================================================

    def maybe_show_fiscal_wizard(self):
        """Si l'utilisateur n'a jamais defini de regime, lui propose
        d'aller dans la vue Regime du hub finance. Non bloquant."""
        if database.has_fiscal_config(USERNAME):
            return
        try:
            self._show_toast(
                "Astuce : configure ton régime fiscal "
                "(Hub > FINANCE > RÉGIME)")
        except Exception:
            pass

    # ============================================================
    # Helpers fallback (au cas ou _show_toast n'est pas defini)
    # ============================================================
    # NB : _show_toast et _hide_all_frames sont fournis par les autres
    # mixins. Si ce mixin est utilise seul, on les complete ici.
    def _finance_safe_toast(self, msg: str):
        try:
            self._show_toast(msg)
        except Exception:
            print(f"[finance] {msg}")
