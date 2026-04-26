# Roadmap — Assistant IA

État au 25 avril 2026.

## Déjà livré dans cette session (8+3 améliorations)

- **#1 Découpage des mixins** — `EmailCommandsMixin` extrait dans `modules/email_commands_mixin.py`. Le code des commandes chat (INBOX_ACTION, MAIL_SEARCH) ne pollue plus `email_mixin.py`.
- **#3 Pool IMAP persistant** — `modules/imap_pool.py`. Connexion réutilisée entre actions (NOOP keepalive 4 min, reconnect auto). Branché sur `delete_mail_on_server`. Migration progressive prévue pour les autres call sites.
- **#4 Inbox virtualisé (soft)** — Plafond de 80 cartes rendues, bouton "Afficher plus" si plus. Reset à chaque changement d'onglet. Évite le freeze tkinter au-delà de 200 mails.
- **#5 Triage deux passes** — `modules/mail_prefilter.py`. Pré-classification locale gratuite (newsletter / spam / notif auto), Mistral seulement sur les mails ambigus. Réduit drastiquement les 429.
- **#6 Logging propre** — `modules/logger.py`. RotatingFileHandler dans `logs/assistant.log` (1 Mo × 5 backups). Variable d'env `ASSISTANT_DEBUG=1` pour DEBUG.
- **#7 Credentials sécurisés** — `modules/secure_creds.py`. Mot de passe IMAP migré vers keyring (Windows Credential Manager) automatiquement à la première lecture. Champ `mdp` retiré du `users_db.json`.
- **#8 Backup automatique** — `modules/backup.py`. Zip horodaté au démarrage dans `backups/`, rotation à 7. Couvre `users_db`, `experience_log`, `permissions_requests`, mémoires, agendas, caches mail.
- **AES-256 mémoire** — `modules/crypto.py` (Fernet). `memory_<user>.json` chiffré au repos. Clé dans keyring (fallback `.crypto_key`). Migration transparente des fichiers en clair.
- **LLM local optionnel** — Variable d'env `LLM_PROVIDER=ollama` + `OLLAMA_URL` / `OLLAMA_MODEL` pour basculer sur un modèle local (Ollama) sans toucher au code.

## Reportés (chantiers conséquents — ne pas attaquer à l'aveugle)

### #2 Séparation business / UI (services purs)
**Volume** : 2-3 jours. **Risque** : moyen.
**Plan** : extraire `MailService`, `AgendaService`, `MemoryService` qui ne touchent pas tkinter. Les mixins UI deviennent de fines coquilles qui appellent les services. Bénéfice principal : tests unitaires possibles, réutilisation pour une future UI web.
**Pré-requis** : décider si on vise une API REST (sinon ça reste académique).

### Migration SQLite (logs et permissions)
**Volume** : 1 jour. **Risque** : faible si dual-write.
**Plan** : créer `modules/db_sqlite.py` avec les mêmes signatures que les fonctions de `database.py`. Dual-write JSON+SQLite pendant 2 semaines, puis bascule. Permet des requêtes par date/type, scaling au-delà de 10K entrées.

### FastAPI server + UI web (React/Streamlit)
**Volume** : 1-2 semaines pour un MVP. **Risque** : élevé (réécriture).
**Plan** : exposer `MailService` / `AgendaService` derrière FastAPI. Streamlit pour un MVP rapide, ou Next.js + WebSocket pour du temps réel. Permet une appli mobile, un dashboard partagé, et casse l'unique-PC-Windows-only.
**Pré-requis** : #2 (services purs) doit être fait avant.

### RAG / mémoire vectorielle
**Volume** : 1 semaine. **Risque** : moyen.
**Plan** : ChromaDB local pour stocker embeddings des mails, des conversations, des notes. Recherche sémantique au lieu de `parsedate_to_datetime` + filtres. Permet à l'assistant de "se souvenir" d'un mail vieux de 6 mois sur une question vague. Modèle d'embedding local (BGE-small via sentence-transformers).

### Function-calling / agent loop (LangChain ou maison)
**Volume** : 1 semaine. **Risque** : moyen.
**Plan** : remplacer les blocs textuels (COMMAND_START, AGENDA_ADD_START…) par de vrais appels à des outils (function calling Mistral / OpenAI tools). Bénéfice : moins de prompt engineering, plus fiable, l'IA peut chaîner les outils ("lis mes mails ET regarde mon agenda demain"). Compatible Ollama via tool-calls.

### RBAC + OAuth2 / LDAP (cible entreprise)
**Volume** : 1 semaine. **Risque** : élevé (sécurité).
**Plan** : `permissions_mixin.py` doit devenir granulaire (lecture/écriture/admin par module). OAuth2 pour intégrer Google Workspace / Microsoft Entra. Pas urgent en usage perso.

### Connecteurs tiers (Slack, Teams, Notion, Jira, CRM)
**Volume** : 2-4 jours par connecteur. **Risque** : faible (chacun isolé).
**Plan** : un mixin par connecteur, OAuth2 pour l'auth, MCP server si on veut les exposer à Claude Desktop / autres clients. Démarrer par Slack ou Notion (les plus utilisés).

### Mobile / Desktop cross-platform
**Volume** : plusieurs semaines. **Risque** : élevé.
**Plan** : avec une API FastAPI, soit React Native (mobile), soit Tauri (desktop léger). Hors scope tant que la version locale Windows n'est pas saturée.

### Rapport quotidien intelligent
**Volume** : 2-3 jours. **Risque** : faible.
**Plan** : `daily_report_standalone.py` actuel est un dump de logs. Le faire passer par Mistral/Ollama pour produire un résumé exécutif (3-5 lignes), avec brouillons de réponses pour les mails urgents non traités.

## Notes de sécurité

- Le mot de passe d'application IMAP est désormais dans le **keyring système** (Windows Credential Manager). À la première exécution après cette mise à jour, le `users_db.json` est nettoyé automatiquement.
- `memory_<user>.json` est désormais chiffré (AES-128 via Fernet). La clé maître est dans le keyring (fallback `.crypto_key` à la racine du projet).
- Le `mdp` (mot de passe utilisateur en clair) n'est plus stocké à la création d'un nouveau compte. Les comptes existants gardent leur champ legacy ; à nettoyer manuellement si besoin.

## Migration vers une autre machine (portabilité)

Le projet est conçu pour ne **pas** être attaché à un PC en particulier. Tous les secrets peuvent être servis depuis des variables d'environnement (priorité sur le keyring), donc une migration tient en deux étapes : copie du dossier + copie d'un `.env`.

### Préparer la migration depuis le PC actuel

1. Récupérer le mot de passe d'application IMAP (depuis le keyring Windows ou ton gestionnaire de mots de passe d'origine).
2. Récupérer la clé de chiffrement courante :
   - soit le contenu de `.crypto_key` (si présent à la racine du projet),
   - soit, depuis Python : `import keyring; print(keyring.get_password("AssistantAI.crypto", "<username>"))`.
3. Créer un fichier `.env` à la racine du projet (modèle dans `.env.example`) :

```env
ASSISTANT_EMAIL_PASSWORD_YALA=abcd-efgh-ijkl-mnop
ASSISTANT_CRYPTO_KEY_YALA=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx=
```

### Sur la nouvelle machine

1. Copier le dossier complet du projet (avec `memory_*.json`, `agenda_*.json`, etc.).
2. Copier le `.env` à la racine.
3. `pip install -r` les dépendances (cryptography, keyring sont optionnelles si tu utilises uniquement le `.env`).
4. Lancer : tout fonctionne immédiatement, sans rien recréer dans le keyring de la nouvelle machine.

### Variables disponibles

| Variable | Effet |
|---|---|
| `ASSISTANT_EMAIL_PASSWORD` | Mot de passe IMAP global (override total) |
| `ASSISTANT_EMAIL_PASSWORD_<USER>` | Mot de passe IMAP par utilisateur (priorité sur global) |
| `ASSISTANT_CRYPTO_KEY` | Clé Fernet globale pour chiffrer les mémoires |
| `ASSISTANT_CRYPTO_KEY_<USER>` | Clé Fernet par utilisateur |
| `LLM_PROVIDER` | `mistral` (défaut) ou `ollama` |
| `OLLAMA_URL` / `OLLAMA_MODEL` | Endpoint et modèle local Ollama |
| `ASSISTANT_DEBUG` | `1` pour logs DEBUG, sinon INFO |

Le `.env` n'est jamais commit (ajouté au `.gitignore`) — tu peux le transporter via une clé USB, un coffre-fort, etc.

## Démarrage en mode local LLM

```cmd
ollama pull mistral
set LLM_PROVIDER=ollama
set OLLAMA_MODEL=mistral
"C:\path\to\Assistant.pyw"
```

Aucun appel cloud n'est alors fait. Confidentialité totale.
