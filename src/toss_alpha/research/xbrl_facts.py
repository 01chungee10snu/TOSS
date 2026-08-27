"""Fail-closed parser for receipt-versioned OpenDART XBRL fundamentals.

The parser intentionally extracts only a small set of factor inputs and keeps
selection provenance for every chosen fact.  It never substitutes an issued-
share count for shares outstanding and never guesses across conflicting XBRL
contexts.  Ambiguous or missing values remain ``None`` so the PIT contract can
block promotion instead of manufacturing a backtest input.
"""
from __future__ import annotations

import math
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


MONETARY_METRICS = {"assets", "book_equity", "revenue"}
INSTANT_METRICS = {"assets", "book_equity", "shares_outstanding"}

# Priority is significant.  Parent-owner equity is preferred for consolidated
# statements because non-controlling interest is not attributable to common
# shareholders.  Shares issued are deliberately excluded from the share list.
CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "assets": (
        "Assets",
    ),
    "book_equity": (
        "EquityAttributableToOwnersOfParent",
        "EquityAttributableToEquityHoldersOfParent",
        "Equity",
    ),
    "revenue": (
        "Revenue",
        "RevenueFromContractsWithCustomers",
        "SalesRevenue",
        "Sales",
    ),
    "shares_outstanding": (
        "NumberOfSharesOutstanding",
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "OrdinarySharesOutstanding",
    ),
}

_FORECAST_WORDS = ("forecast", "estimate", "estimated", "prospective", "scenarioforecast")
_CONSOLIDATED_WORDS = ("consolidated", "cfs", "연결")
_SEPARATE_WORDS = ("separate", "ofs", "별도")


@dataclass(frozen=True)
class XbrlContext:
    context_id: str
    instant: str | None
    start_date: str | None
    end_date: str | None
    dimensions: tuple[str, ...]

    @property
    def duration_days(self) -> int | None:
        if not self.start_date or not self.end_date:
            return None
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError:
            return None
        return (end - start).days + 1


@dataclass(frozen=True)
class XbrlFact:
    concept: str
    value: float
    context_id: str
    unit: str | None
    instant: str | None
    start_date: str | None
    end_date: str | None
    dimensions: tuple[str, ...]
    instance_path: str
    decimals: str | None = None

    @property
    def duration_days(self) -> int | None:
        if not self.start_date or not self.end_date:
            return None
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError:
            return None
        return (end - start).days + 1


@dataclass(frozen=True)
class SelectedFact:
    metric: str
    status: str
    value: float | None
    concept: str | None
    context_id: str | None
    unit: str | None
    instant: str | None
    start_date: str | None
    end_date: str | None
    duration_days: int | None
    dimensions: tuple[str, ...]
    instance_path: str | None
    candidate_count: int
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedFundamentals:
    period_end: str
    reprt_code: str
    assets: SelectedFact
    book_equity: SelectedFact
    revenue: SelectedFact
    shares_outstanding: SelectedFact
    instance_count: int
    raw_fact_count: int

    @property
    def bps(self) -> float | None:
        equity = self.book_equity.value
        shares = self.shares_outstanding.value
        if equity is None or shares is None or not math.isfinite(equity) or not math.isfinite(shares):
            return None
        if equity <= 0 or shares <= 0:
            return None
        return equity / shares

    @property
    def revenue_basis(self) -> str | None:
        days = self.revenue.duration_days
        if days is None:
            return None
        if days <= 120:
            return "quarter"
        if days <= 220:
            return "half_year_cumulative"
        if days <= 320:
            return "nine_month_cumulative"
        return "annual"

    @property
    def ready_for_hml_cma(self) -> bool:
        return (
            self.book_equity.status == "SELECTED"
            and self.revenue.status == "SELECTED"
            and self.shares_outstanding.status == "SELECTED"
            and self.bps is not None
            and self.revenue.value is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_end": self.period_end,
            "reprt_code": self.reprt_code,
            "assets": self.assets.to_dict(),
            "book_equity": self.book_equity.to_dict(),
            "revenue": self.revenue.to_dict(),
            "shares_outstanding": self.shares_outstanding.to_dict(),
            "bps": self.bps,
            "revenue_basis": self.revenue_basis,
            "ready_for_hml_cma": self.ready_for_hml_cma,
            "instance_count": self.instance_count,
            "raw_fact_count": self.raw_fact_count,
        }


def parse_xbrl_archive(
    archive_path: str | Path,
    *,
    period_end: str | date | datetime,
    reprt_code: str,
) -> ParsedFundamentals:
    """Parse factor inputs from one receipt-versioned OpenDART XBRL ZIP."""
    target_period = _iso_date(period_end)
    facts, instance_count = read_numeric_facts_from_archive(archive_path)
    selected = {
        metric: select_metric_fact(facts, metric=metric, period_end=target_period, reprt_code=str(reprt_code))
        for metric in CONCEPT_ALIASES
    }
    return ParsedFundamentals(
        period_end=target_period,
        reprt_code=str(reprt_code),
        assets=selected["assets"],
        book_equity=selected["book_equity"],
        revenue=selected["revenue"],
        shares_outstanding=selected["shares_outstanding"],
        instance_count=instance_count,
        raw_fact_count=len(facts),
    )


def read_numeric_facts_from_archive(archive_path: str | Path) -> tuple[list[XbrlFact], int]:
    """Return numeric facts from every XBRL instance contained in a ZIP."""
    path = Path(archive_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if not zipfile.is_zipfile(path):
        raise ValueError(f"not an XBRL zip archive: {path}")

    facts: list[XbrlFact] = []
    instance_count = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith("/") or Path(name).suffix.lower() not in {".xbrl", ".xml"}:
                continue
            try:
                payload = archive.read(name)
                root = ET.fromstring(payload)
            except (KeyError, ET.ParseError):
                continue
            if _local_name(root.tag).lower() != "xbrl":
                continue
            instance_count += 1
            facts.extend(_facts_from_instance(root, instance_path=name))
    return facts, instance_count


def select_metric_fact(
    facts: Iterable[XbrlFact],
    *,
    metric: str,
    period_end: str | date | datetime,
    reprt_code: str,
) -> SelectedFact:
    """Select one metric fact conservatively, returning AMBIGUOUS/MISSING when needed."""
    if metric not in CONCEPT_ALIASES:
        raise ValueError(f"unsupported XBRL metric: {metric}")
    target_period = _iso_date(period_end)
    aliases = CONCEPT_ALIASES[metric]
    alias_rank = {name.lower(): idx for idx, name in enumerate(aliases)}

    candidates: list[tuple[tuple[int, ...], XbrlFact]] = []
    for fact in facts:
        concept_key = fact.concept.lower()
        if concept_key not in alias_rank:
            continue
        if _forecast_dimension(fact.dimensions):
            continue
        if metric in MONETARY_METRICS and not _is_krw_unit(fact.unit):
            continue
        if metric == "shares_outstanding" and not _is_share_unit(fact.unit):
            continue

        period_score = _period_score(fact, metric=metric, target_period=target_period, reprt_code=reprt_code)
        if period_score is None:
            continue
        score = (
            alias_rank[concept_key],
            period_score,
            len(fact.dimensions),
            _statement_path_score(fact.instance_path),
        )
        candidates.append((score, fact))

    if not candidates:
        return _empty_selection(metric, "MISSING", 0, "no_unambiguous_matching_fact")

    candidates.sort(key=lambda item: item[0])
    best_score = candidates[0][0]
    best = [fact for score, fact in candidates if score == best_score]
    unique_values = {_canonical_number(fact.value) for fact in best}
    if len(unique_values) > 1:
        return _empty_selection(metric, "AMBIGUOUS", len(candidates), "conflicting_best_context_values")

    chosen = best[0]
    return SelectedFact(
        metric=metric,
        status="SELECTED",
        value=chosen.value,
        concept=chosen.concept,
        context_id=chosen.context_id,
        unit=chosen.unit,
        instant=chosen.instant,
        start_date=chosen.start_date,
        end_date=chosen.end_date,
        duration_days=chosen.duration_days,
        dimensions=chosen.dimensions,
        instance_path=chosen.instance_path,
        candidate_count=len(candidates),
        reason=None,
    )


def _facts_from_instance(root: ET.Element, *, instance_path: str) -> list[XbrlFact]:
    contexts: dict[str, XbrlContext] = {}
    units: dict[str, str] = {}
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "context":
            context = _parse_context(element)
            if context.context_id:
                contexts[context.context_id] = context
        elif local == "unit":
            unit_id = str(element.attrib.get("id") or "").strip()
            measures = [_clean_measure(child.text) for child in element.iter() if _local_name(child.tag) == "measure"]
            if unit_id and measures:
                units[unit_id] = "*".join(measure for measure in measures if measure)

    result: list[XbrlFact] = []
    for element in root.iter():
        context_ref = str(element.attrib.get("contextRef") or "").strip()
        if not context_ref:
            continue
        if _is_nil(element):
            continue
        value = _parse_number(element.text, scale=element.attrib.get("scale"))
        if value is None:
            continue
        context = contexts.get(context_ref)
        if context is None:
            continue
        unit_ref = str(element.attrib.get("unitRef") or "").strip()
        result.append(
            XbrlFact(
                concept=_local_name(element.tag),
                value=value,
                context_id=context_ref,
                unit=units.get(unit_ref, unit_ref or None),
                instant=context.instant,
                start_date=context.start_date,
                end_date=context.end_date,
                dimensions=context.dimensions,
                instance_path=instance_path,
                decimals=element.attrib.get("decimals"),
            )
        )
    return result


def _parse_context(element: ET.Element) -> XbrlContext:
    context_id = str(element.attrib.get("id") or "").strip()
    instant = start_date = end_date = None
    dimensions: list[str] = []
    for child in element.iter():
        local = _local_name(child.tag)
        text = str(child.text or "").strip()
        if local == "instant":
            instant = _safe_iso(text)
        elif local == "startDate":
            start_date = _safe_iso(text)
        elif local == "endDate":
            end_date = _safe_iso(text)
        elif local in {"explicitMember", "typedMember"}:
            dimension = str(child.attrib.get("dimension") or "").strip()
            dimensions.append(f"{dimension}={text}" if text else dimension)
    return XbrlContext(
        context_id=context_id,
        instant=instant,
        start_date=start_date,
        end_date=end_date,
        dimensions=tuple(sorted(item for item in dimensions if item)),
    )


def _period_score(fact: XbrlFact, *, metric: str, target_period: str, reprt_code: str) -> int | None:
    if metric in INSTANT_METRICS:
        if fact.instant != target_period:
            return None
        return 0

    if fact.end_date != target_period or fact.start_date is None:
        return None
    days = fact.duration_days
    if days is None or days <= 0:
        return None

    # Annual filings should use an annual duration.  Interim filings prefer a
    # standalone quarter if one is present; cumulative 6/9-month values remain
    # valid but rank behind a 3-month duration and are labelled by the caller.
    if str(reprt_code) == "11011":
        if not 300 <= days <= 430:
            return None
        return abs(days - 365)
    if str(reprt_code) in {"11012", "11013", "11014"}:
        if not 45 <= days <= 320:
            return None
        return abs(days - 91)
    return None


def _statement_path_score(path: str) -> int:
    text = str(path or "").lower()
    if any(word in text for word in _CONSOLIDATED_WORDS):
        return 0
    if any(word in text for word in _SEPARATE_WORDS):
        return 2
    return 1


def _forecast_dimension(dimensions: Iterable[str]) -> bool:
    text = " ".join(str(item).lower() for item in dimensions)
    return any(word in text for word in _FORECAST_WORDS)


def _is_krw_unit(unit: str | None) -> bool:
    text = str(unit or "").lower().replace(" ", "")
    return text in {"krw", "iso4217:krw"} or text.endswith(":krw")


def _is_share_unit(unit: str | None) -> bool:
    text = str(unit or "").lower().replace(" ", "")
    return text in {"shares", "share", "xbrli:shares"} or text.endswith(":shares")


def _clean_measure(value: str | None) -> str:
    return str(value or "").strip()


def _is_nil(element: ET.Element) -> bool:
    for key, value in element.attrib.items():
        if _local_name(key).lower() == "nil" and str(value).strip().lower() in {"true", "1"}:
            return True
    return False


def _parse_number(text: str | None, *, scale: str | None = None) -> float | None:
    raw = str(text or "").strip().replace(",", "")
    if not raw or raw in {"-", "—", "–"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    if negative:
        value = -value
    if scale not in (None, ""):
        try:
            value *= 10 ** int(scale)
        except (TypeError, ValueError, OverflowError):
            return None
    return value if math.isfinite(value) else None


def _local_name(tag: str) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    if ":" in text:
        return text.rsplit(":", 1)[-1]
    return text


def _safe_iso(value: str) -> str | None:
    try:
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError:
        return None


def _iso_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = _safe_iso(str(value))
    if parsed is None:
        raise ValueError(f"invalid period_end: {value}")
    return parsed


def _canonical_number(value: float) -> str:
    return f"{float(value):.12g}"


def _empty_selection(metric: str, status: str, candidate_count: int, reason: str) -> SelectedFact:
    return SelectedFact(
        metric=metric,
        status=status,
        value=None,
        concept=None,
        context_id=None,
        unit=None,
        instant=None,
        start_date=None,
        end_date=None,
        duration_days=None,
        dimensions=(),
        instance_path=None,
        candidate_count=int(candidate_count),
        reason=reason,
    )
