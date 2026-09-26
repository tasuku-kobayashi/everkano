"""予定から作るフィード投稿のキャプション（仕様 §5 C7）。

- LLM の用途 `feed_caption`（テンプレート packages/prompts/templates/feed_caption.ja.txt）。ペルソナの口調・
  絵文字のルールに従った短い文。出力は Gate #1（ペルソナの NG ワード・URL を含む）と OutputGuard（E2 / E3）で
  検査し、ひっかかったら投稿しない（サービス側）。
- MockLLM 用の決定的なハンドラをこのモジュールの import 時に登録する（`register_mock_handler`）。
- 画像は post_image_pool（事前に用意した画像）からタグで選ぶ（サービス側。新しく画像を作ることはしない）。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from app.engine.calendar.models import EventView
from app.engine.calendar.world import WEEKDAYS_JA
from app.engine.types import TAG_VOCABULARY, WorldState, to_jst
from app.services.llm import LLMRequest, clean_reply, register_mock_handler
from app.services.persona import Persona
from app.services.prompt import ChatMessage, PromptTemplate, PromptTemplateError, parse_template, render_speech

CAPTION_PURPOSE: Final[str] = "feed_caption"
CAPTION_TEMPLATE_NAME: Final[str] = "feed_caption"
CAPTION_TEMPLATE_FILE: Final[str] = "feed_caption.ja.txt"
CAPTION_REQUIRED: Final[frozenset[str]] = frozenset({"name", "event", "emoji_rule", "max_chars"})
CAPTION_OPTIONAL: Final[frozenset[str]] = frozenset({"bio", "speech", "world"})
DEFAULT_CAPTION_MAX_CHARS: Final[int] = 120

# 画像タグ（app/engine/types.py の TAG_VOCABULARY。ペルソナ・画像プールと共通）→（写真の説明, 絵文字）
TAG_INFO: Final[dict[str, tuple[str, str]]] = {
    "cafe": ("カフェ", "☕"),
    "food": ("ごはん", "🍽"),
    "sweets": ("スイーツ", "🍰"),
    "izakaya": ("居酒屋", "🍺"),
    "bar": ("バー", "🍸"),
    "office": ("オフィス", "💼"),
    "home": ("家", "🏠"),
    "room": ("部屋", "🛋"),
    "book": ("本", "📚"),
    "study": ("勉強", "✏️"),
    "gym": ("ジム", "💪"),
    "running": ("ランニング", "🏃"),
    "yoga": ("ヨガ", "🧘"),
    "travel": ("旅先", "✈️"),
    "sea": ("海", "🌊"),
    "mountain": ("山", "⛰"),
    "forest": ("森", "🌲"),
    "city": ("街", "🏙"),
    "night_city": ("夜の街", "🌃"),
    "street": ("街角", "🚶"),
    "shopping": ("お買い物", "🛍"),
    "fashion": ("コーデ", "👗"),
    "cosmetics": ("コスメ", "💄"),
    "cooking": ("料理", "🍳"),
    "music": ("音楽", "🎵"),
    "stage": ("ステージ", "🎤"),
    "live": ("ライブ", "🎤"),
    "karaoke": ("カラオケ", "🎤"),
    "game": ("ゲーム", "🎮"),
    "anime": ("アニメ", "📺"),
    "art": ("アート", "🎨"),
    "flowers": ("花", "💐"),
    "sakura": ("桜", "🌸"),
    "rain": ("雨", "☔"),
    "summer": ("夏", "🌻"),
    "festival": ("お祭り", "🏮"),
    "fireworks": ("花火", "🎆"),
    "autumn": ("秋", "🍂"),
    "autumn_leaves": ("紅葉", "🍁"),
    "snow": ("雪", "❄️"),
    "christmas": ("クリスマス", "🎄"),
    "new_year": ("お正月", "🎍"),
    "valentine": ("バレンタイン", "🍫"),
    "halloween": ("ハロウィン", "🎃"),
    "pet": ("ペット", "🐾"),
    "sky": ("空", "☁️"),
    "sunset": ("夕焼け", "🌇"),
    "morning": ("朝", "🌅"),
    "train": ("電車", "🚃"),
    "library": ("図書館", "📚"),
    "school": ("学校", "🏫"),
    "park": ("公園", "🌳"),
}
IMAGE_TAGS: Final[frozenset[str]] = frozenset(TAG_VOCABULARY)
if frozenset(TAG_INFO) != IMAGE_TAGS:  # pragma: no cover - 語彙と説明のずれは起動時に止める（テストでも検査する）
    raise RuntimeError(f"TAG_INFO と TAG_VOCABULARY が一致しません: {sorted(frozenset(TAG_INFO) ^ IMAGE_TAGS)}")

_EMOJI_RE: Final = re.compile("[\U0001f000-\U0001faff☀-➿⭐❤♡♪]")
_NO_EMOJI_WORDS: Final[tuple[str, ...]] = ("使わない", "使いません", "絵文字なし")
_MANY_EMOJI_WORDS: Final[tuple[str, ...]] = ("多め", "たくさん")


def default_prompts_dir() -> Path:
    # apps/api/app/engine/calendar/captions.py → リポジトリ直下
    return Path(__file__).resolve().parents[5] / "packages" / "prompts" / "templates"


def load_caption_template(prompts_dir: Path | None = None) -> PromptTemplate:
    """キャプションのテンプレートを読み込み、プレースホルダを検証する（起動時。失敗したら起動を止める）。"""
    path = (prompts_dir or default_prompts_dir()) / CAPTION_TEMPLATE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptTemplateError(f"{path}: 読み込めません: {exc}") from exc
    template = parse_template(CAPTION_TEMPLATE_NAME, text)
    found = template.placeholders
    missing = CAPTION_REQUIRED - found
    unknown = found - CAPTION_REQUIRED - CAPTION_OPTIONAL
    if missing:
        raise PromptTemplateError(f"{CAPTION_TEMPLATE_NAME}: 必須プレースホルダがありません: {sorted(missing)}")
    if unknown:
        raise PromptTemplateError(f"{CAPTION_TEMPLATE_NAME}: 未知のプレースホルダがあります: {sorted(unknown)}")
    return template


def emoji_level(rule: str) -> int:
    """ペルソナの絵文字ルール（自由記述）から、キャプションに入れる絵文字の数の目安（0〜2）。"""
    if any(w in rule for w in _NO_EMOJI_WORDS):
        return 0
    if any(w in rule for w in _MANY_EMOJI_WORDS):
        return 2
    return 1


def rule_emojis(rule: str) -> list[str]:
    return _EMOJI_RE.findall(rule)


def _format_date(value: Any) -> str:
    local = to_jst(value)
    return f"{local.month}月{local.day}日（{WEEKDAYS_JA[local.weekday()]}）"


def render_event(event: EventView) -> str:
    start, end = to_jst(event.starts_at), to_jst(event.ends_at)
    lines = [f"- いつ: {_format_date(event.starts_at)} {start:%H:%M}〜{end:%H:%M}", f"- 予定: {event.title}"]
    if event.location:
        lines.append(f"- 場所: {event.location}")
    if event.description:
        lines.append(f"- 内容: {event.description}")
    if event.mood:
        lines.append(f"- そのときの気分: {event.mood}")
    photos = [TAG_INFO[t][0] for t in event.post_tags if t in TAG_INFO]
    if photos:
        lines.append(f"- 写真: {'・'.join(photos)}の写真")
    return "\n".join(lines)


def render_world_for_caption(world: WorldState) -> str:
    parts = [f"{_format_date(world.now)} {world.season_ja}・{world.time_of_day_ja}"]
    if world.holiday_name:
        parts.append(f"祝日（{world.holiday_name}）")
    if world.seasonal_labels_ja:
        parts.append("季節の行事: " + "・".join(world.seasonal_labels_ja))
    return " / ".join(parts)


def caption_messages(
    template: PromptTemplate, persona: Persona, event: EventView, world: WorldState, *, max_chars: int
) -> list[ChatMessage]:
    values = {
        "name": persona.name,
        "bio": persona.bio.strip() or persona.profile.strip()[:400],
        "speech": render_speech(persona),
        "event": render_event(event),
        "world": render_world_for_caption(world),
        "emoji_rule": persona.speech.emoji or "控えめ（使っても1つ）",
        "max_chars": str(max_chars),
    }
    return template.render(values)


def caption_mock_context(persona: Persona, event: EventView, *, seed: str, max_chars: int) -> dict[str, Any]:
    """MockLLM の feed_caption ハンドラに渡す構造化データ。"""
    rule = persona.speech.emoji or ""
    level = emoji_level(rule)
    preferred = rule_emojis(rule)
    tag_emojis = [TAG_INFO[t][1] for t in event.post_tags if t in TAG_INFO]
    emojis = [e for e in tag_emojis if e in preferred] or preferred or tag_emojis
    return {
        "persona_key": persona.key,
        "name": persona.name,
        "first_person": persona.speech.first_person,
        "title": event.title,
        "location": event.location,
        "mood": event.mood,
        "tags": list(event.post_tags),
        "emoji_level": level,
        "emojis": emojis,
        "seed": seed,
        "max_chars": max_chars,
    }


def _mock_seed(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


def mock_feed_caption(request: LLMRequest) -> str:
    """MockLLM の feed_caption（決定的）。予定の名前と気分から、キャラの一人称で 1〜2 文を作る。"""
    ctx: Mapping[str, Any] = request.mock_context or {}
    title = str(ctx.get("title") or "今日の予定")
    mood = str(ctx.get("mood") or "").strip()
    location = str(ctx.get("location") or "").strip()
    first_person = str(ctx.get("first_person") or "わたし")
    level = int(ctx.get("emoji_level") or 0)
    emojis = [str(e) for e in ctx.get("emojis") or []]
    seed = _mock_seed(str(ctx.get("seed") or title))
    emoji = "".join(emojis[(seed + i) % len(emojis)] for i in range(min(level, 2))) if emojis else ""
    feeling = f"{mood}。" if mood else "いい時間だった。"
    patterns = (
        f"{title}、おしまい。{feeling}{emoji}",
        f"今日は{title}。{feeling}{emoji}",
        f"{location}で{title}。{feeling}{emoji}" if location else f"{title}の記録。{feeling}{emoji}",
        f"{first_person}の今日の{title}。{feeling}{emoji}",
    )
    text = patterns[seed % len(patterns)].rstrip()
    max_chars = int(ctx.get("max_chars") or DEFAULT_CAPTION_MAX_CHARS)
    return text[:max_chars]


def clean_caption(text: str, persona_name: str, *, max_chars: int) -> str:
    """LLM の出力からキャプションの本文を取り出す（前後の空白・名前の接頭辞・かぎかっこ・4 行目以降を落とす）。"""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    body = "\n".join(lines[:3])
    return clean_reply(body, persona_name, max_chars=max_chars)


register_mock_handler(CAPTION_PURPOSE, mock_feed_caption)
