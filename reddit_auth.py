"""OAuth Reddit + scan subreddit + publication de commentaires."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from storage_paths import data_dir

OAUTH_SCOPES = "read submit identity"
_oauth_states: dict[str, float] = {}


def _auth_file() -> Path:
    return data_dir() / "reddit_auth.json"


def _ua() -> str:
    return os.getenv(
        "REDDIT_USER_AGENT",
        "Redly:1.0.0 (by /u/RedlyLocal)",
    ).strip()


def _client_id() -> str:
    return os.getenv("REDDIT_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.getenv("REDDIT_CLIENT_SECRET", "").strip()


def redirect_uri() -> str:
    return os.getenv(
        "REDDIT_REDIRECT_URI",
        "http://127.0.0.1:8765/api/reddit/auth/callback",
    ).strip()


def credentials_configured() -> bool:
    return bool(_client_id() and _client_secret())


def _load_auth() -> dict:
    path = _auth_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_auth(data: dict) -> None:
    path = _auth_file()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def auth_status() -> dict:
    data = _load_auth()
    connected = bool(data.get("refresh_token"))
    return {
        "configured": credentials_configured(),
        "connected": connected,
        "username": data.get("username", "") if connected else "",
    }


def logout() -> None:
    path = _auth_file()
    if path.exists():
        path.unlink(missing_ok=True)


def start_oauth() -> str:
    if not credentials_configured():
        raise RuntimeError(
            "Ajoute REDDIT_CLIENT_ID et REDDIT_CLIENT_SECRET dans .env "
            "(app sur reddit.com/prefs/apps, type « web app »)."
        )
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = time.time()
    _purge_states()
    params = {
        "client_id": _client_id(),
        "response_type": "code",
        "state": state,
        "redirect_uri": redirect_uri(),
        "duration": "permanent",
        "scope": OAUTH_SCOPES,
    }
    return f"https://www.reddit.com/api/v1/authorize?{urlencode(params)}"


def _purge_states() -> None:
    now = time.time()
    expired = [k for k, t in _oauth_states.items() if now - t > 600]
    for k in expired:
        _oauth_states.pop(k, None)


def verify_state(state: str) -> bool:
    _purge_states()
    return state in _oauth_states


def pop_state(state: str) -> None:
    _oauth_states.pop(state, None)


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0, http2=False) as client:
        r = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(),
            },
            auth=(_client_id(), _client_secret()),
            headers={"User-Agent": _ua()},
        )
        r.raise_for_status()
        tok = r.json()

        access = tok.get("access_token", "")
        refresh = tok.get("refresh_token", "")
        if not refresh:
            raise RuntimeError("Reddit n'a pas renvoyé de refresh_token.")

        me = await client.get(
            "https://oauth.reddit.com/api/v1/me",
            headers={"User-Agent": _ua(), "Authorization": f"bearer {access}"},
        )
        me.raise_for_status()
        username = me.json().get("name", "")

    data = {
        "refresh_token": refresh,
        "access_token": access,
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
        "username": username,
    }
    _save_auth(data)
    return {"username": username}


async def _refresh_access_token(client: httpx.AsyncClient, data: dict) -> str:
    if data.get("access_token") and time.time() < data.get("expires_at", 0):
        return data["access_token"]

    r = await client.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "refresh_token", "refresh_token": data["refresh_token"]},
        auth=(_client_id(), _client_secret()),
        headers={"User-Agent": _ua()},
    )
    r.raise_for_status()
    tok = r.json()
    data["access_token"] = tok["access_token"]
    data["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    _save_auth(data)
    return data["access_token"]


async def _auth_headers(client: httpx.AsyncClient) -> dict:
    data = _load_auth()
    if not data.get("refresh_token"):
        raise RuntimeError("Compte Reddit non connecté — clique « Connecter Reddit ».")
    token = await _refresh_access_token(client, data)
    return {"User-Agent": _ua(), "Authorization": f"bearer {token}"}


def _norm_sub(name: str) -> str:
    name = name.strip().removeprefix("r/").removeprefix("R/")
    if not name or "/" in name:
        raise ValueError("Nom de subreddit invalide.")
    return name


async def scan_subreddit(
    *,
    subreddit: str,
    sort: str = "new",
    limit: int = 10,
    min_score: int = 0,
    max_age_hours: int = 48,
    keyword: str = "",
    text_only: bool = False,
) -> list[dict]:
    sub = _norm_sub(subreddit)
    sort = sort if sort in ("hot", "new", "rising", "top") else "new"
    limit = max(1, min(limit, 25))
    keyword_l = keyword.strip().lower()
    cutoff = time.time() - max_age_hours * 3600

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, http2=False) as client:
        headers = await _auth_headers(client)
        r = await client.get(
            f"https://oauth.reddit.com/r/{sub}/{sort}",
            headers=headers,
            params={"limit": limit, "raw_json": 1},
        )
        r.raise_for_status()
        payload = r.json()

    posts = []
    for child in payload.get("data", {}).get("children", []):
        p = child.get("data") or {}
        if p.get("stickied"):
            continue
        created = float(p.get("created_utc") or 0)
        if created < cutoff:
            continue
        score = int(p.get("score") or 0)
        if score < min_score:
            continue
        title = (p.get("title") or "").strip()
        if keyword_l and keyword_l not in title.lower():
            continue
        body = (p.get("selftext") or "").strip()
        if text_only and not body:
            continue
        post_id = p.get("id") or ""
        if not post_id:
            continue
        permalink = p.get("permalink") or ""
        posts.append(
            {
                "post_id": post_id,
                "title": title,
                "body": body,
                "subreddit": f"r/{p.get('subreddit', sub)}",
                "score": score,
                "url": f"https://www.reddit.com{permalink}",
                "created_utc": created,
            }
        )
    return posts


async def post_comment(*, post_id: str, text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Commentaire vide.")
    thing_id = f"t3_{post_id}"

    async with httpx.AsyncClient(timeout=25.0, http2=False) as client:
        headers = await _auth_headers(client)
        r = await client.post(
            "https://oauth.reddit.com/api/comment",
            headers=headers,
            data={"thing_id": thing_id, "text": text, "api_type": "json"},
        )
        r.raise_for_status()
        data = r.json()

    errors = data.get("json", {}).get("errors") or []
    if errors:
        msg = errors[0][1] if errors[0] else str(errors)
        raise RuntimeError(f"Reddit a refusé : {msg}")

    things = data.get("json", {}).get("data", {}).get("things") or []
    if things:
        fullname = things[0].get("data", {}).get("name", "")
        if fullname.startswith("t1_"):
            comment_id = fullname[3:]
            return f"https://www.reddit.com/comments/{post_id}/comment/{comment_id}"
    return ""
