"""Historique des générations (JSON local)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storage_paths import data_dir

MAX_ITEMS = 50


def _history_file() -> Path:
    return data_dir() / "history.json"


def _ensure() -> None:
    path = _history_file()
    if not path.exists():
        path.write_text('{"items":[]}', encoding="utf-8")


def _load() -> dict:
    _ensure()
    try:
        data = json.loads(_history_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"items": []}


def _save(data: dict) -> None:
    _ensure()
    _history_file().write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_items() -> list[dict]:
    items = _load()["items"]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    out = []
    for it in items:
        out.append(
            {
                "id": it["id"],
                "created_at": it.get("created_at", ""),
                "title": it.get("title", ""),
                "subreddit": it.get("subreddit", ""),
                "reply_count": len(it.get("replies") or []),
                "mode": it.get("mode", "text"),
            }
        )
    return out


def get_item(item_id: str) -> dict | None:
    for it in _load()["items"]:
        if it.get("id") == item_id:
            return it
    return None


def add_item(
    *,
    mode: str,
    content: str,
    title: str,
    body: str,
    subreddit: str,
    tone: str,
    count: int,
    replies: list[str],
    elapsed_ms: int,
) -> dict:
    data = _load()
    entry = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "content": content[:5000],
        "title": title,
        "body": body,
        "subreddit": subreddit,
        "tone": tone,
        "count": count,
        "replies": replies,
        "elapsed_ms": elapsed_ms,
    }
    data["items"].insert(0, entry)
    data["items"] = data["items"][:MAX_ITEMS]
    _save(data)
    return entry


def delete_item(item_id: str) -> bool:
    data = _load()
    before = len(data["items"])
    data["items"] = [it for it in data["items"] if it.get("id") != item_id]
    if len(data["items"]) == before:
        return False
    _save(data)
    return True


def clear_all() -> int:
    data = _load()
    n = len(data["items"])
    data["items"] = []
    _save(data)
    return n
