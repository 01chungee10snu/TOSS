"""Read-only OpenDART original-filing XBRL archive connector.

The ordinary company-account APIs can reflect later corrections.  For strict
PIT research we archive the XBRL package attached to a specific disclosure
receipt number and preserve the disclosure receipt date separately.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

BASE_URL = "https://opendart.fss.or.kr/api"
PERIOD_RE = re.compile(r"\((\d{4})\.(\d{2})\)")


@dataclass(frozen=True)
class DartXbrlArchiveClient:
    api_key: str
    base_url: str = BASE_URL
    timeout: int = 30

    def _require_key(self) -> None:
        if not str(self.api_key or "").strip():
            raise ValueError("OpenDART api_key is required")

    def corp_code_table(self) -> list[dict[str, str]]:
        """Download and parse OpenDART's corporation-code archive."""
        self._require_key()
        response = requests.get(
            f"{self.base_url}/corpCode.xml",
            params={"crtfc_key": self.api_key},
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"OpenDART corpCode HTTP error: {response.status_code}")
        data = response.content
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise RuntimeError(f"OpenDART corpCode response is not a zip archive: {_error_message(data)}")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if not names:
                raise RuntimeError("OpenDART corpCode archive is empty")
            root = ET.fromstring(archive.read(names[0]))
        rows: list[dict[str, str]] = []
        for item in root.findall(".//list"):
            rows.append(
                {
                    "corp_code": (item.findtext("corp_code") or "").strip(),
                    "corp_name": (item.findtext("corp_name") or "").strip(),
                    "stock_code": (item.findtext("stock_code") or "").strip().zfill(6),
                    "modify_date": (item.findtext("modify_date") or "").strip(),
                }
            )
        return [row for row in rows if row["corp_code"]]

    def list_filings(
        self,
        *,
        corp_code: str,
        begin_date: str,
        end_date: str,
        page_count: int = 100,
    ) -> list[dict[str, Any]]:
        """Return disclosure-list rows, preserving receipt number/date verbatim."""
        self._require_key()
        params = {
            "crtfc_key": self.api_key,
            "corp_code": str(corp_code).strip(),
            "bgn_de": str(begin_date).replace("-", ""),
            "end_de": str(end_date).replace("-", ""),
            "page_count": max(1, min(int(page_count), 100)),
            "page_no": 1,
        }
        rows: list[dict[str, Any]] = []
        while True:
            response = requests.get(f"{self.base_url}/list.json", params=params, timeout=self.timeout)
            payload = response.json()
            if not response.ok:
                raise RuntimeError(f"OpenDART list HTTP error: {response.status_code}")
            status = str(payload.get("status") or "")
            if status == "013":  # no data
                return rows
            if status != "000":
                raise RuntimeError(f"OpenDART list error: {status} {payload.get('message')}")
            rows.extend(item for item in payload.get("list", []) if isinstance(item, dict))
            total_page = int(payload.get("total_page") or 1)
            if int(params["page_no"]) >= total_page:
                return rows
            params["page_no"] = int(params["page_no"]) + 1

    def stock_total_status(
        self,
        *,
        corp_code: str,
        business_year: int | str,
        reprt_code: str,
    ) -> list[dict[str, Any]]:
        """Return periodic-report stock-total rows, including the source receipt number."""
        self._require_key()
        response = requests.get(
            f"{self.base_url}/stockTotqySttus.json",
            params={
                "crtfc_key": self.api_key,
                "corp_code": str(corp_code).strip(),
                "bsns_year": str(business_year).strip(),
                "reprt_code": str(reprt_code).strip(),
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"OpenDART stock-total HTTP error: {response.status_code}")
        payload = response.json()
        status = str(payload.get("status") or "")
        if status in {"013", "014"}:
            return []
        if status != "000":
            raise RuntimeError(f"OpenDART stock-total error: {status} {payload.get('message')}")
        return [item for item in payload.get("list", []) if isinstance(item, dict)]

    def download_xbrl(self, *, rcept_no: str, reprt_code: str) -> bytes:
        """Download the XBRL zip tied to one disclosure receipt number."""
        self._require_key()
        response = requests.get(
            f"{self.base_url}/fnlttXbrl.xml",
            params={
                "crtfc_key": self.api_key,
                "rcept_no": str(rcept_no).strip(),
                "reprt_code": str(reprt_code).strip(),
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"OpenDART XBRL HTTP error: {response.status_code}")
        data = response.content
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise RuntimeError(f"OpenDART XBRL response is not a zip archive: {_error_message(data)}")
        return data

    def save_xbrl(self, *, rcept_no: str, reprt_code: str, path: str | Path) -> Path:
        """Download one receipt-versioned XBRL package to a caller-selected path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = self.download_xbrl(rcept_no=rcept_no, reprt_code=reprt_code)
        target.write_bytes(data)
        return target


def report_code_from_name(report_name: str) -> str | None:
    """Map a Korean periodic-report title to the OpenDART report code."""
    text = str(report_name or "")
    period = PERIOD_RE.search(text)
    month = int(period.group(2)) if period else None
    if "사업보고서" in text:
        return "11011"
    if "반기보고서" in text:
        return "11012"
    if "분기보고서" in text:
        if month == 3:
            return "11013"
        if month == 9:
            return "11014"
    return None


def period_end_from_report_name(report_name: str) -> str | None:
    """Return YYYY-MM-DD period end parsed from titles like '(2025.03)'."""
    match = PERIOD_RE.search(str(report_name or ""))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return (pd_month_end(year, month)).isoformat()


def pd_month_end(year: int, month: int):
    # Keep the connector pandas-free; stdlib is sufficient for a month end.
    import calendar
    from datetime import date

    return date(year, month, calendar.monthrange(year, month)[1])


def is_periodic_report(report_name: str) -> bool:
    return report_code_from_name(report_name) is not None


def _error_message(data: bytes) -> str:
    try:
        root = ET.fromstring(data)
        status = root.findtext("status") or root.findtext("./result/status") or "unknown"
        message = root.findtext("message") or root.findtext("./result/message") or "non-zip response"
        return f"{status} {message}"[:300]
    except Exception:
        return "non-zip response"
