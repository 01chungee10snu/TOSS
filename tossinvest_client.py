#!/usr/bin/env python3
import argparse
import os
import sys
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

BASE = "https://openapi.tossinvest.com"


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_config():
    load_dotenv()
    client_id = os.getenv("TOSSINVEST_CLIENT_ID")
    client_secret = os.getenv("TOSSINVEST_CLIENT_SECRET")
    account_seq = os.getenv("TOSSINVEST_ACCOUNT_SEQ") or None
    if not client_id or not client_secret:
        die("Set TOSSINVEST_CLIENT_ID and TOSSINVEST_CLIENT_SECRET in .env")
    return client_id, client_secret, account_seq


def token() -> str:
    client_id, client_secret, _ = load_config()
    r = requests.post(
        f"{BASE}/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=20,
    )
    if not r.ok:
        die(f"token failed: HTTP {r.status_code} {r.text[:500]}")
    data = r.json()
    access_token = data.get("access_token")
    if not access_token:
        die(f"token response has no access_token: {data}")
    return access_token


def request(method: str, path: str, *, params: Optional[Dict[str, Any]] = None, account: bool = False):
    _, _, account_seq = load_config()
    headers = {"Authorization": f"Bearer {token()}"}
    if account:
        if not account_seq:
            die("This endpoint requires TOSSINVEST_ACCOUNT_SEQ in .env")
        headers["X-Tossinvest-Account"] = account_seq
    r = requests.request(method, f"{BASE}{path}", headers=headers, params=params, timeout=20)
    print(f"HTTP {r.status_code}")
    for h in ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After", "X-Request-Id"]:
        if h in r.headers:
            print(f"{h}: {r.headers[h]}")
    try:
        import json
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    except Exception:
        print(r.text)
    if not r.ok:
        raise SystemExit(1)


def main():
    p = argparse.ArgumentParser(description="Toss Securities Open API read-only test client")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("token")
    sp = sub.add_parser("stocks"); sp.add_argument("symbols")
    sp = sub.add_parser("prices"); sp.add_argument("symbols")
    sp = sub.add_parser("candles"); sp.add_argument("symbol"); sp.add_argument("--interval", default="1D")
    sub.add_parser("accounts")
    sub.add_parser("holdings")
    args = p.parse_args()

    if args.cmd == "token":
        t = token()
        print("token_ok", t[:12] + "..." + t[-6:])
    elif args.cmd == "stocks":
        request("GET", "/api/v1/stocks", params={"symbols": args.symbols})
    elif args.cmd == "prices":
        request("GET", "/api/v1/prices", params={"symbols": args.symbols})
    elif args.cmd == "candles":
        request("GET", "/api/v1/candles", params={"symbol": args.symbol, "interval": args.interval})
    elif args.cmd == "accounts":
        request("GET", "/api/v1/accounts", account=True)
    elif args.cmd == "holdings":
        request("GET", "/api/v1/holdings", account=True)


if __name__ == "__main__":
    main()
