"""Génération de réponses Reddit via Claude (Haiku uniquement)."""

from __future__ import annotations

import asyncio
import os
import re

import httpx

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_ANGLES = (
    "1 phrase, opinion nette",
    "2 phrases max, ton décontracté",
    "réponse courte + question à la fin",
    "sec et direct, zéro blabla",
    "mini anecdote (1 phrase) si ça colle",
    "conseil concret en une ligne",
)

SYSTEM = (
    "Tu rédiges UN commentaire Reddit court, percutant et humain. "
    "Comme un vrai utilisateur : naturel, pas de formules creuses, pas de listes, "
    "pas de moraline ni de « voici pourquoi » en trois paragraphes. "
    "LONGUEUR STRICTE : 15 à 40 mots, 1 ou 2 phrases maximum. "
    "Va droit au but — une idée claire, pas de répétition. "
    "Jamais « En tant qu'IA ». Pas de guillemets. "
    "Français sauf si le post est clairement dans une autre langue. Texte seul."
)


def _clip(text: str, limit: int = 1200) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _post_context(title: str, body: str, subreddit: str) -> str:
    parts = []
    if subreddit and subreddit not in ("r/...", ""):
        parts.append(f"Subreddit : {subreddit}")
    parts.append(f"Titre : {title.strip()}")
    if body.strip():
        parts.append(f"Post :\n{_clip(body)}")
    return "\n".join(parts)


def _clean_reply(text: str) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    text = re.sub(r"^```\w*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _trim_long_reply(text)


def _trim_long_reply(text: str) -> str:
    """Filet de sécurité si le modèle dépasse malgré le prompt."""
    if len(text) <= 220:
        return text
    parts = re.split(r"(?<=[.!?…])\s+", text)
    if len(parts) >= 2:
        short = " ".join(parts[:2]).strip()
        if len(short) <= 280:
            return short
    words = text.split()
    if len(words) > 42:
        return " ".join(words[:42]).rstrip(",;:") + "…"
    return text


def _anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquante dans reddit-reply/.env — Claude uniquement."
        )
    return key


async def _one_claude(
    client: httpx.AsyncClient,
    *,
    context: str,
    tone: str,
    angle: str,
    index: int,
) -> str:
    # Léger décalage pour éviter le burst 429 quand on génère 4+ réponses en parallèle.
    if index:
        await asyncio.sleep(index * 0.2)

    user = (
        f"{context}\n\nTon : {tone}\n"
        f"Style (#{index + 1}) : {angle}\n"
        "Écris 1 ou 2 phrases courtes (max ~40 mots). Pas de paragraphe. "
        "Prêt à publier tel quel sur Reddit."
    )
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 90,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": _anthropic_key(),
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    last_err: httpx.HTTPStatusError | None = None
    for attempt in range(4):
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        if r.status_code == 429:
            wait = 1.5 * (attempt + 1)
            await asyncio.sleep(wait)
            last_err = httpx.HTTPStatusError(
                "429 Too Many Requests", request=r.request, response=r
            )
            continue
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = r.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"Erreur Claude ({r.status_code}) : {detail}"
            ) from e
        for block in r.json().get("content", []):
            if block.get("type") == "text":
                return block.get("text", "").strip()
        return ""

    raise RuntimeError(
        "Claude surchargé (429) — attends 30 s et réessaie, ou réduis le nombre de réponses."
    ) from last_err


async def generate_replies(
    *,
    title: str,
    body: str,
    subreddit: str,
    tone: str,
    count: int,
) -> list[str]:
    context = _post_context(title, body, subreddit)

    async with httpx.AsyncClient(timeout=45.0) as client:
        raw = await asyncio.gather(
            *[
                _one_claude(
                    client,
                    context=context,
                    tone=tone,
                    angle=_ANGLES[i % len(_ANGLES)],
                    index=i,
                )
                for i in range(count)
            ]
        )

    replies = [_clean_reply(t) for t in raw if _clean_reply(t)]
    if not replies:
        raise RuntimeError("Claude n'a renvoyé aucune réponse. Réessaie.")
    return replies
