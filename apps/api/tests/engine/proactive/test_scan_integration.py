"""ProactiveMessenger.scan の統合テスト（ローカル DB + 他モジュールの偽物）。

E4（上限・送らない時間帯・停止）、P4（連投しない・責めない）、冪等性、E2（OutputGuard で差し止め）、各きっかけ。
scan は user_ids でテストのユーザーだけに絞る（同じ DB を使う他のテストのデータに送らない）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.core.db import Pool, create_pool
from app.engine.affinity.service import AffinityEngine
from app.engine.proactive.config import ProactiveConfig
from app.engine.proactive.service import ProactiveMessenger
from app.services.audit import AuditLogger
from app.services.llm import LLMClient, LLMRequest, LLMResult, MockLLM
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository
from tests.conftest import FIXTURES_DIR, PROMPTS_DIR, ApiUser, World, make_settings
from tests.engine.affinity.helpers import jst
from tests.engine.proactive.helpers import FakeCalendar, FakeGuard, FakeMemory, event_then_home, snapshot

pytestmark = pytest.mark.integration


class FixedLLM:
    def __init__(self, text: str) -> None:
        self.text = text

    @property
    def model_name(self) -> str:
        return "fixed"

    async def complete(self, request: LLMRequest) -> LLMResult:
        return LLMResult(text=self.text, model="fixed", latency_ms=1, usage={"completion_tokens": 5})


@dataclass
class Harness:
    world: World
    pool: Pool
    calendar: FakeCalendar = field(default_factory=FakeCalendar)
    memory: FakeMemory = field(default_factory=FakeMemory)
    guard: FakeGuard = field(default_factory=FakeGuard)
    users: list[uuid.UUID] = field(default_factory=list)

    def messenger(self, *, llm: LLMClient | None = None, config: ProactiveConfig | None = None) -> ProactiveMessenger:
        personas = PersonaRepository.load_dir(FIXTURES_DIR / "personas")
        affinity = AffinityEngine(
            pool=self.pool, llm=MockLLM(), audit=AuditLogger(self.pool), personas=personas, prompts_dir=PROMPTS_DIR
        )
        return ProactiveMessenger(
            pool=self.pool,
            llm=llm or MockLLM(),
            audit=AuditLogger(self.pool),
            personas=personas,
            moderator=Moderator(),
            guard=self.guard,
            prompts_dir=PROMPTS_DIR,
            calendar=self.calendar,
            memory=self.memory,
            affinity=affinity,
            config=config,
        )

    async def user(self) -> ApiUser:
        user = await self.world.create_user()
        self.users.append(user.id)
        return user

    async def conversation(
        self,
        user_id: uuid.UUID,
        character_id: uuid.UUID,
        *,
        last_user_at: datetime,
        stage: str | None = "friend",
        text: str = "今日もおつかれさま",
    ) -> uuid.UUID:
        conn = self.world.conn
        conversation_id = await conn.fetchval(
            """
            insert into public.conversations (user_id, character_id, created_at, last_message_at, user_last_read_at)
            values ($1, $2, $3, $3, $3) returning id
            """,
            user_id,
            character_id,
            last_user_at - timedelta(days=1),
        )
        await conn.execute(
            """
            insert into public.messages (conversation_id, sender_type, body, created_at)
            values ($1, 'character', 'はじめまして', $2), ($1, 'user', $3, $4), ($1, 'character', 'うんうん', $5)
            """,
            conversation_id,
            last_user_at - timedelta(days=1),
            text,
            last_user_at,
            last_user_at + timedelta(seconds=5),
        )
        if stage is not None:
            await conn.execute(
                "insert into public.affinity_states (user_id, character_id, stage) values ($1, $2, $3)",
                user_id,
                character_id,
                stage,
            )
        return conversation_id

    async def scan(self, messenger: ProactiveMessenger, now: datetime) -> int:
        return await messenger.scan(now=now, user_ids=self.users)

    async def proactive_messages(self, user_id: uuid.UUID) -> list[Any]:
        return await self.world.conn.fetch(
            """
            select m.body, m.created_at, m.is_proactive, m.sender_type, p.trigger, p.trigger_ref, p.sent_at,
                   p.message_id, p.replied_at, p.meta, p.character_id
              from public.proactive_messages p
              left join public.messages m on m.id = p.message_id
             where p.user_id = $1
             order by p.sent_at, p.id
            """,
            user_id,
        )

    async def audits(self, user_id: uuid.UUID, event_type: str) -> list[dict[str, Any]]:
        rows = await self.world.conn.fetch(
            "select payload from public.audit_logs where user_id = $1 and event_type = $2 order by created_at, id",
            user_id,
            event_type,
        )
        return [r["payload"] for r in rows]


@pytest.fixture
async def harness(world: World) -> AsyncIterator[Harness]:
    pool = await create_pool(make_settings())
    try:
        yield Harness(world=world, pool=pool)
    finally:
        await pool.close()


async def test_promise_due_morning_message_then_no_double_send(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    conversation = await harness.conversation(
        user.id, character, last_user_at=jst(2026, 10, 5, 21), text="来週の木曜、面接なんだよね。緊張する"
    )
    promise = harness.memory.add_promise(user.id, character, "来週木曜に面接", jst(2026, 10, 8, 12))
    messenger = harness.messenger()
    morning = jst(2026, 10, 8, 8)

    assert await harness.scan(messenger, morning) == 1
    [sent] = await harness.proactive_messages(user.id)
    assert sent["body"] == "今日だよね、面接。いつも通りでいいんだよ。終わったら教えてね"
    assert sent["is_proactive"] is True
    assert sent["sender_type"] == "character"
    assert sent["created_at"] == morning  # アプリの時計の時刻
    assert sent["sent_at"] == morning
    assert sent["trigger"] == "promise_due"
    assert sent["trigger_ref"] == f"promise:{promise.id}"
    assert harness.memory.mentioned == [promise.id]
    [audit] = await harness.audits(user.id, "proactive.send")
    assert audit["trigger"] == "promise_due"
    assert audit["purpose"] == "proactive_message"
    assert audit["usage"]
    assert audit["conversation_id"] == str(conversation)

    # P4: 返信がないあいだは送らない
    assert await harness.scan(messenger, morning + timedelta(hours=2)) == 0
    # ユーザーが返信 → replied_at
    reply_at = morning + timedelta(hours=3)
    await harness.world.conn.execute(
        "insert into public.messages (conversation_id, sender_type, body, created_at)"
        " values ($1, 'user', 'ありがとう', $2)",
        conversation,
        reply_at,
    )
    await messenger.on_user_message(user_id=user.id, character_id=character, conversation_id=conversation, now=reply_at)
    [sent] = await harness.proactive_messages(user.id)
    assert sent["replied_at"] == reply_at
    # 同じ約束では二度送らない（ペアの 1 日の上限も使い切っている）
    assert await harness.scan(messenger, morning + timedelta(hours=5)) == 0
    assert len(await harness.proactive_messages(user.id)) == 1


async def test_quiet_hours_and_user_override(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 5, 21))
    harness.memory.add_promise(user.id, character, "面接", jst(2026, 10, 8, 12))
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 10, 8, 3)) == 0  # 既定の 0〜7 時
    await harness.world.conn.execute(
        "insert into public.proactive_settings (user_id, character_id, quiet_start, quiet_end) values ($1, null, 0, 9)",
        user.id,
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 8)) == 0  # ユーザーが 9 時までに広げた
    await harness.world.conn.execute(
        "update public.proactive_settings set quiet_end = 0 where user_id = $1 and character_id is null", user.id
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 8, 30)) == 1  # 制限なし（start == end）


async def test_global_and_character_opt_out(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 5, 21))
    harness.memory.add_promise(user.id, character, "面接", jst(2026, 10, 8, 12))
    messenger = harness.messenger()
    conn = harness.world.conn
    await conn.execute(
        "insert into public.proactive_settings (user_id, character_id, enabled) values ($1, null, false)", user.id
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 8)) == 0
    await conn.execute("update public.proactive_settings set enabled = true where user_id = $1", user.id)
    await conn.execute(
        "insert into public.proactive_settings (user_id, character_id, enabled) values ($1, $2, false)",
        user.id,
        character,
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 8, 10)) == 0
    await conn.execute(
        "update public.proactive_settings set enabled = true where user_id = $1 and character_id = $2",
        user.id,
        character,
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 8, 20)) == 1


async def test_acquaintance_gets_only_the_promise_and_dormant_users_get_nothing(harness: Harness) -> None:
    """M6 と P2 / E4 の衝突の解消: 知り合いの段階（ペアの上限 0 通）でも、期日の約束は当日に 1 回だけ話題にする。

    ほかのきっかけ（予定の終了など）は知り合いの段階では送らない。30 日以上話していないユーザーには何も送らない。
    """
    new_user = await harness.user()
    dormant = await harness.user()
    character = harness.world.character_id
    await harness.conversation(new_user.id, character, last_user_at=jst(2026, 10, 5, 21), stage=None)
    await harness.conversation(dormant.id, character, last_user_at=jst(2026, 8, 1, 21), stage="lover")
    promise = harness.memory.add_promise(new_user.id, character, "面接", jst(2026, 10, 8, 12))
    harness.memory.add_promise(dormant.id, character, "面接", jst(2026, 10, 8, 12))
    # 夕方には予定の終了（calendar_event）のきっかけもある
    harness.calendar.state_fn = event_then_home(uuid.uuid4(), jst(2026, 10, 8, 17, 50))
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 10, 8, 8)) == 1
    [row] = await harness.proactive_messages(new_user.id)
    assert row["trigger"] == "promise_due"
    assert row["trigger_ref"] == f"promise:{promise.id}"
    assert harness.memory.mentioned == [promise.id]
    # 返信が無いあいだは送らない（P4）。返信があっても、知り合いの段階では約束以外（予定の終了）は送らない
    assert await harness.scan(messenger, jst(2026, 10, 8, 12)) == 0
    conversation = await harness.world.conn.fetchval(
        "select id from public.conversations where user_id = $1", new_user.id
    )
    reply_at = jst(2026, 10, 8, 12, 30)
    await harness.world.conn.execute(
        "insert into public.messages (conversation_id, sender_type, body, created_at)"
        " values ($1, 'user', 'ありがとう', $2)",
        conversation,
        reply_at,
    )
    await messenger.on_user_message(
        user_id=new_user.id, character_id=character, conversation_id=conversation, now=reply_at
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 18)) == 0
    assert len(await harness.proactive_messages(new_user.id)) == 1
    assert await harness.proactive_messages(dormant.id) == []  # 30 日以上話していない（しつこくしない）


async def test_promise_is_exempt_from_the_stage_limit_but_not_from_user_limits(harness: Harness) -> None:
    """約束の期日は段階ごとのペアの上限の対象外。1 ユーザーの 1 日の上限・送らない時間帯・停止設定・間隔は守る。"""
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 5, 21), stage="acquaintance")
    harness.memory.add_promise(user.id, character, "面接", jst(2026, 10, 8, 12))
    limited = harness.messenger(config=ProactiveConfig(per_user_daily_limit=0))
    assert await harness.scan(limited, jst(2026, 10, 8, 9)) == 0
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 10, 8, 3)) == 0  # 送らない時間帯（0〜7 時）
    await harness.world.conn.execute(
        "insert into public.proactive_settings (user_id, character_id, enabled) values ($1, $2, false)",
        user.id,
        character,
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 9)) == 0  # キャラごとの停止
    await harness.world.conn.execute(
        "update public.proactive_settings set enabled = true where user_id = $1 and character_id = $2",
        user.id,
        character,
    )
    assert await harness.scan(messenger, jst(2026, 10, 8, 9, 10)) == 1


async def test_per_user_daily_limit_across_characters(harness: Harness) -> None:
    user = await harness.user()
    characters = [harness.world.character_id] + [await harness.world.create_character() for _ in range(3)]
    for character in characters:
        await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 5, 21))
        harness.memory.add_promise(user.id, character, "面接", jst(2026, 10, 8, 12))
    messenger = harness.messenger(config=ProactiveConfig(user_min_gap=timedelta(0)))
    sent = 0
    for minute in range(0, 60, 10):
        sent += await harness.scan(messenger, jst(2026, 10, 8, 8, minute))
    assert sent == 3  # 1 日に全キャラ合計 3 通まで
    rows = await harness.proactive_messages(user.id)
    assert len({r["character_id"] for r in rows}) == 3
    # 1 回の走査では同じユーザーに 1 通だけ
    assert len({r["sent_at"] for r in rows}) == 3
    # 翌日（JST）はまた送れる（残りのキャラの約束は前日で期限切れのため、別のきっかけが要る）
    assert await harness.scan(messenger, jst(2026, 10, 9, 8)) == 0


async def test_output_guard_flag_drops_the_message_for_good(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 5, 21))
    harness.memory.add_promise(user.id, character, "面接", jst(2026, 10, 8, 12))
    harness.guard.words = ("面接",)
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 10, 8, 8)) == 0
    [row] = await harness.proactive_messages(user.id)
    assert row["message_id"] is None
    assert "commerce_coupling" in row["meta"]["dropped"]
    [dropped] = await harness.audits(user.id, "proactive.dropped")
    assert dropped["categories"] == ["commerce_coupling"]
    [flag] = await harness.audits(user.id, "moderation.flag")
    assert flag["stage"] == "proactive"
    count = await harness.world.conn.fetchval(
        "select count(*) from public.messages m join public.conversations c on c.id = m.conversation_id"
        " where c.user_id = $1 and m.is_proactive",
        user.id,
    )
    assert count == 0
    # 差し止めたきっかけで生成し直さない
    checked = len(harness.guard.checked)
    assert await harness.scan(messenger, jst(2026, 10, 8, 9)) == 0
    assert len(harness.guard.checked) == checked


async def test_guilt_tripping_text_is_never_sent(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 1, 21))
    messenger = harness.messenger(llm=FixedLLM("なんで返事くれないの？ずっと待ってたのに"))
    assert await harness.scan(messenger, jst(2026, 10, 8, 12)) == 0
    [dropped] = await harness.audits(user.id, "proactive.dropped")
    assert "guilt_trip" in dropped["categories"]


async def test_calendar_event_end_sends_tadaima(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    now = jst(2026, 10, 9, 23, 40)
    event_id = uuid.uuid4()
    harness.calendar.state_fn = event_then_home(event_id, now - timedelta(minutes=20))
    # 飲み会の最中にユーザーからメッセージが来ていた
    await harness.conversation(user.id, character, last_user_at=now - timedelta(hours=1), text="今なにしてる？")
    messenger = harness.messenger()
    assert await harness.scan(messenger, now) == 1
    [row] = await harness.proactive_messages(user.id)
    assert row["body"] == "ただいま〜。さっきはごめんね、ちゃんと返せなくて"
    assert row["trigger"] == "calendar_event"
    assert row["trigger_ref"] == f"event:{event_id}"


async def test_character_busy_or_asleep_sends_nothing(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 5, 21))
    harness.memory.add_promise(user.id, character, "面接", jst(2026, 10, 8, 12))
    harness.calendar.state_fn = lambda _cid, _now: snapshot("会議", busyness=2)
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 10, 8, 10)) == 0


async def test_inactivity_message_is_gentle(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    await harness.conversation(user.id, character, last_user_at=jst(2026, 10, 1, 21))
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 10, 6, 12)) == 1
    [row] = await harness.proactive_messages(user.id)
    assert row["trigger"] == "inactivity"
    assert "いつでも大丈夫" in row["body"]


async def test_seasonal_greeting_once_per_user_across_characters(harness: Harness) -> None:
    user = await harness.user()
    other = await harness.world.create_character()
    for character in (harness.world.character_id, other):
        await harness.conversation(user.id, character, last_user_at=jst(2026, 12, 24, 21))
    harness.calendar.seasonal = [("christmas", "クリスマス")]
    messenger = harness.messenger()
    assert await harness.scan(messenger, jst(2026, 12, 25, 10)) == 1
    assert await harness.scan(messenger, jst(2026, 12, 25, 13)) == 0
    rows = await harness.proactive_messages(user.id)
    assert [r["trigger_ref"] for r in rows] == ["seasonal:christmas:2026"]
    assert rows[0]["body"].startswith("メリークリスマス")


async def test_feed_post_only_for_close_stage(harness: Harness) -> None:
    user = await harness.user()
    friend_user = await harness.user()
    character = harness.world.character_id
    now = jst(2026, 10, 9, 15)
    await harness.world.create_post(
        character_id=character, caption="カフェで読書してた", published_at=now - timedelta(hours=1)
    )
    await harness.conversation(user.id, character, last_user_at=now - timedelta(hours=20), stage="close")
    await harness.conversation(friend_user.id, character, last_user_at=now - timedelta(hours=20), stage="friend")
    messenger = harness.messenger()
    assert await harness.scan(messenger, now) == 1
    [row] = await harness.proactive_messages(user.id)
    assert row["trigger"] == "feed_post"
    assert "写真" in row["body"]
    assert await harness.proactive_messages(friend_user.id) == []


async def paid_post(harness: Harness, character: uuid.UUID, published_at: datetime) -> uuid.UUID:
    post_id = uuid.uuid4()
    await harness.world.conn.execute(
        """
        insert into public.posts (id, character_id, image_url, caption, is_paid, price_tokens, published_at)
        values ($1, $2, 'https://placehold.co/1080x1080', '限定の写真', true, 100, $3)
        """,
        post_id,
        character,
        published_at,
    )
    harness.world.post_ids.append(post_id)
    return post_id


async def test_paid_notice_is_off_by_default_and_capped_weekly_when_enabled(harness: Harness) -> None:
    user = await harness.user()
    character = harness.world.character_id
    now = jst(2026, 10, 9, 15)
    await harness.conversation(user.id, character, last_user_at=now - timedelta(hours=20), stage=None)
    await paid_post(harness, character, now - timedelta(hours=1))
    assert await harness.scan(harness.messenger(), now) == 0  # 既定で無効
    enabled = harness.messenger(config=ProactiveConfig(paid_notice_enabled=True))
    assert await harness.scan(enabled, now + timedelta(minutes=10)) == 1
    [row] = await harness.proactive_messages(user.id)
    assert row["trigger"] == "paid_notice"
    assert row["body"].startswith("お知らせ")
    # ユーザーが返信したあと、別の有料投稿があっても 1 週間に 1 通まで
    conversation = await harness.world.conn.fetchval("select id from public.conversations where user_id = $1", user.id)
    later = now + timedelta(days=2)
    await harness.world.conn.execute(
        "insert into public.messages (conversation_id, sender_type, body, created_at) values ($1, 'user', 'ok', $2)",
        conversation,
        later - timedelta(hours=1),
    )
    await paid_post(harness, character, later - timedelta(hours=2))
    assert await harness.scan(enabled, later) == 0
