"""Safe CLI skeleton for the TOSS research harness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toss-alpha",
        description="Read-only research/backtest/manual-draft harness with safe paper simulation only.",
    )
    sub = parser.add_subparsers(dest="command")

    research = sub.add_parser("research", help="research goal commands")
    research_sub = research.add_subparsers(dest="research_command")
    research_run = research_sub.add_parser("run", help="run a research goal safely")
    research_run.add_argument("goal", nargs="?", help="path to goal YAML")
    research_run.add_argument("--panel-csv", help="path to OHLCV panel CSV")
    research_run.add_argument("--out-dir", help="directory for markdown/json artifacts")
    research_run.set_defaults(handler=_research_run)

    backtest = sub.add_parser("backtest", help="backtest commands")
    backtest_sub = backtest.add_subparsers(dest="backtest_command")
    backtest_run = backtest_sub.add_parser("run", help="run a deterministic research-only backtest")
    backtest_run.add_argument("goal", nargs="?", help="path to goal YAML")
    backtest_run.add_argument("--panel-csv", help="path to OHLCV panel CSV")
    backtest_run.add_argument("--out-dir", help="directory for markdown/json artifacts")
    backtest_run.set_defaults(handler=_research_run)

    draft = sub.add_parser("draft-order", help="create manual review draft only")
    draft.add_argument("goal", nargs="?", help="path to goal YAML")
    draft.add_argument("--panel-csv", help="path to OHLCV panel CSV")
    draft.add_argument("--out-dir", help="directory for markdown/json artifacts")
    draft.set_defaults(handler=_research_run)

    readiness = sub.add_parser("live-readiness", help="check guarded real-order readiness without submitting")
    readiness.set_defaults(handler=_live_readiness)

    paper = sub.add_parser("paper-order", help="simulate a single paper order without touching a broker")
    paper.add_argument("--symbol", required=True)
    paper.add_argument("--side", choices=["BUY", "SELL"], required=True)
    size = paper.add_mutually_exclusive_group(required=True)
    size.add_argument("--quantity", type=float)
    size.add_argument("--notional-krw", type=float)
    paper.add_argument("--price", type=float, required=True)
    paper.add_argument("--cash", type=float, default=1_000_000)
    paper.add_argument("--fees-krw", type=float, default=0.0)
    paper.set_defaults(handler=_paper_order)

    daily_paper = sub.add_parser("daily-paper", help="simulate a batch daily paper portfolio plan from JSON or Google Sheets")
    source = daily_paper.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", help="path to JSON plan with initial_cash_krw, holdings, and orders")
    source.add_argument("--sheet-id", help="Google spreadsheet id or full URL")
    daily_paper.add_argument("--strategy-id", default="daily-paper-cli")
    daily_paper.add_argument("--skip-sheet-writeback", action="store_true", help="when reading from Google Sheets, do not append runs/fills/positions back")
    daily_paper.set_defaults(handler=_daily_paper)

    init_sheet = sub.add_parser("init-daily-paper-sheet", help="create and initialize a Google Sheet template for TOSS daily paper trading")
    init_sheet.add_argument("--title", default="TOSS Daily Paper")
    init_sheet.add_argument("--initial-cash-krw", type=float, default=1_000_000)
    init_sheet.set_defaults(handler=_init_daily_paper_sheet)

    daily = sub.add_parser("daily", help="personal daily decision report commands")
    daily_sub = daily.add_subparsers(dest="daily_command")
    daily_run = daily_sub.add_parser("run", help="generate a safe daily decision packet")
    daily_run.add_argument("--panel-csv", required=True, help="path to OHLCV panel CSV")
    daily_run.add_argument("--symbols", required=True, help="comma-separated symbols to score")
    daily_run.add_argument("--mock-holdings", help="path to mock holdings JSON")
    daily_run.add_argument("--slow-veto-events", help="path to JSON slow-veto events from DART/news/manual review")
    daily_run.add_argument("--paper-plan-out", help="optional path to write a daily-paper JSON plan")
    daily_run.add_argument("--use-toss-account", action="store_true", help="read account/holdings through Toss read-only credentials")
    daily_run.add_argument("--out-dir", help="directory for markdown/json artifacts")
    daily_run.add_argument("--date", help="as-of date, YYYY-MM-DD")
    daily_run.add_argument("--max-notional-krw", type=float, default=100_000)
    daily_run.set_defaults(handler=_daily_run)

    collect_slow = daily_sub.add_parser("collect-slow-events", help="normalize manual/news/DART exports into slow_events.json")
    collect_slow.add_argument("--symbols", required=True, help="comma-separated symbols to keep")
    collect_slow.add_argument("--source", action="append", default=[], help="JSON or CSV event source; can be repeated")
    collect_slow.add_argument("--out", required=True, help="output slow_events.json path")
    collect_slow.add_argument("--date", help="as-of date, YYYY-MM-DD")
    collect_slow.set_defaults(handler=_daily_collect_slow_events)

    paper_loop = daily_sub.add_parser("paper-loop", help="run daily decision, write paper plan, and execute daily-paper simulation")
    paper_loop.add_argument("--panel-csv", required=True, help="path to OHLCV panel CSV")
    paper_loop.add_argument("--symbols", required=True, help="comma-separated symbols to score")
    paper_loop.add_argument("--mock-holdings", help="path to mock holdings JSON")
    paper_loop.add_argument("--slow-veto-events", help="path to JSON slow-veto events")
    paper_loop.add_argument("--out-dir", help="directory for loop artifacts")
    paper_loop.add_argument("--date", help="as-of date, YYYY-MM-DD")
    paper_loop.add_argument("--max-notional-krw", type=float, default=100_000)
    paper_loop.add_argument("--sheet-id", help="optional Google Sheet id or URL to append paper-loop result")
    paper_loop.set_defaults(handler=_daily_paper_loop)

    replay = daily_sub.add_parser("replay", help="run cumulative paper replay on historical panel data")
    replay.add_argument("--panel-csv", required=True, help="path to OHLCV panel CSV")
    replay.add_argument("--initial-cash-krw", type=float, default=1_000_000)
    replay.add_argument("--max-notional-krw", type=float, default=100_000)
    replay.add_argument("--step", type=int, default=5, help="trading-day step between replay dates")
    replay.add_argument("--score-threshold", type=float, default=70.0)
    replay.add_argument("--out-dir", help="directory for replay artifacts")
    replay.set_defaults(handler=_daily_replay)

    sweep = daily_sub.add_parser("sweep", help="run parameter sweep across multiple replay configs")
    sweep.add_argument("--panel-csv", required=True)
    sweep.add_argument("--initial-cash-krw", type=float, default=1_000_000)
    sweep.add_argument("--steps", default="5,10,20", help="comma-separated step values")
    sweep.add_argument("--thresholds", default="60,65,70,75", help="comma-separated score thresholds")
    sweep.add_argument("--stop-losses", default="0.05", help="comma-separated stop loss fractions")
    sweep.add_argument("--take-profits", default="0.08", help="comma-separated take profit fractions")
    sweep.add_argument("--holding-steps", default="20", help="comma-separated max holding steps")
    sweep.add_argument("--max-positions", default="1", help="comma-separated max concurrent positions")
    sweep.add_argument("--trailing-stops", default="0", help="comma-separated trailing stop fractions; 0 disables")
    sweep.add_argument("--sizing-modes", default="flat", help="comma-separated sizing modes: flat,score_weighted")
    sweep.add_argument("--min-volumes", default="0", help="comma-separated latest volume floors")
    sweep.add_argument("--rebalance-modes", default="hold_until_exit", help="comma-separated: hold_until_exit,top_n_rotation,full_liquidate_every_step")
    sweep.add_argument("--out-dir", help="directory for sweep artifacts")
    sweep.set_defaults(handler=_daily_sweep)
    return parser



def _research_run(args: argparse.Namespace) -> int:
    from toss_alpha.research.runner import run_goal

    if not args.goal:
        print("goal path required")
        return 2
    result = run_goal(args.goal, panel_csv=args.panel_csv, out_dir=args.out_dir)
    print(f"goal_id: {result['goal_id']}")
    print(f"selected_symbol: {result['selected_symbol']}")
    print(f"status: {result['backtest']['status']}")
    print(f"qual_gate_status: {result['qual_gate']['status']}")
    print(f"report_path: {result['report_path']}")
    print(f"json_path: {result['json_path']}")
    return 0



def _not_implemented(_args: argparse.Namespace) -> int:
    print("not implemented yet — safe skeleton only; no real order submission")
    return 0



def _live_readiness(_args: argparse.Namespace) -> int:
    from toss_alpha.execution.live_ready import live_readiness

    status = live_readiness()
    print(f"ready: {status['ready']}")
    print(f"default_mode: {status['default_mode']}")
    print("missing:")
    for item in status["missing"]:
        print(f"- {item}")
    print(f"dry_run_available: {status['dry_run_available']}")
    return 0



def _paper_order(args: argparse.Namespace) -> int:
    from toss_alpha.data.schema import OrderIntent
    from toss_alpha.execution.paper_executor import PaperExecutor

    executor = PaperExecutor(initial_cash_krw=args.cash)
    intent = OrderIntent(
        strategy_id="paper-cli",
        symbol=args.symbol,
        side=args.side,
        quantity=args.quantity,
        notional_krw=args.notional_krw,
        reason="paper cli simulation",
        mode="paper_auto",
    )
    result = executor.execute(intent, market_price=args.price, fees_krw=args.fees_krw)
    print(f"status: {result.status}")
    print("mode: paper_auto")
    if result.violations:
        print(f"violations: {', '.join(result.violations)}")
    if result.fill is not None:
        position = executor.ledger.positions[intent.symbol]
        print(f"symbol: {intent.symbol}")
        print(f"side: {intent.side}")
        print(f"fill_price: {result.fill.fill_price}")
        print(f"fill_quantity: {result.fill.fill_quantity}")
        print(f"cash_after: {executor.ledger.cash_krw}")
        print(f"position_qty: {position.quantity}")
        print(f"position_state: {position.state}")
        print(f"realized_pnl_krw: {executor.ledger.realized_pnl_krw}")
    return 0



def _daily_paper(args: argparse.Namespace) -> int:
    from toss_alpha.execution.daily_paper import DailyPaperPlan, run_daily_paper
    from toss_alpha.storage.google_sheets import GoogleSheetsClient, GoogleSheetsDailyPaperStore, parse_google_sheet_id

    if args.plan:
        payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        plan = DailyPaperPlan.from_dict(payload, strategy_id=args.strategy_id)
        sheet_store = None
    else:
        sheet_store = GoogleSheetsDailyPaperStore(
            spreadsheet_id=parse_google_sheet_id(args.sheet_id),
            client=GoogleSheetsClient(),
        )
        plan = sheet_store.load_plan(strategy_id=args.strategy_id)

    result = run_daily_paper(plan)
    if sheet_store is not None and not args.skip_sheet_writeback:
        sheet_store.write_result(result)
    print(f"status: {result.status}")
    print("mode: paper_auto")
    print(f"total_orders: {result.total_orders}")
    print(f"filled_orders: {result.filled_orders}")
    print(f"blocked_orders: {result.blocked_orders}")
    print(f"ending_cash_krw: {result.ledger.cash_krw}")
    print(f"realized_pnl_krw: {result.ledger.realized_pnl_krw}")
    if sheet_store is not None:
        print(f"sheet_id: {sheet_store.spreadsheet_id}")
        print(f"sheet_writeback: {not args.skip_sheet_writeback}")
    for symbol in sorted(result.ledger.positions):
        position = result.ledger.positions[symbol]
        print(f"position: {symbol} qty={position.quantity} avg={position.avg_price} state={position.state}")
    return 0



def _init_daily_paper_sheet(args: argparse.Namespace) -> int:
    from toss_alpha.storage.google_sheets import GoogleSheetsClient, GoogleSheetsDailyPaperStore

    created = GoogleSheetsDailyPaperStore.bootstrap_new_sheet(
        client=GoogleSheetsClient(),
        title=args.title,
        initial_cash_krw=args.initial_cash_krw,
    )
    print(f"spreadsheet_id: {created['spreadsheet_id']}")
    print(f"spreadsheet_url: {created['spreadsheet_url']}")
    print(f"title: {created['title']}")
    return 0


def _daily_run(args: argparse.Namespace) -> int:
    from toss_alpha.daily.decision import daily_decision_to_paper_plan, run_daily_decision

    account_source = None
    if args.use_toss_account:
        from toss_alpha.connectors.toss_readonly import TossReadOnlyClient
        import os

        account_source = TossReadOnlyClient(
            client_id=os.environ.get("TOSSINVEST_CLIENT_ID", ""),
            client_secret=os.environ.get("TOSSINVEST_CLIENT_SECRET", ""),
            account_seq=os.environ.get("TOSSINVEST_ACCOUNT_SEQ"),
            base_url=os.environ.get("TOSSINVEST_BASE_URL", "https://openapi.tossinvest.com"),
        )
    result = run_daily_decision(
        panel_csv=args.panel_csv,
        symbols=[item.strip() for item in args.symbols.split(",") if item.strip()],
        holdings_path=args.mock_holdings,
        account_source=account_source,
        slow_veto_events_path=args.slow_veto_events,
        out_dir=args.out_dir,
        as_of=args.date,
        max_notional_krw=args.max_notional_krw,
    )
    top = result["candidates"][0] if result["candidates"] else None
    paper_plan = daily_decision_to_paper_plan(result, output_path=args.paper_plan_out) if args.paper_plan_out else None
    print(f"mode: {result['mode']}")
    print(f"live_order_submitted: {result['live_order_submitted']}")
    print(f"regime: {result['regime']['status']}")
    print(f"slow_veto: {result['slow_veto']['status']}")
    print(f"top_candidate: {top['symbol'] if top else 'NONE'}")
    print(f"manual_drafts: {len(result['manual_drafts'])}")
    if paper_plan is not None:
        print(f"paper_plan_orders: {len(paper_plan['orders'])}")
        print(f"paper_plan_path: {args.paper_plan_out}")
    print(f"report_path: {result['report_path']}")
    print(f"json_path: {result['json_path']}")
    return 0


def _daily_collect_slow_events(args: argparse.Namespace) -> int:
    from toss_alpha.daily.slow_events import collect_slow_veto_events

    result = collect_slow_veto_events(
        symbols=[item.strip() for item in args.symbols.split(",") if item.strip()],
        source_paths=args.source,
        output_path=args.out,
        as_of=args.date,
    )
    print(f"status: {result['status']}")
    print(f"events: {len(result['events'])}")
    print(f"checked_symbols: {','.join(result['checked_symbols'])}")
    print(f"output_path: {args.out}")
    return 0


def _daily_paper_loop(args: argparse.Namespace) -> int:
    from toss_alpha.daily.paper_loop import run_daily_paper_loop

    sheet_store = None
    if args.sheet_id:
        from toss_alpha.storage.google_sheets import GoogleSheetsClient, GoogleSheetsDailyPaperStore, parse_google_sheet_id

        sheet_store = GoogleSheetsDailyPaperStore(
            spreadsheet_id=parse_google_sheet_id(args.sheet_id),
            client=GoogleSheetsClient(),
        )
    result = run_daily_paper_loop(
        panel_csv=args.panel_csv,
        symbols=[item.strip() for item in args.symbols.split(",") if item.strip()],
        holdings_path=args.mock_holdings,
        slow_veto_events_path=args.slow_veto_events,
        out_dir=args.out_dir,
        as_of=args.date,
        max_notional_krw=args.max_notional_krw,
        sheet_store=sheet_store,
    )
    execution = result["paper_execution"]
    print(f"mode: {result['mode']}")
    print(f"live_order_submitted: {result['live_order_submitted']}")
    print(f"slow_veto: {result['decision']['slow_veto']['status']}")
    print(f"paper_status: {execution['status']}")
    print(f"paper_total_orders: {execution['total_orders']}")
    print(f"paper_filled_orders: {execution['filled_orders']}")
    print(f"paper_blocked_orders: {execution['blocked_orders']}")
    print(f"paper_plan_path: {result['artifacts']['paper_plan_path']}")
    print(f"paper_report_path: {result['artifacts']['paper_report_path']}")
    print(f"paper_json_path: {result['artifacts']['paper_json_path']}")
    print(f"sheet_writeback: {result['sheet_writeback']['enabled']}")
    if result["sheet_writeback"]["enabled"]:
        print(f"sheet_id: {result['sheet_writeback']['spreadsheet_id']}")
    return 0


def _daily_replay(args: argparse.Namespace) -> int:
    from toss_alpha.daily.replay import run_replay

    result = run_replay(
        panel_csv=args.panel_csv,
        symbols=[],
        initial_cash_krw=args.initial_cash_krw,
        max_notional_krw=args.max_notional_krw,
        step=args.step,
        score_threshold=args.score_threshold,
        out_dir=args.out_dir,
    )
    s = result["summary"]
    print(f"mode: paper_replay")
    print(f"live_order_submitted: False")
    print(f"steps: {result['total_steps']}")
    print(f"final_equity_krw: {s['final_equity_krw']:,.0f}")
    print(f"total_return_pct: {s['total_return_pct']:.2f}")
    print(f"max_drawdown_pct: {s['max_drawdown_pct']:.2f}")
    print(f"sharpe_ratio: {s['sharpe_ratio']:.4f}")
    print(f"total_trades: {s['total_trades']}")
    print(f"win_rate_pct: {s['win_rate_pct']:.1f}")
    print(f"equity_curve_csv: {result['equity_curve_csv']}")
    print(f"summary_json: {result['summary_json']}")
    print(f"report_md: {result['report_md']}")
    return 0


def _daily_sweep(args: argparse.Namespace) -> int:
    from toss_alpha.daily.sweep import build_grid_configs, run_sweep
    import pandas as pd

    def _parse_list(raw: str, cast=float):
        return [cast(x.strip()) for x in raw.split(",") if x.strip()]

    configs = build_grid_configs(
        steps=[int(x) for x in _parse_list(args.steps)],
        score_thresholds=_parse_list(args.thresholds),
        stop_losses=_parse_list(args.stop_losses),
        take_profits=_parse_list(args.take_profits),
        max_holding_steps=[int(x) for x in _parse_list(args.holding_steps)],
        max_positions=[int(x) for x in _parse_list(args.max_positions)],
        trailing_stops=_parse_list(args.trailing_stops),
        sizing_modes=[x.strip() for x in args.sizing_modes.split(",") if x.strip()],
        min_volumes=_parse_list(args.min_volumes),
        rebalance_modes=[x.strip() for x in args.rebalance_modes.split(",") if x.strip()],
    )
    panel = pd.read_csv(args.panel_csv, dtype={"code": str}, parse_dates=["Date"])

    result = run_sweep(
        panel=panel,
        configs=configs,
        initial_cash_krw=args.initial_cash_krw,
        out_dir=args.out_dir,
    )
    best = result["best"]
    bs = best["summary"]
    print(f"mode: paper_sweep")
    print(f"live_order_submitted: False")
    print(f"total_configs: {result['total_configs']}")
    print(f"\nbest_config: {best['name']}")
    print(f"best_metric: {best['metric']}={bs['sharpe_ratio']:.4f}")
    print(f"best_return: {bs['total_return_pct']:.2f}%")
    print(f"best_mdd: {bs['max_drawdown_pct']:.2f}%")
    print(f"best_win_rate: {bs['win_rate_pct']:.1f}%")
    print(f"best_trades: {bs['total_trades']}")
    print(f"\ncomparison_csv: {result['comparison_csv']}")
    print(f"report_md: {result['report_md']}")

    # print top 5 by sharpe
    sorted_runs = sorted(result["runs"], key=lambda r: r["summary"]["sharpe_ratio"], reverse=True)
    print(f"\ntop 5 by sharpe:")
    for run in sorted_runs[:5]:
        s = run["summary"]
        print(f"  {run['name']:<30} sharpe={s['sharpe_ratio']:.4f} ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% win={s['win_rate_pct']:.1f}%")
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
