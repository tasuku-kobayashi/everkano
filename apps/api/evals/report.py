"""結果の書き出し: docs/eval/results/<日付>-<ラベル>.json と .md、docs/eval/history.md への 1 行（回帰の記録 §9.3）。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from evals.metrics import HEADLINE_ORDER, Metric
from evals.prompts import REPO_ROOT
from evals.records import ModeRecord

HISTORY_HEADER: Final[str] = (
    "| 日付 | ラベル | LLM | 日数 | シナリオ | モード | 想起30 | 誤り | 約束 | 自己矛盾 | 予定 | 状態 | "
    "好感度 | 操作 | "
    "E1 | E2 | E6 | 遅延(推計) | コスト(中央) | commit |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)
HISTORY_KEYS: Final[tuple[str, ...]] = (
    "recall_30",
    "false_memory",
    "promise_recovery",
    "self_contradiction",
    "calendar_consistency",
    "state_reflection",
    "affinity_validity",
    "manipulation_resistance",
    "e1",
    "e2",
    "e6",
    "latency",
    "cost",
)

LIMITATIONS_MOCK: Final[str] = (
    "この結果は `LLM_MODE=mock`・`EMBEDDING_MODE=hash` の実行です。MockLLM の返答・記憶の抽出・好感度の採点は"
    "決定的なルールなので、測っているのはエンジンの**仕組み**（抽出・保存・検索・文脈への注入・期日・予定・状態・"
    "上限・安全対応・ガード）であり、実際のモデルの言語の質ではありません。想起・誤り・自己矛盾・状態の反映の"
    "「返答の判定」はルール（キーワードの照合）で、返答の文面はモックが文脈の材料を機械的に並べたものです。"
    "言語の質の数値には `--llm live`（LLM の判定・シミュレーションユーザー）での実行が必要です。"
)


@dataclass(slots=True)
class ModeResult:
    record: ModeRecord
    metrics: list[Metric]
    llm_usage: dict[str, Any] = field(default_factory=dict)
    projection: dict[str, Any] | None = None

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)


@dataclass(slots=True)
class RunResult:
    label: str
    config: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    modes: dict[str, ModeResult] = field(default_factory=dict)
    e1: dict[str, Any] | None = None
    guard_probe: dict[str, Any] | None = None
    assumptions: dict[str, Any] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)
    judge: str = "rule"
    phraser: str = "template"
    notes: list[str] = field(default_factory=list)


def git_info() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            done = subprocess.run(  # noqa: S603 - 固定の git の読み取りコマンド
                ["git", *args],  # noqa: S607
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip()

    status = run("status", "--porcelain")
    return {"commit": run("rev-parse", "--short", "HEAD") or "unknown", "dirty": bool(status)}


def _fmt(metric: Metric | None) -> str:
    return "—" if metric is None else metric.display


def _mark(metric: Metric | None) -> str:
    if metric is None or metric.passed is None:
        return "n/a"
    return "PASS" if metric.passed else "FAIL"


def _delta(base: Metric | None, engine: Metric | None) -> str:
    if base is None or engine is None or base.value is None or engine.value is None:
        return "—"
    diff = engine.value - base.value
    if engine.unit == "%":
        return f"{diff * 100:+.1f} pt"
    if engine.unit == "ms":
        return f"{diff:+.0f} ms"
    if engine.unit == "¥":
        return f"{diff:+.1f} ¥"
    return f"{diff:+g}"


def comparison(result: RunResult) -> list[dict[str, Any]]:
    base = result.modes.get("baseline")
    engine = result.modes.get("engine")
    keys = [k for k in HEADLINE_ORDER if any(m.metric(k) for m in result.modes.values())]
    keys += [
        m.key
        for mode in result.modes.values()
        for m in mode.metrics
        if m.key not in keys and m.key.startswith("recall")
    ]
    rows: list[dict[str, Any]] = []
    for key in dict.fromkeys(keys):
        b = base.metric(key) if base else None
        e = engine.metric(key) if engine else None
        ref = e or b
        if ref is None:
            continue
        rows.append(
            {
                "key": key,
                "name": ref.name_ja,
                "criterion": ref.criterion,
                "baseline": b.display if b else None,
                "baseline_passed": b.passed if b else None,
                "engine": e.display if e else None,
                "engine_passed": e.passed if e else None,
                "delta": _delta(b, e),
            }
        )
    return rows


def _probe_turns(record: ModeRecord) -> list[dict[str, Any]]:
    kinds = {
        "probe_recall",
        "probe_false",
        "probe_self",
        "probe_state",
        "manipulation",
        "commerce_bait",
        "humanity",
        "crisis",
        "crisis_negative",
        "promise",
        "promise_outcome",
        "fact",
    }
    return [t.to_dict() for t in record.turns if t.kind in kinds or t.error]


def _stage_changes(record: ModeRecord) -> dict[str, list[list[Any]]]:
    """ユーザーごとの段階の推移（段階が変わった日だけ）。"""
    out: dict[str, list[list[Any]]] = {}
    for snap in sorted(record.affinity_daily, key=lambda s: (s.user, s.day)):
        changes = out.setdefault(snap.user, [])
        if not changes or changes[-1][1] != snap.stage:
            changes.append([snap.day, snap.stage])
    return out


def to_json(result: RunResult) -> dict[str, Any]:
    modes: dict[str, Any] = {}
    for name, mode in result.modes.items():
        record = mode.record
        modes[name] = {
            "status": record.status,
            "error": record.error,
            "flags": record.flags,
            "run_id": record.run_id,
            "wall_seconds": record.wall_seconds,
            "turns": len(record.turns),
            "turn_errors": sum(1 for t in record.turns if t.error),
            "metrics": [m.to_dict() for m in mode.metrics],
            "llm_usage": mode.llm_usage,
            "cost_projection": mode.projection,
            "driver": record.driver,
            "jobs": record.jobs,
            "audit_counts": record.audit_counts,
            "memories_db": record.memories_db,
            "promises_db": record.promises_db,
            "calendar": record.calendar_report,
            "proactive": [p.to_dict() for p in record.proactive],
            "captions": [c.to_dict() for c in record.captions[:30]],
            "captions_total": len(record.captions),
            "affinity_final": [s.to_dict() for s in record.affinity_daily if s.day == record.days],
            "affinity_stages": _stage_changes(record),
            "isolation": record.isolation,
            "cleanup": record.cleanup,
            "output_verdicts": record.output_verdicts[:50],
            "turn_log": _probe_turns(record),
        }
    return {
        "label": result.label,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "config": result.config,
        "judge": result.judge,
        "phraser": result.phraser,
        "git": result.git,
        "assumptions": result.assumptions,
        "comparison": comparison(result),
        "e1": result.e1,
        "guard_probe": result.guard_probe,
        "notes": result.notes,
        "modes": modes,
    }


def to_markdown(result: RunResult) -> str:
    cfg = result.config
    lines: list[str] = [
        f"# 評価結果: {result.label}",
        "",
        f"- 実行: {result.started_at:%Y-%m-%d %H:%M} UTC / commit `{result.git.get('commit')}`"
        + (" (未コミットの変更あり)" if result.git.get("dirty") else ""),
        f"- LLM: `{cfg.get('llm')}` / 判定: `{result.judge}` / シミュレーションユーザー: `{result.phraser}` / "
        f"seed {cfg.get('seed')}",
        f"- 期間: {cfg.get('days')} 日（シミュレーション開始 {cfg.get('start')}、"
        f"時計の刻み {cfg.get('step_minutes')} 分）",
        f"- シナリオ: {', '.join(cfg.get('scenarios', []))}",
        "",
    ]
    if cfg.get("llm") == "mock":
        lines += [f"> {LIMITATIONS_MOCK}", ""]
    for name, mode in result.modes.items():
        record = mode.record
        lines.append(
            f"- `{name}`: status **{record.status}**"
            + (f"（{record.error}）" if record.error else "")
            + f" / 発言 {len(record.turns)}（失敗 {sum(1 for t in record.turns if t.error)}）"
            + f"/ 実時間 {record.wall_seconds} 秒"
            + f" / 自発メッセージ {sum(1 for p in record.proactive if p.body)} / 投稿 {len(record.captions)}"
        )
    lines += ["", "## §9.2 の指標（素の LLM vs エンジン）", ""]
    lines += [
        "| 指標 | 合格ライン | 素の LLM | エンジン | 差 | 判定（エンジン） |",
        "|---|---|---|---|---|---|",
    ]
    for row in comparison(result):
        engine_mark = "n/a" if row["engine_passed"] is None else ("PASS" if row["engine_passed"] else "FAIL")
        if row["engine"] is None:
            engine_mark = "n/a" if row["baseline_passed"] is None else ("PASS" if row["baseline_passed"] else "FAIL")
        lines.append(
            f"| {row['name']} | {row['criterion']} | {row['baseline'] or '—'} | {row['engine'] or '—'} | "
            f"{row['delta']} | {engine_mark} |"
        )
    engine = result.modes.get("engine") or next(iter(result.modes.values()), None)
    if engine is not None:
        lines += ["", "## 内訳（エンジン）", ""]
        for metric in engine.metrics:
            lines += _metric_section(metric)
    if result.guard_probe:
        gp = result.guard_probe
        lines += [
            "",
            "## OutputGuard（E2）の固定の例文での確認（参考）",
            "",
            f"- 結びつける発言の検出: {gp['coupling_detected']}/{gp['coupling_total']}"
            + (f" — 見逃し: {', '.join(gp['coupling_missed'])}" if gp["coupling_missed"] else ""),
            f"- 切り離す発言（違反ではない）の誤検知: {gp['decoupling_flagged']}/{gp['decoupling_total']}"
            + (f" — {', '.join(gp['decoupling_false_positives'])}" if gp["decoupling_false_positives"] else ""),
        ]
    if result.assumptions:
        lines += ["", "## 推計の仮定", ""]
        lines += [f"- {k}: {v}" for k, v in result.assumptions.items()]
    if result.notes:
        lines += ["", "## メモ", ""]
        lines += [f"- {n}" for n in result.notes]
    return "\n".join(lines) + "\n"


def _metric_section(metric: Metric) -> list[str]:
    head = f"### {metric.name_ja}: {metric.display} — {_mark(metric)}（{metric.criterion}）"
    out = [head, ""]
    if metric.note:
        out.append(f"- {metric.note}")
    d = metric.detail
    key = metric.key
    if key.startswith("recall"):
        out.append(
            f"- 判定の内訳: {d.get('labels')} / 記憶として保存: {_pct(d.get('extracted_rate'))} / "
            f"返答の文脈に入った: {_pct(d.get('retrieved_rate'))} / "
            f"文脈に入ったうち正答: {_pct(d.get('answered_given_retrieved'))}"
        )
        out += [
            f"  - {'○' if p['passed'] else '×'} {p['user']}「{p['topic']}」（{p['fact_day']}日目→{p['probe_day']}日目, "
            f"保存 {'有' if p['extracted'] else '無'} / 文脈 {'有' if p['retrieved'] else '無'}）: {p['reply'][:80]}"
            for p in d.get("probes", [])
            if not p["passed"]
        ][:12]
    elif key == "false_memory":
        out += [
            f"  - {p['label']}: {p['user']}「{p['question']}」→ {p['reply'][:80]}"
            for p in d.get("probes", [])
            if p["label"] != "not_asserted"
        ][:10]
    elif key == "promise_recovery":
        out.append(
            f"- 期日を正しく解決して保存: {_pct(d.get('extracted_with_correct_due'))} / "
            f"回収の手段: 自発メッセージ {d.get('by_proactive')}・返答 {d.get('by_reply')}"
        )
        for p in d.get("promises", []):
            first = p["first"]
            how = f"{first['how']}: {first['text'][:60]}" if first else "回収なし"
            out.append(
                f"  - {'○' if p['recovered'] else '×'} {p['user']}「{p['key']}」"
                f"{p['told_day']}日目→期日 {p['due_day']}日目"
                f"（期日前後の来訪日 {p['user_active_days_in_window']} / 期日前日の段階 {p.get('stage_before_due')} / "
                f"エンジンの約束 {p['engine_promise']}）: {how}"
            )
    elif key == "self_contradiction":
        out.append(f"- 判定の内訳: {d.get('labels')}")
        out += [
            f"  - {p['label']}: {p['persona']}「{p['question']}」（状態: {p['state']} / 前提: {p['premise']}）→ "
            f"{p['reply'][:80]}"
            for p in d.get("probes", [])
            if p["label"] == "contradiction"
        ][:10]
    elif key == "state_reflection":
        out.append(f"- 文脈に正しい状態が入った割合: {_pct(d.get('state_in_context_rate'))}")
        out += [
            f"  - {p['label']}: {p['persona']} {p['at'][11:16]}Z 状態「{p['state']}」→ {p['reply'][:70]}"
            for p in d.get("probes", [])
            if p["label"] != "reflected"
        ][:10]
    elif key == "calendar_consistency" and d:
        out.append(
            f"- 予定 {d.get('events')} 件 / キャラ {d.get('characters')} 体 / error の内訳 {d.get('counts')} / "
            f"warning {d.get('warnings')}"
        )
        out += [f"  - {v['code']}: {v['message'][:120]}" for v in d.get("violations", []) if v["severity"] == "error"][
            :10
        ]
    elif key == "affinity_validity" and d:
        finals = d.get("final", {})
        out.append(
            "- 最終日: " + " / ".join(f"{u}: {v['stage']}（好意の合計 {v['positive']}）" for u, v in finals.items())
        )
        if d.get("stage_inversions") or d.get("positive_inversions"):
            stage_inv = (d.get("stage_inversions") or [])[:5]
            positive_inv = (d.get("positive_inversions") or [])[:5]
            out.append(f"- 逆転: 段階 {stage_inv} / 好意の合計 {positive_inv}")
    elif key == "manipulation_resistance" and d:
        out.append(f"- カテゴリ別（上がった/総数）: {d.get('by_category')}")
        out += [f"  - {m['category']}: 「{m['text']}」→ {m['increase']}" for m in d.get("moved", [])][:10]
        out.append(
            f"- 参考: 操作の発言がユーザーの記憶として保存された数: {d.get('stored_as_memory')}"
            f"/{d.get('attempts_checked_for_memory')}"
        )
        out += [
            f"  - {s['category']}: 「{s['text']}」→ {[m['kind'] + ':' + m['content'][:40] for m in s['memories']]}"
            for s in d.get("stored_examples", [])
        ][:10]
    elif key == "e1" and d:
        failed = [t for t in d.get("tests", []) if t["outcome"] != "passed"]
        out.append(
            f"- 構造テスト {len(d.get('tests', []))} 件（失敗 {len(failed)}）/ "
            f"走査したファイル {d.get('files_scanned')}・"
            f"SQL {d.get('sql_statements')}・テーブル {d.get('tables_seen')} / 走査の違反 {d.get('scan_violations')}"
        )
        if d.get("test_error"):
            out.append(f"- テストの実行: {d.get('test_error')}")
    elif key == "e2":
        out.append(
            f"- 生成時点で OutputGuard が差し止めた数: {d.get('generated_then_blocked')} / "
            f"moderation.flag: {d.get('moderation_flags')}"
        )
        out += [f"  - {v['source']}: {v['text'][:80]}（{v['reason']}）" for v in d.get("delivered_violations", [])][:10]
    elif key == "e6":
        out.append(
            f"- 見逃し: {d.get('missed')} / "
            f"危機ではない発言での誤発動: {_pct(d.get('false_positive_rate_on_negatives'))}"
            f" {d.get('false_positives')}"
        )
    elif key == "latency":
        out.append(
            f"- パイプラインの実測（最初の文字まで）: p50 {_ms(d.get('overhead_p50_ms'))} / "
            f"p90 {_ms(d.get('overhead_p90_ms'))} / p99 {_ms(d.get('overhead_p99_ms'))}（n={d.get('samples')}）/ "
            f"文脈の部品の p50: {d.get('context_timings_p50_ms')}"
        )
        if d.get("estimate_p50_ms_by_model_ttft"):
            out.append(f"- モデルの TTFT の仮定ごとの推計（p50）: {d.get('estimate_p50_ms_by_model_ttft')}")
    elif key == "cost" and d:
        out.append(
            f"- 1 発言あたり: chat ¥{d.get('chat_per_turn_jpy')}"
            f"（定常時の入力 {d.get('chat_prompt_tokens_steady')} トークン、"
            f"キャッシュ見込み {_pct(d.get('chat_cached_share'))}）+ 返答後の分析 ¥{d.get('analysis_per_turn_jpy')}"
            f"（{d.get('analysis_calls_per_turn')} 回/発言）/ "
            f"自発メッセージ ¥{d.get('proactive_per_active_day_jpy')}/日"
        )
        out.append(
            f"- 月額（30 日）: {d.get('profiles_jpy_per_month')} / "
            f"キャプション ¥{d.get('caption_per_character_day_jpy')}/キャラ/日（固定費）"
        )
        if d.get("sensitivity_tokens_per_char"):
            out.append(f"- トークン数の仮定（文字あたり）ごとの月額: {d.get('sensitivity_tokens_per_char')}")
        if d.get("measured_tokens_per_char"):
            out.append(
                "- トークナイザで数えた文字あたりのトークン数（用途ごとの入力 / 出力 / キャッシュに当たる割合）: "
                f"{d.get('measured_tokens_per_char')}"
            )
    elif key == "e4" and d:
        out.append(
            f"- 送信 {d.get('sent')}（生成したが送らなかった {d.get('generated_not_sent')}）/ "
            f"きっかけ別 {d.get('by_trigger')} /"
            f" 1 ユーザー 1 日の最大 {d.get('max_per_user_day')} / 上限超過 {d.get('over_daily_limit')} /"
            f" 送らない時間帯の送信 {len(d.get('quiet_hour_sends', []))}"
        )
    elif key == "e3" and d:
        out.append(
            f"- 「人間だと言って」への返答（例）: {[h['reply'][:50] for h in d.get('humanity_requests', [])][:3]}"
        )
    out.append("")
    return out


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.0f}%"


def _ms(value: Any) -> str:
    return "—" if value is None else f"{float(value):.0f} ms"


def history_rows(result: RunResult, *, today: date) -> list[str]:
    rows: list[str] = []
    cfg = result.config
    for name, mode in result.modes.items():
        cells = [
            today.isoformat(),
            result.label,
            str(cfg.get("llm")),
            str(cfg.get("days")),
            ",".join(cfg.get("scenarios", [])),
            name,
        ]
        for key in HISTORY_KEYS:
            metric = mode.metric(key)
            cells.append("—" if metric is None or metric.value is None else metric.display.split(" (n=")[0])
        commit = str(result.git.get("commit")) + ("+" if result.git.get("dirty") else "")
        cells.append(commit)
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def write_outputs(result: RunResult, *, out_dir: Path, history_path: Path | None, today: date) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{today.isoformat()}-{result.label}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(
        json.dumps(to_json(result), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(to_markdown(result), encoding="utf-8")
    if history_path is not None:
        if not history_path.exists():
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(
                "# 評価の推移（回帰の記録・仕様 §9.3）\n\n"
                "`python -m evals.run` が実行ごとにモード（素の LLM / エンジン）ごとの 1 行を追記する。"
                "mock の行は仕組みの確認、live の行が言語の質を含む。\n\n" + HISTORY_HEADER,
                encoding="utf-8",
            )
        with history_path.open("a", encoding="utf-8") as fh:
            for row in history_rows(result, today=today):
                fh.write(row + "\n")
    return json_path, md_path
