"""Analyse des commentaires Reddit + profils de style par subreddit."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ai_providers import _call_claude
from storage_paths import data_dir

MAX_PROFILE_SESSIONS = 40
MAX_STORED_SNIPPETS = 8

_STYLE_ANALYZE_SYSTEM = (
    "Tu analyses des commentaires Reddit populaires COPIÉS mot pour mot ci-dessous. "
    "Décris UNIQUEMENT ce que tu observes dans CES textes (pas un style générique Reddit). "
    "Réponds UNIQUEMENT avec un JSON valide, sans markdown :\n"
    "{\n"
    '  "summary": "style réel observé (ponctuation, longueur, ton)",\n'
    '  "min_words": 10,\n'
    '  "max_words": 55,\n'
    '  "uses_em_dash": false,\n'
    '  "angles": ["angles DISTINCTS vus dans les commentaires"],\n'
    '  "do": ["reprendre ce que font VRAIMENT ces commentaires"],\n'
    '  "dont": ["ce qu\'aucun de ces commentaires ne fait"],\n'
    '  "example_snippets": ["citations EXACTES mot pour mot depuis les commentaires, max 3"]\n'
    "}"
)


def _profiles_path():
    return data_dir() / "style_profiles.json"


def _load_profiles() -> dict:
    path = _profiles_path()
    if not path.exists():
        return {"profiles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"profiles": {}}


def _save_profiles(data: dict) -> None:
    path = _profiles_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_sub(subreddit: str) -> str:
    sub = (subreddit or "").strip().lower()
    if not sub or sub == "r/...":
        return ""
    if not sub.startswith("r/"):
        sub = f"r/{sub}"
    return sub


def load_subreddit_profile(subreddit: str) -> dict | None:
    key = _norm_sub(subreddit)
    if not key:
        return None
    profile = _load_profiles()["profiles"].get(key)
    return profile if isinstance(profile, dict) else None


def _parse_analysis(raw: str) -> dict | None:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _clip_comment(text: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def inspect_comment_style(comments: list[dict]) -> dict[str, Any]:
    """Stats réelles sur les commentaires — ponctuation, longueur (sans IA)."""
    bodies = [
        (c.get("body") or "").strip()
        for c in comments
        if (c.get("body") or "").strip() not in ("[deleted]", "[removed]")
    ]
    if not bodies:
        return {}

    em_dash_hits = sum(
        1
        for b in bodies
        if "—" in b or "–" in b or re.search(r"\w\s-\s+\w", b)
    )
    word_counts = [len(b.split()) for b in bodies]
    avg = sum(word_counts) / len(word_counts)

    return {
        "comment_count": len(bodies),
        "em_dash_rate": em_dash_hits / len(bodies),
        "allow_em_dash": em_dash_hits / len(bodies) >= 0.2,
        "avg_words": int(round(avg)),
        "min_words": max(4, min(word_counts)),
        "max_words": min(130, max(word_counts) + 8),
        "verbatim_examples": [_clip_comment(b, 450) for b in bodies[:6]],
        "sample_openers": [b.split()[0] if b.split() else "" for b in bodies[:5]],
    }


def _merge_style_hints(
    local: dict[str, Any],
    analysis: dict | None,
) -> dict[str, Any]:
    hints: dict[str, Any] = {}

    if local:
        hints["allow_em_dash"] = bool(local.get("allow_em_dash"))
        hints["verbatim_examples"] = local.get("verbatim_examples") or []
        hints["min_words"] = local.get("min_words")
        hints["max_words"] = local.get("max_words")
        hints["avg_words"] = local.get("avg_words")

    if analysis:
        if isinstance(analysis.get("angles"), list) and analysis["angles"]:
            hints["angles"] = tuple(
                str(a).strip() for a in analysis["angles"] if str(a).strip()
            )[:6]
        try:
            mn = int(analysis.get("min_words") or 0)
            mx = int(analysis.get("max_words") or 0)
            if 4 <= mn <= 80:
                hints["min_words"] = mn
            if 10 <= mx <= 130:
                hints["max_words"] = mx
        except (TypeError, ValueError):
            pass
        if "uses_em_dash" in analysis:
            hints["allow_em_dash"] = bool(analysis.get("uses_em_dash"))

    if hints.get("verbatim_examples"):
        dont = list(analysis.get("dont") or []) if analysis else []
        if not hints.get("allow_em_dash"):
            dont.append("tiret long — ou – (aucun top commentaire ne l'utilise)")
        dont.append("structure répétée « oui — explication » sur chaque réponse")
        hints["extra_dont"] = dont[:6]

    return hints


async def analyze_thread_comments(
    client: httpx.AsyncClient,
    *,
    title: str,
    body: str,
    subreddit: str,
    comments: list[dict],
    local_style: dict[str, Any],
) -> dict | None:
    if not comments:
        return None

    samples = []
    for c in comments[:12]:
        body_text = (c.get("body") or "").strip()
        if not body_text or body_text in ("[deleted]", "[removed]"):
            continue
        score = int(c.get("score") or 0)
        samples.append(f"[{score}↑] {body_text[:500]}")

    if not samples:
        return None

    punct_note = ""
    if local_style:
        rate = local_style.get("em_dash_rate", 0)
        punct_note = (
            f"\nObservation auto : tirets longs dans {int(rate * 100)}% des commentaires. "
            f"Longueur moyenne ~{local_style.get('avg_words', '?')} mots."
        )

    user = (
        f"Subreddit : {subreddit or 'r/...'}\n"
        f"Titre : {title.strip()}\n"
    )
    if body.strip():
        user += f"Corps du post :\n{body.strip()[:1200]}\n\n"
    user += (
        "Commentaires populaires (texte EXACT des users) :\n"
        + "\n---\n".join(samples)
        + punct_note
    )

    raw = await _call_claude(
        client,
        system=_STYLE_ANALYZE_SYSTEM,
        user=user,
        max_tokens=500,
        temperature=0.15,
    )
    return _parse_analysis(raw)


def merge_subreddit_profile(
    subreddit: str,
    analysis: dict | None,
    comments: list[dict],
) -> dict:
    key = _norm_sub(subreddit)
    if not key:
        return {}

    store = _load_profiles()
    profiles = store.setdefault("profiles", {})
    existing = profiles.get(key) or {
        "sessions": 0,
        "summary": "",
        "do": [],
        "dont": [],
        "snippets": [],
        "updated_at": "",
    }

    existing["sessions"] = int(existing.get("sessions") or 0) + 1
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()

    if analysis:
        if analysis.get("summary"):
            existing["summary"] = str(analysis["summary"]).strip()
        for field in ("do", "dont"):
            new_vals = analysis.get(field)
            if isinstance(new_vals, list):
                merged = [str(x).strip() for x in new_vals if str(x).strip()]
                old = [str(x) for x in (existing.get(field) or [])]
                existing[field] = (merged + old)[:6]

    snippets: list[str] = list(existing.get("snippets") or [])
    for c in comments[:6]:
        t = (c.get("body") or "").strip()
        if t and t not in snippets:
            snippets.insert(0, _clip_comment(t, 280))

    existing["snippets"] = snippets[:MAX_STORED_SNIPPETS]
    profiles[key] = existing
    _save_profiles(store)
    return existing


def format_style_brief(
    *,
    analysis: dict | None,
    profile: dict | None,
    comments: list[dict],
    local_style: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []

    if comments:
        parts.append(
            "=== COMMENTAIRES RÉELS DU THREAD (imite CE style, pas un template IA) ==="
        )
        for i, c in enumerate(comments[:8], 1):
            body = (c.get("body") or "").strip()
            if not body:
                continue
            score = int(c.get("score") or 0)
            parts.append(f"{i}. [{score}↑] {body[:500]}")

    if local_style:
        allow = local_style.get("allow_em_dash")
        parts.append(
            f"Ponctuation observée : tiret long — "
            f"{'parfois utilisé' if allow else 'ABSENT des top commentaires → NE PAS EN METTRE'}. "
            f"Longueur typique : {local_style.get('min_words')}-{local_style.get('max_words')} mots."
        )

    if analysis and analysis.get("summary"):
        parts.append(f"Analyse : {analysis['summary']}")

    do_list: list[str] = []
    dont_list: list[str] = []
    if analysis:
        do_list.extend(str(x) for x in (analysis.get("do") or []) if x)
        dont_list.extend(str(x) for x in (analysis.get("dont") or []) if x)

    if do_list:
        parts.append("À faire : " + " · ".join(dict.fromkeys(do_list)[:5]))
    if dont_list:
        parts.append("À éviter : " + " · ".join(dict.fromkeys(dont_list)[:5]))

    parts.append(
        "Règle : même ponctuation, même registre, même type de phrases que les commentaires "
        "ci-dessus. Réponds au sujet du POST (titre + corps). Pas de formule inventée."
    )
    return "\n".join(parts)


async def prepare_thread_style(
    client: httpx.AsyncClient,
    *,
    title: str,
    body: str,
    subreddit: str,
    comments: list[dict],
) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    """Retourne (brief pour le prompt, hints génération, meta UI)."""
    profile_before = load_subreddit_profile(subreddit)
    local_style = inspect_comment_style(comments)
    analysis = None

    if comments:
        analysis = await analyze_thread_comments(
            client,
            title=title,
            body=body,
            subreddit=subreddit,
            comments=comments,
            local_style=local_style,
        )

    profile_after = profile_before
    if comments or analysis:
        profile_after = merge_subreddit_profile(subreddit, analysis, comments)

    hints = _merge_style_hints(local_style, analysis)

    brief = None
    if comments:
        brief = format_style_brief(
            analysis=analysis,
            profile=profile_after,
            comments=comments,
            local_style=local_style,
        )
    elif profile_after and profile_after.get("summary"):
        brief = format_style_brief(
            analysis=None,
            profile=profile_after,
            comments=[],
            local_style=None,
        )

    sessions = int((profile_after or {}).get("sessions") or 0)

    meta = {
        "comments_analyzed": len(comments),
        "style_learned": bool(brief and comments),
        "subreddit_sessions": sessions,
        "had_profile": bool(profile_before),
        "em_dash_in_thread": bool(local_style.get("allow_em_dash")) if local_style else False,
    }
    return brief, hints, meta
