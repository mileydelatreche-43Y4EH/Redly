"""Génération de réponses Reddit via Claude (Haiku uniquement)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Literal

import httpx

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

PostKind = Literal["anecdote", "discussion"]

_ANGLES_ANECDOTE = (
    "social : texto/crush/ex ex, gêne ou audace",
    "lazy : t'as rien fait ou procrastiné de ouf",
    "stunt / pari / truc dangereux raté",
    "bouffe, achat impulsif ou dégât matériel",
    "meta : « jamais rien de fou » ou tu retournes la question",
    "confession un peu dark, gênante ou random",
)

_ANGLES_DISCUSSION = (
    "avis clair (oui/non/ça dépend) + raison centrale liée au post",
    "empathie envers la personne la plus touchée dans l'histoire",
    "frontière : ce que l'autre n'a pas le droit d'exiger ou de contrôler",
    "recadrage : jalousie, respect du deuil ou du passé de l'autre",
    "nuance couple : parler sans ultimatum ni faire culpabiliser",
    "expérience perso courte mais 100 % sur le sujet du post",
)

_CLICHE_BAN = (
    "réorganiser/ranger/classer cuisine/chambre/placard/épices/chaussettes, "
    "2h/3h du mat, « for no reason », « absolutely no reason », "
    "terminer par lmao/mdrr sur chaque réponse"
)

_OFF_TOPIC_BAN = (
    "virus informatique, café sur disque dur, disque dur externe, "
    "supprimer des fichiers saoul, rangement de placard, "
    "« c'est personnel y a pas de règle », « j'ai pas la tête à juger », "
    "anecdotes random sans lien avec le post"
)

SYSTEM_ANECDOTE = (
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

SYSTEM_DISCUSSION = (
    "Tu réponds au VRAI sujet du post Reddit (titre + corps). "
    "Comme un vrai commentateur : direct, humain, pas une IA. "
    "Si des commentaires réels du thread sont fournis, COPIE leur style de ponctuation "
    "et leur longueur (ne invente pas un format type « oui — explication »). "
    "Réponds à la question posée dans le post. "
    f"INTERDIT : {_OFF_TOPIC_BAN}, listes à puces, ton coach, « en tant qu'IA ». "
    "LANGUE = celle du post. Texte seul."
)

_TRANSLATE_SYSTEM = (
    "Traduis des commentaires Reddit en français oral et décontracté. "
    "Garde le ton, la longueur, le slang si présent. "
    "Réponds UNIQUEMENT avec un JSON valide : un tableau de strings, même ordre, même nombre."
)

_ANECDOTE_PATTERNS = (
    r"what(?:'s| is) the (?:craziest|wildest|weirdest|dumbest|most)",
    r"have you ever",
    r"what did you do",
    r"what(?:'s| is) something",
    r"tell us about",
    r"most embarrassing",
    r"without (?:any )?reason",
    r"something crazy",
    r"quelle est la chose",
    r"tu as déjà",
    r"qu'as-tu fait",
    r"la chose la plus",
)

_DISCUSSION_PATTERNS = (
    r"is it (?:appropriate|ok|wrong|normal|weird|fair)",
    r"should i\b",
    r"am i the asshole",
    r"\baita\b",
    r"would you\b",
    r"do you think",
    r"how do i\b",
    r"what would you do",
    r"est-ce (?:normal|approprié|ok|mal)",
    r"je devrais",
    r"qu'en pensez",
    r"passed away",
    r"died\b",
    r"deceased",
    r"late (?:boyfriend|girlfriend|partner|husband|wife)",
    r"relationship",
    r"partner",
    r"boyfriend",
    r"girlfriend",
    r"photos? (?:of|from)",
    r"videos? (?:of|from)",
    r"keep (?:them|it|photos)",
    r"delete (?:them|it|photos)",
)


def _clip(text: str, limit: int = 1200) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _classify_post(title: str, body: str, subreddit: str) -> PostKind:
    """AskReddit anecdote vs discussion / avis sur le fond du post."""
    text = f"{subreddit} {title} {body}".lower()

    if "askreddit" in text:
        return "anecdote"

    anecdote = sum(1 for p in _ANECDOTE_PATTERNS if re.search(p, text))
    discussion = sum(1 for p in _DISCUSSION_PATTERNS if re.search(p, text))

    if discussion > 0 and discussion >= anecdote:
        return "discussion"
    if anecdote > 0:
        return "anecdote"
    return "discussion"


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
            r"about|just|never|bored|most|that|this|how|why|i'm|i've|don't|anyone|ever|"
            r"appropriate|partner|photos|videos|died|passed)\b",
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


def _post_context(
    title: str,
    body: str,
    subreddit: str,
    lang: str,
    kind: PostKind,
    style_brief: str | None = None,
) -> str:
    lang_label = "français" if lang == "fr" else "anglais"
    kind_label = (
        "anecdote personnelle (style AskReddit)"
        if kind == "anecdote"
        else "avis / discussion — réponds au fond du sujet"
    )
    parts = [f"Langue du post : {lang_label}", f"Type de post : {kind_label}"]
    if subreddit and subreddit not in ("r/...", ""):
        parts.append(f"Subreddit : {subreddit}")
    parts.append(f"Titre : {title.strip()}")
    if body.strip():
        parts.append(f"Corps du post (contexte obligatoire) :\n{_clip(body, 2000)}")
    if kind == "discussion":
        parts.append(
            "Consigne : chaque réponse doit donner un AVIS sur la situation du post, "
            "pas une histoire random hors-sujet."
        )
    if style_brief:
        parts.append(
            "\n--- Style appris des vrais commentaires sur ce thread / ce subreddit ---\n"
            + style_brief
        )
    return "\n".join(parts)


def _clean_reply(
    text: str,
    *,
    max_words: int,
    allow_em_dash: bool = True,
) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    text = re.sub(r"^```\w*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _trim_long_reply(text, max_words=max_words)
    if not allow_em_dash:
        text = text.replace("—", ", ").replace("–", ", ")
        text = re.sub(r"\s+,\s+", ", ", text)
        text = re.sub(r",\s*,", ",", text)
    return text.strip()


def _trim_long_reply(text: str, *, max_words: int) -> str:
    text = text.strip()
    words = text.split()
    if len(words) <= max_words:
        return text

    parts = re.split(r"(?<=[.!?…])\s+", text, maxsplit=2)
    kept: list[str] = []
    total = 0
    for part in parts:
        w = len(part.split())
        if total + w > max_words:
            break
        kept.append(part.strip())
        total += w
    if kept:
        return " ".join(kept)

    return " ".join(words[:max_words]).rstrip(",;:")


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


def _generation_params(
    kind: PostKind,
    style_hints: dict[str, Any] | None = None,
) -> tuple[str, tuple[str, ...], int, int, str]:
    if kind == "anecdote":
        system, angles, max_tokens, max_words, length_hint = (
            SYSTEM_ANECDOTE,
            _ANGLES_ANECDOTE,
            45,
            18,
            "UNE phrase, 8-18 mots. Anecdote perso sur le thème du post AskReddit.",
        )
    else:
        system, angles, max_tokens, max_words, length_hint = (
            SYSTEM_DISCUSSION,
            _ANGLES_DISCUSSION,
            110,
            55,
            "1-2 phrases, 15-55 mots. Avis direct sur le sujet du post — pas d'histoire hors-sujet.",
        )

    if style_hints:
        custom_angles = style_hints.get("angles")
        if isinstance(custom_angles, (list, tuple)) and custom_angles:
            angles = tuple(str(a) for a in custom_angles if str(a).strip())[:6]
        try:
            mx = int(style_hints.get("max_words") or 0)
            mn = int(style_hints.get("min_words") or 0)
            if 10 <= mx <= 130:
                max_words = mx
                max_tokens = max(max_tokens, min(200, mx * 3))
                if kind == "discussion":
                    if mn < 4:
                        mn = max(4, mx // 3)
                    length_hint = (
                        f"{mn}-{max_words} mots. "
                        "Imite la longueur et la ponctuation des commentaires réels du thread."
                    )
        except (TypeError, ValueError):
            pass

    if style_hints and style_hints.get("verbatim_examples"):
        system = (
            system
            + " OBLIGATION : même ponctuation que les commentaires réels fournis "
            "(pas de tiret long — sauf s'ils en ont)."
        )
        if not style_hints.get("allow_em_dash", True):
            system += " INTERDIT le tiret long — (em dash) : absent des top commentaires."

    return system, angles, max_tokens, max_words, length_hint


async def _one_claude(
    client: httpx.AsyncClient,
    *,
    context: str,
    lang: str,
    tone: str,
    angle: str,
    index: int,
    avoid: list[str],
    kind: PostKind,
    style_hints: dict[str, Any] | None = None,
) -> str:
    system, _, max_tokens, max_words, length_hint = _generation_params(kind, style_hints)
    lang_label = "français" if lang == "fr" else "anglais"
    avoid_block = ""
    if avoid:
        avoid_block = (
            "\n\nRéponses déjà générées — NE PAS répéter idée, structure ou mots-clés :\n"
            + "\n".join(f"• {r}" for r in avoid)
        )

    extra_dont = ""
    if style_hints and style_hints.get("extra_dont"):
        extra_dont = "\nÀ éviter absolument : " + " · ".join(style_hints["extra_dont"])

    user = (
        f"{context}{avoid_block}{extra_dont}\n\n"
        f"Langue OBLIGATOIRE : {lang_label}\n"
        f"Ton : {tone}\n"
        f"Angle (#{index + 1}) : {angle}\n"
        f"{length_hint} Original et différent des autres. Colle tel quel."
    )
    raw = await _call_claude(
        client, system=system, user=user, max_tokens=max_tokens, temperature=0.78
    )
    allow_em = True
    if style_hints and "allow_em_dash" in style_hints:
        allow_em = bool(style_hints.get("allow_em_dash"))
    return _clean_reply(raw, max_words=max_words, allow_em_dash=allow_em)


async def generate_replies(
    *,
    title: str,
    body: str,
    subreddit: str,
    tone: str,
    count: int,
    style_brief: str | None = None,
    style_hints: dict[str, Any] | None = None,
) -> tuple[list[str], str]:
    lang = _detect_language(title, body)
    kind = _classify_post(title, body, subreddit)
    context = _post_context(title, body, subreddit, lang, kind, style_brief)
    _, angles, _, _, _ = _generation_params(kind, style_hints)
    replies: list[str] = []

    async with httpx.AsyncClient(timeout=90.0) as client:
        for i in range(count):
            raw = await _one_claude(
                client,
                context=context,
                lang=lang,
                tone=tone,
                angle=angles[i % len(angles)],
                index=i,
                avoid=replies,
                kind=kind,
                style_hints=style_hints,
            )
            if raw:
                replies.append(raw)

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
