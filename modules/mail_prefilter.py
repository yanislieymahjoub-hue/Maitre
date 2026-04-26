"""Pre-classification locale rapide (sans IA).

Objectif : eviter un aller-retour Mistral quand le mail est manifestement
une newsletter, un spam, une notif auto ou un mail technique. Cela reduit
drastiquement les 429 (rate limit) sur les gros batchs.

API :
    prefilter(sender, subject, body) -> dict | None
        Renvoie un dict au MEME format que _ai_summarize_mail (resume,
        urgence, action, warning, type, agenda_suggestion) quand une regle
        declenche.
        Renvoie None si incertain : on retombe alors sur Mistral.
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# Dictionnaires de motifs
# ---------------------------------------------------------------------------

_RE_NEWSLETTER_SENDER = re.compile(
    r"(newsletter|noreply|no-reply|nepasrepondre|mailer|mailing|"
    r"news@|news\.|notifications?@|notif@|digest@|updates?@|"
    r"marketing@|promo@|offres?@|deals?@|hello@|contact@)",
    re.IGNORECASE,
)

# Expediteurs "pubs/marketing de masse" les plus frequents
_RE_SPAM_SENDER = re.compile(
    r"(seo|leads|growth|partnership|partenariat|booster|affiliation|"
    r"pub@|ads@|advertising|sponsor|campagne|sales@)",
    re.IGNORECASE,
)

# Sujets typiques de spam / promo
_RE_SPAM_SUBJECT = re.compile(
    r"(-?\d{1,2}\s?%|cashback|coupon|code\s?promo|soldes?|offre\s+speciale|"
    r"derniere?\s+chance|exclusif|gratuit\s+pour\s+vous|"
    r"booste[rz]?\s+(votre|vos)|ameliore[rz]?\s+votre\s+business|"
    r"\bseo\b|\bmarketing\b|\bprospection\b|"
    r"felicitations?\s+vous\s+avez|vous\s+avez\s+gagne|vous\s+avez\s+ete\s+selectionne)",
    re.IGNORECASE,
)

# Mots qui indiquent une newsletter legitime (abonnement connu)
_RE_NEWSLETTER_SUBJECT = re.compile(
    r"(newsletter|digest|weekly|daily|rapport\s+hebdo|au\s+programme|a\s+la\s+une|"
    r"ce\s+(qu|que)'il\s+ne\s+faut\s+pas\s+manquer|recap|resume\s+de\s+la\s+semaine)",
    re.IGNORECASE,
)

# Notifications automatiques sans danger (GitHub, Stripe, Notion, etc.)
_AUTO_NOTIF_DOMAINS = (
    "noreply.github.com", "notifications.github.com",
    "stripe.com", "notion.so", "slack.com",
    "linkedin.com", "e.linkedin.com",
    "googlegroups.com", "google.com",
)

# Footers typiques newsletter
_RE_FOOTER_UNSUB = re.compile(
    r"(se\s+desabonn|unsubscribe|gerer\s+mes\s+(preferences|abonnements?)|"
    r"ne\s+plus\s+recevoir|stop\s+emails?)",
    re.IGNORECASE,
)


def _lower_sender(sender: str) -> str:
    return (sender or "").lower()


def _domain_of(sender: str) -> str:
    s = _lower_sender(sender)
    m = re.search(r"@([a-z0-9.\-]+)", s)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Regles
# ---------------------------------------------------------------------------

def _as_result(resume: str, urgence: str, mail_type: str, actions, warning=None):
    return {
        "resume": resume,
        "urgence": urgence,
        "action": list(actions) if actions else ["Lire si pertinent", "Ignorer si inutile"],
        "warning": warning,
        "type": mail_type,
        "agenda_suggestion": None,
        "pieces_jointes": [],
        "_prefilter": True,  # trace, utile pour les logs / stats
    }


def prefilter(sender: str, subject: str, body: str):
    """Retourne un dict "type _ai_summarize_mail" si une regle matche, sinon None."""
    sender_l = _lower_sender(sender)
    subject_l = (subject or "").lower()
    body_l = (body or "")[:2000].lower()  # on ne scanne que le haut du body
    domain = _domain_of(sender)

    has_unsub = bool(_RE_FOOTER_UNSUB.search(body_l)) if body_l else False

    # --- Notifications automatiques sures (GitHub, Stripe, Linkedin...) ---
    if any(domain.endswith(d) for d in _AUTO_NOTIF_DOMAINS):
        # Si le sujet contient "security alert", on laisse Mistral trancher.
        if re.search(r"(security\s+alert|suspicious|phishing|compromised)", subject_l):
            return None
        return _as_result(
            resume=f"Notification automatique de {domain}.",
            urgence="faible",
            mail_type="newsletter" if "digest" in subject_l else "support",
            actions=["Lire si pertinent", "Ignorer si inutile"],
        )

    # --- Spam evident : expediteur marketing + sujet promo ---
    if _RE_SPAM_SENDER.search(sender_l) and _RE_SPAM_SUBJECT.search(subject_l):
        return _as_result(
            resume="Prospection / publicite non sollicitee.",
            urgence="faible",
            mail_type="spam",
            actions=["Supprimer definitivement du serveur", "Se desabonner si lien present"],
        )

    # --- Sujet ultra-promotionnel seul (emoji %, MAJUSCULES, promos) ---
    if _RE_SPAM_SUBJECT.search(subject_l) and has_unsub:
        # Promo + lien desabonnement = classique pub de masse
        return _as_result(
            resume="Offre commerciale / promotion generique.",
            urgence="faible",
            mail_type="spam",
            actions=["Supprimer", "Se desabonner via le lien en bas du mail"],
        )

    # --- Newsletter identifiable : expediteur newsletter + footer unsub ---
    if (_RE_NEWSLETTER_SENDER.search(sender_l) or _RE_NEWSLETTER_SUBJECT.search(subject_l)) and has_unsub:
        # Mais si un mot "critique" apparait, on laisse Mistral decider
        if re.search(r"(facture|invoice|paiement|payment|echeance|retard|"
                     r"contrat|contract|signature|rendez[-\s]?vous|rdv|"
                     r"entretien|urgent)", subject_l + " " + body_l):
            return None
        return _as_result(
            resume="Newsletter / mail d'information.",
            urgence="faible",
            mail_type="newsletter",
            actions=["Lire si pertinent", "Se desabonner si plus utile"],
        )

    # --- Incertain : on delegue a Mistral ---
    return None
