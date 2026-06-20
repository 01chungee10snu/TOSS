from __future__ import annotations

import json
from pathlib import Path

from toss_alpha.cli import main
from toss_alpha.daily.slow_events import collect_slow_veto_events


def test_collect_slow_veto_events_normalizes_manual_json_and_classifies_keywords(tmp_path: Path):
    source = tmp_path / "events.json"
    out = tmp_path / "slow_events.json"
    source.write_text(
        json.dumps(
            {
                "events": [
                    {"symbol": "005930", "title": "전환사채 발행 결정", "source": "manual"},
                    {"symbol": "000660", "title": "거래정지 및 상장폐지 사유 발생", "source": "news"},
                    {"symbol": "035420", "title": "분기보고서 제출", "source": "dart"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = collect_slow_veto_events(
        symbols=["005930", "000660"],
        source_paths=[source],
        output_path=out,
        as_of="2026-06-20",
    )

    assert payload["as_of"] == "2026-06-20"
    assert payload["status"] == "BLOCK"
    assert [event["symbol"] for event in payload["events"]] == ["005930", "000660"]
    assert payload["events"][0]["severity"] == "review"
    assert payload["events"][0]["matched_keywords"] == ["전환사채"]
    assert payload["events"][1]["severity"] == "block"
    assert "거래정지" in payload["events"][1]["matched_keywords"]
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "BLOCK"


def test_collect_slow_veto_events_accepts_csv_sources(tmp_path: Path):
    source = tmp_path / "events.csv"
    source.write_text(
        "symbol,title,source,severity\n"
        "005930,대규모 유상증자 결정,manual,\n"
        "000660,일반 공지,manual,info\n",
        encoding="utf-8",
    )

    payload = collect_slow_veto_events(symbols=["005930", "000660"], source_paths=[source])

    assert payload["status"] == "REVIEW_REQUIRED"
    assert payload["events"][0]["severity"] == "review"
    assert payload["events"][1]["severity"] == "info"


def test_cli_collect_slow_events_writes_file(tmp_path: Path, capsys):
    source = tmp_path / "events.json"
    out = tmp_path / "slow_events.json"
    source.write_text(
        json.dumps({"events": [{"symbol": "005930", "title": "횡령 발생", "source": "news"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = main(
        [
            "daily",
            "collect-slow-events",
            "--symbols",
            "005930,000660",
            "--source",
            str(source),
            "--out",
            str(out),
            "--date",
            "2026-06-20",
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "status: BLOCK" in stdout
    assert "events: 1" in stdout
    assert "output_path:" in stdout
    assert json.loads(out.read_text(encoding="utf-8"))["events"][0]["severity"] == "block"
