#!/usr/bin/env python3
"""ペルソナYAML と seed/feed.yaml から infra/supabase/seed.sql を生成する。

使い方（packages/personas ディレクトリで実行）:
    uv run --with pyyaml python scripts/generate_seed.py           # seed.sql を書き出す
    uv run --with pyyaml python scripts/generate_seed.py --check   # 生成結果と seed.sql が一致しなければ exit 1
    uv run --with pyyaml python scripts/generate_seed.py --stdout  # 標準出力へ

出力は決定的（同じ入力なら同じ SQL）。公開日時などは SQL 側で now() から相対的に計算する。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_DIR.parent.parent
FEED_PATH = PACKAGE_DIR / "seed" / "feed.yaml"
SEED_SQL_PATH = REPO_ROOT / "infra" / "supabase" / "seed.sql"

TIMEZONE = "Asia/Tokyo"
ALL_DAYS = (1, 2, 3, 4, 5, 6, 7)
DAY_ALIASES: dict[str, tuple[int, ...]] = {
    "weekday": (1, 2, 3, 4, 5),
    "weekend": (6, 7),
}
MAX_COMMENT_AFTER_MINUTES = 240
COMMENT_COMPRESS_WINDOW_SECONDS = MAX_COMMENT_AFTER_MINUTES * 60
MIN_COMMENTS_PER_POST = 2
MAX_COMMENTS_PER_POST = 5
MIN_POSTS_PER_CHARACTER = 4
MAX_POSTS_PER_CHARACTER = 6
MIN_PAID_PER_CHARACTER = 1
MAX_PAID_PER_CHARACTER = 2
MIN_PRICE_TOKENS = 80
MAX_PRICE_TOKENS = 200
MIN_FOLLOWERS = 3_000
MAX_FOLLOWERS = 180_000
MIN_CHARACTERS_WITH_RECENT_POST = 7  # ストーリー枠（24時間以内の投稿）に並ぶキャラ数の下限
MIN_SCHEDULED_POSTS = 6  # 予約投稿（未来日時）の下限。フィードが「生きて」更新され続けるため

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
LEAD_RE = re.compile(r"^[1-9][0-9]* (minutes|hours|days)$")


class SeedError(Exception):
    """入力データの不備。メッセージにファイル内の位置を含める。"""


# -----------------------------------------------------------------------------
# 固定UUID（RFC 4122 v4 の形式。値そのものは連番）
# -----------------------------------------------------------------------------
def character_uuid(index: int) -> str:
    """1始まりのキャラ番号 → 00000000-0000-4000-8000-0000000000c1 形式。"""
    return f"00000000-0000-4000-8000-{('c' + str(index)).rjust(12, '0')}"


def post_uuid(character_index: int, post_index: int) -> str:
    return f"00000000-0000-4000-8001-{character_index:010d}{post_index:02d}"


def comment_uuid(character_index: int, post_index: int, comment_index: int, reply_index: int) -> str:
    return f"00000000-0000-4000-8002-{character_index:06d}{post_index:02d}{comment_index:02d}{reply_index:02d}"


def opaque_image_key(kind: str, slug: str) -> str:
    """有料投稿の画像シード。slug を含まず、プレビュー（kind="preview"）と本体（kind="private"）で無関係な値にする。

    クライアントに見えるのはプレビューの URL だけなので、そこから本体の URL を導けないようにする（出力は決定的）。
    本番の Bunny オブジェクトキーも同じ方針で、previews/<uuid>.jpg と private/<別の uuid>.jpg のように
    互いに推測できないキーを使う（連番・slug・同じ uuid の使い回しは不可）。
    """
    return hashlib.sha256(f"everkano-seed/{kind}/{slug}".encode()).hexdigest()[:24]


# -----------------------------------------------------------------------------
# データモデル
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class Schedule:
    day_offset: int | None
    local_time: str | None
    allowed_isodow: tuple[int, ...]
    lead: str | None

    @property
    def is_scheduled(self) -> bool:
        """未来日時の予約投稿か。"""
        return self.lead is not None or (self.day_offset is not None and self.day_offset > 0)

    @property
    def is_recent(self) -> bool:
        """必ず24時間以内に公開済みになる投稿か（day: 0）。"""
        return self.day_offset == 0


@dataclass(frozen=True)
class Reply:
    id: str
    after: int
    body: str


@dataclass(frozen=True)
class Comment:
    id: str
    author_key: str
    after: int
    body: str
    replies: tuple[Reply, ...]


@dataclass(frozen=True)
class Post:
    id: str
    slug: str
    schedule: Schedule
    likes: int
    price: int
    caption: str
    comments: tuple[Comment, ...]

    @property
    def is_paid(self) -> bool:
        return self.price > 0

    @property
    def image_url(self) -> str:
        """posts.image_url。無料投稿は本体画像、有料投稿は別に用意した低解像度のぼかしプレビュー。

        有料投稿のプレビューは本体の URL から派生させない（例: 本体URL + "?blur=10" はクエリを外すだけで
        本体に到達できるので不可）。本体は private_image_url（post_private_assets）に置く。
        """
        if not self.is_paid:
            return f"https://picsum.photos/seed/{self.slug}/1080/1080"
        return f"https://picsum.photos/seed/{opaque_image_key('preview', self.slug)}/400/400?blur=10"

    @property
    def private_image_url(self) -> str:
        """post_private_assets.image_url（有料投稿の本体画像。クライアントから到達不能）。"""
        return f"https://picsum.photos/seed/{opaque_image_key('private', self.slug)}/1080/1080"


@dataclass(frozen=True)
class Character:
    index: int
    key: str
    persona: dict[str, Any]
    follower_count: int
    joined_days_ago: int
    posts: tuple[Post, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        return character_uuid(self.index)


# -----------------------------------------------------------------------------
# 読み込み
# -----------------------------------------------------------------------------
def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_personas() -> dict[str, dict[str, Any]]:
    personas: dict[str, dict[str, Any]] = {}
    for path in sorted(PACKAGE_DIR.glob("*.yaml")):
        data = load_yaml(path)
        if not isinstance(data, dict):
            raise SeedError(f"{path.name}: YAML のトップレベルがマッピングではありません")
        personas[path.stem] = data
    return personas


def _require_int(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SeedError(f"{where}: 整数を指定してください（{value!r}）")
    return value


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeedError(f"{where}: 空でない文字列を指定してください（{value!r}）")
    return value.strip()


def _parse_schedule(raw: Any, where: str) -> Schedule:
    if not isinstance(raw, dict):
        raise SeedError(f"{where}.at: マッピングを指定してください")
    if "lead" in raw:
        if set(raw) != {"lead"}:
            raise SeedError(f"{where}.at: lead は単独で指定してください")
        lead = _require_str(raw["lead"], f"{where}.at.lead")
        if not LEAD_RE.match(lead):
            raise SeedError(f"{where}.at.lead: 「3 hours」のように書いてください（{lead!r}）")
        return Schedule(day_offset=None, local_time=None, allowed_isodow=ALL_DAYS, lead=lead)

    unknown = set(raw) - {"day", "time", "days"}
    if unknown:
        raise SeedError(f"{where}.at: 未知のキー {sorted(unknown)}")
    day = _require_int(raw.get("day"), f"{where}.at.day")
    if not -30 <= day <= 30:
        raise SeedError(f"{where}.at.day: -30〜30 の範囲で指定してください")
    time = _require_str(raw.get("time"), f"{where}.at.time")
    if not TIME_RE.match(time):
        raise SeedError(f"{where}.at.time: HH:MM（00:00〜23:59）で指定してください（{time!r}）")

    days_raw = raw.get("days")
    allowed: tuple[int, ...]
    if days_raw is None:
        allowed = ALL_DAYS
    elif isinstance(days_raw, str):
        if days_raw not in DAY_ALIASES:
            raise SeedError(f"{where}.at.days: weekday / weekend / [1..7] のいずれか（{days_raw!r}）")
        allowed = DAY_ALIASES[days_raw]
    elif isinstance(days_raw, list) and days_raw:
        allowed = tuple(sorted({_require_int(d, f"{where}.at.days") for d in days_raw}))
        if not all(1 <= d <= 7 for d in allowed):
            raise SeedError(f"{where}.at.days: ISO曜日（1=月〜7=日）で指定してください")
    else:
        raise SeedError(f"{where}.at.days: weekday / weekend / [1..7] のいずれか")
    if day == 0 and allowed != ALL_DAYS:
        raise SeedError(f"{where}.at: day: 0（24時間以内の投稿）には days を指定できません")
    return Schedule(day_offset=day, local_time=time, allowed_isodow=allowed, lead=None)


def _parse_post(
    raw: Any,
    *,
    owner_key: str,
    character_index: int,
    post_index: int,
    persona_keys: set[str],
) -> Post:
    where = f"feed.yaml posts.{owner_key}[{post_index - 1}]"
    if not isinstance(raw, dict):
        raise SeedError(f"{where}: マッピングを指定してください")
    unknown = set(raw) - {"slug", "at", "likes", "price", "caption", "comments"}
    if unknown:
        raise SeedError(f"{where}: 未知のキー {sorted(unknown)}")

    slug = _require_str(raw.get("slug"), f"{where}.slug")
    if not SLUG_RE.match(slug):
        raise SeedError(f"{where}.slug: 英小文字・数字・ハイフンのみ（{slug!r}）")
    schedule = _parse_schedule(raw.get("at"), where)
    likes = _require_int(raw.get("likes"), f"{where}.likes")
    if likes < 0:
        raise SeedError(f"{where}.likes: 0以上を指定してください")
    price = 0
    if "price" in raw:
        price = _require_int(raw["price"], f"{where}.price")
        if not MIN_PRICE_TOKENS <= price <= MAX_PRICE_TOKENS:
            raise SeedError(f"{where}.price: {MIN_PRICE_TOKENS}〜{MAX_PRICE_TOKENS} で指定してください")
    caption = _require_str(raw.get("caption"), f"{where}.caption")
    if not 1 <= len(caption.splitlines()) <= 4:
        raise SeedError(f"{where}.caption: 1〜4行で書いてください")

    comments_raw = raw.get("comments")
    if not isinstance(comments_raw, list):
        raise SeedError(f"{where}.comments: リストを指定してください")
    comments: list[Comment] = []
    total = 0
    for c_index, c_raw in enumerate(comments_raw, start=1):
        c_where = f"{where}.comments[{c_index - 1}]"
        if not isinstance(c_raw, dict):
            raise SeedError(f"{c_where}: マッピングを指定してください")
        unknown = set(c_raw) - {"by", "after", "body", "replies"}
        if unknown:
            raise SeedError(f"{c_where}: 未知のキー {sorted(unknown)}")
        author = _require_str(c_raw.get("by"), f"{c_where}.by")
        if author not in persona_keys:
            raise SeedError(f"{c_where}.by: 存在しないキャラ key {author!r}")
        if author == owner_key:
            raise SeedError(f"{c_where}.by: 投稿者本人のコメントは replies に書いてください")
        after = _require_int(c_raw.get("after"), f"{c_where}.after")
        if not 1 <= after <= MAX_COMMENT_AFTER_MINUTES:
            raise SeedError(f"{c_where}.after: 1〜{MAX_COMMENT_AFTER_MINUTES} 分で指定してください")
        body = _require_str(c_raw.get("body"), f"{c_where}.body")
        if len(body) > 200:
            raise SeedError(f"{c_where}.body: 200文字以内にしてください")

        replies: list[Reply] = []
        for r_index, r_raw in enumerate(c_raw.get("replies") or [], start=1):
            r_where = f"{c_where}.replies[{r_index - 1}]"
            if not isinstance(r_raw, dict) or set(r_raw) - {"after", "body"}:
                raise SeedError(f"{r_where}: after と body だけを指定してください")
            r_after = _require_int(r_raw.get("after"), f"{r_where}.after")
            if not after < r_after <= MAX_COMMENT_AFTER_MINUTES:
                raise SeedError(f"{r_where}.after: 親コメント（{after}分）より後、{MAX_COMMENT_AFTER_MINUTES}分以内")
            r_body = _require_str(r_raw.get("body"), f"{r_where}.body")
            if len(r_body) > 200:
                raise SeedError(f"{r_where}.body: 200文字以内にしてください")
            replies.append(
                Reply(
                    id=comment_uuid(character_index, post_index, c_index, r_index),
                    after=r_after,
                    body=r_body,
                )
            )
        comments.append(
            Comment(
                id=comment_uuid(character_index, post_index, c_index, 0),
                author_key=author,
                after=after,
                body=body,
                replies=tuple(replies),
            )
        )
        total += 1 + len(replies)
    if not MIN_COMMENTS_PER_POST <= total <= MAX_COMMENTS_PER_POST:
        raise SeedError(
            f"{where}.comments: 返信を含めて {MIN_COMMENTS_PER_POST}〜{MAX_COMMENTS_PER_POST} 件に"
            f"してください（{total}件）"
        )

    post = Post(
        id=post_uuid(character_index, post_index),
        slug=slug,
        schedule=schedule,
        likes=likes,
        price=price,
        caption=caption,
        comments=tuple(comments),
    )
    if post.is_paid:
        _check_private_asset_unguessable(post, where)
    return post


def _check_private_asset_unguessable(post: Post, where: str) -> None:
    """有料投稿のプレビュー URL（クライアントに見える）から本体の URL を導けないことを確認する。"""
    preview = urlsplit(post.image_url)
    private = urlsplit(post.private_image_url)
    # 本体 URL のうち固有の部分（seed の値など。"seed" や "1080" のような共通部分は除く）
    private_tokens = [segment for segment in private.path.split("/") if len(segment) >= 8]
    if (
        post.image_url == post.private_image_url
        or (preview.netloc, preview.path) == (private.netloc, private.path)  # クエリを外すだけで本体になる
        or any(token in post.image_url for token in private_tokens)
        or post.slug in post.image_url
        or post.slug in post.private_image_url
    ):
        raise SeedError(
            f"{where}: 有料投稿のプレビュー URL から本体の URL を推測できます。"
            "プレビューは本体と無関係なキーの低解像度画像にしてください"
        )


def load_characters() -> list[Character]:
    """feed.yaml とペルソナYAMLを読み込み、整合性を検証して Character のリストを返す。"""
    personas = load_personas()
    feed = load_yaml(FEED_PATH)
    if not isinstance(feed, dict):
        raise SeedError("feed.yaml: トップレベルがマッピングではありません")
    chars_raw = feed.get("characters")
    posts_raw = feed.get("posts")
    if not isinstance(chars_raw, list) or not isinstance(posts_raw, dict):
        raise SeedError("feed.yaml: characters（リスト）と posts（マッピング）が必要です")

    persona_keys = set(personas)
    order = [
        _require_str(c.get("key") if isinstance(c, dict) else None, "feed.yaml characters[].key") for c in chars_raw
    ]
    if len(order) != len(set(order)):
        raise SeedError("feed.yaml characters: key が重複しています")
    if set(order) != persona_keys:
        missing = sorted(persona_keys - set(order))
        extra = sorted(set(order) - persona_keys)
        raise SeedError(
            f"feed.yaml characters とペルソナYAMLが一致しません（feed に無い: {missing} / YAML が無い: {extra}）"
        )
    if set(posts_raw) != persona_keys:
        raise SeedError("feed.yaml posts: すべてのキャラの投稿を定義してください")

    characters: list[Character] = []
    seen_slugs: set[str] = set()
    for index, c_raw in enumerate(chars_raw, start=1):
        key = order[index - 1]
        where = f"feed.yaml characters[{index - 1}]"
        age = personas[key].get("age")
        if not isinstance(age, int) or isinstance(age, bool) or age < 20:
            # 全キャラ成人（20歳以上）。詳細な検証は validate_personas.py が行う
            raise SeedError(f"{key}.yaml: age は 20 以上の整数にしてください（{age!r}）")
        followers = _require_int(c_raw.get("follower_count"), f"{where}.follower_count")
        if not MIN_FOLLOWERS <= followers <= MAX_FOLLOWERS:
            raise SeedError(f"{where}.follower_count: {MIN_FOLLOWERS}〜{MAX_FOLLOWERS} にしてください")
        joined = _require_int(c_raw.get("joined_days_ago"), f"{where}.joined_days_ago")
        if joined < 30:
            raise SeedError(f"{where}.joined_days_ago: 投稿より前（30日以上前）にしてください")

        raw_posts = posts_raw[key]
        if not isinstance(raw_posts, list):
            raise SeedError(f"feed.yaml posts.{key}: リストを指定してください")
        posts = tuple(
            _parse_post(
                p,
                owner_key=key,
                character_index=index,
                post_index=p_index,
                persona_keys=persona_keys,
            )
            for p_index, p in enumerate(raw_posts, start=1)
        )
        if not MIN_POSTS_PER_CHARACTER <= len(posts) <= MAX_POSTS_PER_CHARACTER:
            raise SeedError(f"feed.yaml posts.{key}: 投稿は {MIN_POSTS_PER_CHARACTER}〜{MAX_POSTS_PER_CHARACTER} 件")
        paid = sum(1 for p in posts if p.is_paid)
        if not MIN_PAID_PER_CHARACTER <= paid <= MAX_PAID_PER_CHARACTER:
            raise SeedError(f"feed.yaml posts.{key}: 有料投稿は {MIN_PAID_PER_CHARACTER}〜{MAX_PAID_PER_CHARACTER} 件")
        for p in posts:
            if p.slug in seen_slugs:
                raise SeedError(f"feed.yaml: slug {p.slug!r} が重複しています")
            seen_slugs.add(p.slug)
        characters.append(
            Character(
                index=index,
                key=key,
                persona=personas[key],
                follower_count=followers,
                joined_days_ago=joined,
                posts=posts,
            )
        )

    recent = sum(1 for c in characters if any(p.schedule.is_recent for p in c.posts))
    if recent < MIN_CHARACTERS_WITH_RECENT_POST:
        raise SeedError(
            f"feed.yaml: day: 0 の投稿を持つキャラが {recent} 体です（{MIN_CHARACTERS_WITH_RECENT_POST} 体以上必要）"
        )
    scheduled = sum(1 for c in characters for p in c.posts if p.schedule.is_scheduled)
    if scheduled < MIN_SCHEDULED_POSTS:
        raise SeedError(f"feed.yaml: 予約投稿が {scheduled} 件です（{MIN_SCHEDULED_POSTS} 件以上必要）")
    return characters


# -----------------------------------------------------------------------------
# system_prompt（YAML が読めない場合のフォールバック用の静的テキスト）
# -----------------------------------------------------------------------------
# 仕様書 §8.2 の「制約」。validate_personas.py はこの行を未成年表現チェックの対象外にする
PROMPT_CONSTRAINTS = (
    "- 返答は1〜3文を基本とする（長文を求められた場合を除く）",
    "- 設定から外れた振る舞いをしない",
    "- 相手を傷つける表現、差別的表現、実在人物への言及をしない",
    "- 未成年を想起させる表現を一切しない",
    "- お金を払うこと・有料コンテンツを、好意・仲直り・関係の継続と結びつけない",
)


def render_system_prompt(persona: dict[str, Any]) -> str:
    speech = persona["speech"]
    relationship = persona["relationship"]
    examples = "\n".join(f"  - {line}" for line in speech["examples"])
    ng_words = "、".join(speech.get("ng_words") or []) or "（なし）"
    lines = [
        f"あなたは「{persona['name']}」という人物です。以下の設定に完全になりきってください。",
        # E3（キャラクターエンジン v1.0）: 役は演じ続けてよいが、実在の人間だとは主張しない
        "自分が実在する人間だと主張しないでください。本気でAIかどうかを聞かれたら、役柄の口調のまま、"
        "AIキャラクターであると正直に答えてください。",
        "",
        "# 人物設定",
        f"名前: {persona['name']} / 年齢: {persona['age']}歳（成人） / タイプ: {persona['archetype']}",
        persona["profile"].strip(),
        "",
        "# 話し方",
        f"- 一人称: {speech['first_person']} / 相手の呼び方: {speech['second_person']}",
        f"- 口調: {speech['tone']}",
        f"- 文の長さ: {speech['sentence_length']}",
        f"- 絵文字: {speech['emoji']}",
        f"- 使ってはいけない言葉: {ng_words}",
        "- 話し方の例:",
        examples,
        "",
        "# 関係性",
        f"- はじめ: {relationship['initial']}",
        f"- 変化: {relationship['progression']}",
        "",
        "# 日々の予定",
        persona["schedule_pattern"].strip(),
        "",
        "# 制約",
        *PROMPT_CONSTRAINTS,
    ]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# SQL 生成
# -----------------------------------------------------------------------------
def sql_str(value: str) -> str:
    if "\x00" in value:
        raise SeedError("NUL 文字は使えません")
    return "'" + value.replace("'", "''") + "'"


def sql_nullable(value: str | None) -> str:
    return "null" if value is None else sql_str(value)


def sql_int_or_null(value: int | None) -> str:
    return "null" if value is None else str(value)


def _join_rows(rows: list[str]) -> str:
    return ",\n".join(rows) + ";"


HEADER = f"""\
-- =============================================================================
-- everkano (Project P MVP) — シードデータ
--
-- !!! このファイルは自動生成です。直接編集しないでください !!!
--   生成元 : packages/personas/*.yaml          … キャラクター設定（ペルソナ）
--            packages/personas/seed/feed.yaml  … 投稿・コメント・フォロワー数
--   再生成 : pnpm --filter @everkano/personas seed:generate
--   検証   : pnpm --filter @everkano/personas validate
--   反映   : pnpm db:reset（= supabase db reset。マイグレーション後に postgres ロールで実行され RLS はバイパス）
--
-- 内容:
--   * characters           10体。固定UUID 00000000-0000-4000-8000-0000000000c1 〜 …-000000000c10
--                          system_prompt は YAML から組み立てた静的テキスト（YAML が読めない場合のフォールバック）
--   * posts                各キャラ5件。固定UUID 00000000-0000-4000-8001-<キャラ番号10桁><投稿番号2桁>
--                          各キャラ1件が有料投稿（is_paid / price_tokens 80〜200）
--                          published_at は now() 基準の相対値で、日本時間（{TIMEZONE}）でキャラの生活リズムに
--                          合う時刻に置く。24時間以内の投稿（ストーリー枠）と、未来日時の予約投稿を含む
--                          （予約投稿は RLS により公開時刻まで見えない → フィードが時間とともに更新される）
--   * post_private_assets  有料投稿の本体画像（クライアントからは到達不能）
--   * comments             各投稿2〜5件。キャラ同士のコメントと、投稿者本人の返信（parent_comment_id）
--                          固定UUID 00000000-0000-4000-8002-…。comment_count はトリガーが自動更新する
--
-- 画像URLについて:
--   avatar_url / image_url は開発用のプレースホルダURL（api.dicebear.com のイラスト / picsum.photos）。
--   Web は StorageAdapter（NEXT_PUBLIC_STORAGE_DRIVER）経由で解決する。http(s) の絶対URLはそのまま使われ、
--   本番（bunny ドライバ）ではここをオブジェクトキー（例: characters/misaki/avatar.jpg）に置き換える。
--   有料投稿の posts.image_url は別に用意した低解像度のぼかしプレビューで、本体は post_private_assets に置く。
--   プレビューと本体は互いに推測できない別キーにする（本体URL + "?blur=10" のようにクエリを外すだけで本体に
--   届く形は不可）。本番の Bunny でも previews/<uuid>.jpg と private/<別の uuid>.jpg のように分けること。
--
-- BEGIN/COMMIT は書かない（supabase CLI 側で制御する）。
-- =============================================================================
"""


def render_seed_sql(characters: list[Character]) -> str:
    parts: list[str] = [HEADER]

    # ---- characters -----------------------------------------------------------
    char_rows: list[str] = []
    for c in characters:
        p = c.persona
        char_rows.append(
            f"  -- {p['name']}（{p['archetype']}） packages/personas/{c.key}.yaml\n"
            f"  ({sql_str(c.id)}, {sql_str(p['handle'])}, {sql_str(p['name'])}, {sql_str(c.key)}, "
            f"{c.follower_count},\n"
            f"   {sql_str(p['avatar'])},\n"
            f"   {sql_str(p['bio'].strip())},\n"
            f"   {sql_str(render_system_prompt(p))},\n"
            f"   now() - interval '{c.joined_days_ago} days')"
        )
    parts.append(
        "\n-- -----------------------------------------------------------------------------\n"
        "-- characters\n"
        "-- -----------------------------------------------------------------------------\n"
        "insert into public.characters\n"
        "  (id, handle, name, persona_key, follower_count, avatar_url, bio, system_prompt, created_at)\n"
        "values\n" + _join_rows(char_rows) + "\n"
    )

    # ---- posts ------------------------------------------------------------------
    post_rows: list[str] = []
    for c in characters:
        post_rows.append(f"    -- {c.persona['name']}（{c.persona['handle']}）")
        for post in c.posts:
            s = post.schedule
            isodow = "{" + ",".join(str(d) for d in s.allowed_isodow) + "}"
            post_rows.append(
                f"    ({sql_str(post.id)}, {sql_str(c.id)},\n"
                f"     {sql_str(post.image_url)},\n"
                f"     {sql_str(post.caption)},\n"
                f"     {post.price}, {post.likes}, {sql_int_or_null(s.day_offset)}, "
                f"{sql_nullable(s.local_time)}, {sql_str(isodow)}, {sql_nullable(s.lead)})"
            )
    # コメント行（-- で始まる行）の後ろにカンマが付かないよう、値の行だけを連結する
    value_lines: list[str] = []
    pending_comments: list[str] = []
    for row in post_rows:
        if row.lstrip().startswith("--"):
            pending_comments.append(row)
            continue
        value_lines.append("\n".join([*pending_comments, row]))
        pending_comments = []
    values_sql = ",\n".join(value_lines)
    parts.append(
        f"""
-- -----------------------------------------------------------------------------
-- posts
--   day_offset / local_time / allowed_isodow / lead_time から published_at を計算する（日本時間基準）:
--     lead_time あり   : now() + lead_time（時刻を問わない内容の予約投稿）
--     day_offset = 0   : 直近に過ぎた local_time（= 必ず24時間以内）
--     day_offset < 0   : (今日 + day_offset) 以前で allowed_isodow に当たる最も近い日の local_time
--     day_offset > 0   : (今日 + day_offset) 以降で allowed_isodow に当たる最も近い日の local_time（予約投稿）
-- -----------------------------------------------------------------------------
with v (id, character_id, image_url, caption, price_tokens, like_count,
        day_offset, local_time, allowed_isodow, lead_time) as (
  values
{values_sql}
),
jst as (
  select (now() at time zone '{TIMEZONE}')::date as today
),
scheduled as (
  select
    v.*,
    (
      select jst.today + v.day_offset + (case when v.day_offset > 0 then k else -k end)
        from generate_series(0, 6) as k
       where v.day_offset = 0
          or extract(isodow from jst.today + v.day_offset + (case when v.day_offset > 0 then k else -k end))
             = any (v.allowed_isodow::int[])
       order by k
       limit 1
    ) as local_day
  from v
  cross join jst
),
resolved as (
  select
    s.*,
    case
      when s.lead_time is not null
        then now() + s.lead_time::interval
      when s.day_offset = 0
       and (s.local_day + s.local_time::time) at time zone '{TIMEZONE}' > now()
        then (s.local_day - 1 + s.local_time::time) at time zone '{TIMEZONE}'
      else (s.local_day + s.local_time::time) at time zone '{TIMEZONE}'
    end as published_at
  from scheduled as s
)
insert into public.posts
  (id, character_id, image_url, caption, is_paid, price_tokens, like_count, published_at, created_at)
select
  r.id::uuid,
  r.character_id::uuid,
  r.image_url,
  r.caption,
  r.price_tokens > 0,
  r.price_tokens,
  r.like_count,
  r.published_at,
  least(r.published_at, now())
from resolved as r;
"""
    )

    # ---- post_private_assets ----------------------------------------------------
    asset_rows = [
        f"  ({sql_str(post.id)}, {sql_str(post.private_image_url)})"
        for c in characters
        for post in c.posts
        if post.is_paid
    ]
    parts.append(
        "\n-- -----------------------------------------------------------------------------\n"
        "-- post_private_assets（有料投稿の本体画像。クライアント非公開）\n"
        "-- -----------------------------------------------------------------------------\n"
        "insert into public.post_private_assets (post_id, image_url)\n"
        "values\n" + _join_rows(asset_rows) + "\n"
    )

    # ---- comments ---------------------------------------------------------------
    by_key = {c.key: c for c in characters}
    top_rows: list[tuple[str, str]] = []
    reply_rows: list[tuple[str, str]] = []
    for c in characters:
        for post in c.posts:
            for comment in post.comments:
                author = by_key[comment.author_key]
                top_rows.append(
                    (
                        f"    ({sql_str(comment.id)}, {sql_str(post.id)}, {sql_str(author.id)}, "
                        f"{comment.after}, {sql_str(comment.body)})",
                        f"{author.persona['name']} → {post.slug}",
                    )
                )
                for reply in comment.replies:
                    reply_rows.append(
                        (
                            f"    ({sql_str(reply.id)}, {sql_str(post.id)}, {sql_str(comment.id)}, "
                            f"{sql_str(c.id)}, {reply.after}, {sql_str(reply.body)})",
                            f"{c.persona['name']}（投稿者）の返信 → {post.slug}",
                        )
                    )

    def _rows_with_trailing_comments(rows: list[tuple[str, str]]) -> str:
        # 「値,  -- 説明」の形にする（行末コメントの前にカンマを置く）
        out: list[str] = []
        for i, (value, note) in enumerate(rows):
            sep = "," if i < len(rows) - 1 else ""
            out.append(f"{value}{sep}  -- {note}")
        return "\n".join(out)

    created_at_expr = (
        "p.published_at + make_interval(mins => v.after_minutes) * (\n"
        "         case\n"
        "           when p.published_at > now() then 1.0\n"
        "           else least(1.0, extract(epoch from now() - p.published_at) / "
        f"{COMMENT_COMPRESS_WINDOW_SECONDS}.0)\n"
        "         end\n"
        "       )::float8"
    )
    parts.append(
        f"""
-- -----------------------------------------------------------------------------
-- comments（キャラ同士のコメント）
--   created_at = 投稿の published_at + after_minutes。
--   公開から{MAX_COMMENT_AFTER_MINUTES // 60}時間未満の投稿は、未来時刻のコメントに
--   ならないよう経過時間に比例して圧縮する。
-- -----------------------------------------------------------------------------
with v (id, post_id, author_character_id, after_minutes, body) as (
  values
{_rows_with_trailing_comments(top_rows)}
)
insert into public.comments (id, post_id, author_type, author_character_id, body, created_at)
select
  v.id::uuid,
  v.post_id::uuid,
  'character',
  v.author_character_id::uuid,
  v.body,
  {created_at_expr}
from v
join public.posts as p on p.id = v.post_id::uuid;

-- -----------------------------------------------------------------------------
-- comments（投稿者本人からの返信。parent_comment_id 付き）
-- -----------------------------------------------------------------------------
with v (id, post_id, parent_comment_id, author_character_id, after_minutes, body) as (
  values
{_rows_with_trailing_comments(reply_rows)}
)
insert into public.comments
  (id, post_id, parent_comment_id, author_type, author_character_id, body, created_at)
select
  v.id::uuid,
  v.post_id::uuid,
  v.parent_comment_id::uuid,
  'character',
  v.author_character_id::uuid,
  v.body,
  {created_at_expr}
from v
join public.posts as p on p.id = v.post_id::uuid;
"""
    )
    return "".join(parts)


def render() -> str:
    return render_seed_sql(load_characters())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="seed.sql が最新か確認する（書き込まない）")
    mode.add_argument("--stdout", action="store_true", help="seed.sql を書かずに標準出力へ出す")
    args = parser.parse_args(argv)

    try:
        sql = render()
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.stdout:
        sys.stdout.write(sql)
        return 0
    rel = SEED_SQL_PATH.relative_to(REPO_ROOT)
    if args.check:
        current = SEED_SQL_PATH.read_text(encoding="utf-8") if SEED_SQL_PATH.exists() else ""
        if current != sql:
            print(f"ERROR: {rel} が最新ではありません。seed:generate を実行してください", file=sys.stderr)
            return 1
        print(f"OK: {rel} は最新です")
        return 0
    SEED_SQL_PATH.write_text(sql, encoding="utf-8")
    print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
