"""File d'attente : posts scannés → réponse générée → validation → publication."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "data" / "queue.json"


def _ensure() -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        QUEUE_FILE.write_text('{"items":[]}', encoding="utf-8")


def _load() -> dict:
    _ensure()
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"items": []}


def _save(data: dict) -> None:
    _ensure()
    QUEUE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_queue(status: str | None = None) -> list[dict]:
    items = _load()["items"]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if status:
        items = [it for it in items if it.get("status") == status]
    return items


def get_item(item_id: str) -> dict | None:
    for it in _load()["items"]:
        if it.get("id") == item_id:
            return it
    return None


def has_post(post_id: str) -> bool:
    for it in _load()["items"]:
        if it.get("post_id") == post_id and it.get("status") in ("pending", "posted"):
            return True
    return False


def add_item(
    *,
    post_id: str,
    title: str,
    body: str,
    subreddit: str,
    score: int,
    url: str,
    reply_text: str = "",
) -> dict | None:
    if has_post(post_id):
        return None
    entry = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "post_id": post_id,
        "url": url,
        "subreddit": subreddit,
        "title": title,
        "body": body,
        "score": score,
        "reply_text": reply_text,
        "posted_url": "",
        "error": "",
    }
    data = _load()
    data["items"].insert(0, entry)
    _save(data)
    return entry


def update_reply(item_id: str, reply_text: str) -> dict | None:
    data = _load()
    for it in data["items"]:
        if it.get("id") == item_id:
            it["reply_text"] = reply_text.strip()
            _save(data)
            return it
    return None


def mark_posted(item_id: str, posted_url: str) -> dict | None:
    data = _load()
    for it in data["items"]:
        if it.get("id") == item_id:
            it["status"] = "posted"
            it["posted_url"] = posted_url
            it["error"] = ""
            _save(data)
            return it
    return None


def mark_skipped(item_id: str) -> dict | None:
    data = _load()
    for it in data["items"]:
        if it.get("id") == item_id:
            it["status"] = "skipped"
            _save(data)
            return it
    return None


def mark_error(item_id: str, error: str) -> dict | None:
    data = _load()
    for it in data["items"]:
        if it.get("id") == item_id:
            it["error"] = error[:500]
            _save(data)
            return it
    return None


def delete_item(item_id: str) -> bool:
    data = _load()
    before = len(data["items"])
    data["items"] = [it for it in data["items"] if it.get("id") != item_id]
    if len(data["items"]) == before:
        return False
    _save(data)
    return True


def clear_done() -> int:
    data = _load()
    before = len(data["items"])
    data["items"] = [
        it for it in data["items"] if it.get("status") not in ("posted", "skipped")
    ]
    n = before - len(data["items"])
    _save(data)
    return n
