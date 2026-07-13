"""Shared KIS access-token cache helpers.

KIS rejects frequent oauth2/tokenP calls. The live loop may touch both read-only
market/account endpoints and live-order endpoints, so token issuance must be
centralized and cached across modules/processes.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from toss_alpha.connectors.kis_rate_limit import locked_path


TokenFetcher = Callable[[], Mapping[str, Any]]


def cached_kis_access_token(
    *,
    app_key: str,
    base_url: str,
    fetch_token: TokenFetcher,
    cache_path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Return a cached KIS access token, fetching once when absent/expired.

    Set ``KIS_ACCESS_TOKEN_CACHE`` or ``KIS_TOKEN_CACHE_PATH`` to enable a
    durable cache. With no cache path this behaves like a direct token fetch,
    preserving test/default behavior.
    """
    resolved_cache = cache_path or os.getenv("KIS_ACCESS_TOKEN_CACHE") or os.getenv("KIS_TOKEN_CACHE_PATH")
    if not resolved_cache:
        return _extract_access_token(fetch_token())

    path = Path(resolved_cache).expanduser()
    key = _cache_key(app_key=app_key, base_url=base_url)
    current = now or datetime.now(timezone.utc)

    # KIS rejects token issuance bursts (observed EGW00133: about 1/minute).
    # Keep the whole read-check-fetch-write sequence under one cross-process
    # lock so concurrent cron/manual processes do not all miss the cache and
    # stampede oauth2/tokenP at the same time.
    with locked_path(path.with_suffix(path.suffix + ".lock")):
        return _cached_kis_access_token_locked(
            app_key=app_key,
            base_url=base_url,
            fetch_token=fetch_token,
            path=path,
            key=key,
            current=current,
        )


def _cached_kis_access_token_locked(
    *,
    app_key: str,
    base_url: str,
    fetch_token: TokenFetcher,
    path: Path,
    key: str,
    current: datetime,
) -> str:
    cache = _load_cache(path)
    entry = (cache.get("tokens") or {}).get(key) if isinstance(cache.get("tokens"), dict) else None
    if isinstance(entry, dict):
        token = entry.get("access_token")
        expires_at = _parse_datetime(entry.get("expires_at"))
        if token and expires_at and expires_at > current + timedelta(minutes=5):
            return str(token)

    _pace_token_issue(cache, key=key)
    payload = dict(fetch_token())
    token = _extract_access_token(payload)
    expires_at = _resolve_expires_at(payload, current)
    cache.setdefault("tokens", {})[key] = {
        "access_token": token,
        "expires_at": expires_at.isoformat(),
        "base_url_hash": hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:12],
        "app_key_hash": hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:12],
        "last_token_fetch_epoch": time.time(),
    }
    _write_cache(path, cache)
    return token


def _pace_token_issue(cache: dict[str, Any], *, key: str) -> None:
    """Respect KIS token issuance frequency before calling oauth2/tokenP.

    KIS can reject token issuance faster than roughly once per minute. This
    wait happens under the token-cache lock, so concurrent processes queue
    instead of stampeding tokenP and producing EGW00133.
    """
    try:
        interval = float(os.getenv("KIS_TOKEN_MIN_ISSUE_INTERVAL_SEC", "65"))
    except Exception:
        interval = 65.0
    if interval <= 0:
        return
    entry = (cache.get("tokens") or {}).get(key) if isinstance(cache.get("tokens"), dict) else None
    last = 0.0
    if isinstance(entry, dict):
        try:
            last = float(entry.get("last_token_fetch_epoch") or 0.0)
        except Exception:
            last = 0.0
    if last <= 0:
        return
    now = time.time()
    wait = interval - (now - last)
    # Repair corrupt/future timestamps rather than freezing the loop.
    if wait > min(interval, 120.0):
        return
    if wait > 0:
        time.sleep(wait)


def _cache_key(*, app_key: str, base_url: str) -> str:
    return hashlib.sha256(f"{base_url}\0{app_key}".encode("utf-8")).hexdigest()


def _extract_access_token(payload: Mapping[str, Any]) -> str:
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"token response has no access_token: {dict(payload)}")
    return str(token)


def _resolve_expires_at(payload: Mapping[str, Any], now: datetime) -> datetime:
    expires_in = payload.get("expires_in") or payload.get("expires_in_sec")
    try:
        seconds = int(str(expires_in))
        if seconds > 0:
            return now + timedelta(seconds=max(60, seconds - 300))
    except Exception:
        pass

    explicit = payload.get("access_token_token_expired") or payload.get("expires_at")
    parsed = _parse_datetime(explicit)
    if parsed:
        return parsed - timedelta(minutes=5)

    return now + timedelta(hours=23)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def _write_cache(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
