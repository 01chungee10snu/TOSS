"""Collect news sentiment for TOSS panel symbols.

Uses Google News RSS + KLUE-RoBERTa sentiment model.
Processes panel universe in batches, saves daily sentiment scores.

Paper/research only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import torch
from bs4 import BeautifulSoup
from transformers import AutoModelForSequenceClassification, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports/harness"
MODEL_NAME = "FISA-conclave/klue-roberta-news-sentiment"
NAME_MAP_CSV = OUT_DIR / "panel_code_name_mapping.csv"
OUTPUT_CSV = OUT_DIR / "news_sentiment_panel_20260621.csv"


def init_model() -> tuple[Any, Any, str]:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    return model, tokenizer, device


def fetch_news(name: str, when: str = "1y", limit: int = 20) -> list[dict]:
    query = f"{name} 주가"
    url = f"https://news.google.com/rss/search?q={quote(query)}+when:{when}&hl=ko&gl=KR&ceid=KR:ko"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "xml")
        items = soup.find_all("item")
        results = []
        for item in items[:limit]:
            title = item.find("title")
            pubdate = item.find("pubDate")
            if title and pubdate:
                results.append({"title": title.get_text(strip=True), "date": pubdate.get_text(strip=True)})
        return results
    except Exception:
        return []


def parse_date(date_str: str) -> str | None:
    from datetime import datetime
    try:
        return datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S GMT").date().isoformat()
    except Exception:
        return None


def score_batch(titles: list[str], model: Any, tokenizer: Any, device: str, bs: int = 32) -> list[float]:
    scores = []
    for i in range(0, len(titles), bs):
        batch = titles[i : i + bs]
        inputs = tokenizer(batch, return_tensors="pt", truncation=True, max_length=128, padding=True).to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)
        for j in range(len(batch)):
            pid = probs[j].argmax().item()
            label = model.config.id2label[pid]
            conf = probs[j][pid].item()
            if label == "positive":
                scores.append(conf)
            elif label == "negative":
                scores.append(-conf)
            else:
                scores.append(0.0)
    return scores


def main(n_symbols: int = 496, when: str = "1y") -> None:
    names_df = pd.read_csv(NAME_MAP_CSV, dtype={"Code": str})
    names_df["Code"] = names_df["Code"].astype(str).str.zfill(6)
    target = names_df.head(n_symbols)
    print(f"Processing {len(target)} symbols, when={when}")

    model, tokenizer, device = init_model()
    print(f"Device: {device}")

    all_rows = []
    for idx, (_, row) in enumerate(target.iterrows()):
        code = row["Code"]
        name = str(row["Name"])
        news = fetch_news(name, when=when, limit=20)
        if not news:
            continue
        titles = [n["title"] for n in news]
        sent_scores = score_batch(titles, model, tokenizer, device)
        for n_item, score in zip(news, sent_scores):
            all_rows.append({
                "code": code,
                "name": name,
                "date": parse_date(n_item["date"]),
                "title": n_item["title"][:120],
                "sentiment_score": round(score, 4),
            })
        if (idx + 1) % 50 == 0 or idx == 0:
            stock_scores = [r["sentiment_score"] for r in all_rows if r["code"] == code]
            avg = sum(stock_scores) / len(stock_scores) if stock_scores else 0
            print(f"  [{idx+1}/{len(target)}] {code} {name}: {len(news)} articles, avg={avg:+.3f}", flush=True)
        time.sleep(0.3)

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved {len(df)} rows to {OUTPUT_CSV}")
    print(f"Symbols covered: {df['code'].nunique()}")


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 496
    when = sys.argv[2] if len(sys.argv) > 2 else "1y"
    main(n_symbols=n, when=when)
