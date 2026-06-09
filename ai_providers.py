"""Génération de réponses Reddit via Claude (Haiku uniquement)."""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_ANGLES = (
    "social : texto/crush/ex ex, gêne ou audace",
    "lazy : t'as rien fait ou procrastiné de ouf",
    "stunt / pari / truc dangereux raté",
    "bouffe, achat impulsif ou dégât matériel",
    "meta : « jamais rien de fou » ou tu retournes la question",
    "confession un peu dark, gênante ou random",
)

_CLICHE_BAN = (
    "réorganiser/ranger/classer cuisine/chambre/placard/épices/chaussettes, "
    "2h/3h du mat, « for no reason », « absolutely no reason », "
    "terminer par lmao/mdrr sur chaque réponse"
)

SYSTEM = (
    "Tu écris UN commentaire Reddit ultra-court — vrai user r/AskReddit, pas une IA. "
    "LONGUEUR : UNE phrase, 8 à 18 mots. "
    "Exemples : « j'ai envoyé un message à tous mes crush d'enfance » · "
    "« Soulé à mort et j'ai essayé de coucher avec genre 5 filles mdrr » · "
    "« je n'ai jamais fait quoi que ce soit de fou, suis-je une personne ennuyeuse ? ». "
    "Varie les sujets : social, lazy, stunt, bouffe, meta, confession — pas toujours le même angle. "
    "Style oral : minuscules ok, point final optionnel. "
    "Slang rare (lmao/mdrr max 1 fois sur plusieurs réponses). "
    f"Interdit : {_CLICHE_BAN}, listes, moraline, blabla IA. "
    "Pas de fautes bêtes. LANGUE = celle du post. Texte seul."
)

_TRANSLATE_SYSTEM = (
    "Traduis des commentaires Reddit en français oral et décontracté. "
    "Garde le ton, la longueur, le slang si présent. "
    "Réponds UNIQUEMENT avec un JSON valide : un tableau de strings, même ordre, même nombre."
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


_MAX_WORDS = 18


def _trim_long_reply(text: str) -> str:
    """Filet de sécurité — une phrase courte."""
    text = text.strip()
    words = text.split()
    if len(words) <= _MAX_WORDS:
        return text

    parts = re.split(r"(?<=[.!?…])\s+", text, maxsplit=1)
    first = parts[0].strip()
    if first and len(first.split()) <= _MAX_WORDS:
        return first

    return " ".join(words[:_MAX_WORDS]).rstrip(",;:")


def _anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquante dans reddit-reply/.env — Claude uniquement."
        )
    return key


async def _call_claude(
    client: httpx.AsyncClient,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float = 1.0,
) -> str:
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
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
            await asyncio.sleep(1.5 * (attempt + 1))
            last_err = httpx.HTTPStatusError(
                "429 Too Many Requests", request=r.request, response=r
            )
            continue
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"Erreur Claude ({e.response.status_code}) : {detail}"
            ) from e
        for block in r.json().get("content", []):
            if block.get("type") == "text":
                return block.get("text", "").strip()
        return ""

    raise RuntimeError(
        "Claude surchargé (429) — attends 30 s et réessaie, ou réduis le nombre de réponses."
    ) from last_err


async def _one_claude(
    client: httpx.AsyncClient,
    *,
    context: str,
    lang: str,
    tone: str,
    angle: str,
    index: int,
    avoid: list[str],
) -> str:
    lang_label = "français" if lang == "fr" else "anglais"
    avoid_block = ""
    if avoid:
        avoid_block = (
            "\n\nRéponses déjà générées — NE PAS répéter idée, structure ou mots-clés :\n"
            + "\n".join(f"• {r}" for r in avoid)
        )

    user = (
        f"{context}{avoid_block}\n\n"
        f"Langue OBLIGATOIRE : {lang_label}\n"
        f"Ton : {tone}\n"
        f"Angle (#{index + 1}) : {angle}\n"
        "UNE phrase, 8-18 mots. Original et différent des autres. Colle tel quel."
    )
    return await _call_claude(
        client, system=SYSTEM, user=user, max_tokens=45, temperature=1.0
    )


async def generate_replies(
    *,
    title: str,
    body: str,
    subreddit: str,
    tone: str,
    count: int,
) -> tuple[list[str], str]:
    lang = _detect_language(title, body)
    context = _post_context(title, body, subreddit, lang)
    replies: list[str] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(count):
            raw = await _one_claude(
                client,
                context=context,
                lang=lang,
                tone=tone,
                angle=_ANGLES[i % len(_ANGLES)],
                index=i,
                avoid=replies,
            )
            cleaned = _clean_reply(raw)
            if cleaned:
                replies.append(cleaned)

    if not replies:
        raise RuntimeError("Claude n'a renvoyé aucune réponse. Réessaie.")
    return replies, lang


async def translate_replies_fr(texts: list[str]) -> list[str]:
    """Traduction aperçu FR — ne modifie pas les originaux."""
    if not texts:
        return []
    if _detect_language("", " ".join(texts)) == "fr":
        return list(texts)

    user = json.dumps(texts, ensure_ascii=False)
    raw = ""
    async with httpx.AsyncClient(timeout=30.0) as client:
        raw = await _call_claude(
            client,
            system=_TRANSLATE_SYSTEM,
            user=user,
            max_tokens=400,
            temperature=0.2,
        )

    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            if isinstance(parsed, list) and len(parsed) == len(texts):
                return [str(x).strip() for x in parsed]
    except (json.JSONDecodeError, ValueError):
        pass

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) >= len(texts):
        return lines[: len(texts)]
    return list(texts)
