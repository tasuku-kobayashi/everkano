"""process_turns（返答の後の分析）の統合テスト: モックの memory_analysis で DB まで通す。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.security import CurrentUser
from app.engine.memory import MemoryEngineUnavailableError, UserMemoryService
from app.engine.memory.embedding import HashEmbedding
from app.engine.types import JST
from app.models.memories import CreateMemoryRequest
from app.services.audit import AuditLogger
from app.services.llm import LLMError, LLMRequest, LLMResult, MockLLM
from app.services.moderation import Moderator
from tests.conftest import World, make_settings
from tests.engine.memory.conftest import Pair, make_pair, make_service, process

pytestmark = pytest.mark.integration

# 2026-09-26（土）12:00 JST
NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)


def _panel(pair: Pair, pool: Any) -> UserMemoryService:
    return UserMemoryService(
        settings=make_settings(),
        pool=pool,
        embedder=HashEmbedding(1536),
        moderator=Moderator(),
        audit=AuditLogger(pool),
    )


async def test_spec_example_promise_with_absolute_due_date_and_emotion(pool: Any, pair: Pair) -> None:
    """仕様 4.2: 「来週の木曜、面接なんだよね。緊張する」→ 約束（10/1）と気持ちの記憶。"""
    service = make_service(pool)
    turn = await pair.turn("来週の木曜、面接なんだよね。緊張する", "そうなんだ、応援してるよ", at=NOW)
    result = await process(service, pair, [turn], NOW)

    assert result.error is None
    [promise] = await pair.promises()
    assert promise["content"] == "面接"
    assert promise["status"] == "pending"
    assert promise["due_precision"] == "day"
    # 日付だけ分かる約束の期日は日本時間のその日の 12:00
    assert promise["due_at"] == datetime(2026, 10, 1, 12, 0, tzinfo=JST)
    assert promise["source_message_id"] == turn.user_message_id
    assert promise["created_at"] == NOW
    assert result.promises_created == (promise["id"],)

    memories = await pair.memories()
    kinds = {m["kind"]: m for m in memories}
    assert set(kinds) == {"promise", "emotion"}
    assert "10月1日（木）" in kinds["promise"]["content"]
    assert promise["source_memory_id"] == kinds["promise"]["id"]
    assert "緊張" in kinds["emotion"]["content"]
    for memory in memories:
        assert memory["created_at"] == NOW  # アプリの時計で書く（時間の早送り）
        assert memory["updated_at"] == NOW
        assert memory["source_conversation_id"] == pair.conversation_id
        assert memory["is_user_edited"] is False
    assert set(result.created) == {m["id"] for m in memories}

    creates = await pair.audit("memory.create")
    assert {c["kind"] for c in creates} == {"promise", "emotion"}
    [promise_audit] = await pair.audit("promise.create")
    assert promise_audit["promise_id"] == str(promise["id"])
    assert promise_audit["conversation_id"] == str(pair.conversation_id)
    [analysis] = await pair.audit("memory.analysis")
    assert analysis["purpose"] == "memory_analysis"
    assert analysis["usage"]["total_tokens"] > 0
    assert analysis["attempts"] == 1
    assert analysis["user_message_ids"] == [str(turn.user_message_id)]
    assert analysis["prompt_messages"][0]["role"] == "system"


async def test_same_fact_twice_is_not_duplicated(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    first = await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    assert len(first.created) == 1
    later = NOW + timedelta(hours=1)
    second = await process(service, pair, [await pair.turn("猫が好きなんだ", at=later)], later)
    assert second.created == ()
    assert len(await pair.memories()) == 1


async def test_contradiction_supersedes_old_memory_and_keeps_history(pool: Any, pair: Pair) -> None:
    """M4: 「転職した」→ 新しい記憶が有効、古い記憶は superseded として残る。"""
    service = make_service(pool)
    await process(service, pair, [await pair.turn("広告代理店で働いてるんだ", at=NOW)], NOW)
    [old] = await pair.memories()
    later = NOW + timedelta(days=30)
    result = await process(service, pair, [await pair.turn("転職して、今は銀行で働いてるよ", at=later)], later)

    assert result.superseded == (old["id"],)
    [new_id] = result.created
    rows = {m["id"]: m for m in await pair.memories()}
    assert rows[old["id"]]["status"] == "superseded"
    assert rows[old["id"]]["superseded_by"] == new_id
    assert rows[old["id"]]["superseded_at"] == later
    assert rows[new_id]["status"] == "active"
    assert "銀行" in rows[new_id]["content"]
    [supersede] = await pair.audit("memory.supersede")
    assert supersede["old_memory_id"] == str(old["id"])
    assert supersede["new_memory_id"] == str(new_id)

    # 検索では新しい方だけが出る
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="仕事はどう？",
        query_embedding=(await HashEmbedding(1536).embed(["仕事 働いて"]))[0],
        now=later,
    )
    ids = {m.id for m in context.memories}
    assert new_id in ids
    assert old["id"] not in ids


async def test_user_edited_memory_is_never_superseded(pool: Any, pair: Pair) -> None:
    """E5: ユーザーが書いた記憶は自動で置き換えない。新しい情報は別の記憶として追加する。"""
    service = make_service(pool)
    [vector] = await HashEmbedding(1536).embed(["広告代理店で働いてる"])
    edited_id = await pair.world.conn.fetchval(
        """
        insert into public.memories (user_id, character_id, kind, content, importance, embedding, is_user_edited)
        values ($1, $2, 'fact', '広告代理店で働いてる', 0.8, $3::text::extensions.vector, true) returning id
        """,
        pair.user_id,
        pair.character_id,
        "[" + ",".join(f"{v:.7g}" for v in vector) + "]",
    )
    before = await pair.world.conn.fetchrow("select * from public.memories where id = $1", edited_id)
    result = await process(service, pair, [await pair.turn("転職して、今は銀行で働いてるよ", at=NOW)], NOW)

    assert result.skipped_user_edited == 1
    assert result.superseded == ()
    after = await pair.world.conn.fetchrow("select * from public.memories where id = $1", edited_id)
    assert dict(after) == dict(before)
    active = await pair.memories(include_superseded=False)
    assert len(active) == 2
    assert any("銀行" in m["content"] for m in active)
    [skipped] = await pair.audit("memory.user_edited_skipped")
    assert skipped["memory_id"] == str(edited_id)
    assert skipped["op"] == "supersede"


async def test_deleted_memory_is_not_resurrected(pool: Any, pair: Pair) -> None:
    """E5: 削除 → 墓標 → 同じ発言をもう一度しても自動では作り直さない（ユーザーの手動追加は可）。"""
    service = make_service(pool)
    panel = _panel(pair, pool)
    user = CurrentUser(id=pair.user_id, email=None)
    await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    [memory] = await pair.memories()
    await panel.delete(user, memory["id"])

    assert await pair.memories() == []
    tombstone = await pair.world.conn.fetchrow(
        "select * from public.memory_tombstones where user_id = $1", pair.user_id
    )
    assert tombstone is not None
    assert tombstone["embedding"] is not None
    assert "content" not in dict(tombstone)  # 本文は持たない

    later = NOW + timedelta(days=1)
    result = await process(service, pair, [await pair.turn("猫が好きなんだ", at=later)], later)
    assert result.created == ()
    assert result.skipped_tombstoned == 1
    assert await pair.memories() == []
    [suppressed] = await pair.audit("memory.tombstone_suppressed")
    assert suppressed["tombstone_id"] == str(tombstone["id"])
    assert "content" not in suppressed

    # ユーザー自身が追加し直すのは可
    created = await panel.create(user, CreateMemoryRequest(character_id=pair.character_id, content="猫が好き"))
    assert created.is_user_edited is True


async def test_promise_lifecycle_mentioned_then_done(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=NOW)], NOW)
    [promise] = await pair.promises()

    # キャラが返答で触れた → mentioned
    t1 = NOW + timedelta(days=5)
    await process(service, pair, [await pair.turn("ただいま", "おかえり！そういえば面接は明日だよね。", at=t1)], t1)
    [promise] = await pair.promises()
    assert promise["status"] == "mentioned"
    assert promise["mentioned_at"] == t1

    # ユーザーが「終わった」→ done
    t2 = NOW + timedelta(days=6)
    await process(service, pair, [await pair.turn("面接終わったよ！緊張した", at=t2)], t2)
    [promise] = await pair.promises()
    assert promise["status"] == "done"
    assert promise["completed_at"] == t2
    assert promise["updated_at"] == t2
    changes = await pair.audit("promise.status_change")
    assert [(c["before"], c["after"], c["source"]) for c in changes] == [
        ("pending", "mentioned", "analysis"),
        ("mentioned", "done", "analysis"),
    ]


async def test_cancelled_promise_from_analysis(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    await process(service, pair, [await pair.turn("土曜に一緒に映画見に行こうね", at=NOW)], NOW)
    [promise] = await pair.promises()
    assert promise["due_at"].astimezone(JST).date().isoformat() == "2026-10-03"
    assert "映画" in promise["content"]
    later = NOW + timedelta(days=2)
    result = await process(service, pair, [await pair.turn("ごめん、映画行けなくなった", at=later)], later)
    [promise] = await pair.promises()
    assert promise["status"] == "cancelled"
    assert promise["cancelled_at"] == later
    # 元の記憶（「〜と約束した」）はもう正しくないので履歴にする（検索に出てこない）
    assert result.superseded == (promise["source_memory_id"],)
    source = await pair.world.conn.fetchrow(
        "select status, superseded_at from public.memories where id = $1", promise["source_memory_id"]
    )
    assert (source["status"], source["superseded_at"]) == ("superseded", later)
    [retired] = await pair.audit("memory.supersede")
    assert retired["reason"] == "promise_cancelled"


async def test_character_statements_are_saved_once(pool: Any, pair: Pair) -> None:
    """M8: キャラが自分について話したこと（「昨日カフェに行った」）を覚えておく。"""
    service = make_service(pool)
    reply = "わたしは昨日、駅前のカフェに行ったんだ。すごく落ち着いたよ。"
    result = await process(service, pair, [await pair.turn("最近どう？", reply, at=NOW)], NOW)
    [statement_id] = result.character_memories_created
    row = await pair.world.conn.fetchrow("select * from public.character_memories where id = $1", statement_id)
    assert row["user_id"] == pair.user_id
    assert row["kind"] == "self_statement"
    assert "カフェ" in row["content"]
    assert row["occurred_at"] == datetime(2026, 9, 25, 12, 0, tzinfo=JST)
    assert row["source_message_id"] == pair.turns[-1].character_message_id
    assert row["embedding"] is not None
    [audit] = await pair.audit("character_memory.create")
    assert audit["character_memory_id"] == str(statement_id)

    later = NOW + timedelta(minutes=5)
    again = await process(service, pair, [await pair.turn("へえ", reply, at=later)], later)
    assert again.character_memories_created == ()


async def test_moderated_and_safety_turns_are_not_analyzed(pool: Any, pair: Pair) -> None:
    llm = CountingLLM()
    service = make_service(pool, llm=llm)
    moderated = replace(await pair.turn("来週の木曜、面接なんだよね", at=NOW), moderated=True)
    safety = replace(await pair.turn("もう消えたい。猫が好きなんだ", at=NOW), safety_triggered=True)
    result = await process(service, pair, [moderated, safety], NOW)
    assert result.created == ()
    assert llm.calls == 0
    assert await pair.memories() == []


async def test_trivial_batches_do_not_call_the_llm(pool: Any, pair: Pair) -> None:
    """相づち・あいさつだけのバッチは分析しない（E7 のコスト）。キャラが過去の出来事を話した返答は分析する。"""
    llm = CountingLLM()
    service = make_service(pool, llm=llm)
    trivial = [await pair.turn("うん", "そっか", at=NOW), await pair.turn("おやすみ〜", "おやすみ", at=NOW)]
    assert (await process(service, pair, trivial, NOW)).created == ()
    assert llm.calls == 0
    statement = await pair.turn("うん", "わたしは昨日、駅前のカフェに行ったんだ。", at=NOW)
    result = await process(service, pair, [statement], NOW)
    assert llm.calls == 1
    assert len(result.character_memories_created) == 1


async def test_relationship_nickname_and_its_change(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    await process(service, pair, [await pair.turn("これからはたっくんって呼んで", at=NOW)], NOW)
    [memory] = await pair.memories()
    assert memory["kind"] == "relationship"
    assert memory["content"] == "ユーザーは「たっくん」と呼ばれたい"
    later = NOW + timedelta(days=3)
    result = await process(service, pair, [await pair.turn("やっぱりタクって呼んでほしい", at=later)], later)
    assert result.superseded == (memory["id"],)
    active = await pair.memories(include_superseded=False)
    assert [m["content"] for m in active] == ["ユーザーは「タク」と呼ばれたい"]


async def test_capacity_evicts_superseded_history_first(pool: Any, pair: Pair) -> None:
    service = make_service(pool, max_per_character=10)
    conn = pair.world.conn
    for i in range(9):
        await conn.execute(
            "insert into public.memories (user_id, character_id, content, importance, is_user_edited)"
            " values ($1, $2, $3, 0.9, false)",
            pair.user_id,
            pair.character_id,
            f"大事な記憶{i}",
        )
    await conn.execute(
        "insert into public.memories (user_id, character_id, content, importance, status, superseded_at)"
        " values ($1, $2, '古い履歴', 0.95, 'superseded', now())",
        pair.user_id,
        pair.character_id,
    )
    result = await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    assert len(result.created) == 1
    contents = [m["content"] for m in await pair.memories()]
    assert len(contents) == 10
    assert "古い履歴" not in contents
    [eviction] = await pair.audit("memory.delete")
    assert eviction["source"] == "capacity_eviction"
    assert eviction["status"] == "superseded"


async def test_pairs_are_isolated(pool: Any, world: World) -> None:
    """M9: キャラAに話したことは、キャラBとの分析・検索に出てこない。"""
    service = make_service(pool)
    pair_a = await make_pair(world)
    other_character = await world.create_character()
    pair_b = Pair(
        world=world,
        user_id=pair_a.user_id,
        character_id=other_character,
        conversation_id=await world.conn.fetchval(
            "insert into public.conversations (user_id, character_id) values ($1, $2) returning id",
            pair_a.user_id,
            other_character,
        ),
        headers=pair_a.headers,
    )
    await process(service, pair_a, [await pair_a.turn("広告代理店で働いてるんだ", at=NOW)], NOW)
    result = await process(service, pair_b, [await pair_b.turn("転職して、今は銀行で働いてるよ", at=NOW)], NOW)
    assert result.superseded == ()
    assert len(await pair_a.memories(include_superseded=False)) == 1
    context = await service.retrieve_context(
        user_id=pair_b.user_id,
        character_id=pair_b.character_id,
        query_text="広告代理店",
        query_embedding=(await HashEmbedding(1536).embed(["広告代理店で働いてる"]))[0],
        now=NOW,
    )
    assert all("広告代理店" not in m.content for m in context.memories)


# --------------------------------------------------------------------------- LLM の失敗


class CountingLLM(MockLLM):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResult:
        if request.purpose == "memory_analysis":
            self.calls += 1
        return await super().complete(request)


class ScriptedLLM(MockLLM):
    """memory_analysis の出力を順に返す（最後の要素を繰り返す）。例外なら投げる。"""

    def __init__(self, outputs: Sequence[str | Exception]) -> None:
        super().__init__()
        self.outputs = list(outputs)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResult:
        if request.purpose != "memory_analysis":
            return await super().complete(request)
        self.requests.append(request)
        output = self.outputs[min(len(self.requests) - 1, len(self.outputs) - 1)]
        if isinstance(output, Exception):
            raise output
        return LLMResult(text=output, model="stub", latency_ms=1, usage={"total_tokens": 10})


VALID = (
    '{"memories": [{"op": "add", "kind": "preference", "content": "ユーザーは猫が好き", "importance": 0.7,'
    ' "turn": 1}], "promises": [], "promise_updates": [], "character_statements": []}'
)


async def test_malformed_json_is_retried_once_with_the_error(pool: Any, pair: Pair) -> None:
    llm = ScriptedLLM(["はい、分析しました！", VALID])
    service = make_service(pool, llm=llm)
    result = await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    assert result.error is None
    assert len(result.created) == 1
    assert len(llm.requests) == 2
    retry_messages = llm.requests[1].messages
    assert retry_messages[-2] == {"role": "assistant", "content": "はい、分析しました！"}
    assert "JSON" in retry_messages[-1]["content"]
    [analysis] = await pair.audit("memory.analysis")
    assert analysis["attempts"] == 2
    assert analysis["usage"]["total_tokens"] == 20


async def test_invalid_output_twice_is_a_noop_with_llm_error(pool: Any, pair: Pair) -> None:
    bad = '{"memories": [{"op": "replace", "content": "x"}]}'
    llm = ScriptedLLM([bad, bad])
    service = make_service(pool, llm=llm)
    result = await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    assert result.error is not None
    assert result.error.startswith("invalid_output:")
    assert "memories.0.op" in llm.requests[1].messages[-1]["content"]
    assert await pair.memories() == []
    [error] = await pair.audit("llm.error")
    assert error["purpose"] == "memory_analysis"
    assert error["attempts"] == 2
    assert error["raw_outputs"] == [bad, bad]
    assert await pair.audit("memory.analysis") == []


async def test_transient_llm_failure_raises_for_the_job_retry(pool: Any, pair: Pair) -> None:
    llm = ScriptedLLM([LLMError("HTTP 503", status_code=503, retryable=True, attempts=3)])
    service = make_service(pool, llm=llm)
    with pytest.raises(MemoryEngineUnavailableError):
        await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    assert await pair.memories() == []
    [error] = await pair.audit("llm.error")
    assert error["purpose"] == "memory_analysis"
    assert error["status_code"] == 503


async def test_content_rejection_returns_error_without_retry(pool: Any, pair: Pair) -> None:
    llm = ScriptedLLM([LLMError("HTTP 400: filtered", status_code=400)])
    service = make_service(pool, llm=llm)
    result = await process(service, pair, [await pair.turn("猫が好きなんだ", at=NOW)], NOW)
    assert result.error is not None
    assert result.error.startswith("llm_rejected:")


async def test_unknown_targets_and_low_importance_are_handled_safely(pool: Any, pair: Pair) -> None:
    output = (
        '{"memories": ['
        '{"op": "update", "target": "m9", "kind": "fact", "content": "ユーザーは大阪に住んでいる", "importance": 0.8},'
        '{"op": "add", "kind": "emotion", "content": "ユーザーは少し眠い", "importance": 0.3},'
        '{"op": "noop", "target": "m1"}],'
        ' "promises": [{"content": "昔の旅行", "due_date": "2020-01-01", "due_precision": "day"}],'
        ' "promise_updates": [{"target": "p7", "status": "done"}]}'
    )
    service = make_service(pool, llm=ScriptedLLM([output]))
    result = await process(service, pair, [await pair.turn("大阪に住んでるんだ", at=NOW)], NOW)
    memories = await pair.memories()
    # 捏造された参照（m9）は重複判定つきの追加になり、重要度の低い add と過去の期日の約束は捨てる
    assert [m["content"] for m in memories] == ["ユーザーは大阪に住んでいる"]
    assert result.promises_created == ()
    [analysis] = await pair.audit("memory.analysis")
    assert analysis["dropped_low_importance"] == 1
    assert analysis["ops"] == {"update": 1, "add": 1, "noop": 1}


async def test_turn_limit_uses_the_newest_turns(pool: Any, pair: Pair) -> None:
    service = make_service(pool, analysis_max_turns=2)
    turns = [
        await pair.turn(text, at=NOW + timedelta(minutes=i))
        for i, text in enumerate(["猫が好き", "犬が好き", "鳥が好き"])
    ]
    llm_requests: list[dict[str, Any]] = []

    class Spy(MockLLM):
        async def complete(self, request: LLMRequest) -> LLMResult:
            if request.purpose == "memory_analysis" and request.mock_context is not None:
                llm_requests.append(dict(request.mock_context))
            return await super().complete(request)

    service = make_service(pool, llm=Spy(), analysis_max_turns=2)
    await process(service, pair, turns, NOW + timedelta(minutes=5))
    assert [t["user"] for t in llm_requests[0]["turns"]] == ["犬が好き", "鳥が好き"]
    assert uuid.UUID(str(pair.user_id))


async def test_conversation_deleted_before_the_job_is_a_noop(pool: Any, pair: Pair) -> None:
    """退会・テストの後片付けで会話が消えていたら、何もせずに終える（ジョブを失敗させない）。"""
    llm = CountingLLM()
    service = make_service(pool, llm=llm)
    turn = await pair.turn("猫が好きなんだ", at=NOW)
    await pair.world.conn.execute("delete from auth.users where id = $1", pair.user_id)
    result = await process(service, pair, [turn], NOW)
    assert result.error == "conversation_not_found"
    assert llm.calls == 0


async def test_user_deleted_during_the_analysis_is_a_noop(pool: Any, pair: Pair) -> None:
    """分析（LLM）の途中でユーザーが削除されても、外部キー違反で失敗・再試行を繰り返さない。"""
    conn = pair.world.conn

    class DeletingLLM(MockLLM):
        async def complete(self, request: LLMRequest) -> LLMResult:
            result = await super().complete(request)
            if request.purpose == "memory_analysis":
                await conn.execute("delete from auth.users where id = $1", pair.user_id)
            return result

    service = make_service(pool, llm=DeletingLLM())
    result = await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=NOW)], NOW)
    assert result.error == "conversation_not_found"
    assert await conn.fetchval("select count(*) from public.memories where user_id = $1", pair.user_id) == 0
