#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from toss_alpha.connectors.toss_readonly import TossReadOnlyClient

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


def make_client() -> TossReadOnlyClient:
    client_id, client_secret, account_seq = load_config()
    return TossReadOnlyClient(client_id=client_id, client_secret=client_secret, account_seq=account_seq)


def token() -> str:
    try:
        return make_client().token()
    except Exception as exc:
        die(str(exc))


def request(method: str, path: str, *, params: Optional[Dict[str, Any]] = None, account: bool = False):
    client = make_client()
    try:
        if path == "/api/v1/stocks":
            result = client.stocks(params["symbols"] if params else "")
        elif path == "/api/v1/prices":
            result = client.prices(params["symbols"] if params else "")
        elif path == "/api/v1/candles":
            result = client.candles(params["symbol"], params.get("interval", "1D"))
        elif path == "/api/v1/accounts":
            result = client.accounts()
        elif path == "/api/v1/holdings":
            result = client.holdings()
        else:
            die(f"unsupported read-only path: {path}")
    except Exception as exc:
        die(str(exc))

    print(f"HTTP {result['status_code']}")
    for name, value in result["headers"].items():
        print(f"{name}: {value}")
    if result["json"] is not None:
        print(json.dumps(result["json"], ensure_ascii=False, indent=2))
    else:
        print(result["text"])


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
