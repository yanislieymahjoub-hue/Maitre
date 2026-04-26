"""Briefing matinal personnel : agrege tout en un seul message structure.

Sources :
- Inbox : mails urgents non traites (cache mail)
- Agenda : evenements du jour
- Finance : factures clients en retard, fournisseurs a payer
- Actions : perf portefeuille depuis hier
- Permissions : nb d'actions en attente
- Date / meteo (optionnel)

L'IA (Mistral / Ollama) synthetise les 3 priorites du jour a partir de ces
donnees brutes. Si le LLM est indispo, fallback texte brut sans priorisation.

API publique :
    generate_briefing(username) -> dict
        {
          "title": "BRIEFING — Lundi 26 avril",
          "sections": [{"label": ..., "items": [...]}, ...],
          "priorities": ["...", "...", "..."],
          "summary": "synthese 1-2 phrases",
          "raw_text": "version texte complete pretes a afficher"
        }
"""
from __future__ import annotations
import json
from datetime import datetime, date

from modules.logger import get_logger

_log = get_logger("morning_briefing")

_DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi",
            "samedi", "dimanche"]
_MONTHS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin",
              "juillet", "aout", "septembre", "octobre", "novembre",
              "decembre"]


# ===========================================================================
# Collecte des donnees brutes
# ===========================================================================

def _collect_inbox_urgent(username: str) -> list:
    """Renvoie les mails urgents non lus / non traites du cache."""
    try:
        from modules import mail_cache_db
        cache = mail_cache_db.load_cache(username)
    except Exception as e:
        _log.info("inbox cache : %s", e)
        return []
    urgent = []
    for uid, m in cache.items():
        if not isinstance(m, dict):
            continue
        urg = (m.get("urgency") or "").lower()
        seen = m.get("seen", False)
        # On garde haute urgence ET (non lu OU action en attente)
        if urg in ("haute", "urgent") and not seen:
            urgent.append(m)
    # Tri par date si dispo
    try:
        urgent.sort(key=lambda x: x.get("date_str", ""), reverse=True)
    except Exception:
        pass
    return urgent[:10]


def _collect_agenda_today(username: str) -> list:
    """Renvoie les evenements de l'agenda pour aujourd'hui."""
    today_iso = date.today().isoformat()
    agenda_file = f"agenda_{username}.json"
    try:
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, agenda_file)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            events = json.load(f)
    except Exception as e:
        _log.info("agenda : %s", e)
        return []
    out = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("date") == today_iso:
            out.append(ev)
    out.sort(key=lambda x: x.get("time", "00:00"))
    return out


def _collect_finance() -> dict:
    """Renvoie les chiffres finance cles : factures en retard, fournisseurs."""
    try:
        from modules import finance
        stats = finance.quick_stats()
        overdue = finance.list_overdue()
        return {
            "stats": stats,
            "overdue_invoices": overdue[:5],
        }
    except Exception as e:
        _log.info("finance : %s", e)
        return {"stats": {}, "overdue_invoices": []}


def _collect_portfolio(username: str) -> dict:
    """Renvoie un mini snapshot du portefeuille (si broker connecte)."""
    try:
        from modules import actions
        snap = actions.portfolio_snapshot(username)
        if not snap.get("is_connected"):
            return {}
        return {
            "broker": snap.get("broker_name"),
            "is_paper": snap.get("is_paper"),
            "equity": snap["account"].get("equity", 0),
            "cash": snap["account"].get("cash", 0),
            "currency": snap["account"].get("currency", ""),
            "day_pl": snap.get("day_pl", 0),
            "n_positions": len(snap.get("positions") or []),
        }
    except Exception as e:
        _log.info("portfolio : %s", e)
        return {}


def _collect_permissions(username: str) -> int:
    """Renvoie le nb de permissions en attente."""
    try:
        import database
        return len(database.get_pending_requests(username))
    except Exception as e:
        _log.info("perms : %s", e)
        return 0


# ===========================================================================
# Synthese LLM (3 priorites du jour)
# ===========================================================================

def _llm_synthesize(data: dict) -> dict:
    """Demande au LLM de produire 3 priorites + 1-2 phrases de synthese."""
    try:
        from modules.config import URL, API_KEY, MODELE
        import requests as _rq
        # On envoie les donnees brutes au LLM, qui en tire les priorites.
        prompt = (
            "Tu es l'assistant personnel. Voici les donnees du jour de ton "
            "utilisateur. Produis :\n"
            "1) Les 3 priorites concretes du jour (action verbe -> objet)\n"
            "2) Une synthese en 1-2 phrases\n\n"
            "Reponds en JSON STRICT : "
            '{"priorities": ["..", "..", ".."], "summary": ".."}\n\n'
            f"Date : {data.get('date_label')}\n"
            f"Mails urgents non traites : {len(data.get('urgent_mails') or [])}\n"
            f"RDV aujourd\\'hui : {len(data.get('agenda_today') or [])}\n"
            f"Factures clients en retard : "
            f"{data.get('finance', {}).get('stats', {}).get('factures_en_retard', 0)} "
            f"({data.get('finance', {}).get('stats', {}).get('montant_en_retard', 0)} €)\n"
            f"Permissions en attente : {data.get('pending_perms', 0)}\n"
            f"Portefeuille day PL : {data.get('portfolio', {}).get('day_pl', 0)}\n\n"
            f"Top mails (5 max) :\n"
            + "\n".join(
                f"- {(m.get('sender') or '')[:40]} : {(m.get('subject') or '')[:80]}"
                for m in (data.get('urgent_mails') or [])[:5])
            + "\n\nRDV :\n"
            + "\n".join(
                f"- {ev.get('time','?')} {ev.get('title','?')}"
                for ev in (data.get('agenda_today') or []))
        )
        headers = {"Authorization": f"Bearer {API_KEY}",
                   "Content-Type": "application/json"}
        body = {
            "model": MODELE, "temperature": 0.3,
            "messages": [
                {"role": "system",
                 "content": "Tu produis des briefings matinaux concis et actionnables."},
                {"role": "user", "content": prompt},
            ],
        }
        r = _rq.post(URL, headers=headers, json=body, timeout=20)
        if r.status_code == 200:
            txt = r.json()["choices"][0]["message"]["content"]
            # Trouve le 1er objet JSON dans la reponse
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", txt)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    pri = obj.get("priorities") or []
                    if isinstance(pri, list):
                        pri = [str(p) for p in pri[:3]]
                    summ = str(obj.get("summary") or "")
                    return {"priorities": pri, "summary": summ}
                except Exception:
                    pass
    except Exception as e:
        _log.info("llm synth : %s", e)
    return {"priorities": [], "summary": ""}


# ===========================================================================
# Mise en forme texte
# ===========================================================================

def _date_label(d: date = None) -> str:
    d = d or date.today()
    return f"{_DAYS_FR[d.weekday()].capitalize()} {d.day} {_MONTHS_FR[d.month - 1]}"


def _format_text(data: dict, synth: dict) -> str:
    lines = []
    lines.append(f"☀  BRIEFING — {data.get('date_label', '')}")
    lines.append("")

    # Synthese
    if synth.get("summary"):
        lines.append(f"   {synth['summary']}")
        lines.append("")

    # Priorites du jour
    pri = synth.get("priorities") or []
    if pri:
        lines.append("✦ PRIORITÉS DU JOUR")
        for i, p in enumerate(pri, 1):
            lines.append(f"   {i}. {p}")
        lines.append("")

    # RDV
    rdv = data.get("agenda_today") or []
    if rdv:
        lines.append(f"📅 AGENDA ({len(rdv)} RDV)")
        for ev in rdv:
            t = ev.get("time", "??:??")
            title = ev.get("title", "(sans titre)")
            place = ev.get("place") or ""
            extra = f"  @ {place}" if place else ""
            lines.append(f"   {t}  {title}{extra}")
        lines.append("")

    # Mails urgents
    mails = data.get("urgent_mails") or []
    if mails:
        lines.append(f"📨 MAILS URGENTS ({len(mails)})")
        for m in mails[:5]:
            sender = (m.get("sender") or "?")[:40]
            subj = (m.get("subject") or "")[:60]
            lines.append(f"   • {sender}  —  {subj}")
        if len(mails) > 5:
            lines.append(f"   ... et {len(mails) - 5} de plus")
        lines.append("")

    # Finance
    fin = data.get("finance") or {}
    stats = fin.get("stats") or {}
    if stats.get("factures_en_retard"):
        lines.append("💰 FINANCE")
        n = stats.get("factures_en_retard", 0)
        m = stats.get("montant_en_retard", 0)
        lines.append(f"   {n} facture(s) client en retard ({m:.0f} €)")
        if stats.get("fournisseurs_a_payer"):
            lines.append(
                f"   {stats['fournisseurs_a_payer']} facture(s) "
                "fournisseur a payer")
        lines.append("")

    # Portefeuille
    port = data.get("portfolio") or {}
    if port:
        sign = "+" if port.get("day_pl", 0) >= 0 else ""
        lines.append("📈 PORTEFEUILLE")
        mode = "PAPER" if port.get("is_paper") else "LIVE"
        lines.append(
            f"   {port.get('broker','?').upper()} {mode} : "
            f"{port.get('equity',0):.0f} {port.get('currency','')}  "
            f"({sign}{port.get('day_pl',0):.2f} sur la journee)")
        lines.append("")

    # Permissions
    pp = data.get("pending_perms", 0)
    if pp:
        lines.append(f"⚡ {pp} permission(s) en attente de validation")
        lines.append("")

    if not (rdv or mails or stats.get("factures_en_retard") or pp or pri):
        lines.append("   Rien d'urgent. Bonne journee.")

    return "\n".join(lines)


# ===========================================================================
# API publique
# ===========================================================================

def generate_briefing(username: str) -> dict:
    """Construit le briefing complet pour cet utilisateur."""
    data = {
        "date": date.today().isoformat(),
        "date_label": _date_label(),
        "urgent_mails": _collect_inbox_urgent(username),
        "agenda_today": _collect_agenda_today(username),
        "finance": _collect_finance(),
        "portfolio": _collect_portfolio(username),
        "pending_perms": _collect_permissions(username),
    }
    # Synthese LLM (peut renvoyer vide en cas d'echec)
    synth = _llm_synthesize(data)

    raw_text = _format_text(data, synth)

    # Sections structurees pour une UI riche eventuelle
    sections = []
    if data["agenda_today"]:
        sections.append({"label": "Agenda", "items": data["agenda_today"]})
    if data["urgent_mails"]:
        sections.append({"label": "Mails urgents",
                         "items": data["urgent_mails"]})
    if data["finance"]["stats"].get("factures_en_retard"):
        sections.append({"label": "Finance",
                         "items": data["finance"]["overdue_invoices"]})
    if data["portfolio"]:
        sections.append({"label": "Portefeuille",
                         "items": [data["portfolio"]]})
    if data["pending_perms"]:
        sections.append({"label": "Permissions",
                         "items": [{"count": data["pending_perms"]}]})

    return {
        "title": f"BRIEFING — {data['date_label']}",
        "date": data["date"],
        "sections": sections,
        "priorities": synth.get("priorities") or [],
        "summary": synth.get("summary") or "",
        "raw_text": raw_text,
    }
