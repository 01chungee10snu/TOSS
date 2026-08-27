#!/usr/bin/env python3
"""Render, check, or explicitly install the read-only ETF forward-paper LaunchAgent.

Default mode is ``--check`` and is side-effect free.  ``--install`` is explicit
because it writes ``~/Library/LaunchAgents`` and calls launchctl.  The scheduled
program is the repository's read-only wrapper; this manager never enables live
orders or changes any trading flags.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

LABEL = "com.toss.etf-forward-paper"
DEFAULT_HOUR = 10
DEFAULT_MINUTE = 5
WEEKDAYS = (2, 3, 4, 5, 6)  # launchd: Sunday=1, Monday=2, ... Saturday=7


def build_plist(*, repo: Path, hour: int = DEFAULT_HOUR, minute: int = DEFAULT_MINUTE) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    if not 0 <= int(hour) <= 23:
        raise ValueError("hour must be 0..23")
    if not 0 <= int(minute) <= 59:
        raise ValueError("minute must be 0..59")
    wrapper = repo / "scripts" / "run_executable_etf_paper.sh"
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(wrapper)],
        "WorkingDirectory": str(repo),
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": int(hour), "Minute": int(minute)}
            for weekday in WEEKDAYS
        ],
        "StandardOutPath": str(repo / "logs" / "executable_etf_forward_paper.launchd.out.log"),
        "StandardErrorPath": str(repo / "logs" / "executable_etf_forward_paper.launchd.err.log"),
        "RunAtLoad": False,
        "KeepAlive": False,
        "ProcessType": "Background",
    }


def plist_bytes(payload: dict[str, Any]) -> bytes:
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def installed_path(*, home: Path) -> Path:
    return home.expanduser() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def check_installation(*, repo: Path, home: Path, hour: int = DEFAULT_HOUR, minute: int = DEFAULT_MINUTE) -> dict[str, Any]:
    expected = build_plist(repo=repo, hour=hour, minute=minute)
    path = installed_path(home=home)
    if not path.exists():
        return {"status": "MISSING", "path": str(path), "matches_expected": False}
    try:
        actual = plistlib.loads(path.read_bytes())
    except Exception as exc:
        return {
            "status": "INVALID_PLIST",
            "path": str(path),
            "matches_expected": False,
            "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
        }
    matches = actual == expected
    return {
        "status": "MATCH" if matches else "DRIFT",
        "path": str(path),
        "matches_expected": matches,
        "label": actual.get("Label"),
        "program": (actual.get("ProgramArguments") or [None, None])[-1],
        "schedule": actual.get("StartCalendarInterval"),
    }


def install(*, repo: Path, home: Path, hour: int = DEFAULT_HOUR, minute: int = DEFAULT_MINUTE) -> Path:
    """Write the plist and bootstrap it for the current GUI user."""
    repo = repo.expanduser().resolve()
    wrapper = repo / "scripts" / "run_executable_etf_paper.sh"
    if not wrapper.exists():
        raise FileNotFoundError(wrapper)
    path = installed_path(home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plist_bytes(build_plist(repo=repo, hour=hour, minute=minute)))

    uid = os.getuid()
    domain = f"gui/{uid}"
    # Bootout is best-effort so install is idempotent when the label already exists.
    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{LABEL}"], check=True)
    return path


def parse_args() -> argparse.Namespace:
    default_repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="side-effect-free installed plist comparison (default)")
    mode.add_argument("--render", action="store_true", help="print expected plist XML; no write")
    mode.add_argument("--install", action="store_true", help="explicitly install/bootstrap the LaunchAgent")
    parser.add_argument("--repo", type=Path, default=default_repo)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    parser.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.render:
        print(plist_bytes(build_plist(repo=args.repo, hour=args.hour, minute=args.minute)).decode(), end="")
        return 0
    if args.install:
        path = install(repo=args.repo, home=args.home, hour=args.hour, minute=args.minute)
        print(f"installed={path}")
        print(f"label={LABEL}")
        print("order_submission=False")
        return 0

    result = check_installation(repo=args.repo, home=args.home, hour=args.hour, minute=args.minute)
    print(f"status={result['status']}")
    print(f"matches_expected={result['matches_expected']}")
    print(f"path={result['path']}")
    return 0 if result["matches_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
