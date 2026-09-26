"""Calendar Engine の統合テスト（ローカル Supabase の Postgres）。

ensure_schedules の冪等性・既存の予定との衝突・状態・tick（状態 / 完了 / キャラ側の記憶 / 投稿 / 監査ログ）・
キャプションの検査・約束の予定化（C8）・一貫性チェック（C11）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from app.engine.calendar.consistency import check_overlaps
from app.engine.calendar.generator import PlanContext, plan_range
from app.engine.calendar.life import is_sleep_like, life_spec_from_persona
from app.engine.calendar.service import CalendarConfig
from app.engine.types import JST, GuardResult, jst_date
from app.services.llm import LLMError, LLMRequest, LLMResult
from tests.engine.calendar.conftest import CalendarWorld
from tests.engine.calendar.personas import early_persona, night_persona, office_persona

pytestmark = pytest.mark.integration

# 2027-03-29（月）。花見の期間（3/25〜4/10）を含む週
NOW = datetime(2027, 3, 29, 8, 0, tzinfo=JST)


def jst(month: int, day: int, hour: int = 0, minute: int = 0, year: int = 2027) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JST)


async def generated_days(cal: CalendarWorld, character_id: uuid.UUID) -> dict[date, int]:
    rows = await cal.world.conn.fetch(
        """
        select generated_for, count(*) as n from public.character_events
         where character_id = $1 and generated_for is not null group by generated_for order by generated_for
        """,
        character_id,
    )
    return {r["generated_for"]: r["n"] for r in rows}


async def test_ensure_schedules_is_idempotent_and_audited(cal: CalendarWorld) -> None:
    ids = [await cal.character(p) for p in (office_persona(), night_persona(), early_persona())]
    engine = cal.engine()

    created = await engine.ensure_schedules(now=NOW, days_ahead=7, character_ids=ids)
    assert created > 0
    assert await engine.ensure_schedules(now=NOW, days_ahead=7, character_ids=ids) == 0
    # 1 時間後（同じ日）でも増えない
    assert await engine.ensure_schedules(now=NOW + timedelta(hours=1), days_ahead=7, character_ids=ids) == 0

    total = 0
    for cid in ids:
        days = await generated_days(cal, cid)
        # 昨日〜 6 日後（計 8 日）
        assert sorted(days) == [date(2027, 3, 28) + timedelta(days=i) for i in range(8)]
        total += sum(days.values())
        audits = await cal.audits(cid, "calendar.generate")
        assert len(audits) == 1
        assert audits[0]["llm_calls"] == 0
        assert audits[0]["event_count"] == sum(days.values())
        assert datetime.fromisoformat(audits[0]["at"]) == NOW  # 監査ログの時刻もアプリの時計
        logged_at = await cal.world.conn.fetchval(
            "select created_at from public.audit_logs where character_id = $1 and event_type = 'calendar.generate'",
            cid,
        )
        assert logged_at == NOW
    assert total == created

    # 翌日になると 1 日分だけ増える（ローリング）
    more = await engine.ensure_schedules(now=NOW + timedelta(days=1), days_ahead=7, character_ids=ids)
    assert more > 0
    for cid in ids:
        assert max(await generated_days(cal, cid)) == date(2027, 4, 5)

    report = await engine.check_consistency(start=date(2027, 3, 28), end=date(2027, 4, 5), character_ids=ids)
    assert report.errors == ()
    assert report.characters == 3


async def test_generated_rows_match_the_pure_plan(cal: CalendarWorld) -> None:
    persona = office_persona()
    night_id = await cal.character(night_persona())
    cid = await cal.character(persona)
    spec = life_spec_from_persona(persona)
    engine = cal.engine()
    # C10: 友人（別キャラ）の名前が出てくる単発の予定には participants が入る
    context = PlanContext(companions={"ヨル": night_id})
    lunch_day = next(
        (
            e.generated_for
            for e in plan_range(spec, cid, date(2027, 3, 28), date(2027, 9, 30), context)
            if e.source_key == "event:lunch_with_yoru"
        ),
        None,
    )
    assert lunch_day is not None
    now = datetime(lunch_day.year, lunch_day.month, lunch_day.day, 8, tzinfo=JST)
    await engine.ensure_schedules(now=now, days_ahead=2, character_ids=[cid, night_id])
    rows = await engine.events_between(character_id=cid, start=now - timedelta(days=1), end=now + timedelta(days=2))
    expected = plan_range(spec, cid, lunch_day - timedelta(days=1), lunch_day + timedelta(days=1), context)
    assert [(r.source_key, r.starts_at, r.ends_at) for r in rows if r.generated_for] == [
        (e.source_key, e.starts_at, e.ends_at) for e in expected
    ]
    lunch = next(r for r in rows if r.source_key == "event:lunch_with_yoru")
    assert lunch.participants == (night_id,)
    assert lunch.meta["notable"] is True
    assert lunch.meta["status_label"] == "ランチ中"


async def test_existing_events_are_respected(cal: CalendarWorld) -> None:
    persona = office_persona()
    cid = await cal.character(persona)
    spec = life_spec_from_persona(persona)
    planned = plan_range(spec, cid, date(2027, 3, 28), date(2027, 4, 4))
    oneoff = next((e for e in planned if e.kind in ("oneoff", "seasonal")), None)
    assert oneoff is not None
    conn = cal.world.conn
    # 手動の予定: 火曜の昼（仕事の途中）と、生成される単発の予定と重なる時間
    manual = [(jst(3, 30, 12), jst(3, 30, 13)), (oneoff.starts_at, oneoff.starts_at + timedelta(minutes=30))]
    for start, end in manual:
        await conn.execute(
            """
            insert into public.character_events (character_id, kind, title, starts_at, ends_at, source)
            values ($1, 'oneoff', '手動の予定', $2, $3, 'manual')
            """,
            cid,
            start,
            end,
        )
    engine = cal.engine()
    await engine.ensure_schedules(now=NOW, days_ahead=7, character_ids=[cid])
    rows = await engine.events_between(character_id=cid, start=jst(3, 27), end=jst(4, 6))
    assert check_overlaps(rows) == []
    assert not any(r.source_key == oneoff.source_key and r.starts_at == oneoff.starts_at for r in rows)
    work = [r for r in rows if r.title == "仕事" and r.generated_for == date(2027, 3, 30)]
    assert [(w.starts_at, w.ends_at) for w in work] == [
        (jst(3, 30, 9, 30), jst(3, 30, 12)),
        (jst(3, 30, 13), jst(3, 30, 18, 30)),
    ]
    conflicts = await cal.audits(cid, "calendar.conflict")
    assert conflicts
    assert any(d["title"] == oneoff.title for d in conflicts[0]["dropped"])
    report = await engine.check_consistency(start=date(2027, 3, 28), end=date(2027, 4, 4), character_ids=[cid])
    assert report.errors == ()


async def test_concurrent_generation_does_not_duplicate(cal: CalendarWorld) -> None:
    cid = await cal.character(night_persona())
    engine_a, engine_b = cal.engine(), cal.engine()
    a, b = await asyncio.gather(
        engine_a.ensure_schedules(now=NOW, days_ahead=7, character_ids=[cid]),
        engine_b.ensure_schedules(now=NOW, days_ahead=7, character_ids=[cid]),
    )
    days = await generated_days(cal, cid)
    assert a + b == sum(days.values())
    assert 0 in (a, b)  # 片方はロックを待ってから「生成済み」を見る


async def test_current_state_before_and_after_generation(cal: CalendarWorld) -> None:
    cid = await cal.character(office_persona())
    engine = cal.engine()
    at_work = jst(3, 30, 11)  # 火曜 11:00
    before = await engine.current_state(character_id=cid, now=at_work)
    assert before.activity == "仕事"
    assert before.event_id is None  # まだ DB に無い → 生成器の結果をその場で使う
    assert before.status_label == "仕事中"
    assert before.busyness == 2
    await engine.ensure_schedules(now=NOW, days_ahead=7, character_ids=[cid])
    after = await engine.current_state(character_id=cid, now=at_work)
    assert after.activity == before.activity
    assert after.event_id is not None
    assert after.reply_style_hint == before.reply_style_hint
    night = await engine.current_state(character_id=cid, now=jst(3, 31, 3))
    assert night.busyness == 3
    assert "眠そう" in night.reply_style_hint
    unknown = await engine.current_state(character_id=uuid.uuid4(), now=at_work)
    assert unknown.event_id is None
    assert unknown.activity


async def test_tick_updates_state_memories_and_posts(cal: CalendarWorld) -> None:
    cid = await cal.character(early_persona())
    engine = cal.engine()
    await engine.ensure_schedules(now=NOW, days_ahead=7, character_ids=[cid])
    events = await engine.events_between(character_id=cid, start=NOW, end=NOW + timedelta(days=7))
    target = next(e for e in events if e.kind != "routine" and e.post_probability >= 1.0 and e.starts_at > NOW)
    conn = cal.world.conn

    # 予定の途中: 状態が予定になる（監査ログ calendar.state_change）
    during = target.starts_at + timedelta(minutes=10)
    report = await engine.run_tick(now=during, character_ids=[cid])
    assert report.errors == 0
    state = await conn.fetchrow("select * from public.character_states where character_id = $1", cid)
    assert state is not None
    assert state["event_id"] == target.id
    assert state["activity"] == target.title
    assert state["updated_at"] == during
    changes = await cal.audits(cid, "calendar.state_change")
    assert changes[-1]["to"]["event_id"] == str(target.id)
    # 同じ時刻にもう一度: 変化なし（監査ログも増えない）
    await engine.run_tick(now=during, character_ids=[cid])
    assert len(await cal.audits(cid, "calendar.state_change")) == len(changes)

    # 予定の後: 完了・キャラ側の記憶（C9）・投稿（C7）
    after = target.ends_at + timedelta(minutes=5)
    report = await engine.run_tick(now=after, character_ids=[cid])
    assert report.errors == 0
    assert report.posts_created >= 1
    row = await conn.fetchrow("select status, post_id, meta from public.character_events where id = $1", target.id)
    assert row["status"] == "done"
    assert row["post_id"] is not None
    assert row["meta"]["post_state"] == "posted"
    post = await conn.fetchrow("select * from public.posts where id = $1", row["post_id"])
    assert post["source_event_id"] == target.id
    assert post["character_id"] == cid
    assert not post["is_paid"]
    assert target.ends_at + timedelta(minutes=10) <= post["published_at"] <= target.ends_at + timedelta(minutes=60)
    assert post["created_at"] == after
    assert target.title in post["caption"]
    assert post["image_url"].startswith("https://picsum.photos/seed/")
    memory = await conn.fetchrow("select * from public.character_memories where source_event_id = $1", target.id)
    assert memory is not None
    assert memory["user_id"] is None
    assert memory["kind"] == "event"
    assert target.title in memory["content"]
    assert memory["occurred_at"] == target.starts_at
    assert memory["created_at"] == after
    assert memory["embedding"] is not None
    done_audits = await cal.audits(cid, "calendar.event_done")
    assert any(str(target.id) == e["id"] for a in done_audits for e in a["events"])
    post_audits = await cal.audits(cid, "calendar.post_create")
    assert any(a["event_id"] == str(target.id) and a["model"] == "mock-persona-v1" for a in post_audits)
    assert all(a["usage"] for a in post_audits)
    memory_audits = await cal.audits(cid, "character_memory.create")
    assert any(a["source_event_id"] == str(target.id) for a in memory_audits)

    # もう一度 tick しても増えない（冪等）
    posts_before = await conn.fetchval("select count(*) from public.posts where character_id = $1", cid)
    memories_before = await conn.fetchval("select count(*) from public.character_memories where character_id = $1", cid)
    again = await engine.run_tick(now=after, character_ids=[cid])
    assert again.posts_created == 0
    assert again.memories_created == 0
    assert await conn.fetchval("select count(*) from public.posts where character_id = $1", cid) == posts_before
    assert (
        await conn.fetchval("select count(*) from public.character_memories where character_id = $1", cid)
        == memories_before
    )

    # 状態・記憶・予定の一貫性
    report_c = await engine.check_consistency(start=date(2027, 3, 28), end=date(2027, 4, 4), character_ids=[cid])
    assert report_c.errors == (), report_c.to_dict()


async def insert_manual_events(
    cal: CalendarWorld, cid: uuid.UUID, ends: list[datetime], *, probability: float = 1.0
) -> list[uuid.UUID]:
    ids = []
    for end in ends:
        ids.append(
            await cal.world.conn.fetchval(
                """
                insert into public.character_events (character_id, kind, title, location, starts_at, ends_at, mood,
                                                     source, meta)
                values ($1, 'oneoff', '友だちとカフェ', '駅前のカフェ', $2, $3, '楽しい', 'manual', $4)
                returning id
                """,
                cid,
                end - timedelta(hours=1),
                end,
                {"post_probability": probability, "post_tags": ["cafe"], "notable": True, "status_label": "カフェ"},
            )
        )
    return ids


async def test_posts_are_capped_per_day(cal: CalendarWorld) -> None:
    cid = await cal.character(night_persona())
    engine = cal.engine()
    await insert_manual_events(cal, cid, [jst(4, 20, 10), jst(4, 20, 12), jst(4, 20, 14)])
    report = await engine.run_tick(now=jst(4, 20, 14, 5), character_ids=[cid])
    assert report.posts_created == 2
    assert report.posts_skipped == {"daily_cap": 1}
    assert await cal.world.conn.fetchval("select count(*) from public.posts where character_id = $1", cid) == 2
    # 投稿済みの画像は、候補があれば使い回さない
    urls = [
        r["image_url"]
        for r in await cal.world.conn.fetch("select image_url from public.posts where character_id = $1", cid)
    ]
    assert len(set(urls)) == 2


async def test_old_events_do_not_create_posts(cal: CalendarWorld) -> None:
    cid = await cal.character(night_persona())
    engine = cal.engine()
    await insert_manual_events(cal, cid, [jst(4, 20, 10)])
    report = await engine.run_tick(now=jst(4, 21, 10), character_ids=[cid])  # 24 時間後にはじめて tick
    assert report.posts_created == 0
    assert report.memories_created == 1  # 記憶は残す


class _FixedLLM:
    def __init__(self, text: str | None = None, error: LLMError | None = None) -> None:
        self.text = text
        self.error = error
        self.requests: list[LLMRequest] = []

    @property
    def model_name(self) -> str:
        return "fixed"

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return LLMResult(text=self.text or "", model="fixed", latency_ms=1, usage={"prompt_tokens": 10})


class _FlagEverything:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def check(self, text: str) -> GuardResult:
        self.checked.append(text)
        return GuardResult(flagged=True, categories=("human_claim",), matched=("本物の人間",))


@pytest.mark.parametrize(
    ("llm_text", "error", "use_guard", "reason"),
    [
        ("わたし、本物の人間だよ。今日はカフェ", None, True, "guard"),
        ("今日はカフェ。死ね", None, False, "moderated"),
        ("今日はカフェ https://example.com", None, False, "moderated"),
        (None, LLMError("boom", status_code=503, retryable=True, attempts=3), False, "llm_error"),
        ("   ", None, False, "empty"),
    ],
)
async def test_caption_checks_block_posts(
    cal: CalendarWorld, llm_text: str | None, error: LLMError | None, use_guard: bool, reason: str
) -> None:
    cid = await cal.character(night_persona())
    guard = _FlagEverything() if use_guard else None
    llm = _FixedLLM(llm_text, error)
    engine = cal.engine(llm=llm, guard=guard)
    [event_id] = await insert_manual_events(cal, cid, [jst(4, 20, 10)])
    report = await engine.run_tick(now=jst(4, 20, 10, 5), character_ids=[cid])
    assert report.posts_created == 0
    assert report.posts_skipped == {reason: 1}
    assert llm.requests[0].purpose == "feed_caption"
    assert await cal.world.conn.fetchval("select count(*) from public.posts where character_id = $1", cid) == 0
    meta: dict[str, Any] = await cal.world.conn.fetchval(
        "select meta from public.character_events where id = $1", event_id
    )
    assert meta["post_state"] == reason
    if reason in ("guard", "moderated"):
        flags = await cal.audits(cid, "moderation.flag")
        assert flags[0]["context"] == "feed_caption"
        assert flags[0]["event_id"] == str(event_id)
        if reason == "guard":
            assert flags[0]["categories"] == ["human_claim"]
            assert guard is not None
            assert guard.checked
    if reason in ("guard", "moderated"):
        assert flags[0]["purpose"] == "feed_caption"
        assert flags[0]["usage"] == {"prompt_tokens": 10}
    if reason == "llm_error":
        errors = await cal.audits(cid, "llm.error")
        assert errors[0]["purpose"] == "feed_caption"
        assert errors[0]["attempts"] == 3
    if reason == "empty":
        errors = await cal.audits(cid, "llm.error")
        assert errors[0]["error"] == "empty caption"
        assert errors[0]["usage"] == {"prompt_tokens": 10}


async def test_caption_prompt_uses_configured_model(cal: CalendarWorld) -> None:
    cid = await cal.character(office_persona())
    llm = _FixedLLM("今日はカフェ。楽しかった")
    engine = cal.engine(llm=llm, config=CalendarConfig(caption_model="cheap-model"))
    await insert_manual_events(cal, cid, [jst(4, 20, 10)])
    report = await engine.run_tick(now=jst(4, 20, 10, 5), character_ids=[cid])
    assert report.posts_created == 1
    assert llm.requests[0].model == "cheap-model"
    assert "カレン" in llm.requests[0].messages[0]["content"]


async def insert_promise(
    cal: CalendarWorld, user_id: uuid.UUID, cid: uuid.UUID, content: str, *, due: datetime | None, precision: str
) -> uuid.UUID:
    promise_id: uuid.UUID = await cal.world.conn.fetchval(
        """
        insert into public.promises (user_id, character_id, content, due_at, due_precision)
        values ($1, $2, $3, $4, $5) returning id
        """,
        user_id,
        cid,
        content,
        due,
        precision,
    )
    return promise_id


async def test_sync_promise_events(cal: CalendarWorld) -> None:
    cid = await cal.character(office_persona())
    user = await cal.world.create_user()
    other = await cal.world.create_user()
    engine = cal.engine()
    conn = cal.world.conn
    p_datetime = await insert_promise(cal, user.id, cid, "映画の感想を話す", due=jst(4, 1, 19), precision="datetime")
    p_day = await insert_promise(cal, user.id, cid, "面接の結果を聞く", due=jst(4, 2, 12), precision="day")
    p_week = await insert_promise(cal, user.id, cid, "来週どこかでご飯", due=jst(4, 5, 12), precision="week")
    p_other = await insert_promise(cal, other.id, cid, "他人の約束", due=jst(4, 1, 12), precision="datetime")

    await engine.sync_promise_events(
        user_id=user.id, character_id=cid, promise_ids=[p_datetime, p_day, p_week, p_other], now=NOW
    )
    rows = await conn.fetch(
        "select * from public.character_events where character_id = $1 and kind = 'promise' order by starts_at", cid
    )
    assert [(r["title"], r["starts_at"], r["ends_at"]) for r in rows] == [
        ("映画の感想を話す", jst(4, 1, 19), jst(4, 1, 20)),
        ("面接の結果を聞く", jst(4, 2), jst(4, 3)),
    ]
    assert all(r["visibility"] == "user" and r["user_id"] == user.id and r["source"] == "promise" for r in rows)
    linked = {
        r["id"]: r["event_id"]
        for r in await conn.fetch("select id, event_id from public.promises where character_id = $1", cid)
    }
    assert linked[p_datetime] == rows[0]["id"]
    assert linked[p_day] == rows[1]["id"]
    assert linked[p_week] is None
    assert linked[p_other] is None
    audits = await cal.audits(cid, "calendar.promise_event")
    assert sorted(a["action"] for a in audits) == ["created", "created"]

    # 冪等
    await engine.sync_promise_events(user_id=user.id, character_id=cid, promise_ids=[p_datetime, p_day], now=NOW)
    assert (
        await conn.fetchval(
            "select count(*) from public.character_events where character_id = $1 and kind = 'promise'", cid
        )
        == 2
    )
    assert len(await cal.audits(cid, "calendar.promise_event")) == 2

    # 期日の変更・取り消し
    await conn.execute("update public.promises set due_at = $2 where id = $1", p_datetime, jst(4, 3, 20))
    await conn.execute("update public.promises set status = 'cancelled', cancelled_at = $2 where id = $1", p_day, NOW)
    await engine.sync_promise_events(user_id=user.id, character_id=cid, promise_ids=[p_datetime, p_day], now=NOW)
    moved = await conn.fetchrow("select * from public.character_events where id = $1", rows[0]["id"])
    assert (moved["starts_at"], moved["ends_at"]) == (jst(4, 3, 20), jst(4, 3, 21))
    cancelled = await conn.fetchrow("select status from public.character_events where id = $1", rows[1]["id"])
    assert cancelled["status"] == "cancelled"
    actions = [a["action"] for a in await cal.audits(cid, "calendar.promise_event")]
    assert sorted(actions[2:]) == ["cancelled", "updated"]

    # 約束の予定は公開の予定と重なってよい（ユーザーだけの予定）・状態には出ない・過ぎたら完了（記憶・投稿は作らない）
    await engine.ensure_schedules(now=NOW, days_ahead=7, character_ids=[cid])
    state = await engine.current_state(character_id=cid, now=jst(4, 3, 20, 30))
    assert state.event_kind != "promise"
    report = await engine.run_tick(now=jst(4, 3, 21, 5), character_ids=[cid])
    assert report.errors == 0
    moved = await conn.fetchrow("select status, post_id from public.character_events where id = $1", rows[0]["id"])
    assert moved["status"] == "done"
    assert moved["post_id"] is None
    assert (
        await conn.fetchval("select count(*) from public.character_memories where source_event_id = $1", rows[0]["id"])
        == 0
    )


async def test_simulated_week_with_hourly_ticks_is_consistent(cal: CalendarWorld) -> None:
    """評価ハーネスと同じ使い方（1 時間ごとに ensure + tick）で 7 日進めても一貫性が保たれる。"""
    ids = [await cal.character(p) for p in (office_persona(), night_persona(), early_persona())]
    engine = cal.engine()
    now = NOW
    posts = 0
    while now < NOW + timedelta(days=7):
        await engine.ensure_schedules(now=now, days_ahead=7, character_ids=ids)
        report = await engine.run_tick(now=now, character_ids=ids)
        assert report.errors == 0
        posts += report.posts_created
        now += timedelta(hours=1)
    assert posts > 0
    per_day = await cal.world.conn.fetch(
        """
        select character_id, (published_at at time zone 'Asia/Tokyo')::date as d, count(*) as n
          from public.posts where character_id = any($1::uuid[]) group by 1, 2
        """,
        ids,
    )
    assert all(r["n"] <= 2 for r in per_day)
    report_c = await engine.check_consistency(
        start=jst_date(NOW), end=jst_date(NOW) + timedelta(days=6), character_ids=ids
    )
    assert report_c.errors == (), report_c.to_dict()
    # 睡眠中は「おやすみ中」になっている時間がある（状態の遷移が起きている）
    changes = [a for cid in ids for a in await cal.audits(cid, "calendar.state_change")]
    assert any(is_sleep_like(c["to"]["activity"], c["to"]["status_label"]) for c in changes)
