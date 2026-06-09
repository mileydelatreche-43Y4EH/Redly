"""Serveur local Redly — réponses Reddit via Claude."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_providers import generate_replies
from history_store import add_item as history_add
from history_store import clear_all, delete_item, get_item, list_items
from queue_store import (
    add_item as queue_add,
    clear_done,
    delete_item as queue_delete,
    get_item as queue_get,
    list_queue,
    mark_error,
    mark_posted,
    mark_skipped,
    update_reply,
)
from reddit_auth import (
    auth_status,
    credentials_configured,
    exchange_code,
    logout,
    pop_state,
    post_comment,
    scan_subreddit,
    start_oauth,
    verify_state,
)
from reddit_fetch import fetch_reddit_post

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

if not os.getenv("ANTHROPIC_API_KEY", "").strip():
    import sys

    print("⚠ ANTHROPIC_API_KEY manquante dans reddit-reply/.env", file=sys.stderr)

app = FastAPI(title="Redly", docs_url=None, redoc_url=None)


def _normalize_post(title: str, body: str) -> tuple[str, str]:
    title = (title or "").strip()
    body = (body or "").strip()
    if not title and body:
        return body, ""
    if not body or body == title:
        return title or body, ""
    return title, body


def _parse_text_content(content: str) -> tuple[str, str]:
    text = content.strip()
    if not text:
        return "", ""
    parts = text.split("\n", 1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip() and len(parts[0]) < 300:
        return _normalize_post(parts[0].strip(), parts[1].strip())
    return _normalize_post(text, "")


class GenerateRequest(BaseModel):
    mode: str = Field(..., pattern="^(link|text)$")
    content: str = Field(..., min_length=3, max_length=50_000)
    tone: str = "naturel et humain"
    count: int = Field(4, ge=2, le=6)


class GenerateResponse(BaseModel):
    title: str
    body: str
    subreddit: str
    replies: list[str]
    elapsed_ms: int = 0
    history_id: str = ""


class HistoryItemSummary(BaseModel):
    id: str
    created_at: str
    title: str
    subreddit: str
    reply_count: int
    mode: str


class ScanRequest(BaseModel):
    subreddit: str = Field(..., min_length=2, max_length=50)
    sort: str = "new"
    limit: int = Field(8, ge=1, le=25)
    min_score: int = Field(0, ge=0)
    max_age_hours: int = Field(48, ge=1, le=168)
    keyword: str = ""
    text_only: bool = False
    tone: str = "naturel et humain"


class QueueReplyUpdate(BaseModel):
    reply_text: str = Field(..., min_length=1, max_length=10_000)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/auto")
async def auto_page():
    return FileResponse(ROOT / "static" / "auto.html")


@app.post("/api/generate", response_model=GenerateResponse)
async def api_generate(req: GenerateRequest):
    import time

    t0 = time.perf_counter()
    content = req.content.strip()
    title = ""
    body = ""
    subreddit = ""

    try:
        if req.mode == "link":
            post = await fetch_reddit_post(content)
            title, body = _normalize_post(post["title"], post["body"])
            subreddit = post["subreddit"]
        else:
            title, body = _parse_text_content(content)
            subreddit = ""
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Lien Reddit inaccessible : {e}") from e

    try:
        replies = await generate_replies(
            title=title,
            body=body,
            subreddit=subreddit,
            tone=req.tone,
            count=req.count,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:200].replace("\n", " ")
        raise HTTPException(502, f"Erreur Claude ({e.response.status_code}). {detail}") from e
    except httpx.TimeoutException as e:
        raise HTTPException(504, "Délai dépassé — réduis le nombre de réponses.") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Erreur réseau : {e}") from e

    elapsed = int((time.perf_counter() - t0) * 1000)
    entry = history_add(
        mode=req.mode,
        content=content,
        title=title,
        body=body,
        subreddit=subreddit or "r/...",
        tone=req.tone,
        count=req.count,
        replies=replies,
        elapsed_ms=elapsed,
    )
    return GenerateResponse(
        title=title,
        body=body,
        subreddit=subreddit or "r/...",
        replies=replies,
        elapsed_ms=elapsed,
        history_id=entry["id"],
    )


@app.get("/api/history", response_model=list[HistoryItemSummary])
async def api_history_list():
    return list_items()


@app.get("/api/history/{item_id}")
async def api_history_get(item_id: str):
    item = get_item(item_id)
    if not item:
        raise HTTPException(404, "Entrée introuvable.")
    return item


@app.delete("/api/history/{item_id}")
async def api_history_delete(item_id: str):
    if not delete_item(item_id):
        raise HTTPException(404, "Entrée introuvable.")
    return {"ok": True}


@app.delete("/api/history")
async def api_history_clear():
    n = clear_all()
    return {"ok": True, "deleted": n}


# --- Mode auto : OAuth + scan + file + publication ---


@app.get("/api/reddit/auth/status")
async def api_reddit_status():
    return auth_status()


@app.get("/api/reddit/auth/start")
async def api_reddit_auth_start():
    try:
        url = start_oauth()
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse(url)


@app.get("/api/reddit/auth/callback")
async def api_reddit_auth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    if error:
        return RedirectResponse(f"/auto?auth_error={error}")
    if not code or not verify_state(state):
        return RedirectResponse("/auto?auth_error=invalid_state")
    pop_state(state)
    try:
        await exchange_code(code)
    except Exception as e:
        return RedirectResponse(f"/auto?auth_error={str(e)[:120]}")
    return RedirectResponse("/auto?auth_ok=1")


@app.post("/api/reddit/auth/logout")
async def api_reddit_logout():
    logout()
    return {"ok": True}


@app.post("/api/reddit/run")
async def api_reddit_run(req: ScanRequest):
    """Scanne un subreddit, génère une réponse par post, met en file d'attente."""
    if not credentials_configured():
        raise HTTPException(
            400,
            "Configure REDDIT_CLIENT_ID et REDDIT_CLIENT_SECRET dans .env.",
        )
    status = auth_status()
    if not status["connected"]:
        raise HTTPException(400, "Connecte ton compte Reddit d'abord.")

    try:
        posts = await scan_subreddit(
            subreddit=req.subreddit,
            sort=req.sort,
            limit=req.limit,
            min_score=req.min_score,
            max_age_hours=req.max_age_hours,
            keyword=req.keyword,
            text_only=req.text_only,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Erreur Reddit ({e.response.status_code}).") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Réseau Reddit : {e}") from e

    added = []
    skipped = 0
    for post in posts:
        entry = queue_add(
            post_id=post["post_id"],
            title=post["title"],
            body=post["body"],
            subreddit=post["subreddit"],
            score=post["score"],
            url=post["url"],
        )
        if not entry:
            skipped += 1
            continue
        try:
            replies = await generate_replies(
                title=post["title"],
                body=post["body"],
                subreddit=post["subreddit"],
                tone=req.tone,
                count=1,
            )
            entry = update_reply(entry["id"], replies[0]) or entry
        except Exception as e:
            mark_error(entry["id"], str(e))
            entry = queue_get(entry["id"]) or entry
        added.append(entry)

    return {
        "scanned": len(posts),
        "queued": len(added),
        "skipped_duplicates": skipped,
        "items": added,
    }


@app.get("/api/reddit/queue")
async def api_reddit_queue(status: str | None = None):
    return list_queue(status=status)


@app.patch("/api/reddit/queue/{item_id}")
async def api_reddit_queue_update(item_id: str, req: QueueReplyUpdate):
    item = update_reply(item_id, req.reply_text)
    if not item:
        raise HTTPException(404, "Entrée introuvable.")
    return item


@app.post("/api/reddit/queue/{item_id}/publish")
async def api_reddit_queue_publish(item_id: str):
    item = queue_get(item_id)
    if not item:
        raise HTTPException(404, "Entrée introuvable.")
    if item.get("status") == "posted":
        raise HTTPException(400, "Déjà publié.")
    if not item.get("reply_text", "").strip():
        raise HTTPException(400, "Réponse vide — édite le texte d'abord.")

    try:
        posted_url = await post_comment(
            post_id=item["post_id"],
            text=item["reply_text"],
        )
        item = mark_posted(item_id, posted_url) or item
    except RuntimeError as e:
        mark_error(item_id, str(e))
        raise HTTPException(400, str(e)) from e
    except httpx.HTTPStatusError as e:
        msg = f"Reddit ({e.response.status_code})"
        mark_error(item_id, msg)
        raise HTTPException(502, msg) from e
    except httpx.HTTPError as e:
        mark_error(item_id, str(e))
        raise HTTPException(502, str(e)) from e

    return item


@app.post("/api/reddit/queue/{item_id}/skip")
async def api_reddit_queue_skip(item_id: str):
    item = mark_skipped(item_id)
    if not item:
        raise HTTPException(404, "Entrée introuvable.")
    return item


@app.delete("/api/reddit/queue/{item_id}")
async def api_reddit_queue_delete(item_id: str):
    if not queue_delete(item_id):
        raise HTTPException(404, "Entrée introuvable.")
    return {"ok": True}


@app.delete("/api/reddit/queue")
async def api_reddit_queue_clear_done():
    n = clear_done()
    return {"ok": True, "deleted": n}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
