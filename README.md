# Redly

Réponses Reddit en local — **Claude** + mode **Auto** (scan, génération, validation, publication).

## Setup

```powershell
cd c:\Users\miley\screen-replay\reddit-reply
copy .env.example .env
# ANTHROPIC_API_KEY=sk-ant-...
# REDDIT_CLIENT_ID=...   (mode Auto)
# REDDIT_CLIENT_SECRET=...
```

### App Reddit (mode Auto)

1. Va sur [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
2. **Create app** → type **web app**
3. Redirect URI : `http://127.0.0.1:8765/api/reddit/auth/callback`
4. Copie **client id** (sous le nom) et **secret** dans `.env`

## Lancer

```powershell
c:\Users\miley\screen-replay\reddit-reply\lancer.bat
```

→ http://127.0.0.1:8765

## Mode manuel

1. **Lien** ou **Texte** du post
2. Ton + nombre de réponses
3. **Générer** (Ctrl+Entrée)
4. **Copier**

## Mode auto

→ http://127.0.0.1:8765/auto

1. **Connecter Reddit** (une fois)
2. Subreddit + critères (tri, score min, mot-clé, âge max…)
3. **Scanner & générer** — Claude propose une réponse par post
4. **Relire** chaque réponse, modifier si besoin
5. **Publier** ou **Ignorer**

Les posts déjà en file ne sont pas dupliqués. Publie avec modération — Reddit bannit le spam.

Modèle : `claude-haiku-4-5` (override via `CLAUDE_MODEL`).
