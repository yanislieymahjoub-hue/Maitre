import tkinter as tk
import customtkinter as ctk
import pywinstyles
import subprocess
import sys
import os
import ctypes
import time
import database
import keyboard

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ==========================================
#        PARAMÈTRES VISUELS
# ==========================================
couleur_accent   = "#22b9ff"
couleur_sombre   = "#004488"
couleur_erreur   = "#ff4444"
couleur_succes   = "#44ff88"
couleur_gold     = "#ffd700"   # Couleur niveau 2 (Super-admin / Père)
Assistant_SCRIPT = "Assistant.pyw"

ROLES_METIER = ["", "Finance", "RH", "Manager", "Technique", "Commercial", "Marketing", "Juridique", "Direction", "Support", "Autre"]

# Régimes fiscaux (cf. database.FISCAL_REGIMES). Code -> label affiche.
FISCAL_REGIMES_LABELS = [
    ("none",                    "Aucun (configurer plus tard)"),
    ("auto_entrepreneur_bic",   "Auto-entrepreneur — Vente / Commerce"),
    ("auto_entrepreneur_bnc",   "Auto-entrepreneur — Prestations services"),
    ("auto_entrepreneur_lib",   "Auto-entrepreneur — Libéral"),
    ("sas",                     "Société — SAS / SASU (IS)"),
    ("sarl",                    "Société — SARL / EURL (IS)"),
    ("bnc_reel",                "Profession libérale (BNC réel)"),
]

# Préférences IA
AI_TONES_LABELS = [
    ("neutre",       "Neutre"),
    ("direct",       "Direct"),
    ("pedagogue",    "Pédagogue"),
    ("decontracte",  "Décontracté"),
    ("formel",       "Formel"),
]
AI_LENGTHS_LABELS = [
    ("court",  "Concis"),
    ("moyen",  "Équilibré"),
    ("long",   "Détaillé"),
]

ctk.set_appearance_mode("dark")


class LoginWindow:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "#010101")
        self.root.configure(fg_color="#010101")

        user32 = ctypes.windll.user32
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        w, h = 400, 580
        self.root.geometry(f"{w}x{h}+{sw//2 - w//2}+{sh//2 - h//2}")

        self._ox, self._oy = 0, 0
        self.root.bind("<Button-1>", self._click)
        self.root.bind("<B1-Motion>", self._drag)

        self.current_user = None

        self.main_container = ctk.CTkFrame(
            self.root, fg_color="#0a0a12",
            border_color=couleur_accent, border_width=2, corner_radius=30
        )
        self.main_container.pack(fill="both", expand=True)

        self._show_login_frame()

        pywinstyles.apply_style(self.main_container, "dark")
        self.root.withdraw()
        self._visible = False
        keyboard.add_hotkey("ctrl+alt+l", self.toggle_login)

        if not database.user_exists("admin"):
            database.create_user("admin", "admin", permissions=[0], role="Direction", business_role="Super-administrateur")

    def _clear_container(self):
        for widget in self.main_container.winfo_children():
            widget.destroy()

    def _show_login_frame(self):
        self._clear_container()

        ctk.CTkButton(
            self.main_container, text="✕", width=30, height=30,
            fg_color="transparent", hover_color="#330000",
            text_color="#888888", command=self.root.destroy
        ).place(x=355, y=15)

        # Logo animé
        ctk.CTkLabel(
            self.main_container, text="◈",
            font=("Arial", 64), text_color=couleur_accent
        ).pack(pady=(44, 0))

        ctk.CTkLabel(
            self.main_container, text="NEURAL ASSISTANT",
            font=("Futura", 20, "bold"), text_color=couleur_accent
        ).pack(pady=(4, 0))

        ctk.CTkLabel(
            self.main_container, text="Système de Sécurité Avancé",
            font=("Segoe UI", 9), text_color="#444466"
        ).pack(pady=(0, 28))

        self.entry_user = ctk.CTkEntry(
            self.main_container, placeholder_text="IDENTIFIANT",
            fg_color="#111122", border_color=couleur_sombre, border_width=1,
            height=46, corner_radius=23, width=300, font=("Segoe UI", 12)
        )
        self.entry_user.pack(pady=8)

        self.entry_pass = ctk.CTkEntry(
            self.main_container, placeholder_text="MOT DE PASSE",
            fg_color="#111122", border_color=couleur_sombre, border_width=1,
            height=46, corner_radius=23, width=300, show="●", font=("Segoe UI", 12)
        )
        self.entry_pass.pack(pady=8)

        self.lbl_status = ctk.CTkLabel(
            self.main_container, text="",
            font=("Segoe UI", 11), text_color=couleur_erreur
        )
        self.lbl_status.pack(pady=4)

        ctk.CTkButton(
            self.main_container, text="CONNEXION",
            fg_color=couleur_sombre, hover_color=couleur_accent,
            corner_radius=23, height=46, width=300,
            font=("Futura", 14, "bold"), command=self._action_login
        ).pack(pady=(12, 6))

        tk.Frame(self.main_container, bg="#1a1a2e", height=1).pack(fill="x", padx=40, pady=14)

        ctk.CTkButton(
            self.main_container, text="CRÉER UN COMPTE",
            fg_color="transparent", border_color=couleur_sombre, border_width=1,
            hover_color="#111122", corner_radius=23, height=42, width=300,
            font=("Futura", 11), command=self._action_register
        ).pack(pady=4)

        # Bind Enter
        self.entry_user.bind("<Return>", lambda e: self.entry_pass.focus_set())
        self.entry_pass.bind("<Return>", lambda e: self._action_login())

    def _show_register_frame(self):
        """Étape 1/4 — Identité : identifiant, mot de passe, prénom, nom, rôle, métier."""
        self._clear_container()

        ctk.CTkButton(
            self.main_container, text="← Retour", width=80, height=28,
            fg_color="transparent", hover_color="#1a1a2e",
            text_color=couleur_accent, font=("Futura", 10),
            command=self._show_login_frame
        ).place(x=15, y=15)

        ctk.CTkLabel(
            self.main_container, text="◈",
            font=("Arial", 36), text_color=couleur_succes
        ).pack(pady=(28, 0))

        ctk.CTkLabel(
            self.main_container, text="NOUVEAU COMPTE",
            font=("Futura", 16, "bold"), text_color=couleur_succes
        ).pack(pady=(2, 0))
        ctk.CTkLabel(
            self.main_container, text="Étape 1/4 — Identité",
            font=("Segoe UI", 9), text_color="#555577"
        ).pack(pady=(0, 12))

        self.reg_entry_user = ctk.CTkEntry(
            self.main_container, placeholder_text="IDENTIFIANT",
            fg_color="#111122", border_color="#1a3a1a", border_width=1,
            height=38, corner_radius=19, width=300
        )
        self.reg_entry_user.pack(pady=4)

        self.reg_entry_pass = ctk.CTkEntry(
            self.main_container, placeholder_text="MOT DE PASSE",
            fg_color="#111122", border_color="#1a3a1a", border_width=1,
            height=38, corner_radius=19, width=300, show="●"
        )
        self.reg_entry_pass.pack(pady=4)

        # Prénom et nom (ligne)
        name_row = ctk.CTkFrame(self.main_container, fg_color="transparent")
        name_row.pack(pady=4)
        self.reg_entry_first = ctk.CTkEntry(
            name_row, placeholder_text="Prénom",
            fg_color="#111122", border_color="#1a3a1a", border_width=1,
            height=38, corner_radius=19, width=145)
        self.reg_entry_first.pack(side="left", padx=(0, 5))
        self.reg_entry_last = ctk.CTkEntry(
            name_row, placeholder_text="Nom",
            fg_color="#111122", border_color="#1a3a1a", border_width=1,
            height=38, corner_radius=19, width=145)
        self.reg_entry_last.pack(side="left")

        # Rôle métier
        ctk.CTkLabel(
            self.main_container, text="Rôle dans l'entreprise",
            font=("Segoe UI", 9), text_color="#555577"
        ).pack(pady=(8, 2))
        self.reg_role_var = tk.StringVar(value="")
        self.reg_role_menu = ctk.CTkOptionMenu(
            self.main_container, values=ROLES_METIER,
            variable=self.reg_role_var,
            fg_color="#111122", button_color=couleur_sombre,
            button_hover_color=couleur_accent,
            dropdown_fg_color="#0d0d1a",
            width=300, height=36, corner_radius=18)
        self.reg_role_menu.pack(pady=2)

        self.reg_entry_bizdesc = ctk.CTkEntry(
            self.main_container, placeholder_text="Description du métier (optionnel)",
            fg_color="#111122", border_color="#1a1a3a", border_width=1,
            height=34, corner_radius=17, width=300)
        self.reg_entry_bizdesc.pack(pady=4)

        self.lbl_reg_status = ctk.CTkLabel(
            self.main_container, text="",
            font=("Segoe UI", 10), text_color=couleur_erreur)
        self.lbl_reg_status.pack(pady=2)

        ctk.CTkButton(
            self.main_container, text="SUIVANT →",
            fg_color="#1a3a1a", hover_color=couleur_succes,
            text_color=couleur_succes,
            corner_radius=20, height=40, width=300,
            font=("Futura", 12, "bold"), command=self._do_register
        ).pack(pady=8)

    def _show_email_setup_frame(self):
        """Étape 2/4 — Configuration email IMAP."""
        self._clear_container()

        ctk.CTkLabel(
            self.main_container, text="✉",
            font=("Arial", 40), text_color=couleur_accent
        ).pack(pady=(28, 0))

        ctk.CTkLabel(
            self.main_container, text="CONFIGURATION EMAIL",
            font=("Futura", 16, "bold"), text_color=couleur_accent
        ).pack(pady=(2, 0))

        is_new = getattr(self, "is_new_account", False)
        if is_new:
            ctk.CTkLabel(
                self.main_container, text="Étape 2/4 — Email + IMAP",
                font=("Segoe UI", 9), text_color="#555577"
            ).pack(pady=(0, 4))

        role = database.get_user_role(self.current_user)
        role_txt = f"  [{role}]" if role else ""
        ctk.CTkLabel(
            self.main_container,
            text=f"Utilisateur : {self.current_user}{role_txt}",
            font=("Segoe UI", 10), text_color="#556688"
        ).pack(pady=(4, 12))

        self.entry_email = ctk.CTkEntry(
            self.main_container, placeholder_text="VOTRE ADRESSE EMAIL",
            fg_color="#111122", height=42, corner_radius=21, width=300)
        self.entry_email.pack(pady=4)

        self.entry_app_pass = ctk.CTkEntry(
            self.main_container, placeholder_text="MOT DE PASSE D'APPLICATION",
            fg_color="#111122", height=42, corner_radius=21, width=300,
            show="●")
        self.entry_app_pass.pack(pady=4)

        ctk.CTkLabel(
            self.main_container,
            text=("Gmail / Outlook / Yahoo : utilise un mot de passe\n"
                  "d'application (pas le mot de passe principal)."),
            font=("Segoe UI", 8), text_color="#444466",
            justify="center").pack(pady=4)

        self.lbl_email_status = ctk.CTkLabel(
            self.main_container, text="",
            font=("Segoe UI", 10), text_color=couleur_erreur)
        self.lbl_email_status.pack(pady=2)

        level = database.get_user_level(self.current_user)
        btn_color = (couleur_gold if level == 2
                     else (couleur_sombre if level == 0 else "#2a2a55"))
        btn_text = "SUIVANT →" if is_new else (
            "ACTIVER NEURAL MASTER" if level == 0
            else ("ACTIVER NEURAL SUPER" if level == 2
                  else "ACTIVER NEURAL SLAVE"))

        ctk.CTkButton(
            self.main_container, text=btn_text,
            fg_color=btn_color, hover_color=couleur_accent,
            corner_radius=21, height=42, width=300,
            font=("Futura", 12, "bold"),
            command=self._save_email_config).pack(pady=10)

        # En creation de compte, on peut sauter l'email pour le configurer plus tard.
        if is_new:
            ctk.CTkButton(
                self.main_container, text="Passer cette étape",
                fg_color="transparent", text_color="#888",
                hover_color="#1a1a2e", height=28,
                command=lambda: self._show_fiscal_setup_frame()
            ).pack(pady=4)

    def _show_fiscal_setup_frame(self):
        """Étape 3/4 — Régime fiscal (optionnel mais recommandé)."""
        self._clear_container()
        ctk.CTkLabel(
            self.main_container, text="€",
            font=("Arial", 38), text_color=couleur_gold).pack(pady=(28, 0))
        ctk.CTkLabel(
            self.main_container, text="RÉGIME FISCAL",
            font=("Futura", 16, "bold"),
            text_color=couleur_gold).pack(pady=(2, 0))
        ctk.CTkLabel(
            self.main_container,
            text="Étape 3/4 — Pour le hub Finance",
            font=("Segoe UI", 9),
            text_color="#555577").pack(pady=(0, 12))

        self.fiscal_regime_var = tk.StringVar(value="none")
        for code, label in FISCAL_REGIMES_LABELS:
            ctk.CTkRadioButton(
                self.main_container, text=label,
                variable=self.fiscal_regime_var, value=code,
                text_color="#cccccc",
                border_color=couleur_gold, fg_color=couleur_gold).pack(
                anchor="w", padx=40, pady=2)

        # Champs SIRET / IBAN / raison sociale (optionnels)
        ctk.CTkLabel(self.main_container,
                     text="\nOptionnel (peut être saisi plus tard)",
                     font=("Segoe UI", 9),
                     text_color="#555577").pack(pady=(6, 2))
        self.fiscal_raison_e = ctk.CTkEntry(
            self.main_container, placeholder_text="Raison sociale",
            width=300, height=32, corner_radius=16,
            fg_color="#111122")
        self.fiscal_raison_e.pack(pady=2)
        self.fiscal_siret_e = ctk.CTkEntry(
            self.main_container, placeholder_text="SIRET (14 chiffres)",
            width=300, height=32, corner_radius=16,
            fg_color="#111122")
        self.fiscal_siret_e.pack(pady=2)
        self.fiscal_iban_e = ctk.CTkEntry(
            self.main_container, placeholder_text="IBAN",
            width=300, height=32, corner_radius=16,
            fg_color="#111122")
        self.fiscal_iban_e.pack(pady=2)

        self.fiscal_franchise_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self.main_container,
            text="Franchise en base de TVA (auto-entrepreneur sous seuil)",
            variable=self.fiscal_franchise_var,
            text_color="#aaaaaa",
            border_color=couleur_gold,
            fg_color=couleur_gold).pack(anchor="w", padx=40, pady=8)

        ctk.CTkButton(
            self.main_container, text="SUIVANT →",
            fg_color="#3a2a00", hover_color=couleur_gold,
            text_color=couleur_gold,
            corner_radius=20, height=40, width=300,
            font=("Futura", 12, "bold"),
            command=self._save_fiscal_config).pack(pady=12)

    def _save_fiscal_config(self):
        try:
            database.set_fiscal_config(
                self.current_user,
                regime=self.fiscal_regime_var.get(),
                tva_franchise=self.fiscal_franchise_var.get(),
                siret=self.fiscal_siret_e.get().strip(),
                iban=self.fiscal_iban_e.get().strip(),
                raison_sociale=self.fiscal_raison_e.get().strip())
        except Exception as e:
            print(f"Save fiscal : {e}")
        self._show_ai_prefs_frame()

    def _show_ai_prefs_frame(self):
        """Étape 4/4 — Préférences IA (ton, longueur, langue, tutoiement)."""
        self._clear_container()
        ctk.CTkLabel(
            self.main_container, text="◈",
            font=("Arial", 38),
            text_color=couleur_accent).pack(pady=(28, 0))
        ctk.CTkLabel(
            self.main_container, text="PRÉFÉRENCES IA",
            font=("Futura", 16, "bold"),
            text_color=couleur_accent).pack(pady=(2, 0))
        ctk.CTkLabel(
            self.main_container, text="Étape 4/4 — Style de l'assistant",
            font=("Segoe UI", 9),
            text_color="#555577").pack(pady=(0, 14))

        # Ton
        ctk.CTkLabel(self.main_container, text="Ton :",
                     font=("Segoe UI", 10),
                     text_color="#aaa").pack(anchor="w", padx=40)
        self.ai_ton_var = tk.StringVar(value="neutre")
        ton_row = ctk.CTkFrame(self.main_container, fg_color="transparent")
        ton_row.pack(pady=2)
        for code, label in AI_TONES_LABELS:
            ctk.CTkRadioButton(
                ton_row, text=label, variable=self.ai_ton_var, value=code,
                text_color="#cccccc",
                border_color=couleur_accent,
                fg_color=couleur_accent).pack(side="left", padx=6)

        # Longueur
        ctk.CTkLabel(self.main_container, text="Longueur des réponses :",
                     font=("Segoe UI", 10),
                     text_color="#aaa").pack(anchor="w", padx=40, pady=(8, 2))
        self.ai_long_var = tk.StringVar(value="moyen")
        long_row = ctk.CTkFrame(self.main_container, fg_color="transparent")
        long_row.pack(pady=2)
        for code, label in AI_LENGTHS_LABELS:
            ctk.CTkRadioButton(
                long_row, text=label, variable=self.ai_long_var, value=code,
                text_color="#cccccc",
                border_color=couleur_accent,
                fg_color=couleur_accent).pack(side="left", padx=10)

        # Langue
        ctk.CTkLabel(self.main_container, text="Langue par défaut :",
                     font=("Segoe UI", 10),
                     text_color="#aaa").pack(anchor="w", padx=40, pady=(8, 2))
        self.ai_lang_var = tk.StringVar(value="fr")
        lang_row = ctk.CTkFrame(self.main_container, fg_color="transparent")
        lang_row.pack(pady=2)
        for code in ("fr", "en", "es", "de", "it"):
            ctk.CTkRadioButton(
                lang_row, text=code.upper(),
                variable=self.ai_lang_var, value=code,
                text_color="#cccccc",
                border_color=couleur_accent,
                fg_color=couleur_accent).pack(side="left", padx=8)

        # Tutoiement
        self.ai_tu_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self.main_container, text="Tutoiement",
            variable=self.ai_tu_var,
            text_color="#cccccc",
            border_color=couleur_accent,
            fg_color=couleur_accent).pack(pady=8)

        # Niveau technique
        ctk.CTkLabel(self.main_container, text="Niveau technique :",
                     font=("Segoe UI", 10),
                     text_color="#aaa").pack(anchor="w", padx=40, pady=(2, 2))
        self.ai_tech_var = tk.StringVar(value="moyen")
        tech_row = ctk.CTkFrame(self.main_container, fg_color="transparent")
        tech_row.pack(pady=2)
        for code, label in [("debutant", "Débutant"), ("moyen", "Moyen"),
                            ("expert", "Expert")]:
            ctk.CTkRadioButton(
                tech_row, text=label, variable=self.ai_tech_var, value=code,
                text_color="#cccccc",
                border_color=couleur_accent,
                fg_color=couleur_accent).pack(side="left", padx=10)

        ctk.CTkButton(
            self.main_container, text="TERMINER ✓",
            fg_color=couleur_sombre, hover_color=couleur_accent,
            corner_radius=20, height=40, width=300,
            font=("Futura", 12, "bold"),
            command=self._save_ai_prefs_and_launch).pack(pady=14)

    def _save_ai_prefs_and_launch(self):
        try:
            database.set_ai_preferences(
                self.current_user,
                ton=self.ai_ton_var.get(),
                longueur=self.ai_long_var.get(),
                langue=self.ai_lang_var.get(),
                tutoiement=self.ai_tu_var.get(),
                niveau_technique=self.ai_tech_var.get())
        except Exception as e:
            print(f"Save AI prefs : {e}")
        self._launch_slave(self.current_user)

    def _action_register(self):
        self._show_register_frame()

    def _do_register(self):
        user = self.reg_entry_user.get().strip()
        pw = self.reg_entry_pass.get().strip()
        role = self.reg_role_var.get().strip()
        biz = self.reg_entry_bizdesc.get().strip()
        first = self.reg_entry_first.get().strip()
        last = self.reg_entry_last.get().strip()

        if not user or not pw:
            self.lbl_reg_status.configure(text="IDENTIFIANT / MOT DE PASSE OBLIGATOIRES")
            return

        if database.create_user(user, pw, permissions=[1], role=role,
                                business_role=biz):
            self.current_user = user
            self.is_new_account = True  # active le mode wizard
            # Sauve le profil etendu (prenom / nom)
            try:
                if first or last:
                    database.set_profile(user, first_name=first,
                                         last_name=last)
            except Exception:
                pass
            self._show_email_setup_frame()
        else:
            self.lbl_reg_status.configure(text="UTILISATEUR DÉJÀ EXISTANT")
            self._shake()

    def _action_login(self):
        user = self.entry_user.get().strip()
        pw = self.entry_pass.get().strip()
        if database.verify_user(user, pw):
            self.current_user = user
            config = database.get_email_config(user)
            if config:
                self._launch_slave(user)
            else:
                self._show_email_setup_frame()
        else:
            self._error_feedback("IDENTIFIANTS INVALIDES")

    def _save_email_config(self):
        email_addr = self.entry_email.get().strip()
        app_pw = self.entry_app_pass.get().strip()
        if "@" not in email_addr or len(app_pw) < 4:
            self.lbl_email_status.configure(text="INFOS INVALIDES")
            return
        database.set_email_config(self.current_user, email_addr, app_pw)
        # Suite du wizard si on est en creation de compte ; sinon on lance.
        if getattr(self, "is_new_account", False):
            self._show_fiscal_setup_frame()
        else:
            self._launch_slave(self.current_user)

    def _error_feedback(self, message):
        self.lbl_status.configure(text=message, text_color=couleur_erreur)
        self._shake()

    def _shake(self):
        orig_x = self.root.winfo_x()
        for _ in range(5):
            self.root.geometry(f"+{orig_x + 8}+{self.root.winfo_y()}"); self.root.update(); time.sleep(0.02)
            self.root.geometry(f"+{orig_x - 8}+{self.root.winfo_y()}"); self.root.update(); time.sleep(0.02)
        self.root.geometry(f"+{orig_x}+{self.root.winfo_y()}")

    def _launch_slave(self, username):
        self.root.destroy()
        if os.path.exists(Assistant_SCRIPT):
            subprocess.Popen([sys.executable, Assistant_SCRIPT, username])

    def _click(self, e): self._ox, self._oy = e.x, e.y
    def _drag(self, e):
        self.root.geometry(f"+{self.root.winfo_pointerx() - self._ox}+{self.root.winfo_pointery() - self._oy}")

    def toggle_login(self):
        if self._visible:
            self.root.withdraw()
            self._visible = False
        else:
            self.root.deiconify()
            self.root.focus_force()
            self._visible = True


if __name__ == "__main__":
    LoginWindow().root.mainloop()
