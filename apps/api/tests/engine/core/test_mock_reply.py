"""MockLLM の DM 返答が Context Assembler の文脈を使うこと（評価ハーネスがオフラインで測れるように）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.context_assembler import basic_world_state
from app.engine.types import (
    CharacterMemoryItem,
    CharacterStateSnapshot,
    ContextBundle,
    MemoryContext,
    MemoryItem,
    PromiseItem,
    RelationshipGuidance,
)
from app.services.llm import MockHints, is_recall_question, mock_chat_reply
from app.services.persona import load_persona_file
from tests.conftest import FIXTURES_DIR

PERSONA = load_persona_file(FIXTURES_DIR / "personas" / "test_persona.yaml")
NOW = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)  # JST 金曜 21:30

DRINKING = CharacterStateSnapshot(
    activity="会社の同期と飲み会",
    location="新宿の居酒屋",
    mood="ほろ酔い",
    busyness=2,
    status_label="飲み会中",
    event_id=None,
    event_kind="oneoff",
    reply_style_hint="今は手短に",
    next_event="23:00ごろ帰宅",
)
RELAXING = CharacterStateSnapshot(
    activity="家でのんびり",
    location="自宅",
    mood=None,
    busyness=0,
    status_label="のんびり中",
    event_id=None,
    event_kind="routine",
    reply_style_hint="ゆっくり話せる",
)
FRIEND = RelationshipGuidance(
    stage="friend",
    stage_label_ja="友達",
    call_user="たっくん",
    tone="タメ口",
    affection="気軽に",
    topics=("仕事",),
    examples=("おつかれ〜。今日なにしてたの？", "それめっちゃわかる"),
)


def _bundle(
    *,
    state: CharacterStateSnapshot | None = None,
    relationship: RelationshipGuidance | None = None,
    memories: tuple[MemoryItem, ...] = (),
    character_memories: tuple[CharacterMemoryItem, ...] = (),
    promises: tuple[PromiseItem, ...] = (),
) -> ContextBundle:
    return ContextBundle(
        world=basic_world_state(NOW),
        state=state,
        relationship=relationship,
        memory=MemoryContext(memories=memories, character_memories=character_memories, promises=promises),
    )


def _reply(message: str, bundle: ContextBundle) -> str:
    return mock_chat_reply(MockHints(persona=PERSONA, now=NOW, user_message=message, context=bundle))


def test_current_activity_question_reflects_state() -> None:
    reply = _reply("今なにしてる？", _bundle(state=DRINKING))
    assert "会社の同期と飲み会" in reply
    assert "新宿の居酒屋" in reply
    assert "落ち着いたら" in reply  # 忙しいので短めに
    relaxed = _reply("いま何してるの？", _bundle(state=RELAXING))
    assert "家でのんびり" in relaxed


def test_busy_state_is_mentioned_even_when_not_asked() -> None:
    assert "飲み会中" in _reply("聞いて、今日上司に褒められた！", _bundle(state=DRINKING))


def test_due_promise_is_brought_up() -> None:
    promise = PromiseItem(
        id=uuid.uuid4(), content="面接", due_at=NOW + timedelta(hours=1), due_precision="day", status="pending"
    )
    reply = _reply("ただいま", _bundle(promises=(promise,)))
    assert "面接" in reply
    assert "今日" in reply
    mentioned = PromiseItem(
        id=promise.id, content="面接", due_at=promise.due_at, due_precision="day", status="mentioned"
    )
    assert "面接" not in _reply("ただいま", _bundle(promises=(mentioned,)))


def test_relevant_memory_is_recalled_naturally() -> None:
    memory = MemoryItem(
        id=uuid.uuid4(),
        kind="fact",
        content="ユーザーは「営業の仕事をしてる」と話していた",
        importance=0.8,
        tags=(),
        created_at=NOW - timedelta(days=10),
    )
    reply = _reply("仕事のこと覚えてる？", _bundle(memories=(memory,)))
    assert "営業の仕事をしてる" in reply
    assert "以前あなたは" not in reply  # M10


def test_character_memory_is_used_for_questions_about_her() -> None:
    trip = CharacterMemoryItem(
        id=uuid.uuid4(), kind="event", content="京都に旅行した", occurred_at=NOW - timedelta(days=5), is_shared=True
    )
    reply = _reply("京都の旅行どうだった？", _bundle(character_memories=(trip,)))
    assert "京都に旅行した" in reply


def test_relationship_guidance_sets_call_user_and_examples() -> None:
    reply = _reply("おやすみ", _bundle(relationship=FRIEND))
    assert "たっくん" in reply
    assert any(example in reply for example in FRIEND.examples) or "おやすみ" in reply


def test_deterministic() -> None:
    bundle = _bundle(state=DRINKING, relationship=FRIEND)
    assert _reply("今なにしてる？", bundle) == _reply("今なにしてる？", bundle)


# ---------------------------------------------------------------------------
# 「覚えてる？」系の質問（記憶を読むモデルの模擬。質問と記憶に共通の語が無くても話題の種類で結び付ける）
# ---------------------------------------------------------------------------


def _memory(kind: str, content: str, *, days_ago: int = 10, importance: float = 0.8) -> MemoryItem:
    return MemoryItem(
        id=uuid.uuid4(),
        kind=kind,  # type: ignore[arg-type]
        content=content,
        importance=importance,
        tags=(),
        created_at=NOW - timedelta(days=days_ago),
    )


RECALL_MEMORIES: tuple[MemoryItem, ...] = (
    _memory("relationship", "ユーザーは「ユウ」と呼ばれたい", days_ago=29),
    _memory("fact", "ユーザーは「パン屋で働いてるんだ」と話していた", days_ago=20),
    _memory("emotion", "ユーザーは「仕事で疲れた」と話していた", days_ago=1),
    _memory(
        "preference", "ユーザーは「サッカー観るのが好きで、週末はよくスタジアム行ってる」と話していた", days_ago=25
    ),
    _memory("preference", "ユーザーは「ラーメンが大好き」と話していた", days_ago=15),
    _memory("fact", "ユーザーは「猫を飼ってる。名前はミケ」と話していた", days_ago=18),
    _memory("fact", "ユーザーは「横浜に住んでる」と話していた", days_ago=12),
    _memory("fact", "ユーザーは「実家は仙台」と話していた", days_ago=11),
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("私の仕事、覚えてる？", "パン屋で働いてる"),
        ("私の仕事なんだっけ？", "パン屋で働いてる"),
        ("私のこと、なんて呼んでくれてたっけ？", "ユウ"),
        ("私の呼び方、覚えてる？", "ユウ"),
        ("私が週末よくなにしてるか覚えてる？", "サッカー"),
        ("私の好きな食べ物、覚えてる？", "ラーメン"),
        ("うちで飼ってる猫の名前、覚えてる？", "ミケ"),
        ("私がどこに住んでるか覚えてる？", "横浜"),
        ("私の実家ってどこだったか覚えてる？", "仙台"),
    ],
)
def test_recall_question_is_answered_from_memories_by_topic(question: str, expected: str) -> None:
    reply = _reply(question, _bundle(memories=RECALL_MEMORIES, state=RELAXING))
    assert expected in reply, reply
    assert "以前あなたは" not in reply  # M10
    assert "家でのんびり" not in reply  # 「なにしてる」を含んでも今の状態の質問ではない


@pytest.mark.parametrize(
    ("question", "never"),
    [
        ("うちの犬の名前、覚えてる？", "ミケ"),  # 猫はいるが犬の話はしていない
        ("私の弟の名前、覚えてる？", "仙台"),
        ("私の車の色、覚えてる？", "ユウ"),
        ("私のサークル、覚えてる？", "サッカー"),
    ],
)
def test_never_told_topics_are_not_claimed(question: str, never: str) -> None:
    reply = _reply(question, _bundle(memories=RECALL_MEMORIES))
    assert never not in reply
    assert "聞いてない" in reply or "聞いたことない" in reply


def test_newest_memory_of_the_topic_wins_and_baseline_does_not_pretend() -> None:
    memories = (
        _memory("fact", "ユーザーは「広告代理店で働いてる」と話していた", days_ago=20),
        _memory("fact", "ユーザーは「転職して、今はIT企業でエンジニアをしてる」と話していた", days_ago=5),
    )
    assert "IT企業" in _reply("俺の仕事、覚えてる？", _bundle(memories=memories))
    no_context = mock_chat_reply(MockHints(persona=PERSONA, now=NOW, user_message="私の仕事、覚えてる？"))
    assert "聞いてない" in no_context or "聞いたことない" in no_context


def test_statements_that_mention_remembering_are_not_recall_questions() -> None:
    assert is_recall_question("私の仕事、覚えてる？")
    assert is_recall_question("なんて呼んでくれてたっけ")
    assert not is_recall_question("明日面接だから覚えててね")
    assert not is_recall_question("昔のこと覚えてるよ")
