"""Récupère le titre + corps d'un post Reddit depuis une URL."""

from __future__ import annotations

import html as html_lib
import os
import re
from urllib.parse import urlparse

import httpx

_REDDIT_HOSTS = ("reddit.com", "www.reddit.com", "old.reddit.com", "redd.it")
_COMMENTS_PATH = re.compile(r"(/r/[^/]+/comments/[^/?#]+)", re.I)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}


def _is_reddit_url(url: str) -> bool:
    try:
        host = urlparse(url.strip()).netloc.lower().removeprefix("www.")
        return any(host == h or host.endswith("." + h) for h in _REDDIT_HOSTS)
    except Exception:
        return False


def _comments_path(url: str) -> str | None:
    m = _COMMENTS_PATH.search(urlparse(url).path)
    return m.group(1) if m else None


def _sub_from_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "r":
        return f"r/{parts[1]}"
    return ""


def _post_id_from_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "r" and parts[2] == "comments":
        return parts[3]
    return ""


def _canonical_reddit_url(path: str) -> str:
    return f"https://www.reddit.com{path.rstrip('/')}/"


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"</p>\s*", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_post(payload: list) -> tuple[str, str, str]:
    """Retourne (titre, corps, subreddit) depuis l'API JSON."""
    post = payload[0]["data"]["children"][0]["data"]
    title = (post.get("title") or "").strip()
    body = (post.get("selftext") or "").strip()
    sub = (post.get("subreddit_name_prefixed") or post.get("subreddit") or "").strip()
    if not body and post.get("is_video"):
        body = "[Post vidéo — contexte limité au titre]"
    elif not body and post.get("url"):
        body = f"Lien partagé : {post.get('url')}"
    return title, body, sub


def _parse_old_reddit_html(page: str, path: str) -> tuple[str, str, str]:
    """Parse la page HTML old.reddit (fallback quand .json est bloqué)."""
    title_m = re.search(r'property="og:title" content="([^"]+)"', page)
    if not title_m:
        title_m = re.search(r'<a class="title[^"]*"[^>]*>([^<]+)</a>', page)
    title = html_lib.unescape(title_m.group(1).strip()) if title_m else ""

    sub = _sub_from_path(path)

    body = ""
    body_m = re.search(
        r'<div[^>]+id="thing_t3_[^"]+"[^>]*>.*?'
        r'<div class="usertext-body[^"]*">\s*<div class="md">(.*?)</div>\s*</div>',
        page,
        re.S | re.I,
    )
    if not body_m:
        body_m = re.search(
            r'<div class="expando[^"]*">.*?<div class="md">(.*?)</div>',
            page,
            re.S | re.I,
        )
    if body_m:
        body = _strip_html(body_m.group(1))

    return title, body, sub


async def _resolve_post_path(client: httpx.AsyncClient, url: str) -> tuple[str, list[str]]:
    u = url.strip().split("?")[0].rstrip("/")
    host = urlparse(u).netloc.lower().removeprefix("www.")

    if host == "redd.it" or not _comments_path(u):
        r = await client.get(u, headers=_HEADERS)
        r.raise_for_status()
        u = str(r.url).split("?")[0].rstrip("/")

    path = _comments_path(u)
    if not path:
        raise ValueError("URL Reddit invalide — il faut un lien vers un post (/comments/...).")

    page_path = urlparse(u).path.rstrip("/")
    candidates = []
    if page_path.count("/") >= 5:
        candidates.append(f"https://old.reddit.com{page_path}/")
        candidates.append(f"https://www.reddit.com{page_path}/")
    candidates.append(f"https://old.reddit.com{path}/")
    candidates.append(f"https://www.reddit.com{path}/")

    seen: set[str] = set()
    page_urls = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            page_urls.append(item)

    return path, page_urls


async def _fetch_html(client: httpx.AsyncClient, page_urls: list[str]) -> str:
    last_err: Exception | None = None
    for page_url in page_urls:
        try:
            r = await client.get(page_url, headers=_HEADERS)
            if r.status_code == 200 and "og:title" in r.text:
                return r.text
            last_err = httpx.HTTPStatusError(
                f"HTTP {r.status_code}", request=r.request, response=r
            )
        except httpx.HTTPError as e:
            last_err = e
    if last_err:
        raise last_err
    raise httpx.HTTPError("Aucune page Reddit accessible.")


async def _fetch_via_oembed(
    client: httpx.AsyncClient, canonical_url: str, path: str
) -> tuple[str, str, str] | None:
    try:
        r = await client.get(
            "https://www.reddit.com/oembed",
            params={"url": canonical_url, "format": "json"},
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None

    title = (data.get("title") or "").strip()
    if not title:
        return None

    sub = _sub_from_path(path)
    embed_html = data.get("html") or ""
    sub_m = re.search(r'href="https://www\.reddit\.com/r/([^/]+)/"', embed_html, re.I)
    if sub_m:
        sub = f"r/{sub_m.group(1)}"

    return title, "", sub


async def _fetch_via_pullpush(
    client: httpx.AsyncClient, post_id: str, path: str
) -> tuple[str, str, str] | None:
    if not post_id:
        return None
    try:
        r = await client.get(
            "https://api.pullpush.io/reddit/search/submission/",
            params={"ids": post_id},
            headers=_HEADERS,
            timeout=15.0,
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows:
            return None
        post = rows[0]
    except (httpx.HTTPError, ValueError, IndexError, TypeError):
        return None

    title = (post.get("title") or "").strip()
    if not title:
        return None

    body = (post.get("selftext") or "").strip()
    sub = (post.get("subreddit_name_prefixed") or post.get("subreddit") or "").strip()
    if not sub:
        sub = _sub_from_path(path)
    return title, body, sub


async def _fetch_via_oauth(client: httpx.AsyncClient, path: str) -> list | None:
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        return None

    token_r = await client.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, secret),
        headers={"User-Agent": _HEADERS["User-Agent"]},
    )
    if token_r.status_code != 200:
        return None

    token = token_r.json().get("access_token")
    if not token:
        return None

    parts = path.strip("/").split("/")
    if len(parts) < 4 or parts[0] != "r" or parts[2] != "comments":
        return None
    post_id = parts[3]

    api_r = await client.get(
        f"https://oauth.reddit.com/comments/{post_id}",
        headers={
            "User-Agent": _HEADERS["User-Agent"],
            "Authorization": f"bearer {token}",
        },
        params={"limit": 1, "depth": 0},
    )
    if api_r.status_code != 200:
        return None
    data = api_r.json()
    if isinstance(data, list) and data:
        return data
    return None


def _result(title: str, body: str, sub: str, source_url: str) -> dict:
    if not title:
        raise ValueError("Impossible de lire le titre du post.")
    if sub and not sub.startswith("r/"):
        sub = f"r/{sub}"
    return {
        "title": title,
        "body": body,
        "subreddit": sub or "r/...",
        "source_url": source_url,
    }


async def fetch_reddit_post(url: str) -> dict:
    if not _is_reddit_url(url):
        raise ValueError("Ce n'est pas une URL Reddit valide.")

    source = url.strip()
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, http2=False) as client:
        path, page_urls = await _resolve_post_path(client, source)
        post_id = _post_id_from_path(path)
        canonical = _canonical_reddit_url(path)

        try:
            page = await _fetch_html(client, page_urls)
            title, body, sub = _parse_old_reddit_html(page, path)
            if title:
                return _result(title, body, sub, source)
        except httpx.HTTPError:
            pass

        data = await _fetch_via_oauth(client, path)
        if data:
            title, body, sub = _extract_post(data)
            if not sub:
                sub = _sub_from_path(path)
            return _result(title, body, sub, source)

        pullpush = await _fetch_via_pullpush(client, post_id, path)
        if pullpush:
            title, body, sub = pullpush
            return _result(title, body, sub, source)

        oembed = await _fetch_via_oembed(client, canonical, path)
        if oembed:
            title, body, sub = oembed
            return _result(title, body, sub, source)

    raise ValueError(
        "Impossible de charger le post Reddit. "
        "Essaie l'onglet « Texte » et colle le titre + corps du post."
    )


def clean_pasted_text(text: str) -> str:
    return text.strip()
