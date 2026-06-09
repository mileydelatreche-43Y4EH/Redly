"""Génération de réponses Reddit via Claude (Haiku uniquement)."""

from __future__ import annotations

import asyncio
import os
import re

import httpx

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_ANGLES = (
    "anecdote perso courte, un peu absurde ou débile — comme un vrai commentaire upvoté",
    "réponse relatable en 1 phrase, mdrr/lmao seulement si ça sonne naturel",
    "confession casual, minuscules ok, pas besoin de point final",
    "twist inattendu ou self-deprecating, zéro blabla avant",
    "one-liner sec qui fait rire ou surprend",
    "renvoie la question au OP ou deadpan, style oral Reddit",
)

SYSTEM = (
    "Tu écris UN commentaire Reddit fait pour être upvoté — comme un vrai user sur r/AskReddit. "
    "Pas un assistant, pas un essayiste. "
    "LONGUEUR : 1 à 2 phrases courtes, ~10 à 35 mots. "
    "Contenu : anecdote perso crédible, chaos relatable, humour sec, ou punchline. "
    "Style oral : minuscules au début ok, pas de point final obligatoire, "
    "slang internet ok (mdrr, lmao, ngl, genre) si naturel. "
    "Interdit : listes, moraline, « spoiler alert », formules creuses, "
    "« en tant qu'… », guillemets autour du texte, toujours finir par une question. "
    "Pas de fautes bêtes (lettre oubliée dans un mot). "
    "LANGUE : réponds STRICTEMENT dans la langue indiquée (post FR → FR, post EN → EN). "
    "Texte seul, prêt à coller sur Reddit."
)


def _clip(text: str, limit: int = 1200) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _detect_language(title: str, body: str) -> str:
    """fr ou en — selon le titre/corps du post."""
    sample = f"{title} {body}".strip().lower()
    if not sample:
        return "fr"

    fr = len(
        re.findall(
            r"[àâäéèêëïîôùûüç]|"
            r"\b(je|j'ai|tu|t'as|vous|qu'|c'est|une|des|les|pas|plus|truc|genre|"
            r"quoi|quand|pour|être|fais|fait|ennuy|folle|chose|jamais|suis|ai|as)\b",
            sample,
        )
    )
    en = len(
        re.findall(
            r"\b(the|you|what|when|did|have|was|were|my|your|something|crazy|thing|"
            r"about|just|never|bored|most|that|this|how|why|i'm|i've|don't|anyone|ever)\b",
            sample,
        )
    )
    if en > fr and en >= 2:
        return "en"
    if fr >= 2:
        return "fr"
    if en >= 1 and fr == 0:
        return "en"
    return "fr"


def _post_context(title: str, body: str, subreddit: str, lang: str) -> str:
    lang_label = "français" if lang == "fr" else "anglais"
    parts = [f"Langue du post : {lang_label}"]
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
    lang: str,
    tone: str,
    angle: str,
    index: int,
) -> str:
    if index:
        await asyncio.sleep(index * 0.2)

    lang_label = "français" if lang == "fr" else "anglais"
    user = (
        f"{context}\n\n"
        f"Langue OBLIGATOIRE pour ta réponse : {lang_label}\n"
        f"Ton : {tone}\n"
        f"Angle (#{index + 1}) : {angle}\n"
        "1 ou 2 phrases max, style commentaire Reddit qui marche. "
        "Colle tel quel, sans intro."
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
    lang = _detect_language(title, body)
    context = _post_context(title, body, subreddit, lang)

    async with httpx.AsyncClient(timeout=45.0) as client:
        raw = await asyncio.gather(
            *[
                _one_claude(
                    client,
                    context=context,
                    lang=lang,
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
