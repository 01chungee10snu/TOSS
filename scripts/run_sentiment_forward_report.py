"""Daily forward sentiment report for the TOSS research harness.

Collects recent Korean news titles for the current TOSS panel universe, scores
sentiment with a local KLUE-RoBERTa model, and emits a Telegram-friendly report
with base candidates and sentiment-overlay candidates.

Research/manual-draft only. No live order submission.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import torch
from bs4 import BeautifulSoup
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from toss_alpha.daily.decision import _classify_regime, _score_candidates

ROOT = Path(__file__).resolve().parents[1]
PANEL_CSV = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
NAME_MAP_CSV = ROOT / "reports/harness/panel_code_name_mapping.csv"
OUT_DIR = ROOT / "reports/harness/sentiment_forward"
MODEL_NAME = "FISA-conclave/klue-roberta-news-sentiment"
DEFAULT_LIMIT_SYMBOLS = 496
DEFAULT_ARTICLES_PER_SYMBOL = 8
DEFAULT_WHEN = "7d"
SENTIMENT_LOOKBACK_DAYS = 7
PENALTY_ALPHA = 10.0
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def init_model() -> tuple[Any, Any, str]:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    return model, tokenizer, device


def load_universe(limit_symbols: int) -> pd.DataFrame:
    if not NAME_MAP_CSV.exists():
        raise FileNotFoundError(f"missing code/name mapping: {NAME_MAP_CSV}")
    names = pd.read_csv(NAME_MAP_CSV, dtype={"Code": str})
    names["Code"] = names["Code"].astype(str).str.zfill(6)
    names = names.dropna(subset=["Name"]).drop_duplicates("Code")
    return names.head(limit_symbols).copy()


def fetch_news(name: str, when: str, limit: int) -> list[dict[str, str]]:
    query = f"{name} 주가"
    url = f"https://news.google.com/rss/search?q={quote(query)}+when:{when}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        soup = BeautifulSoup(resp.text, "xml")
        rows = []
        for item in soup.find_all("item")[:limit]:
            title = item.find("title")
            pubdate = item.find("pubDate")
            link = item.find("link")
            if title and pubdate:
                rows.append({
                    "title": title.get_text(strip=True),
                    "pub_date": pubdate.get_text(strip=True),
                    "link": link.get_text(strip=True) if link else "",
                })
        return rows
    except Exception as exc:
        return [{"title": f"FETCH_ERROR: {exc}", "pub_date": "", "link": ""}]


def parse_pub_date(value: str) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT").date().isoformat()
    except Exception:
        return None


def score_titles(titles: list[str], model: Any, tokenizer: Any, device: str, batch_size: int = 32) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i : i + batch_size]
        inputs = tokenizer(batch, return_tensors="pt", truncation=True, max_length=128, padding=True).to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)
        for j in range(len(batch)):
            pred_id = int(probs[j].argmax().item())
            label = model.config.id2label[pred_id]
            confidence = float(probs[j][pred_id].item())
            if label == "positive":
                score = confidence
            elif label == "negative":
                score = -confidence
            else:
                score = 0.0
            results.append({"label": label, "confidence": confidence, "score": score})
    return results


def collect_sentiment(
    limit_symbols: int,
    articles_per_symbol: int,
    when: str,
    sleep_seconds: float,
    target_universe: pd.DataFrame | None = None,
) -> pd.DataFrame:
    universe = target_universe.copy() if target_universe is not None else load_universe(limit_symbols)
    if limit_symbols and len(universe) > limit_symbols:
        universe = universe.head(limit_symbols)
    model, tokenizer, device = init_model()
    rows: list[dict[str, Any]] = []
    run_ts = _now_kst().isoformat(timespec="seconds")
    for idx, row in universe.iterrows():
        code = str(row["Code"]).zfill(6)
        name = str(row["Name"])
        news = fetch_news(name, when=when, limit=articles_per_symbol)
        valid_news = [n for n in news if not n["title"].startswith("FETCH_ERROR")]
        if valid_news:
            scored = score_titles([n["title"] for n in valid_news], model, tokenizer, device)
            for item, score in zip(valid_news, scored):
                rows.append({
                    "run_ts": run_ts,
                    "code": code,
                    "name": name,
                    "date": parse_pub_date(item["pub_date"]),
                    "title": item["title"][:180],
                    "sentiment_label": score["label"],
                    "sentiment_confidence": round(score["confidence"], 6),
                    "sentiment_score": round(score["score"], 6),
                    "link": item.get("link", ""),
                })
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return pd.DataFrame(rows)


def build_latest_sentiment_by_code(sentiment_df: pd.DataFrame) -> dict[str, float]:
    if sentiment_df.empty:
        return {}
    df = sentiment_df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    cutoff = df["date"].max() - pd.Timedelta(days=SENTIMENT_LOOKBACK_DAYS)
    recent = df[df["date"].isna() | (df["date"] >= cutoff)].copy()
    grouped = recent.groupby("code")["sentiment_score"].mean()
    return {code: float(score) for code, score in grouped.items() if not math.isnan(float(score))}


def latest_candidates(sentiment_by_code: dict[str, float]) -> tuple[pd.Timestamp, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    latest_date = pd.Timestamp(panel["Date"].max())
    sub = panel[panel["Date"] <= latest_date].copy()
    regime = _classify_regime(sub)
    candidates = _score_candidates(sub, regime=regime)
    base = sorted([c for c in candidates if c["final_score"] >= 55], key=lambda c: c["final_score"], reverse=True)

    enriched = []
    for c in base:
        code = str(c["symbol"]).zfill(6)
        sent = sentiment_by_code.get(code, 0.0)
        row = dict(c)
        row["symbol"] = code
        row["sentiment_score"] = sent
        row["adjusted_score"] = float(c["final_score"]) + PENALTY_ALPHA * sent
        enriched.append(row)

    rerank = sorted(enriched, key=lambda c: (c["sentiment_score"], c["final_score"]), reverse=True)
    penalty = sorted(enriched, key=lambda c: c["adjusted_score"], reverse=True)
    return latest_date, regime, base, rerank, penalty


def format_candidate(row: dict[str, Any], names: dict[str, str], rank: int, include_adjusted: bool = False) -> str:
    code = str(row["symbol"]).zfill(6)
    name = names.get(code, "")
    base_score = float(row.get("final_score", 0.0))
    sent = float(row.get("sentiment_score", 0.0))
    if include_adjusted:
        adj = float(row.get("adjusted_score", base_score))
        return f"{rank}. {code} {name} — adj {adj:.2f}, base {base_score:.2f}, sent {sent:+.2f}"
    return f"{rank}. {code} {name} — base {base_score:.2f}, sent {sent:+.2f}"


def write_outputs(sentiment_df: pd.DataFrame, report: str, payload: dict[str, Any], run_date: str) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_csv = OUT_DIR / f"news_sentiment_forward_{run_date}.csv"
    report_md = OUT_DIR / f"sentiment_forward_report_{run_date}.md"
    report_json = OUT_DIR / f"sentiment_forward_report_{run_date}.json"
    sentiment_df.to_csv(raw_csv, index=False)
    report_md.write_text(report, encoding="utf-8")
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return raw_csv, report_md, report_json


def run(limit_symbols: int, articles_per_symbol: int, when: str, sleep_seconds: float, quiet_success: bool, candidate_pool: int = 120) -> str:
    run_date = _now_kst().strftime("%Y%m%d")
    names_df = load_universe(limit_symbols=DEFAULT_LIMIT_SYMBOLS)
    names = dict(zip(names_df["Code"].astype(str).str.zfill(6), names_df["Name"].astype(str)))

    # To keep cron runs short, collect sentiment only for the current base candidate pool.
    # This is sufficient for forward tracking because sentiment is only used to re-rank
    # base-approved candidates, not the full 496-symbol universe.
    latest_date0, regime0, base0, _, _ = latest_candidates({})
    pool_codes = [str(row["symbol"]).zfill(6) for row in base0[:candidate_pool]]
    target_universe = names_df[names_df["Code"].astype(str).str.zfill(6).isin(pool_codes)].copy()
    # Preserve candidate order, not code sort order.
    order = {code: i for i, code in enumerate(pool_codes)}
    target_universe["_order"] = target_universe["Code"].astype(str).str.zfill(6).map(order)
    target_universe = target_universe.sort_values("_order").drop(columns=["_order"])

    sentiment_df = collect_sentiment(
        limit_symbols=limit_symbols,
        articles_per_symbol=articles_per_symbol,
        when=when,
        sleep_seconds=sleep_seconds,
        target_universe=target_universe,
    )
    sentiment_by_code = build_latest_sentiment_by_code(sentiment_df)
    latest_date, regime, base, rerank, penalty = latest_candidates(sentiment_by_code)

    coverage_symbols = sentiment_df["code"].nunique() if not sentiment_df.empty else 0
    coverage_articles = len(sentiment_df)
    avg_score = float(sentiment_df["sentiment_score"].mean()) if not sentiment_df.empty else 0.0
    pos = int((sentiment_df["sentiment_score"] > 0).sum()) if not sentiment_df.empty else 0
    neg = int((sentiment_df["sentiment_score"] < 0).sum()) if not sentiment_df.empty else 0
    neu = int((sentiment_df["sentiment_score"] == 0).sum()) if not sentiment_df.empty else 0

    lines = [
        "## TOSS 뉴스 감성 Forward Tracking",
        f"기준일: {latest_date.date().isoformat()} / 수집일: {_now_kst().strftime('%Y-%m-%d %H:%M')}",
        "live_order_submitted: False / research-manual-draft only",
        "",
        f"뉴스 수집: {coverage_symbols}종목, {coverage_articles}기사, avg sentiment {avg_score:+.3f} (pos {pos}, neg {neg}, neutral {neu})",
        f"시장 regime: {regime.get('status')} / breadth {regime.get('breadth_positive_20d', 0):.2f} / index_ret20 {regime.get('index_ret20_mean', 0):+.2%}",
        "",
        "### Base 후보 Top 10",
    ]
    lines += [format_candidate(row, names, i + 1) for i, row in enumerate(base[:10])]
    lines += ["", "### Sentiment rerank Top 10"]
    lines += [format_candidate(row, names, i + 1) for i, row in enumerate(rerank[:10])]
    lines += ["", f"### Sentiment penalty Top 10 (alpha={PENALTY_ALPHA:.0f})"]
    lines += [format_candidate(row, names, i + 1, include_adjusted=True) for i, row in enumerate(penalty[:10])]

    payload = {
        "run_date": run_date,
        "latest_panel_date": latest_date.date().isoformat(),
        "live_order_submitted": False,
        "coverage_symbols": coverage_symbols,
        "coverage_articles": coverage_articles,
        "avg_sentiment": avg_score,
        "regime": regime,
        "base_top10": base[:10],
        "sentiment_rerank_top10": rerank[:10],
        "sentiment_penalty_top10": penalty[:10],
    }
    raw_csv, report_md, report_json = write_outputs(sentiment_df, "\n".join(lines) + "\n", payload, run_date)
    lines += ["", "### Artifacts", f"- raw: `{raw_csv}`", f"- report: `{report_md}`", f"- json: `{report_json}`"]
    final = "\n".join(lines) + "\n"
    report_md.write_text(final, encoding="utf-8")

    if quiet_success and coverage_articles == 0:
        return ""
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-symbols", type=int, default=DEFAULT_LIMIT_SYMBOLS)
    parser.add_argument("--articles-per-symbol", type=int, default=DEFAULT_ARTICLES_PER_SYMBOL)
    parser.add_argument("--when", default=DEFAULT_WHEN)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--candidate-pool", type=int, default=120)
    parser.add_argument("--quiet-success", action="store_true")
    args = parser.parse_args()
    print(run(args.limit_symbols, args.articles_per_symbol, args.when, args.sleep, args.quiet_success, args.candidate_pool))


if __name__ == "__main__":
    main()
