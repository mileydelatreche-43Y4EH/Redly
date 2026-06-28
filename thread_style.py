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
    "Tu analyses des commentaires Reddit populaires sur UN post pour comprendre "
    "comment de VRAIS utilisateurs répondent (ton, longueur, angles). "
    "Réponds UNIQUEMENT avec un JSON valide, sans markdown :\n"
    "{\n"
    '  "summary": "2-3 phrases sur le style dominant de CE thread",\n'
    '  "min_words": 10,\n'
    '  "max_words": 55,\n'
    '  "angles": ["4 à 6 angles distincts utilisés par les commentateurs"],\n'
    '  "do": ["3-5 choses à faire pour coller au thread"],\n'
    '  "dont": ["3-5 choses à éviter"],\n'
    '  "example_snippets": ["citations courtes mot pour mot, max 3"]\n'
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


async def analyze_thread_comments(
    client: httpx.AsyncClient,
    *,
    title: str,
    body: str,
    subreddit: str,
    comments: list[dict],
) -> dict | None:
    if not comments:
        return None

    samples = []
    for c in comments[:10]:
        body_text = _clip_comment(c.get("body") or "", 300)
        if not body_text:
            continue
        score = int(c.get("score") or 0)
        samples.append(f"[{score}↑] {body_text}")

    if not samples:
        return None

    user = (
        f"Subreddit : {subreddit or 'r/...'}\n"
        f"Titre : {title.strip()}\n"
    )
    if body.strip():
        user += f"Post :\n{body.strip()[:800]}\n\n"
    user += "Commentaires populaires sur ce post :\n" + "\n".join(
        f"• {s}" for s in samples
    )

    raw = await _call_claude(
        client,
        system=_STYLE_ANALYZE_SYSTEM,
        user=user,
        max_tokens=500,
        temperature=0.25,
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
    if analysis and isinstance(analysis.get("example_snippets"), list):
        for s in analysis["example_snippets"]:
            t = str(s).strip()
            if t and t not in snippets:
                snippets.insert(0, t)

    for c in comments[:5]:
        t = _clip_comment(c.get("body") or "", 200)
        if t and t not in snippets:
            snippets.insert(0, t)

    existing["snippets"] = snippets[:MAX_STORED_SNIPPETS]
    profiles[key] = existing
    _save_profiles(store)
    return existing


def format_style_brief(
    *,
    analysis: dict | None,
    profile: dict | None,
    comments: list[dict],
) -> str:
    parts: list[str] = []

    if profile and profile.get("summary"):
        sessions = int(profile.get("sessions") or 1)
        parts.append(
            f"Mémoire subreddit ({sessions} thread{'s' if sessions > 1 else ''} analysé"
            f"{'s' if sessions > 1 else ''}) : {profile['summary']}"
        )

    if analysis and analysis.get("summary"):
        parts.append(f"Style de CE thread : {analysis['summary']}")

    do_list: list[str] = []
    dont_list: list[str] = []
    if analysis:
        do_list.extend(str(x) for x in (analysis.get("do") or []) if x)
        dont_list.extend(str(x) for x in (analysis.get("dont") or []) if x)
    if profile:
        do_list.extend(str(x) for x in (profile.get("do") or []) if x)
        dont_list.extend(str(x) for x in (profile.get("dont") or []) if x)

    if do_list:
        parts.append("À faire : " + " · ".join(dict.fromkeys(do_list)[:5]))
    if dont_list:
        parts.append("À éviter : " + " · ".join(dict.fromkeys(dont_list)[:5]))

    if comments:
        parts.append("Vrais commentaires (top upvotes) — imite ce registre :")
        for c in comments[:6]:
            body = _clip_comment(c.get("body") or "", 260)
            if body:
                parts.append(f"  • [{int(c.get('score') or 0)}↑] {body}")

    snippets: list[str] = []
    if analysis and isinstance(analysis.get("example_snippets"), list):
        snippets.extend(str(s).strip() for s in analysis["example_snippets"] if s)
    if profile and isinstance(profile.get("snippets"), list):
        snippets.extend(str(s).strip() for s in profile["snippets"] if s)
    if snippets:
        unique = list(dict.fromkeys(snippets))[:4]
        parts.append("Formulations typiques : " + " | ".join(unique))

    parts.append(
        "Consigne finale : écris comme ces commentateurs, sur LE SUJET du post — "
        "pas d'anecdote random hors-sujet."
    )
    return "\n".join(parts)


def style_hints_from_analysis(analysis: dict | None) -> dict[str, Any]:
    if not analysis:
        return {}
    hints: dict[str, Any] = {}
    angles = analysis.get("angles")
    if isinstance(angles, list) and angles:
        hints["angles"] = tuple(str(a).strip() for a in angles if str(a).strip())[:6]
    try:
        mn = int(analysis.get("min_words") or 0)
        mx = int(analysis.get("max_words") or 0)
        if 5 <= mn <= 80:
            hints["min_words"] = mn
        if 10 <= mx <= 120:
            hints["max_words"] = mx
    except (TypeError, ValueError):
        pass
    return hints


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
    analysis = None

    if comments:
        analysis = await analyze_thread_comments(
            client,
            title=title,
            body=body,
            subreddit=subreddit,
            comments=comments,
        )

    profile_after = profile_before
    if comments or analysis:
        profile_after = merge_subreddit_profile(subreddit, analysis, comments)

    brief = None
    if comments or profile_after:
        brief = format_style_brief(
            analysis=analysis,
            profile=profile_after,
            comments=comments,
        )

    hints = style_hints_from_analysis(analysis)
    sessions = int((profile_after or {}).get("sessions") or 0)

    meta = {
        "comments_analyzed": len(comments),
        "style_learned": bool(brief),
        "subreddit_sessions": sessions,
        "had_profile": bool(profile_before),
    }
    return brief, hints, meta
