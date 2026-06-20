"""Collect Korean stock news via Google News RSS and score sentiment.

Proof of concept: test with a small sample of TOSS universe symbols first.

Paper/research only.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
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
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = "FISA-conclave/klue-roberta-news-sentiment"
SAMPLE_SYMBOLS = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("035420", "NAVER"),
    ("005935", "삼성전자우"),
    ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"),
    ("068270", "셀트리온"),
    ("051910", "LG화학"),
    ("006400", "삼성SDI"),
    ("000270", "기아"),
]


def init_sentiment_model() -> tuple[Any, Any, str]:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    return model, tokenizer, device


def fetch_google_news_rss(query: str, when: str = "1y", limit: int = 100) -> list[dict]:
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
            title_text = title.get_text(strip=True) if title else ""
            date_text = pubdate.get_text(strip=True) if pubdate else ""
            results.append({"title": title_text, "date": date_text})
        return results
    except Exception as e:
        print(f"  fetch error: {e}")
        return []


def parse_date(date_str: str) -> str | None:
    try:
        dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S GMT")
        return dt.date().isoformat()
    except Exception:
        return None


def score_sentiment_batch(
    titles: list[str],
    model: Any,
    tokenizer: Any,
    device: str,
    batch_size: int = 32,
) -> list[tuple[str, float]]:
    """Score sentiment for a batch of titles. Returns (label, prob) per title."""
    results = []
    for i in range(0, len(titles), batch_size):
        batch = titles[i : i + batch_size]
        inputs = tokenizer(batch, return_tensors="pt", truncation=True, max_length=128, padding=True).to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)
        for j in range(len(batch)):
            pred_id = probs[j].argmax().item()
            label = model.config.id2label[pred_id]
            confidence = probs[j][pred_id].item()
            results.append((label, confidence))
    return results


def label_to_score(label: str, confidence: float) -> float:
    """Convert sentiment to numeric score: positive=+1, negative=-1, neutral=0."""
    if label == "positive":
        return confidence
    elif label == "negative":
        return -confidence
    return 0.0


def main() -> None:
    print("Loading sentiment model...")
    model, tokenizer, device = init_sentiment_model()
    print(f"Device: {device}")

    all_rows = []
    for code, name in SAMPLE_SYMBOLS:
        print(f"\n--- {code} {name} ---")
        news = fetch_google_news_rss(f"{name} 주가", when="1y", limit=50)
        print(f"  fetched {len(news)} articles")

        if not news:
            continue

        titles = [n["title"] for n in news]
        scores = score_sentiment_batch(titles, model, tokenizer, device)

        for news_item, (label, conf) in zip(news, scores):
            score = label_to_score(label, conf)
            parsed_date = parse_date(news_item["date"])
            all_rows.append({
                "code": code,
                "name": name,
                "date": parsed_date,
                "title": news_item["title"][:120],
                "sentiment_label": label,
                "sentiment_confidence": round(conf, 4),
                "sentiment_score": round(score, 4),
            })

        # Sentiment summary for this stock
        stock_scores = [r["sentiment_score"] for r in all_rows if r["code"] == code]
        avg = sum(stock_scores) / len(stock_scores) if stock_scores else 0
        pos = sum(1 for s in stock_scores if s > 0)
        neg = sum(1 for s in stock_scores if s < 0)
        neu = sum(1 for s in stock_scores if s == 0)
        print(f"  avg={avg:.3f}  pos={pos} neg={neg} neu={neu}")
        time.sleep(0.5)  # rate limit courtesy

    df = pd.DataFrame(all_rows)
    out_csv = OUT_DIR / "news_sentiment_sample_20260621.csv"
    df.to_csv(out_csv, index=False)
    print(f"\nSaved {len(df)} rows to {out_csv}")

    # Summary
    print("\n=== SUMMARY ===")
    for code, name in SAMPLE_SYMBOLS:
        stock_df = df[df["code"] == code]
        if stock_df.empty:
            continue
        avg = stock_df["sentiment_score"].mean()
        print(f"  {code} {name:10s}: avg_score={avg:+.3f}  n={len(stock_df)}")


if __name__ == "__main__":
    main()
