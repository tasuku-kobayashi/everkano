"""評価ハーネスの通しの実行（mock・3 日・会社員のシナリオ）。ローカル Supabase の DB が必要。

素の LLM とエンジンの両方を動かし、指標が計算され、評価のデータが後片付けされることを確認する。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from evals.config import RunConfig
from evals.e1 import check_e1
from evals.harness import PREMISES, ModeRunner
from evals.meter import Meter, PriceTable
from evals.run import run_eval
from evals.simuser import TemplatePhraser
from evals.timeline import DEFAULT_START, SimCalendar
from evals.world import leftover_run_ids
from tests.conftest import DATABASE_URL

pytestmark = pytest.mark.integration


async def test_smoke_run_office_worker_three_days() -> None:
    config = RunConfig(
        days=3,
        scenarios=("office_worker",),
        modes=("baseline", "engine"),
        database_url=DATABASE_URL,
        all_characters=False,
        min_fact_age_days=0,
        step=timedelta(minutes=10),
        run_e1=False,
        write_results=False,
        history_path=None,
        label="smoke",
    )
    e1 = check_e1(run_tests=False)
    result = await run_eval(config, e1_metric=e1.to_metric())
    assert set(result.modes) == {"baseline", "engine"}
    for name, mode in result.modes.items():
        record = mode.record
        assert record.status == "ok", (name, record.error)
        assert len(record.turns) >= 20
        assert not [t for t in record.turns if t.error]
        assert record.cleanup is not None
        assert record.cleanup["characters"] == 1
        assert record.cleanup["users"] == 1
        keys = {m.key for m in mode.metrics}
        assert {"recall_30", "false_memory", "state_reflection", "e1", "e2", "latency", "cost"} <= keys
    engine = result.modes["engine"]
    baseline = result.modes["baseline"]
    # エンジン: 記憶が文脈に入り、カレンダーが生成され、状態が返答に反映される（mock の仕組みの確認）
    recall = engine.metric("recall_30")
    assert recall is not None
    assert recall.detail["retrieved_rate"] is not None
    assert recall.detail["retrieved_rate"] > 0.5
    calendar = engine.metric("calendar_consistency")
    assert calendar is not None
    assert calendar.passed is True
    assert (calendar.n or 0) > 0
    assert engine.record.flags == {"memory": True, "calendar": True, "affinity": True, "proactive": True}
    # 素の LLM: 記憶・カレンダーは使わない
    base_recall = baseline.metric("recall_30")
    assert base_recall is not None
    assert base_recall.detail["retrieved_rate"] == 0
    assert baseline.record.flags == {"memory": False, "calendar": False, "affinity": False, "proactive": False}
    assert "chat" in engine.llm_usage
    assert "memory_analysis" in engine.llm_usage
    assert "memory_analysis" not in baseline.llm_usage
    # 後片付け: 評価のキャラ・ユーザーが残っていない
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        leftovers = set(await leftover_run_ids(conn))
    finally:
        await conn.close()
    assert not leftovers & {m.record.run_id for m in result.modes.values()}


async def test_last_week_probe_queries_both_branches() -> None:
    """「この前の〇〇どうだった？」: 最近の出来事が無いキャラでは、無かった出来事（旅行など）を尋ねる。

    SQL のパラメータの型の検査も兼ねる（この分岐は 30 日の実行では通らず、90 日の実行で初めて失敗した）。
    """
    runner = ModeRunner(
        RunConfig(database_url=DATABASE_URL),
        "engine",
        plans=[],
        cal=SimCalendar(DEFAULT_START, 30),
        meter=Meter(PriceTable({"m": {"input": 1.0, "output": 1.0}}, default_model="m")),
        phraser_factory=lambda _llm: TemplatePhraser(),
    )
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=1)
    try:
        text, context = await runner._last_week_question(pool, uuid.uuid4(), datetime(2030, 1, 20, 12, tzinfo=UTC))
    finally:
        await pool.close()
    assert text == f"先週の{PREMISES[0]}、どうだった？"
    assert context["premise"] == PREMISES[0]
