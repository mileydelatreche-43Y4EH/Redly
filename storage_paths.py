"""Répertoire data : local ou /tmp sur Vercel (filesystem read-only)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def data_dir() -> Path:
    if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        path = Path("/tmp/redly-data")
    else:
        path = ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
