"""memory_analysis の出力の検証（JSON・スキーマ）とプロンプトの組み立て。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest

from app.engine.memory.analysis import (
    AnalysisInput,
    AnalysisOutput,
    AnalysisOutputError,
    AnalysisPrompt,
    MemoryRef,
    PromiseRef,
    mock_context,
    parse_analysis,
)
from app.engine.memory.summary import parse_summary
from app.engine.types import TurnRecord
from app.services.persona import load_persona_file
from app.services.prompt import PromptTemplateError, parse_template
from tests.conftest import FIXTURES_DIR, PROMPTS_DIR

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
PERSONA = load_persona_file(FIXTURES_DIR / "personas" / "test_persona.yaml")


def test_parse_full_output() -> None:
    text = """```json
    {"memories": [
      {"op": "add", "kind": "fact", "content": "ユーザーは銀行で働いている", "importance": 0.8, "turn": 1},
      {"op": "update", "target": "m2", "content": "ユーザーは三毛猫のミケを飼っている", "importance": "0.7"},
      {"op": "supersede", "target": "m1", "kind": "fact", "content": "ユーザーは転職した", "importance": 1.4},
      {"op": "noop", "target": "m3", "extra": "ignored"}
    ],
    "promises": [
      {"content": "面接", "memory": "ユーザーは10月1日に面接", "due_date": "2026-10-01", "due_time": "15:00",
       "due_precision": "datetime", "turn": 1},
      {"content": "一緒に映画", "due_date": null, "due_precision": "day"},
      {"content": "試験", "due_date": "2026-10-05", "due_time": null, "due_precision": "datetime"}
    ],
    "promise_updates": [{"target": "p1", "status": "done"}],
    "character_statements": [{"content": "昨日カフェに行った", "occurred_date": "2026-09-25", "turn": 1}]}
    ```"""
    output = parse_analysis(text)
    assert [op.op for op in output.memories] == ["add", "update", "supersede", "noop"]
    assert output.memories[1].importance == 0.7
    assert output.memories[2].importance == 1.0  # 範囲外は丸める
    first, undated, no_time = output.promises
    assert (first.due_date, first.due_time, first.due_precision) == (date(2026, 10, 1), time(15, 0), "datetime")
    assert (undated.due_date, undated.due_precision) == (None, "unknown")  # 期日なし → unknown
    assert no_time.due_precision == "day"  # 時刻なしの datetime → day
    assert output.promise_updates[0].status == "done"
    assert output.character_statements[0].occurred_date == date(2026, 9, 25)


def test_empty_and_null_lists_are_valid() -> None:
    assert parse_analysis('{"memories": []}') == AnalysisOutput()
    assert parse_analysis('{"memories": null, "promises": null}') == AnalysisOutput()
    assert parse_analysis('前置き {"memories": []} 後書き') == AnalysisOutput()


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("not json at all", "JSON"),
        ("[1, 2]", "トップレベル"),
        ('{"memories": [{"op": "replace", "content": "x"}]}', "memories.0.op"),
        ('{"memories": [{"op": "add", "content": "x"}]}', "kind"),
        ('{"memories": [{"op": "update", "content": "x"}]}', "target"),
        ('{"memories": [{"op": "supersede", "target": "m1"}]}', "content"),
        ('{"memories": [{"op": "add", "kind": "summary", "content": "x"}]}', "memories.0.kind"),
        ('{"memories": [{"op": "update", "target": "memory-1", "content": "x"}]}', "target"),
        ('{"promises": [{"content": "x", "due_date": "来週"}]}', "promises.0.due_date"),
        ('{"promise_updates": [{"target": "p1", "status": "pending"}]}', "promise_updates.0.status"),
        ('{"character_statements": [{"content": ""}]}', "character_statements.0.content"),
    ],
)
def test_invalid_outputs_explain_the_error(text: str, fragment: str) -> None:
    with pytest.raises(AnalysisOutputError) as exc:
        parse_analysis(text)
    assert fragment in str(exc.value)


def test_content_is_one_line_and_truncated() -> None:
    output = parse_analysis(
        '{"memories": [{"op": "add", "kind": "fact", "content": "ユーザーは\\n改行を\\u2028含む' + "あ" * 400 + '"}]}'
    )
    content = output.memories[0].content
    assert content is not None
    assert "\n" not in content
    assert "\u2028" not in content
    assert len(content) == 300


def test_truncated_limits_counts() -> None:
    ops = ",".join('{"op": "noop"}' for _ in range(20))
    output = parse_analysis('{"memories": [' + ops + "]}").truncated(max_ops=12, max_promises=5, max_statements=5)
    assert len(output.memories) == 12


def test_parse_summary_never_stores_json_text_as_summary() -> None:
    assert parse_summary('{"summary": "ユーザーは営業職。"}') == "ユーザーは営業職。"
    assert parse_summary('{"summary": ""}') == ""
    assert parse_summary('{"result": "x"}') == ""
    assert parse_summary("ユーザーは猫を飼っている。") == "ユーザーは猫を飼っている。"


def _input() -> AnalysisInput:
    turn = TurnRecord(
        conversation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        character_id=uuid.uuid4(),
        user_message_id=uuid.uuid4(),
        character_message_id=uuid.uuid4(),
        user_text="来週の木曜、面接なんだよね。\n緊張する",
        reply_text="応援してるよ",
        occurred_at=NOW,
    )
    return AnalysisInput(
        persona=PERSONA,
        now=NOW,
        turns=[turn],
        memories=[
            MemoryRef(
                ref="m1",
                id=uuid.uuid4(),
                kind="fact",
                content="ユーザーは広告代理店で働いている",
                importance=0.8,
                is_user_edited=True,
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        ],
        promises=[
            PromiseRef(
                ref="p1",
                id=uuid.uuid4(),
                content="歯医者",
                due_at=datetime(2026, 9, 28, 3, tzinfo=UTC),
                due_precision="day",
                status="pending",
            )
        ],
        threshold=0.6,
    )


def test_prompt_renders_date_reference_refs_and_turns() -> None:
    prompt = AnalysisPrompt.load(PROMPTS_DIR)
    system, user = prompt.render(_input())
    assert system["role"] == "system"
    # 分単位の「現在」は user 側（system は日付の早見表まで同じ = 同じ日のうちはプレフィックスキャッシュに当たる）
    assert "2026年9月26日（土）12:00" not in system["content"]
    assert "# 現在（日本時間）\n2026年9月26日（土）12:00" in user["content"]
    assert "来週: 2026-09-28(月)〜2026-10-04(日)" in system["content"]
    assert "0.6" in system["content"]
    assert "ユーザーの仕事・生活リズム" in system["content"]  # memory_focus
    assert "m1: [fact] ユーザーは広告代理店で働いている（ユーザー編集）（記録 2026-09-01）" in user["content"]
    assert "p1: 歯医者 / 期日 2026-09-28（day） / pending" in user["content"]
    assert (
        "[1] 2026年9月26日（土）12:00\nユーザー: 来週の木曜、面接なんだよね。 緊張する\nテスト美咲: 応援してるよ"
        in (user["content"])
    )


def test_prompt_template_placeholders_are_validated() -> None:
    with pytest.raises(PromptTemplateError):
        AnalysisPrompt(parse_template("memory_analysis", "{name}\n=== user ===\n{turns}"))
    with pytest.raises(PromptTemplateError):
        AnalysisPrompt(
            parse_template("memory_analysis", "{existing_memories}{pending_promises}{turns}{unknown_placeholder}")
        )


def test_mock_context_is_structured() -> None:
    context = mock_context(_input())
    assert context["persona"]["first_person"] == "わたし"
    assert context["memories"][0] == {
        "ref": "m1",
        "kind": "fact",
        "content": "ユーザーは広告代理店で働いている",
        "user_edited": True,
    }
    assert context["promises"][0]["due_date"] == "2026-09-28"
    assert context["turns"][0]["index"] == 1
