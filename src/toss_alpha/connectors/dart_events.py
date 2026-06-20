"""Read-only OpenDART disclosure events connector."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from toss_alpha.data.schema import DisclosureEvent

BASE_URL = "https://opendart.fss.or.kr/api"


@dataclass(frozen=True)
class DartEventsClient:
    api_key: str | None = None
    base_url: str = BASE_URL
    timeout: int = 20

    def list_disclosures(
        self,
        *,
        corp_code: str,
        begin_date: str | None = None,
        end_date: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
        bsn_tp: str | None = None,
    ) -> list[DisclosureEvent]:
        if not self.api_key:
            raise ValueError("api_key is required")
        params: dict[str, Any] = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "page_no": page_no,
            "page_count": page_count,
        }
        if begin_date:
            params["bgn_de"] = begin_date
        if end_date:
            params["end_de"] = end_date
        if bsn_tp:
            params["bsn_tp"] = bsn_tp
        response = requests.get(f"{self.base_url}/list.json", params=params, timeout=self.timeout)
        payload = response.json()
        if not response.ok:
            raise RuntimeError(f"OpenDART HTTP error: {response.status_code} {response.text[:500]}")
        if payload.get("status") != "000":
            raise RuntimeError(f"OpenDART error: {payload.get('status')} {payload.get('message')}")
        return [_to_disclosure_event(item) for item in payload.get("list", []) if isinstance(item, dict)]


def _to_disclosure_event(item: dict[str, Any]) -> DisclosureEvent:
    reported_at = _parse_yyyymmdd(str(item.get("rcept_dt") or ""))
    receipt_no = str(item.get("rcept_no") or "").strip()
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}" if receipt_no else None
    return DisclosureEvent(
        symbol=str(item.get("stock_code") or "").zfill(6),
        event_type="disclosure",
        title=str(item.get("report_nm") or "").strip(),
        reported_at=reported_at,
        available_at=reported_at,
        url=url,
        source="opendart",
        raw=dict(item),
    )


def _parse_yyyymmdd(value: str) -> datetime:
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"invalid yyyymmdd date: {value!r}")
    dt = datetime.strptime(value, "%Y%m%d")
    return dt.replace(tzinfo=timezone.utc)
