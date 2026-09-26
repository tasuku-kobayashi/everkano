"""フィード投稿のキャプション（C7）: テンプレート・描画・MockLLM のハンドラ・後処理。"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from app.engine.calendar.captions import (
    CAPTION_PURPOSE,
    IMAGE_TAGS,
    TAG_INFO,
    caption_messages,
    caption_mock_context,
    clean_caption,
    emoji_level,
    load_caption_template,
    mock_feed_caption,
    rule_emojis,
)
from app.engine.calendar.models import EventView
from app.engine.calendar.world import build_world_state
from app.engine.types import JST, TAG_VOCABULARY
from app.services.llm import LLMRequest, MockLLM, registered_mock_purposes
from app.services.moderation import Moderator
from app.services.prompt import PromptTemplateError
from tests.conftest import REPO_ROOT
from tests.engine.calendar.personas import early_persona, night_persona, office_persona

BRIEF_TAGS = (
    "cafe, food, sweets, izakaya, bar, office, home, room, book, study, gym, running, yoga, travel, sea, mountain, "
    "forest, city, night_city, street, shopping, fashion, cosmetics, cooking, music, stage, live, karaoke, game, "
    "anime, art, flowers, sakura, rain, summer, festival, fireworks, autumn, autumn_leaves, snow, christmas, "
    "new_year, valentine, halloween, pet, sky, sunset, morning, train, library, school, park"
)

DRINKS = EventView(
    id=uuid.uuid4(),
    kind="oneoff",
    title="同期と飲み会",
    starts_at=datetime(2026, 9, 25, 19, 30, tzinfo=JST),
    ends_at=datetime(2026, 9, 25, 23, 0, tzinfo=JST),
    busyness=2,
    source="generator",
    source_key="event:friday_drinks",
    generated_for=datetime(2026, 9, 25, tzinfo=JST).date(),
    location="新宿の居酒屋",
    mood="ほろ酔いで上機嫌",
    meta={"status_label": "飲み会中", "post_tags": ["izakaya"], "post_probability": 0.9},
)


def test_tag_vocabulary_matches_the_brief() -> None:
    brief = tuple(t.strip() for t in BRIEF_TAGS.split(","))
    assert brief == TAG_VOCABULARY  # 語彙の定義は app/engine/types.py の 1 か所
    assert len(set(TAG_VOCABULARY)) == len(TAG_VOCABULARY)
    assert frozenset(TAG_VOCABULARY) == IMAGE_TAGS
    # キャプションの写真の説明・絵文字が全タグにある（過不足なし）
    assert set(TAG_INFO) == set(TAG_VOCABULARY)


def test_template_loads_and_renders() -> None:
    template = load_caption_template()
    world = build_world_state(datetime(2026, 9, 25, 23, 20, tzinfo=JST))
    messages = caption_messages(template, office_persona(), DRINKS, world, max_chars=120)
    assert [m["role"] for m in messages] == ["system", "user"]
    system = messages[0]["content"]
    assert "「カレン」" in system
    assert "同期と飲み会" in system
    assert "新宿の居酒屋" in system
    assert "居酒屋の写真" in system
    assert "120文字以内" in system
    assert "控えめ（1つまで）" in system  # ペルソナの絵文字ルール
    assert "実在する人間だと主張しない" in system
    assert "9月25日（金）" in system


def test_template_with_missing_placeholder_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "feed_caption.ja.txt").write_text("あなたは{name}です。{event}\n=== user ===\n書いて", encoding="utf-8")
    with pytest.raises(PromptTemplateError, match="必須プレースホルダ"):
        load_caption_template(tmp_path)
    (tmp_path / "feed_caption.ja.txt").write_text("{name}{event}{emoji_rule}{max_chars}{typo_value}", encoding="utf-8")
    with pytest.raises(PromptTemplateError, match="未知のプレースホルダ"):
        load_caption_template(tmp_path)
    with pytest.raises(PromptTemplateError, match="読み込めません"):
        load_caption_template(tmp_path / "missing")


@pytest.mark.parametrize(
    ("rule", "level"),
    [
        ("控えめ（♡ や 笑 程度。絵文字は1メッセージに最大1つ）", 1),
        ("多め（✨💅🫶💖😂 など、1メッセージ2〜3個まで）", 2),
        ("ほとんど使わない。ごくたまに「♪」を添える程度", 0),
        ("基本使わない主義。嬉しいときだけ、うっかり🍰を1つ付けてしまう", 0),
        ("ときどき使う（🐧🌊🌅 など海っぽいもの中心）", 1),
    ],
)
def test_emoji_level(rule: str, level: int) -> None:
    assert emoji_level(rule) == level


def test_rule_emojis() -> None:
    assert rule_emojis("ときどき使う（🐧🌊🌅 など）") == ["🐧", "🌊", "🌅"]


def test_mock_handler_is_registered_and_deterministic() -> None:
    assert CAPTION_PURPOSE in registered_mock_purposes()
    context = caption_mock_context(office_persona(), DRINKS, seed="a", max_chars=120)
    request = LLMRequest(purpose=CAPTION_PURPOSE, messages=[], temperature=0.9, max_tokens=100, mock_context=context)
    first = mock_feed_caption(request)
    assert first == mock_feed_caption(request)
    assert "同期と飲み会" in first
    assert "🍺" in first  # 控えめ = 1 つ（タグの絵文字）
    assert not Moderator().check(first, block_links=True).flagged


async def test_mock_llm_uses_the_handler() -> None:
    context = caption_mock_context(night_persona(), DRINKS, seed="b", max_chars=120)
    result = await MockLLM().complete(
        LLMRequest(purpose=CAPTION_PURPOSE, messages=[], temperature=0.9, max_tokens=100, mock_context=context)
    )
    assert "同期と飲み会" in result.text
    assert result.usage is not None


def test_mock_caption_respects_no_emoji_personas() -> None:
    context = caption_mock_context(early_persona(), DRINKS, seed="c", max_chars=120)  # ほとんど使わない
    assert context["emoji_level"] == 0
    text = mock_feed_caption(
        LLMRequest(purpose=CAPTION_PURPOSE, messages=[], temperature=0.9, max_tokens=100, mock_context=context)
    )
    assert "🍺" not in text


def test_mock_caption_prefers_persona_emojis() -> None:
    context = caption_mock_context(night_persona(), DRINKS, seed="d", max_chars=120)  # 多め（🌙✨🎮 など）
    assert context["emoji_level"] == 2
    assert context["emojis"] == ["🌙", "✨", "🎮"]


def test_clean_caption() -> None:
    assert clean_caption("カレン: 「今日は飲み会🍺」", "カレン", max_chars=120) == "今日は飲み会🍺"
    assert clean_caption("1行目\n\n2行目\n3行目\n4行目", "カレン", max_chars=120) == "1行目\n2行目\n3行目"
    long = clean_caption("あ" * 200, "カレン", max_chars=50)
    assert len(long) == 50
    assert long.endswith("…")
    assert clean_caption("   ", "カレン", max_chars=50) == ""


def test_seed_image_pool_covers_every_tag() -> None:
    """infra/supabase/seed_engine.sql: 全キャラ共通の画像がタグごとに 3 枚以上ある。"""
    sql = (REPO_ROOT / "infra" / "supabase" / "seed_engine.sql").read_text(encoding="utf-8")
    shared = Counter(re.findall(r"null, array\['([a-z_]+)'\]", sql))
    assert set(shared) == set(TAG_VOCABULARY)
    assert min(shared.values()) >= 3
    per_character = re.findall(r"'00000000-0000-4000-8000-[0-9a-f]{12}', array\['([a-z_]+)'\]", sql)
    assert per_character
    assert set(per_character) <= set(TAG_VOCABULARY)
    # タグの配列の書き方が変わってもすり抜けないよう、array[...] に現れるすべてのタグを語彙と突き合わせる
    every_tag = {t for group in re.findall(r"array\[([^\]]*)\]", sql) for t in re.findall(r"'([^']*)'", group)}
    assert every_tag == set(TAG_VOCABULARY)
