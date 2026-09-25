"""scripts/reembed_memories.py（app.services.reembed）の統合テスト。テスト用ユーザーの記憶だけを対象にする。"""

from __future__ import annotations

import uuid

import pytest

from app.core.db import create_pool, vector_literal
from app.services.embedding import HashEmbedding
from app.services.reembed import ReembedStats, reembed_memories
from tests.conftest import World, make_settings

pytestmark = pytest.mark.integration

CONTENTS = [f"ユーザーは「記憶その{i}」と話していた" for i in range(5)]


async def _insert_stale_memories(world: World, user_id: uuid.UUID) -> list[uuid.UUID]:
    # 別のベクトル空間（= 切り替え前の設定）で埋め込まれた状態を再現する
    stale = vector_literal(HashEmbedding().embed_one("まったく別の文章"))
    ids: list[uuid.UUID] = []
    for content in CONTENTS:
        memory_id = await world.conn.fetchval(
            """
            insert into public.memories (user_id, character_id, content, importance, tags, embedding)
            values ($1, $2, $3, 0.7, '{}', $4::text::extensions.vector)
            returning id
            """,
            user_id,
            world.character_id,
            content,
            stale,
        )
        ids.append(memory_id)
    return ids


async def _similarities(world: World, user_id: uuid.UUID) -> dict[str, float]:
    embedder = HashEmbedding()
    result: dict[str, float] = {}
    for content in CONTENTS:
        result[content] = await world.conn.fetchval(
            "select 1 - (embedding operator(extensions.<=>) $3::text::extensions.vector)"
            " from public.memories where user_id = $1 and content = $2",
            user_id,
            content,
            vector_literal(embedder.embed_one(content)),
        )
    return result


async def test_reembed_memories_is_scoped_batched_and_idempotent(world: World) -> None:
    user = await world.create_user()
    other = await world.create_user()
    await _insert_stale_memories(world, user.id)
    await _insert_stale_memories(world, other.id)
    pool = await create_pool(make_settings())
    try:
        before = await _similarities(world, user.id)
        assert all(sim < 0.5 for sim in before.values())

        dry = await reembed_memories(pool, HashEmbedding(), batch_size=2, dry_run=True, user_id=user.id)
        assert (dry.scanned, dry.updated, dry.batches) == (5, 0, 3)
        assert await _similarities(world, user.id) == before

        progress: list[int] = []

        def on_batch(stats: ReembedStats) -> None:
            progress.append(stats.updated)

        stats = await reembed_memories(pool, HashEmbedding(), batch_size=2, user_id=user.id, on_batch=on_batch)
        assert (stats.scanned, stats.updated, stats.batches) == (5, 5, 3)
        assert progress == [2, 4, 5]
        after = await _similarities(world, user.id)
        assert all(sim > 0.999 for sim in after.values()), after
        # 他のユーザーの記憶には触れない
        assert all(sim < 0.5 for sim in (await _similarities(world, other.id)).values())
        # 冪等（もう一度実行しても同じ）
        again = await reembed_memories(pool, HashEmbedding(), batch_size=10, user_id=user.id)
        assert (again.scanned, again.updated) == (5, 5)
        assert await _similarities(world, user.id) == pytest.approx(after)
    finally:
        await pool.close()
