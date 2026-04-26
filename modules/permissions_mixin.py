"""Demandes de permissions."""
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


class PermissionsMixin:
    def _create_mail_permission_request(self, raw_text):
        """En mode normal, crée une requête de permission pour l'envoi de mail."""
        try:
            block_m = re.search(r"COMMAND_START(.*?)COMMAND_END", raw_text, re.DOTALL)
            block = block_m.group(1) if block_m else raw_text
            to_m   = re.search(r"TO\s*:\s*([\w\.\-]+@[\w\.\-]+\.\w+)", block)
            sub_m  = re.search(r"SUBJECT\s*:\s*(.+)", block)
            body_m = re.search(r"BODY\s*:(.*?)$", block, re.DOTALL)
            dest  = to_m.group(1).strip() if to_m else None
            sujet = sub_m.group(1).strip() if sub_m else "Message"
            corps = body_m.group(1).strip() if body_m else ""
            if dest:
                database.add_permission_request(
                    USERNAME, "mail_send",
                    f"Envoyer mail à {dest}",
                    f"Sujet : {sujet}",
                    {"to": dest, "subject": sujet, "body": corps}
                )
                self.root.after(0, lambda: self.add_message("SYSTEM",
                    f"📨 Mail en attente → {dest}. Ouvrez Permissions (⚡) pour valider."))
        except Exception as e:
            print(f"Erreur permission request : {e}")

    def _create_agenda_permission_request(self, title, date_str, time_str, note, source="chat", source_mail_uid=None):
        """Cree une requete de permission pour ajouter un evenement a l'agenda.

        Appele quand lacher_prise_mode est OFF. En mode ON, add_agenda_event
        est appele directement.
        """
        try:
            time_display = time_str if time_str else "heure a definir"
            description = f"Date : {date_str} a {time_display}"
            if note:
                description += f"\n{note[:120]}"
            if source:
                description += f"\n(source : {source})"
            import database
            database.add_permission_request(
                USERNAME, "agenda_modify",
                f"Ajouter evenement : {title[:60]}",
                description,
                {
                    "title": title,
                    "date": date_str,
                    "time": time_str or "",
                    "note": note or "",
                    "source_mail_uid": str(source_mail_uid) if source_mail_uid else "",
                }
            )
            try:
                self.root.after(0, lambda: self._show_toast(
                    f"Nouvel evenement en attente dans Permissions"))
            except Exception:
                pass
        except Exception as e:
            print(f"Erreur create agenda permission : {e}")

    def _refresh_permissions(self):
        for w in self.permissions_scroll.winfo_children():
            w.destroy()
        reqs = database.get_pending_requests(None if USER_LEVEL == 0 else USERNAME)
        if not reqs:
            ctk.CTkLabel(self.permissions_scroll, text="Aucune requête en attente ✦",
                         font=("Segoe UI", 11), text_color="#331a1a").pack(pady=30)
            return
        for req in reqs:
            self._add_permission_card(req)

    def _add_permission_card(self, req):
        icons = {"mail_send":"✉","file_organize":"📁","agenda_modify":"📅","rule_create":"⚙","mail_delete":"🗑",
                 "mail_delete_spam":"🗑","mail_delete_receipt":"🗑","mail_bulk_delete_spam":"🧹",
                 "mail_bulk_archive":"📦","mail_unsubscribe":"⛔","mail_archive":"📦","mail_categorize":"🗂",
                 "mail_forward_son":"📨","mail_delete_triage":"🗑","mail_archive_triage":"📦",
                 "attachment_extract":"📎",
                 "finance_invoice_add":"💰","finance_invoice_validate":"💰",
                 "finance_relance_send":"🔔","finance_bank_import":"🏦",
                 "actions_buy":"📈","actions_sell":"📉","actions_close":"📉"}
        rtype = req.get("type", "")
        icon = icons.get(rtype, "⚡")

        action_data = req.get("action_data") or {}

        # Detection des cartes a mettre en valeur :
        # - attachment_extract et tous les finance_* sont prioritaires
        # - on peut aussi forcer via highlight=True ou highlight_finance=True
        is_finance = (rtype.startswith("finance_")
                      or rtype.startswith("actions_")
                      or bool(action_data.get("highlight_finance")))
        is_highlight = (rtype == "attachment_extract") or is_finance or bool(
            action_data.get("highlight"))

        # --- Style ---
        if is_highlight:
            card_bg     = "#1a1000"           # fond chaud, distinct du habituel
            card_border = couleur_gold        # bordure doree
            border_w    = 2
            side_color  = couleur_gold
            side_w      = 6
            title_color = couleur_gold
            title_font  = ("Futura", 13, "bold")
            desc_color  = "#bba577"
            inner_pady  = 14
        else:
            card_bg     = "#0d0005"
            card_border = "#3a0020"
            border_w    = 1
            side_color  = couleur_permissions
            side_w      = 4
            title_color = couleur_permissions
            title_font  = ("Futura", 11, "bold")
            desc_color  = "#887777"
            inner_pady  = 10

        card = ctk.CTkFrame(self.permissions_scroll, fg_color=card_bg,
                            corner_radius=14, border_width=border_w,
                            border_color=card_border)
        card.pack(fill="x", padx=8, pady=(8 if is_highlight else 5))
        tk.Frame(card, bg=side_color, width=side_w).pack(side="left", fill="y")
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True,
                     padx=12, pady=inner_pady)
        top = ctk.CTkFrame(content, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=f"{icon}  {req.get('title','?')}",
                     font=title_font, text_color=title_color).pack(side="left")
        # Badge "PIECE JOINTE" en haut a droite pour les cartes mises en valeur
        if is_highlight:
            ctk.CTkLabel(top, text="  PIECE JOINTE  ",
                         font=("Futura", 8, "bold"),
                         text_color="#1a1000",
                         fg_color=couleur_gold,
                         corner_radius=6).pack(side="right")
        else:
            ctk.CTkLabel(top, text=req.get("username",""),
                         font=("Futura", 8), text_color="#553333").pack(side="right")
        ctk.CTkLabel(content, text=req.get("description",""),
                     font=("Segoe UI", 9), text_color=desc_color,
                     wraplength=340, justify="left").pack(anchor="w", pady=(2, 6))
        btn_row = ctk.CTkFrame(content, fg_color="transparent")
        btn_row.pack(fill="x")
        req_id = req.get("id")
        accept_color = "#ffd166" if is_highlight else "#44ff88"
        ctk.CTkButton(btn_row, text="✔ Accepter",
                      width=110 if is_highlight else 100,
                      height=32 if is_highlight else 28,
                      fg_color="#1a0010", hover_color=accept_color,
                      text_color=accept_color,
                      corner_radius=14,
                      font=("Futura", 10 if is_highlight else 9, "bold"),
                      command=lambda r=req_id: self._resolve_req(r, "approved")).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_row, text="✕ Rejeter",
                      width=90,
                      height=32 if is_highlight else 28,
                      fg_color="transparent", hover_color="#330000",
                      text_color="#553333", corner_radius=14,
                      font=("Futura", 9),
                      command=lambda r=req_id: self._resolve_req(r, "rejected")).pack(side="left")

    def _resolve_req(self, req_id, status):
        resolved = database.resolve_permission_request(req_id, status)
        if resolved and status == "approved":
            self._execute_permission_action(resolved)
        elif resolved and status == "rejected":
            # Cas particulier : pour les PJ rejetees, il faut purger le
            # dossier _pending pour ne pas laisser de fichiers orphelins
            # sur disque.
            if resolved.get("type") == "attachment_extract":
                try:
                    from modules.attachment_extractor import discard_pending
                    pending = (resolved.get("action_data") or {}).get("pending_dir")
                    if pending:
                        discard_pending(pending)
                except Exception as e:
                    print(f"Erreur discard pending : {e}")
        self._refresh_permissions()

    def _execute_permission_action(self, req):
        action_type = req.get("type","")
        data = req.get("action_data",{})
        if action_type == "mail_send":
            threading.Thread(target=self.execute_auto_mail,
                             args=(data.get("to"), data.get("subject"), data.get("body")),
                             daemon=True).start()
        elif action_type == "agenda_modify":
            self.add_agenda_event(data.get("title","?"), data.get("date",""),
                                  data.get("time",""), data.get("note",""),
                                  source_mail_uid=data.get("source_mail_uid") or None)
        # ============ Actions d'optimisation inbox ============
        elif action_type in ("mail_delete_spam", "mail_delete_receipt"):
            threading.Thread(target=self.delete_mail_on_server,
                             args=(data.get("uid"), data.get("subject","")),
                             daemon=True).start()
        elif action_type == "mail_bulk_delete_spam":
            uids = data.get("uids", []) or []
            if uids:
                self.execute_bulk_delete_spam(uids)
        elif action_type == "mail_bulk_archive":
            uids = data.get("uids", []) or []
            if uids:
                def _bulk_arch():
                    for u in uids:
                        try:
                            self.archive_mail_on_server(u, "")
                        except Exception as e:
                            print(f"Erreur bulk archive : {e}")
                threading.Thread(target=_bulk_arch, daemon=True).start()
        elif action_type == "mail_unsubscribe":
            se = data.get("sender_email")
            if se:
                self.execute_unsubscribe(se)
        elif action_type == "mail_archive":
            threading.Thread(target=self.archive_mail_on_server,
                             args=(data.get("uid"), data.get("subject","")),
                             daemon=True).start()
        elif action_type == "mail_categorize":
            threading.Thread(target=self.categorize_mail_on_server,
                             args=(data.get("uid"), data.get("folder"),
                                   data.get("subject","")),
                             daemon=True).start()
        # ============ Actions de triage (forward fils / delete / archive) ============
        elif action_type == "mail_forward_son":
            threading.Thread(target=self.execute_auto_mail,
                             args=(data.get("to"), data.get("subject",""),
                                   data.get("body","")),
                             daemon=True).start()
        elif action_type == "mail_delete_triage":
            threading.Thread(target=self.delete_mail_on_server,
                             args=(data.get("uid"), data.get("subject","")),
                             daemon=True).start()
        elif action_type == "mail_archive_triage":
            threading.Thread(target=self.archive_mail_on_server,
                             args=(data.get("uid"), data.get("subject","")),
                             daemon=True).start()
        # ============ Sauvegarde de pieces jointes ============
        elif action_type == "attachment_extract":
            def _do_finalize():
                try:
                    from modules.attachment_extractor import finalize_pending
                    saved = finalize_pending(
                        data.get("pending_dir") or "",
                        data.get("sender") or "",
                        data.get("subject") or "",
                        data.get("date_str") or "",
                    )
                    if saved:
                        cats = ", ".join(sorted({s["category"] for s in saved}))
                        try:
                            self.root.after(0, lambda nn=len(saved), cc=cats:
                                self._show_toast(
                                    f"📎 {nn} PJ rangee(s) -> {cc}"))
                        except Exception:
                            pass
                        # Si une categorie "Factures" a ete attribuee,
                        # chaine vers l'extraction des champs facture.
                        try:
                            self._chain_invoice_from_attachments(saved, data)
                        except Exception as e:
                            print(f"Erreur chaine facture : {e}")
                except Exception as e:
                    print(f"Erreur finalize pending : {e}")
            threading.Thread(target=_do_finalize, daemon=True).start()
        # ============ Finance : ajout d'une facture ============
        elif action_type == "finance_invoice_add":
            def _add_inv():
                try:
                    from modules import finance as _fin
                    inv = _fin.Invoice(**data.get("invoice", {}))
                    new_id = _fin.add_invoice(inv)
                    if new_id:
                        self.root.after(0, lambda: self._show_toast(
                            f"💰 Facture ajoutée (#{new_id})"))
                    else:
                        self.root.after(0, lambda: self._show_toast(
                            "Doublon : facture ignorée"))
                except Exception as e:
                    print(f"Erreur finance_invoice_add : {e}")
            threading.Thread(target=_add_inv, daemon=True).start()
        # ============ Finance : envoi d'une relance ============
        elif action_type == "finance_relance_send":
            def _do_relance():
                try:
                    self.execute_auto_mail(
                        data.get("to"), data.get("subject"),
                        data.get("body"))
                    inv_id = data.get("invoice_id")
                    if inv_id:
                        from modules import finance as _fin
                        _fin.mark_relance_sent(inv_id)
                except Exception as e:
                    print(f"Erreur finance_relance_send : {e}")
            threading.Thread(target=_do_relance, daemon=True).start()
        # ============ Finance : import CSV bancaire ============
        elif action_type == "finance_bank_import":
            def _do_import():
                try:
                    from modules import finance as _fin
                    n = _fin.import_bank_csv(data.get("path") or "")
                    self.root.after(0, lambda nn=n: self._show_toast(
                        f"🏦 {nn} mouvement(s) importe(s)"))
                except Exception as e:
                    print(f"Erreur finance_bank_import : {e}")
            threading.Thread(target=_do_import, daemon=True).start()
        # ============ Actions / Trading ============
        elif action_type in ("actions_buy", "actions_sell", "actions_close"):
            def _do_order():
                try:
                    from modules import actions as _act
                    res = _act.execute_order(
                        USERNAME,
                        data.get("symbol", ""),
                        float(data.get("qty", 0) or 0),
                        data.get("side", "buy"),
                        order_type=data.get("order_type", "market"),
                        limit_price=float(data.get("limit_price", 0) or 0))
                    if res.get("ok"):
                        sym = data.get("symbol", "?")
                        side = data.get("side", "?").upper()
                        qty = data.get("qty", 0)
                        self.root.after(0,
                            lambda s=sym, sd=side, q=qty: self._show_toast(
                                f"📈 {sd} {q} {s} envoye"))
                    else:
                        err = res.get("error") or "echec"
                        self.root.after(0,
                            lambda e=err: self._show_toast(
                                f"✕ Ordre rejete : {e[:60]}"))
                except Exception as e:
                    print(f"Erreur dispatch action : {e}")
            threading.Thread(target=_do_order, daemon=True).start()
        # ============ Finance : validation manuelle d'une facture extraite ============
        elif action_type == "finance_invoice_validate":
            # Idem add (on a deja l'objet pre-rempli, on le confirme)
            def _validate():
                try:
                    from modules import finance as _fin
                    inv = _fin.Invoice(**data.get("invoice", {}))
                    new_id = _fin.add_invoice(inv)
                    if new_id:
                        self.root.after(0, lambda: self._show_toast(
                            f"💰 Facture validée (#{new_id})"))
                except Exception as e:
                    print(f"Erreur finance_invoice_validate : {e}")
            threading.Thread(target=_validate, daemon=True).start()

    def _chain_invoice_from_attachments(self, saved_files,
                                         attachment_data=None,
                                         sender=None, subject=None,
                                         uid=None):
        """Pour chaque fichier classe en 'Factures', tente d'extraire une
        Invoice. En mode lacher prise -> add_invoice direct. Sinon ->
        permission finance_invoice_validate avec donnees pre-remplies.

        saved_files : liste [{path, filename, category, size}, ...]
        attachment_data : (optionnel) dict de la permission attachment_extract.
        sender / subject / uid : alternative directe a attachment_data.
        """
        from modules import finance as _fin
        from dataclasses import asdict
        if attachment_data:
            sender = sender or attachment_data.get("sender") or ""
            subject = subject or attachment_data.get("subject") or ""
            uid = uid or attachment_data.get("uid") or ""
        sender = sender or ""
        subject = subject or ""
        uid = uid or ""
        user_email = getattr(self, "user_email", "") or ""
        for f in saved_files:
            if (f.get("category") or "").lower() != "factures":
                continue
            try:
                inv = _fin.extract_from_file(
                    f["path"], sender_email=sender,
                    subject=subject, user_email=user_email,
                    source_mail_uid=attachment_data.get("uid", ""))
                if not inv:
                    continue
                if getattr(self, "lacher_prise_mode", False):
                    new_id = _fin.add_invoice(inv)
                    if new_id:
                        try:
                            self.root.after(
                                0,
                                lambda nm=inv.party, mt=inv.montant_ttc:
                                self._show_toast(
                                    f"💰 Facture ajoutée : {nm} ({mt:.2f}€)"))
                        except Exception:
                            pass
                else:
                    inv_dict = asdict(inv)
                    # Retire les champs auto/internes pour la perm
                    for k in ("id", "created_at", "updated_at"):
                        inv_dict.pop(k, None)
                    flags = _fin.detect_anomaly(inv)
                    flag_txt = (" ⚠ " + " ".join(flags)) if flags else ""
                    database.add_permission_request(
                        USERNAME, "finance_invoice_validate",
                        f"💰 Valider facture : {inv.party or '?'} "
                        f"({inv.montant_ttc:.2f} {inv.currency})",
                        f"N° {inv.invoice_number or '?'}    "
                        f"Émise : {inv.date_emission or '?'}    "
                        f"Échéance : {inv.date_echeance or '?'}{flag_txt}",
                        {"invoice": inv_dict, "highlight_finance": True})
            except Exception as e:
                print(f"Chain invoice {f.get('filename')} : {e}")

    def _accept_all_permissions(self):
        reqs = database.get_pending_requests(None if USER_LEVEL == 0 else USERNAME)
        for req in reqs:
            resolved = database.resolve_permission_request(req["id"], "approved")
            if resolved:
                self._execute_permission_action(resolved)
        self._show_toast(f"✔ {len(reqs)} requête(s) acceptée(s) et exécutée(s).")
        self._refresh_permissions()
