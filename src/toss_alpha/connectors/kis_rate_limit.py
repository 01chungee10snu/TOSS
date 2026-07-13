"""KIS Open API call-safety harness.

This module centralizes the operational protections needed for Korea Investment
& Securities (KIS) API calls:

* cross-process request pacing via a small state file;
* retry/backoff for known KIS rate-limit responses;
* optional audit events for post-run inspection.

It is intentionally lightweight and stdlib-only so it can run inside cron/shell
wrappers without extra dependencies.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

try:  # pragma: no cover - exercised on Linux/macOS in normal operation
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

RATE_LIMIT_ERROR_CODES = {"EGW00133", "EGW00215"}
RATE_LIMIT_TEXT_MARKERS = (
    "1분당 1회",
    "초당 거래건수",
    "허용 가능한 초당",
    "too many requests",
    "rate limit",
)


@dataclass(frozen=True)
class KisCallSafetyConfig:
    enabled: bool = True
    min_interval_sec: float = 0.60
    max_retries: int = 3
    base_delay_sec: float = 1.0
    max_delay_sec: float = 8.0
    state_path: Path = Path("reports/harness/kis_api_rate_limit_state.json")
    audit_path: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KisCallSafetyConfig":
        source = os.environ if env is None else env
        return cls(
            enabled=_env_bool(source.get("KIS_RATE_LIMIT_ENABLED"), default=True),
            min_interval_sec=_env_float(source.get("KIS_RATE_MIN_INTERVAL_SEC"), 0.60),
            max_retries=_env_int(source.get("KIS_RATE_MAX_RETRIES"), 3),
            base_delay_sec=_env_float(source.get("KIS_RATE_RETRY_BASE_DELAY_SEC"), 1.0),
            max_delay_sec=_env_float(source.get("KIS_RATE_RETRY_MAX_DELAY_SEC"), 8.0),
            state_path=Path(source.get("KIS_RATE_LIMIT_STATE_PATH") or "reports/harness/kis_api_rate_limit_state.json").expanduser(),
            # Audit is opt-in. The operational wrapper exports this path;
            # unit tests without explicit env must not pollute live harness logs.
            audit_path=(
                None
                if str(source.get("KIS_RATE_LIMIT_AUDIT_PATH", "")).strip().lower() in {"", "none", "off", "false"}
                else Path(str(source.get("KIS_RATE_LIMIT_AUDIT_PATH"))).expanduser()
            ),
        )


def kis_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    return request_with_kis_safety(lambda: requests.request(method, url, **kwargs), method=method, url=url)


def kis_post(url: str, **kwargs: Any) -> requests.Response:
    return request_with_kis_safety(lambda: requests.post(url, **kwargs), method="POST", url=url)


def request_with_kis_safety(call: Callable[[], requests.Response], *, method: str, url: str) -> requests.Response:
    """Execute one KIS HTTP call with process-safe pacing and retry/backoff."""
    config = KisCallSafetyConfig.from_env()
    if not config.enabled:
        return call()

    last_response: requests.Response | None = None
    attempts = max(1, config.max_retries + 1)
    for attempt in range(attempts):
        _pace_call(config, method=method, url=url)
        response = call()
        last_response = response
        if not _is_rate_limited(response):
            _audit(config, event="kis_call", method=method, url=url, attempt=attempt + 1, status_code=response.status_code, rate_limited=False)
            return response

        delay = _retry_delay(response, config, attempt)
        _audit(
            config,
            event="kis_rate_limited",
            method=method,
            url=url,
            attempt=attempt + 1,
            status_code=response.status_code,
            delay_sec=round(delay, 3),
            rate_limited=True,
            body=_safe_text(response)[:300],
        )
        if attempt < attempts - 1:
            time.sleep(delay)

    assert last_response is not None
    return last_response


def _pace_call(config: KisCallSafetyConfig, *, method: str, url: str) -> None:
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = config.state_path.with_suffix(config.state_path.suffix + ".lock")
    with _locked_file(lock_path):
        # Persist epoch seconds rather than time.monotonic().  A monotonic value
        # is process/boot-local; persisting it can create huge sleeps after a
        # reboot or when the state file is shared across runners.
        now = time.time()
        state = _load_json(config.state_path)
        last = _as_float(state.get("last_call_epoch"), 0.0)
        elapsed = now - last if last > 0 else config.min_interval_sec
        wait_sec = max(0.0, config.min_interval_sec - elapsed)
        # Fail open on corrupt/future timestamps instead of freezing a trading
        # loop for minutes/days; the next write repairs the state file.
        if wait_sec > min(config.min_interval_sec, 5.0):
            wait_sec = 0.0
        if wait_sec > 0:
            time.sleep(wait_sec)
            now = time.time()
        state.update(
            {
                "last_call_epoch": now,
                "last_call_wall_time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
                "last_method": method,
                "last_url_path": _safe_url_path(url),
            }
        )
        _write_json(config.state_path, state)


def _is_rate_limited(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    text = _safe_text(response).lower()
    if any(marker in text for marker in RATE_LIMIT_TEXT_MARKERS):
        return True
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, Mapping):
        code = str(body.get("msg_cd") or body.get("error_code") or "").strip()
        if code in RATE_LIMIT_ERROR_CODES:
            return True
        msg = str(body.get("msg1") or body.get("error_description") or "").lower()
        return any(marker in msg for marker in RATE_LIMIT_TEXT_MARKERS)
    return False


def _is_token_frequency_limited(response: requests.Response) -> bool:
    text = _safe_text(response)
    if "EGW00133" in text or "1분당 1회" in text:
        return True
    try:
        body = response.json()
    except Exception:
        return False
    if not isinstance(body, Mapping):
        return False
    code = str(body.get("msg_cd") or body.get("error_code") or "").strip()
    msg = str(body.get("msg1") or body.get("error_description") or "")
    return code == "EGW00133" or "1분당 1회" in msg


def _retry_delay(response: requests.Response, config: KisCallSafetyConfig, attempt: int) -> float:
    if _is_token_frequency_limited(response):
        try:
            return max(config.base_delay_sec, float(os.getenv("KIS_TOKEN_RETRY_DELAY_SEC", "65")))
        except Exception:
            return max(config.base_delay_sec, 65.0)
    retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    try:
        if retry_after is not None:
            return min(config.max_delay_sec, max(config.base_delay_sec, float(retry_after)))
    except Exception:
        pass
    exponential = config.base_delay_sec * (2 ** attempt)
    jitter = random.uniform(0.0, min(0.25, config.base_delay_sec / 2))
    return min(config.max_delay_sec, exponential + jitter)


def _audit(config: KisCallSafetyConfig, **record: Any) -> None:
    if config.audit_path is None:
        return
    try:
        config.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with config.audit_path.open("a", encoding="utf-8") as handle:
            payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


@contextmanager
def locked_path(path: str | os.PathLike[str]):
    """Cross-process exclusive file lock used by token cache and call pacing."""
    path_obj = Path(path).expanduser()
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    with _locked_file(path_obj):
        yield


@contextmanager
def _locked_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        Path(tmp_name).replace(path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _safe_text(response: requests.Response) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _safe_url_path(url: str) -> str:
    for marker in ("koreainvestment.com", "openapivts.koreainvestment.com"):
        if marker in url:
            return url.split(marker, 1)[-1]
    return url[-120:]


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(value: str | None, default: float) -> float:
    try:
        return float(str(value))
    except Exception:
        return default


def _env_int(value: str | None, default: int) -> int:
    try:
        return int(str(value))
    except Exception:
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default
