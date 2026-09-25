from __future__ import annotations

import math

from app.core.db import vector_literal
from app.services.embedding import HashEmbedding, cosine_similarity


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    emb = HashEmbedding(1536)
    a = emb.embed_one("来週、大阪に出張するんだ")
    b = HashEmbedding(1536).embed_one("来週、大阪に出張するんだ")
    assert a == b
    assert len(a) == 1536
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)


def test_hash_embedding_empty_text_is_unit_vector() -> None:
    v = HashEmbedding(1536).embed_one("")
    assert math.isclose(sum(x * x for x in v), 1.0)


def test_hash_embedding_similarity_sanity() -> None:
    emb = HashEmbedding(1536)
    memory = emb.embed_one("ユーザーは「来週、大阪に出張するんだ」と話していた")
    related = emb.embed_one("大阪のお土産、何がいいと思う？")
    unrelated = emb.embed_one("お昼ごはん食べた？")
    assert cosine_similarity(memory, memory) > 0.999
    assert cosine_similarity(memory, related) > cosine_similarity(memory, unrelated)
    assert cosine_similarity(memory, related) > 0.1
    near_dup = emb.embed_one("ユーザーは「来週大阪に出張する」と話していた")
    assert cosine_similarity(memory, near_dup) > 0.7


async def test_embed_batch_matches_single() -> None:
    emb = HashEmbedding(64)
    batch = await emb.embed(["a", "bb"])
    assert batch == [emb.embed_one("a"), emb.embed_one("bb")]


def test_vector_literal() -> None:
    assert vector_literal([0.5, -1.0, 0.0]) == "[0.5,-1,0]"
