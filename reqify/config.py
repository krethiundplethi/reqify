from __future__ import annotations

import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data" / "sessions"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def safe_name(name: str) -> str:
    base = Path(name or "document.reqif").name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip()
    return cleaned or "document.reqif"

