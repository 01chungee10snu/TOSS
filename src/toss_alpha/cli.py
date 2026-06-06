"""Safe CLI skeleton for the TOSS research harness."""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toss-alpha",
        description="Read-only research/backtest/manual-draft harness. Execution commands are intentionally absent.",
    )
    sub = parser.add_subparsers(dest="command")

    research = sub.add_parser("research", help="research goal commands")
    research_sub = research.add_subparsers(dest="research_command")
    research_run = research_sub.add_parser("run", help="run a research goal safely")
    research_run.add_argument("goal", nargs="?", help="path to goal YAML")
    research_run.set_defaults(handler=_not_implemented)

    backtest = sub.add_parser("backtest", help="backtest commands")
    backtest_sub = backtest.add_subparsers(dest="backtest_command")
    backtest_run = backtest_sub.add_parser("run", help="run a deterministic research-only backtest")
    backtest_run.add_argument("goal", nargs="?", help="path to goal YAML")
    backtest_run.set_defaults(handler=_not_implemented)

    draft = sub.add_parser("draft-order", help="create manual review draft only")
    draft.add_argument("goal", nargs="?", help="path to goal YAML")
    draft.set_defaults(handler=_not_implemented)
    return parser


def _not_implemented(_args: argparse.Namespace) -> int:
    print("not implemented yet — safe skeleton only; no live trading")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
