"""評価ハーネス: E1 の走査・結果の書き出し（JSON / Markdown / history.md）・まとめの表。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from app.engine.safety import DefaultOutputGuard
from evals.e1 import scan_affinity_package
from evals.metrics import Metric
from evals.records import ModeRecord
from evals.report import ModeResult, RunResult, comparison, write_outputs
from evals.run import _overrides, guard_probe, parse_args
from evals.summarize import BEGIN, END, write_summary
from evals.timeline import DEFAULT_START


def test_e1_scanner_detects_forbidden_imports_and_tables(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text('SQL = "select stage from public.affinity_states where user_id = $1"\n')
    (tmp_path / "bad.py").write_text(
        "import app.services.comments\n"
        'SQL = "select caption from public.posts p join public.likes l on l.post_id = p.id"\n'
        'Q = "update public.affinity_states set closeness = 1 where is_paid"\n'
    )
    violations, files, statements, tables = scan_affinity_package(tmp_path)
    assert files == 2
    assert statements == 3
    assert "posts" in tables
    joined = "\n".join(violations)
    assert "import app.services.comments" in joined
    assert "posts" in joined
    assert "is_paid" in joined


def test_e1_scanner_passes_on_the_real_package() -> None:
    violations, files, _statements, tables = scan_affinity_package()
    assert violations == []
    assert files > 0
    assert set(tables) <= {"affinity_states", "affinity_history", "characters"}


def _metric(key: str, value: float, passed: bool) -> Metric:
    return Metric(key=key, name_ja=key, value=value, unit="%", criterion="≥ 85%", passed=passed, n=10)


def _result() -> RunResult:
    result = RunResult(
        label="unit",
        config={"days": 3, "scenarios": ["office_worker"], "llm": "mock", "seed": 7, "start": "x", "step_minutes": 10},
        started_at=datetime(2026, 9, 26, tzinfo=UTC),
        git={"commit": "abc1234", "dirty": True},
    )
    for mode, value in (("baseline", 0.1), ("engine", 0.9)):
        record = ModeRecord(mode=mode, flags={}, run_id="r", days=3, start=DEFAULT_START, scenarios=["office_worker"])
        result.modes[mode] = ModeResult(record=record, metrics=[_metric("recall_30", value, value >= 0.85)])
    return result


def test_comparison_and_outputs(tmp_path: Path) -> None:
    result = _result()
    rows = comparison(result)
    assert rows[0]["key"] == "recall_30"
    assert rows[0]["delta"] == "+80.0 pt"
    history = tmp_path / "history.md"
    json_path, md_path = write_outputs(
        result, out_dir=tmp_path / "results", history_path=history, today=date(2026, 9, 26)
    )
    assert json_path.name == "2026-09-26-unit.json"
    data = json.loads(json_path.read_text())
    assert data["comparison"][0]["engine"] == "90.0% (n=10)"
    assert "mock" in md_path.read_text()
    write_outputs(result, out_dir=tmp_path / "results", history_path=history, today=date(2026, 9, 26))
    lines = history.read_text().splitlines()
    assert sum(1 for line in lines if line.startswith("| 2026-09-26 | unit |")) == 4  # 2 モード × 2 回
    assert "abc1234+" in lines[-1]


def test_summary_keeps_hand_written_text(tmp_path: Path) -> None:
    result = _result()
    json_path, _ = write_outputs(result, out_dir=tmp_path, history_path=None, today=date(2026, 9, 26))
    out = tmp_path / "SUMMARY.md"
    out.write_text(f"# まとめ\n\n手書きの考察\n\n{BEGIN}\nold\n{END}\n\n末尾のメモ\n")
    write_summary([json_path], out)
    text = out.read_text()
    assert "手書きの考察" in text
    assert "末尾のメモ" in text
    assert "old" not in text
    assert "90.0% (n=10) ✅" in text


def test_cli_parsing_and_guard_probe() -> None:
    config = parse_args(
        ["--days", "3", "--scenario", "office_worker", "--engine", "off", "--set", "engine_post_turn_delay_seconds=120"]
    )
    assert config.modes == ("baseline",)
    assert config.scenarios == ("office_worker",)
    assert config.judge == "rule"
    assert config.sim == "template"
    assert config.settings_overrides == {"engine_post_turn_delay_seconds": 120}
    assert parse_args(["--llm", "live"]).judge == "llm"
    assert _overrides(["a=true", "b=x"]) == {"a": True, "b": "x"}
    probe = guard_probe(DefaultOutputGuard())
    assert probe["coupling_total"] == 5
    assert probe["coupling_detected"] >= 4
