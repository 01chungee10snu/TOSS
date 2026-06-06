from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _ensure_local_opendart_on_path() -> None:
    """Prefer the user's existing C:/Github/OpenDart repo if present."""
    local = Path("/mnt/c/Github/OpenDart")
    if local.exists() and str(local.parent) not in sys.path:
        sys.path.insert(0, str(local.parent))


def get_dart_reader() -> Any:
    """Create an OpenDartReader using OPENDART_API_KEY.

    Requires either the local /mnt/c/Github/OpenDart repo or pip package
    OpenDartReader. This module intentionally does not read other repos' .env
    files; put OPENDART_API_KEY in this project's .env if you want DART data.
    """
    api_key = os.getenv("OPENDART_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENDART_API_KEY in .env to use DART integration")
    _ensure_local_opendart_on_path()
    try:
        import OpenDartReader  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError("Install OpenDartReader or keep /mnt/c/Github/OpenDart available") from exc
    return OpenDartReader(api_key)


def recent_filings(symbol: str, *, start: str | None = None):
    dart = get_dart_reader()
    return dart.list(symbol, start=start)
