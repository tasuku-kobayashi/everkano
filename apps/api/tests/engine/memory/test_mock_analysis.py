"""MockLLM の memory_analysis（決定的なルールベース）の単体テスト。評価ハーネスが頼る振る舞いを固定する。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import app.engine.memory  # noqa: F401  - import 時に MockLLM へ用途を登録する
from app.engine.memory.analysis import parse_analysis
from app.engine.memory.mock import mock_memory_analysis, mock_memory_summary
from app.services.llm import LLMRequest, MockLLM, registered_mock_purposes

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)  # 2026-09-26（土）12:00 JST


def run(
    *turns: tuple[str, str],
    memories: list[dict[str, Any]] | None = None,
    promises: list[dict[str, Any]] | None = None,
    now: datetime = NOW,
) -> dict[str, Any]:
    context = {
        "now": now.isoformat(),
        "threshold": 0.6,
        "persona": {"name": "テスト美咲", "first_person": "わたし", "second_person": "きみ"},
        "turns": [{"index": i, "user": u, "reply": r, "at": now.isoformat()} for i, (u, r) in enumerate(turns, 1)],
        "memories": memories or [],
        "promises": promises or [],
    }
    text = mock_memory_analysis(
        LLMRequest(purpose="memory_analysis", messages=[], temperature=0, max_tokens=1, mock_context=context)
    )
    parse_analysis(text)  # live と同じスキーマに合う
    result: dict[str, Any] = json.loads(text)
    return result


def adds(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(op["kind"], op["content"]) for op in result["memories"] if op["op"] == "add"]


def test_registered_on_import() -> None:
    assert {"memory_analysis", "memory_summary"} <= registered_mock_purposes()


def test_spec_example() -> None:
    result = run(("来週の木曜、面接なんだよね。緊張する", "そうなんだ"))
    [promise] = result["promises"]
    assert promise["content"] == "面接"
    assert promise["due_date"] == "2026-10-01"
    assert promise["due_precision"] == "day"
    assert promise["memory"] == "ユーザーは10月1日（木）に「面接」の予定がある"
    assert adds(result) == [("emotion", "ユーザーは「面接」のことで「緊張する」と話していた")]


def test_facts_preferences_and_personal() -> None:
    result = run(
        ("広告代理店で働いてるんだ。猫が好き。実家は北海道", "へえ"),
        ("今日は疲れた", "おつかれさま"),
    )
    assert adds(result) == [
        ("fact", "ユーザーは「広告代理店で働いてるんだ」と話していた"),
        ("preference", "ユーザーは「猫が好き」と話していた"),
        ("fact", "ユーザーは「実家は北海道」と話していた"),
    ]  # 「今日は疲れた」だけの気分は残さない


def test_short_follow_up_sentence_is_merged_and_birthday_is_a_fact() -> None:
    result = run(("猫を2匹飼ってるよ。ミケとタマ", "かわいい"), ("誕生日は3月14日だよ", "覚えとくね"))
    assert adds(result) == [
        ("fact", "ユーザーは「猫を2匹飼ってるよ。ミケとタマ」と話していた"),
        ("fact", "ユーザーは「誕生日は3月14日だよ」と話していた"),
    ]
    assert result["promises"] == []


def test_contradiction_supersedes_same_attribute() -> None:
    existing = [
        {"ref": "m1", "kind": "fact", "content": "ユーザーは「広告代理店で働いてるんだ」と話していた"},
        {"ref": "m2", "kind": "preference", "content": "ユーザーは「猫が好き」と話していた"},
    ]
    result = run(("転職して、今は銀行で働いてるよ", "そうなんだ"), memories=existing)
    [op] = result["memories"]
    assert op["op"] == "supersede"
    assert op["target"] == "m1"
    assert "銀行" in op["content"]
    # 同じことを言い直しただけなら noop
    same = run(("広告代理店で働いてるんだ", "うん"), memories=existing)
    assert same["memories"] == [{"op": "noop", "target": "m1"}]
    # 引っ越し・恋人も同じ属性の記憶を置き換える
    home = run(
        ("先月、大阪に引っ越した", "へえ"),
        memories=[{"ref": "m1", "kind": "fact", "content": "ユーザーは「東京に住んでる」と話していた"}],
    )
    assert home["memories"][0]["op"] == "supersede"
    partner = run(
        ("彼女と別れたんだ", "そっか"),
        memories=[{"ref": "m1", "kind": "fact", "content": "ユーザーは「彼女がいる」と話していた"}],
    )
    assert partner["memories"][0]["op"] == "supersede"


def test_relationship_nickname_speech_and_affection() -> None:
    result = run(("これからはたっくんって呼んで。タメ口でいいよ", "わかった"), ("美咲のこと好きだよ", "えっ"))
    assert adds(result) == [
        ("relationship", "ユーザーは「たっくん」と呼ばれたい"),
        ("relationship", "ユーザーは「タメ口でいいよ」と話し、くだけた話し方を望んでいる"),
        ("relationship", "ユーザーはテスト美咲に「美咲のこと好きだよ」と伝えた"),
    ]
    changed = run(
        ("やっぱりタクって呼んで", "うん"),
        memories=[{"ref": "m1", "kind": "relationship", "content": "ユーザーは「たっくん」と呼ばれたい"}],
    )
    assert changed["memories"][0]["op"] == "supersede"
    assert changed["memories"][0]["content"] == "ユーザーは「タク」と呼ばれたい"


def test_promises_with_relative_dates() -> None:
    result = run(
        ("明日は歯医者に行くんだ", "えらい"),
        ("今度の土曜に一緒に映画見に行こうね", "うん！"),
        ("来月の5日にライブがある", "いいね"),
        ("今度おすすめの本教えて", "もちろん"),
        ("明日は雨らしい", "そっか"),
    )
    promises = {p["content"]: p for p in result["promises"]}
    assert promises["歯医者に行く"]["due_date"] == "2026-09-27"
    assert promises["一緒に映画見に行こう"]["due_date"] == "2026-10-03"
    assert promises["一緒に映画見に行こう"]["memory"] == "ユーザーと10月3日（土）に「一緒に映画見に行こう」と約束した"
    assert promises["ライブがある"]["due_date"] == "2026-10-05"
    assert promises["おすすめの本教えて"]["due_precision"] == "unknown"
    assert promises["おすすめの本教えて"]["due_date"] is None
    assert len(promises) == 4  # 「明日は雨らしい」は予定ではない


def test_polite_plans_are_promises() -> None:
    """丁寧語の予定（「行きます」「予定です」「受けます」）も約束にする（2026-09-26 の評価で取りこぼした形）。"""
    result = run(
        ("今度の土曜、友達と美術館に行きます！", "いいですね"),
        ("来週の水曜に面談を受けます", "がんばって"),
        ("明後日は友達とランチの予定です", "楽しんで"),
        ("今度一緒に水族館に行きましょう", "ぜひ"),
        ("昨日は美術館に行きました", "よかったね"),
    )
    promises = {p["content"]: p for p in result["promises"]}
    assert promises["友達と美術館に行く"]["due_date"] == "2026-10-03"
    assert promises["友達と美術館に行く"]["memory"] == "ユーザーは10月3日（土）に「友達と美術館に行く」の予定がある"
    assert promises["面談を受ける"]["due_date"] == "2026-09-30"
    assert promises["友達とランチ"]["due_date"] == "2026-09-28"
    assert promises["一緒に水族館に行こう"]["due_precision"] == "unknown"
    assert len(promises) == 4  # 過去の「行きました」は約束ではない


def test_past_events_are_episodes_not_promises() -> None:
    result = run(("昨日、友達と温泉に行ったんだ", "いいね"))
    assert result["promises"] == []
    assert adds(result) == [("episode", "ユーザーは「昨日、友達と温泉に行ったんだ」と話していた")]


def test_questions_are_not_memories() -> None:
    assert run(("猫って好き？", "うん"))["memories"] == []


def test_secret_tag() -> None:
    result = run(("これは秘密なんだけど、実は猫アレルギーなんだ", "内緒ね"))
    assert all(op["secret"] for op in result["memories"] if op["op"] == "add")


def test_promise_updates() -> None:
    promises = [
        {"ref": "p1", "content": "面接", "due_date": "2026-10-01", "status": "pending"},
        {"ref": "p2", "content": "一緒に映画見に行こう", "due_date": "2026-10-03", "status": "pending"},
        {"ref": "p3", "content": "歯医者に行く", "due_date": "2026-09-27", "status": "pending"},
    ]
    result = run(
        ("面接終わったよ", "おつかれさま！"),
        ("映画行けなくなった、ごめん", "そっか"),
        ("ただいま", "おかえり。歯医者は明日だっけ？"),
        promises=promises,
    )
    assert {u["target"]: u["status"] for u in result["promise_updates"]} == {
        "p1": "done",
        "p2": "cancelled",
        "p3": "mentioned",
    }
    # 同じ約束はもう一度作らない
    again = run(("来週の木曜、面接なんだよね", "うん"), promises=promises[:1])
    assert again["promises"] == []


def test_character_statements() -> None:
    result = run(
        (
            "最近どう？",
            "わたしは昨日、駅前のカフェに行ったんだ。わたし、猫を飼ってるの。きみは何してた？そういえば前に言ってたよね。",
        )
    )
    assert [(s["content"], s["occurred_date"]) for s in result["character_statements"]] == [
        ("昨日、駅前のカフェに行った", "2026-09-25"),
        ("猫を飼ってる", None),
    ]


def test_empty_context() -> None:
    text = mock_memory_analysis(LLMRequest(purpose="memory_analysis", messages=[], temperature=0, max_tokens=1))
    assert json.loads(text) == {"memories": [], "promises": [], "promise_updates": [], "character_statements": []}


async def test_summary_mock_uses_transcript_and_mock_llm_routes_to_it() -> None:
    request = LLMRequest(
        purpose="memory_summary",
        messages=[{"role": "user", "content": "x"}],
        temperature=0,
        max_tokens=10,
        mock_context={
            "transcript": [
                {"sender": "user", "body": "仕事で疲れた話"},
                {"sender": "character", "body": "おつかれさま"},
                {"sender": "user", "body": "いい天気"},
            ]
        },
    )
    summary = json.loads(mock_memory_summary(request))["summary"]
    assert summary == "これまでの3件のやりとりで、ユーザーは「仕事で疲れた話」と話していた。"
    result = await MockLLM().complete(request)
    assert json.loads(result.text)["summary"] == summary
