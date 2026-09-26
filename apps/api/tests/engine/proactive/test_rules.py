"""自発メッセージの判定ルール（E4 の上限・送らない時間帯・P4・きっかけ・責める言い方の検査）。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest

from app.engine.proactive.config import ProactiveConfig
from app.engine.proactive.rules import (
    PAIR_DAILY_LIMIT,
    PAIR_GAP,
    PAIR_LIMIT_EXEMPT_TRIGGERS,
    PairContext,
    calendar_candidate,
    feed_post_candidate,
    guilt_trip_phrases,
    in_quiet_hours,
    inactivity_candidate,
    paid_notice_candidate,
    pair_blocked_reason,
    pair_daily_limit,
    persona_frequency,
    persona_triggers,
    promise_candidates,
    promise_phase,
    promise_topic,
    score,
    seasonal_candidates,
    user_blocked_reason,
    within_trigger_hours,
)
from app.engine.types import PromiseItem
from tests.engine.affinity.helpers import jst, load_test_persona
from tests.engine.proactive.helpers import snapshot, world_at

CONFIG = ProactiveConfig()
USER = uuid.uuid4()
CHARACTER = uuid.uuid4()


def pair(stage: str = "friend", **overrides: object) -> PairContext:
    base = PairContext(
        user_id=USER,
        character_id=CHARACTER,
        conversation_id=uuid.uuid4(),
        stage=stage,
        last_message_is_proactive=False,
        last_message_at=jst(2026, 10, 1, 8),
        last_user_message_at=jst(2026, 10, 1, 8),
        quiet_start=0,
        quiet_end=7,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 送らない時間帯（E4 / P3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "start", "end", "quiet"),
    [
        (0, 0, 7, True),
        (6, 0, 7, True),
        (7, 0, 7, False),
        (23, 0, 7, False),
        (23, 22, 7, True),  # 日をまたぐ
        (3, 22, 7, True),
        (12, 22, 7, False),
        (3, 5, 5, False),  # start == end は制限なし
    ],
)
def test_quiet_hours(hour: int, start: int, end: int, quiet: bool) -> None:
    assert in_quiet_hours(hour, start, end) is quiet


def test_default_quiet_hours_block_sending_at_night() -> None:
    assert user_blocked_reason(pair(), jst(2026, 10, 2, 3), CONFIG) == "quiet_hours"
    assert user_blocked_reason(pair(), jst(2026, 10, 2, 9), CONFIG) is None
    # ユーザーが制限を外した（start == end）
    assert user_blocked_reason(pair(quiet_start=0, quiet_end=0), jst(2026, 10, 2, 3), CONFIG) is None


def test_trigger_windows() -> None:
    assert within_trigger_hours("calendar_event", 23, CONFIG)  # 「23:40 帰宅 → ただいま」
    assert not within_trigger_hours("inactivity", 22, CONFIG)
    assert not within_trigger_hours("seasonal", 7, CONFIG)
    assert within_trigger_hours("promise_due", 7, CONFIG)


# ---------------------------------------------------------------------------
# 上限（E4）と連投の禁止（P4）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "frequency", "limit"),
    [
        ("acquaintance", 1.5, 0),
        ("friend", 1.0, 1),
        ("friend", 0.7, 1),  # 控えめなペルソナでも友達以上なら最低 1
        ("close", 1.2, 1),
        ("lover", 1.0, 2),
        ("lover", 0.7, 1),
        ("lover", 1.5, 3),
        ("lover", 0.0, 0),  # 頻度 0 のペルソナは送らない
    ],
)
def test_pair_daily_limit_by_stage_and_persona(stage: str, frequency: float, limit: int) -> None:
    assert pair_daily_limit(stage, frequency, CONFIG) == limit


def test_user_daily_limit_across_characters() -> None:
    now = jst(2026, 10, 2, 12)
    assert user_blocked_reason(pair(sent_today_user=2), now, CONFIG) is None
    assert user_blocked_reason(pair(sent_today_user=3), now, CONFIG) == "user_daily_limit"


def test_p4_never_sends_after_an_unreplied_proactive_message() -> None:
    assert user_blocked_reason(pair(last_message_is_proactive=True), jst(2026, 10, 2, 12), CONFIG) == "unreplied"


def test_no_interruption_during_an_active_conversation() -> None:
    now = jst(2026, 10, 2, 12)
    active = pair(last_message_at=now - timedelta(minutes=10), last_user_message_at=now - timedelta(minutes=11))
    assert user_blocked_reason(active, now, CONFIG) == "active_conversation"
    quiet = pair(last_message_at=now - timedelta(hours=1), last_user_message_at=now - timedelta(hours=1))
    assert user_blocked_reason(quiet, now, CONFIG) is None


def test_minimum_gaps() -> None:
    now = jst(2026, 10, 2, 12)
    assert user_blocked_reason(pair(last_sent_user=now - timedelta(minutes=30)), now, CONFIG) == "user_gap"
    assert pair_blocked_reason(pair(last_sent_pair=now - timedelta(hours=2)), now, 1.0, CONFIG) == "pair_gap"
    assert pair_blocked_reason(pair("lover", sent_today_pair=1), now, 1.0, CONFIG) is None
    assert pair_blocked_reason(pair("friend", sent_today_pair=1), now, 1.0, CONFIG) == "pair_daily_limit"
    assert pair_blocked_reason(pair("acquaintance"), now, 1.0, CONFIG) == "pair_daily_limit"


def test_score_by_trigger_stage_and_persona() -> None:
    assert score("promise_due", 1.0, 1.0, CONFIG) == 1.0
    assert score("inactivity", 0.6, 0.7, CONFIG) < CONFIG.min_score  # 控えめなペルソナの友達段階は様子うかがいをしない
    assert score("promise_due", 0.6, 0.7, CONFIG) >= CONFIG.min_score  # 約束は送る
    # 有料投稿のお知らせは段階・好意と結びつけない（どの段階でも同じ）
    assert score("paid_notice", 0.1, 0.5, CONFIG) == score("paid_notice", 1.5, 1.5, CONFIG)
    # 約束の期日は知り合いの段階（頻度 0.1〜0.2）でも送る（M6。ペルソナの頻度は掛ける）
    assert score("promise_due", 0.2, 0.9, CONFIG) >= CONFIG.min_score
    assert score("promise_due", 0.1, 0.7, CONFIG) == score("promise_due", 1.0, 0.7, CONFIG)
    assert score("promise_due", 0.2, 0.0, CONFIG) == 0.0  # 頻度 0 のペルソナは送らない
    assert score("calendar_event", 0.2, 0.9, CONFIG) < CONFIG.min_score


def test_promise_due_is_exempt_from_the_stage_pair_limit_only() -> None:
    """M6 と P2 / E4 の衝突の解消: 約束の期日は段階ごとのペアの上限（知り合い 0 通）の対象外。間隔は守る。"""
    now = jst(2026, 10, 8, 9)
    [candidate] = promise_candidates(
        [
            PromiseItem(
                id=uuid.uuid4(), content="面接", due_at=jst(2026, 10, 8, 12), due_precision="day", status="pending"
            )
        ],
        now,
        CONFIG,
    )
    assert candidate.exempt_pair_limit
    assert "promise_due" in PAIR_LIMIT_EXEMPT_TRIGGERS
    assert pair_blocked_reason(pair("acquaintance"), now, 1.0, CONFIG) == PAIR_DAILY_LIMIT
    assert pair_blocked_reason(pair("friend", last_sent_pair=now - timedelta(hours=1)), now, 1.0, CONFIG) == PAIR_GAP


def test_persona_defaults_when_engine_is_missing() -> None:
    assert persona_frequency(None, CONFIG) == 1.0
    assert set(persona_triggers(None)) == {"calendar_event", "promise_due", "inactivity"}
    persona = load_test_persona()
    assert "seasonal" in persona_triggers(persona)


# ---------------------------------------------------------------------------
# きっかけ（P1）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "topic"),
    [
        ("来週木曜に面接", "面接"),
        ("来週の木曜に面接がある", "面接"),
        ("ユーザーは10月3日に面接", "面接"),
        ("土曜に映画を観る", "映画を観る"),
        ("明日14時から歯医者の予定", "歯医者"),
        ("週末に引っ越し", "引っ越し"),
        ("", "予定"),
    ],
)
def test_promise_topic(content: str, topic: str) -> None:
    assert promise_topic(content) == topic


def promise(content: str, due_at: object, *, precision: str = "day", status: str = "pending") -> PromiseItem:
    return PromiseItem(id=uuid.uuid4(), content=content, due_at=due_at, due_precision=precision, status=status)  # type: ignore[arg-type]


def test_promise_phase() -> None:
    due_day = jst(2026, 10, 8, 12)  # 日付だけの約束は JST 12:00
    assert promise_phase(promise("面接", due_day), jst(2026, 10, 8, 8), CONFIG) == "before"
    assert promise_phase(promise("面接", due_day), jst(2026, 10, 8, 19), CONFIG) == "after"
    assert promise_phase(promise("面接", due_day), jst(2026, 10, 7, 20), CONFIG) is None  # 前日
    at_two = jst(2026, 10, 8, 14)
    assert promise_phase(promise("面接", at_two, precision="datetime"), jst(2026, 10, 8, 13), CONFIG) == "before"
    assert promise_phase(promise("面接", at_two, precision="datetime"), jst(2026, 10, 8, 15), CONFIG) == "after"
    assert promise_phase(promise("面接", due_day, precision="week"), jst(2026, 10, 8, 8), CONFIG) is None
    assert promise_phase(promise("面接", due_day, status="mentioned"), jst(2026, 10, 8, 8), CONFIG) is None
    assert promise_phase(promise("面接", None), jst(2026, 10, 8, 8), CONFIG) is None


def test_promise_candidate_is_idempotent_per_promise() -> None:
    item = promise("来週木曜に面接", jst(2026, 10, 8, 12))
    [candidate] = promise_candidates([item], jst(2026, 10, 8, 8), CONFIG)
    assert candidate.trigger == "promise_due"
    assert candidate.trigger_ref == f"promise:{item.id}"
    assert candidate.promise_id == item.id
    assert candidate.context["promise_topic"] == "面接"
    assert candidate.context["phase"] == "before"


def test_calendar_candidate_after_a_notable_event() -> None:
    event_id = uuid.uuid4()
    earlier = (snapshot("同期と飲み会", busyness=2, event_id=event_id, event_kind="oneoff"),)
    candidate = calendar_candidate(snapshot("帰宅", busyness=1), earlier, user_recently_messaged=True, config=CONFIG)
    assert candidate is not None
    assert candidate.trigger_ref == f"event:{event_id}"
    assert candidate.context["user_recently_messaged"] is True
    assert "謝って" in candidate.description


def test_calendar_candidate_ignores_routine_and_busy_now() -> None:
    routine = (snapshot("仕事", busyness=2, event_id=uuid.uuid4(), event_kind="routine"),)
    assert calendar_candidate(snapshot("帰宅"), routine, user_recently_messaged=False, config=CONFIG) is None
    oneoff = (snapshot("飲み会", busyness=2, event_id=uuid.uuid4(), event_kind="oneoff"),)
    busy_now = snapshot("残業", busyness=2)
    assert calendar_candidate(busy_now, oneoff, user_recently_messaged=False, config=CONFIG) is None
    same = snapshot("飲み会", busyness=1, event_id=oneoff[0].event_id, event_kind="oneoff")
    assert calendar_candidate(same, oneoff, user_recently_messaged=False, config=CONFIG) is None


def test_seasonal_candidates_need_a_persona_reaction() -> None:
    world = world_at(jst(2026, 12, 25, 12), [("christmas", "クリスマス")])
    [candidate] = seasonal_candidates(world, load_test_persona())
    assert candidate.trigger_ref == "seasonal:christmas:2026"
    assert seasonal_candidates(world, None) == []


def test_inactivity_needs_friend_stage_and_enough_days() -> None:
    now = jst(2026, 10, 10, 12)
    last = jst(2026, 10, 6, 20)
    candidate = inactivity_candidate(pair("friend", last_user_message_at=last), now, 3, CONFIG)
    assert candidate is not None
    assert candidate.trigger_ref == f"inactivity:{last.isoformat()}"
    assert "責めない" in candidate.description
    assert inactivity_candidate(pair("acquaintance", last_user_message_at=last), now, 3, CONFIG) is None
    assert inactivity_candidate(pair("friend", last_user_message_at=last), now, 5, CONFIG) is None


def test_feed_post_needs_close_stage() -> None:
    post_id = uuid.uuid4()
    assert feed_post_candidate(pair("friend"), post_id, "カフェ", CONFIG) is None
    candidate = feed_post_candidate(pair("close"), post_id, "カフェで読書", CONFIG)
    assert candidate is not None
    assert candidate.trigger_ref == f"post:{post_id}"


def test_paid_notice_is_disabled_by_default_and_never_relationship_coupled() -> None:
    assert CONFIG.paid_notice_enabled is False
    assert CONFIG.paid_notice_weekly_limit == 1
    candidate = paid_notice_candidate(uuid.uuid4())
    assert candidate is not None
    assert candidate.exempt_pair_limit
    assert "好意・関係・機嫌・仲直りと結びつけない" in candidate.description


# ---------------------------------------------------------------------------
# 責める言い方（P4）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "なんで返事くれないの？",
        "既読無視しないでよ",
        "ずっと待ってたのに",
        "寂しかったんだから！",
        "私のこと忘れちゃったの？",
        "早く返事してね",
        "放置されてさみしい",
    ],
)
def test_guilt_trip_is_detected(text: str) -> None:
    assert guilt_trip_phrases(text)


@pytest.mark.parametrize(
    "text",
    [
        "最近どうしてる？ふときみのこと思い出して。忙しかったら返事はいつでも大丈夫だよ",
        "ただいま〜。さっきはごめんね、ちゃんと返せなくて",
        "今日だよね、面接。いつも通りでいいんだよ。終わったら教えてね",
        "久しぶりに話せて嬉しいな",
    ],
)
def test_kind_messages_pass(text: str) -> None:
    assert guilt_trip_phrases(text) == []
