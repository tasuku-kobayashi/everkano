from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.persona import load_persona_file
from app.services.prompt import (
    SECRET_MARKER,
    PromptBuilder,
    PromptTemplateError,
    format_now,
    parse_template,
)
from app.services.types import HistoryItem, RetrievedMemory
from tests.conftest import FIXTURES_DIR, PROMPTS_DIR

PERSONA = load_persona_file(FIXTURES_DIR / "personas" / "test_persona.yaml")
NOW = datetime(2026, 9, 25, 12, 30, tzinfo=UTC)  # JST 21:30（金）


def _memory(content: str, tags: tuple[str, ...] = (), importance: float = 0.7) -> RetrievedMemory:
    return RetrievedMemory(
        id=uuid.uuid4(), content=content, importance=importance, tags=tags, similarity=0.5, created_at=NOW
    )


def _history() -> list[HistoryItem]:
    return [
        HistoryItem(id=uuid.uuid4(), sender_type="character", body="はじめまして", created_at=NOW),
        HistoryItem(id=uuid.uuid4(), sender_type="user", body="よろしく", created_at=NOW),
        HistoryItem(id=uuid.uuid4(), sender_type="character", body="よろしくね", created_at=NOW),
    ]


def test_chat_messages_structure_and_secret_marker() -> None:
    builder = PromptBuilder.load_dir(PROMPTS_DIR)
    memories = [_memory("ユーザーは猫を飼っている", tags=("secret",)), _memory("ユーザーは営業職", tags=())]
    messages = builder.chat_messages(
        PERSONA, memories=memories, history=_history(), user_message="今日なにしてた？", now=NOW
    )
    system = messages[0]
    assert system["role"] == "system"
    content = system["content"]
    assert "あなたは「テスト美咲」という人物です。" in content
    assert f"- {SECRET_MARKER}ユーザーは猫を飼っている（2026年9月25日（金）に記録）" in content
    assert "- ユーザーは営業職（2026年9月25日（金）に記録）" in content
    assert "一人称は「わたし」" in content
    # 固定の呼び方より、記憶にある呼び方の希望を優先させる（§9.2 呼び方）
    assert (
        "相手のことは基本「きみ」と呼ぶが、「あなたが覚えていること」に呼び方の希望があればそちらを優先する" in content
    )
    assert "相手の呼び方（基本）: 「きみ」" in content
    assert "2026年9月25日（金）21:30" in content
    assert "直近3件のやりとり" in content
    # 履歴は user / assistant メッセージとして続き、最後に今回の発言
    assert [m["role"] for m in messages[1:]] == ["assistant", "user", "assistant", "user"]
    assert messages[-1]["content"] == "今日なにしてた？"


def test_no_unrendered_placeholders() -> None:
    builder = PromptBuilder.load_dir(PROMPTS_DIR)
    messages = builder.chat_messages(PERSONA, memories=[], history=[], user_message="hi", now=NOW)
    assert "（まだ特にない）" in messages[0]["content"]
    assert "これが二人の最初のやりとり" in messages[0]["content"]
    for m in messages:
        for placeholder in ("{name}", "{profile}", "{memories}", "{short_term}", "{speech}"):
            assert placeholder not in m["content"]


def test_consecutive_roles_are_merged() -> None:
    builder = PromptBuilder.load_dir(PROMPTS_DIR)
    history = [HistoryItem(id=uuid.uuid4(), sender_type="user", body="a", created_at=NOW)]
    messages = builder.chat_messages(PERSONA, memories=[], history=history, user_message="b", now=NOW)
    assert messages[-1] == {"role": "user", "content": "a\nb"}


def test_extraction_summary_and_comment_templates() -> None:
    builder = PromptBuilder.load_dir(PROMPTS_DIR)
    extraction = builder.extraction_messages(
        PERSONA, history=_history(), user_message="来週大阪に行く", now=NOW, threshold=0.6
    )
    assert [m["role"] for m in extraction] == ["system", "user"]
    assert '{"memories": []}' in extraction[0]["content"]
    assert "来週大阪に行く" in extraction[1]["content"]
    # 相対日付を絶対日付に直すための現在日時と、保存される重要度の下限が入る
    assert "現在は2026年9月25日（金）21:30（日本時間）です。" in extraction[0]["content"]
    assert "importance が 0.6 未満の記憶は保存されません" in extraction[0]["content"]
    assert "0.7〜0.8:" in extraction[0]["content"]
    custom = builder.extraction_messages(PERSONA, history=[], user_message="x", now=NOW, threshold=0.65)
    assert "importance が 0.65 未満" in custom[0]["content"]
    summary = builder.summary_messages(PERSONA, transcript=_history())
    assert "テスト美咲: はじめまして" in summary[1]["content"]
    comment = builder.comment_reply_messages(PERSONA, post_caption="カフェなう", comment_body="かわいい！")
    assert "カフェなう" in comment[1]["content"]
    assert "かわいい！" in comment[1]["content"]


def test_template_validation() -> None:
    with pytest.raises(PromptTemplateError, match="必須"):
        parse_template("dm_system", "{name}")
    with pytest.raises(PromptTemplateError, match="未知"):
        parse_template("memory_summary", "{conversation} {typo_name}")
    template = parse_template("memory_summary", 'JSON: {"summary": "x"}\n=== user ===\n{conversation}')
    assert template.render({"conversation": "log"})[1]["content"] == "log"


def test_format_now_uses_jst() -> None:
    assert format_now(datetime(2026, 9, 26, 15, 0, tzinfo=UTC)) == "2026年9月27日（日）00:00"


def test_memory_date_and_gap_since_last_message() -> None:
    builder = PromptBuilder.load_dir(PROMPTS_DIR)
    week_ago = NOW - timedelta(days=7)
    memory = RetrievedMemory(
        id=uuid.uuid4(),
        content="ユーザーは「明日は早起きしないと」と話していた",
        importance=0.7,
        tags=(),
        similarity=0.5,
        created_at=week_ago,
    )
    history = [HistoryItem(id=uuid.uuid4(), sender_type="user", body="明日は早起きしないと", created_at=week_ago)]
    messages = builder.chat_messages(PERSONA, memories=[memory], history=history, user_message="ただいま", now=NOW)
    content = messages[0]["content"]
    assert "- ユーザーは「明日は早起きしないと」と話していた（2026年9月18日（金）に記録）" in content
    assert "前回のやりとり（2026年9月18日（金））から7日たっています。" in content
    assert "記録した日を基準に解釈" in content
    # 同じ日のうちは経過日数を書かない
    same_day = builder.chat_messages(PERSONA, memories=[], history=_history(), user_message="x", now=NOW)
    assert "日たっています" not in same_day[0]["content"]
