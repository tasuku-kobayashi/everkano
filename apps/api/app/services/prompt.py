"""`packages/prompts/templates/*.ja.txt` を読み込み、LLM に渡すメッセージ列へ描画する（仕様 §8.2）。

テンプレート書式（詳細は packages/prompts/README.md）:
- `{placeholder}`（英小文字・数字・_）を値で置換する。JSON 例の `{"key": ...}` は置換対象外。
- 行 `=== user ===` があれば、それより前を system メッセージ、後ろを user メッセージとして扱う。
- 起動時に必須プレースホルダの有無と未知のプレースホルダを検証する（typo を早期検出）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, TypedDict
from zoneinfo import ZoneInfo

from app.engine.types import (
    MEMORY_KIND_LABELS_JA,
    CharacterMemoryItem,
    CharacterStateSnapshot,
    ContextBundle,
    MemoryItem,
    PromiseItem,
    RelationshipGuidance,
    WorldState,
)
from app.services.persona import Persona
from app.services.types import HistoryItem, RetrievedMemory

JST: Final = ZoneInfo("Asia/Tokyo")
SECRET_MARKER: Final[str] = "（二人だけの秘密）"  # noqa: S105 - 表示用マーカー
SUMMARY_MARKER: Final[str] = "（これまでの会話の要約）"
USER_SEPARATOR: Final[str] = "=== user ==="
_PLACEHOLDER_RE: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# 改行の類（CR/LF・Unicode の行区切り等）。利用者由来の文章を1行にまとめるのに使う
_LINE_BREAKS_RE: Final = re.compile(r"[\r\n\v\f\x85\u2028\u2029]+")
_WEEKDAYS_JA: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")

# DM の応答生成に渡す履歴（短期メモリ）の文字数の上限。直近 MEMORY_SHORT_TERM_TURNS ターン（60 件 × 最大 2000 字）を
# 全文で渡すと、長文を貼り付ける利用者のプロンプトが数万字に膨らみ（応答が遅く・高くなる）、コンテキストの小さい
# モデルでは 400（再試行しない）で以後その会話が送れなくなる。新しい側から数えて HISTORY_FULL_MESSAGES 件は全文、
# それより古い発言は HISTORY_OLDER_MESSAGE_MAX_CHARS 字に切り詰め、合計が HISTORY_MAX_CHARS を超える古い分は渡さない
# （今回の発言は別枠で必ず渡す。最大 2000 字）。
HISTORY_MAX_CHARS: Final[int] = 16000
HISTORY_FULL_MESSAGES: Final[int] = 6
HISTORY_OLDER_MESSAGE_MAX_CHARS: Final[int] = 500
_TRUNCATION_MARK: Final[str] = "…"

Role = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    required: frozenset[str]
    optional: frozenset[str] = frozenset()


TEMPLATE_SPECS: Final[dict[str, TemplateSpec]] = {
    "dm_system": TemplateSpec(
        required=frozenset({"name", "profile", "speech", "relationship", "memories", "short_term"}),
        optional=frozenset(
            {
                "first_person",
                "second_person",
                "schedule",
                "now",
                "archetype",
                "bio",
                # キャラクターエンジン v1.0（Context Assembler が組み立てる動的なセクション）
                "world",
                "state",
                "relationship_guidance",
                "call_user",
                "character_memories",
                "promises",
            }
        ),
    ),
    "memory_summary": TemplateSpec(
        required=frozenset({"conversation"}),
        optional=frozenset({"name"}),
    ),
    "comment_reply": TemplateSpec(
        required=frozenset({"name", "post_caption", "comment_body"}),
        optional=frozenset({"bio", "speech", "comment_style", "profile", "first_person", "second_person"}),
    ),
}


# 各モジュール（記憶・好感度・自発メッセージなど）が追加するテンプレートの検証仕様。
# モジュールは import 時に `register_template_spec()` で登録する（PromptBuilder.load_dir より前に import される）。
_EXTRA_SPECS: dict[str, TemplateSpec] = {}


def register_template_spec(name: str, *, required: Sequence[str], optional: Sequence[str] = ()) -> None:
    """追加のテンプレート（`packages/prompts/templates/<name>.ja.txt`）を必須として登録し、起動時に検証させる。"""
    if name in TEMPLATE_SPECS:
        raise ValueError(f"template spec '{name}' is reserved by the core")
    _EXTRA_SPECS[name] = TemplateSpec(required=frozenset(required), optional=frozenset(optional))


def template_specs() -> dict[str, TemplateSpec]:
    """検証するテンプレートの一覧（コア + モジュールが登録したもの）。"""
    return {**TEMPLATE_SPECS, **_EXTRA_SPECS}


class PromptTemplateError(Exception):
    """テンプレートの読込・検証エラー（起動を中止する）。"""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    system: str
    user: str | None

    @property
    def placeholders(self) -> frozenset[str]:
        text = self.system + (self.user or "")
        return frozenset(_PLACEHOLDER_RE.findall(text))

    def render(self, values: Mapping[str, str]) -> list[ChatMessage]:
        messages: list[ChatMessage] = [{"role": "system", "content": _substitute(self.system, values)}]
        if self.user is not None:
            messages.append({"role": "user", "content": _substitute(self.user, values)})
        return messages


def _substitute(text: str, values: Mapping[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PromptTemplateError(f"missing value for placeholder '{{{key}}}'")
        return values[key]

    return _PLACEHOLDER_RE.sub(repl, text).strip()


def parse_template(name: str, text: str) -> PromptTemplate:
    lines = text.splitlines()
    if USER_SEPARATOR in (line.strip() for line in lines):
        index = next(i for i, line in enumerate(lines) if line.strip() == USER_SEPARATOR)
        system = "\n".join(lines[:index])
        user: str | None = "\n".join(lines[index + 1 :])
    else:
        system, user = text, None
    template = PromptTemplate(name=name, system=system, user=user)
    spec = template_specs().get(name)
    if spec is not None:
        found = template.placeholders
        missing = spec.required - found
        unknown = found - spec.required - spec.optional
        if missing:
            raise PromptTemplateError(f"{name}: 必須プレースホルダがありません: {sorted(missing)}")
        if unknown:
            raise PromptTemplateError(f"{name}: 未知のプレースホルダがあります: {sorted(unknown)}")
    return template


# ---------------------------------------------------------------------------
# 描画ヘルパー
# ---------------------------------------------------------------------------


def format_now(now: datetime) -> str:
    local = now.astimezone(JST)
    return f"{local.year}年{local.month}月{local.day}日（{_WEEKDAYS_JA[local.weekday()]}）{local:%H:%M}"


def format_date(value: datetime) -> str:
    local = value.astimezone(JST)
    return f"{local.year}年{local.month}月{local.day}日（{_WEEKDAYS_JA[local.weekday()]}）"


def render_speech(persona: Persona) -> str:
    s = persona.speech
    lines: list[str] = []
    if s.tone:
        lines.append(f"- 口調: {s.tone}")
    if s.sentence_length:
        lines.append(f"- 文の長さ: {s.sentence_length}")
    if s.emoji:
        lines.append(f"- 絵文字: {s.emoji}")
    lines.append(
        f"- 一人称: 「{s.first_person}」 / 相手の呼び方（基本）: 「{s.second_person}」"
        "（呼び方の希望を覚えていればそちらを優先）"
    )
    if s.examples:
        lines.append("- 話し方の例:")
        lines.extend(f"  - 「{ex}」" for ex in s.examples)
    if s.ng_words:
        lines.append("- 絶対に使わない言葉: " + "、".join(f"「{w}」" for w in s.ng_words))
    return "\n".join(lines)


def render_relationship(persona: Persona) -> str:
    r = persona.relationship
    if not r.initial and not r.progression:
        return "（特になし）"
    return f"- はじめの距離感: {r.initial}\n- 関係の変化: {r.progression}"


def one_line(text: str) -> str:
    """利用者由来の文章を1行にする（改行で system プロンプトの見出し・箇条書きを偽造させない）。"""
    return _LINE_BREAKS_RE.sub(" ", text).strip()


def render_memory_line(memory: RetrievedMemory) -> str:
    """記憶1件。末尾に記録日（日本時間）を付け、「来週」「明日」などを記録日基準で解釈できるようにする。

    記憶の本文はユーザーが書ける（メモリパネル）ため、改行を空白にして1行に収める
    （`# 制約` のような見出しを本文に書いて、system プロンプトの別セクションに見せかけることを防ぐ）。
    """
    prefix = ""
    if memory.is_summary:
        prefix += SUMMARY_MARKER
    if memory.is_secret:
        prefix += SECRET_MARKER
    return f"- {prefix}{one_line(memory.content)}（{format_date(memory.created_at)}に記録）"


def render_memories(memories: Sequence[RetrievedMemory]) -> str:
    if not memories:
        return "（まだ特にない）"
    return "\n".join(render_memory_line(m) for m in memories)


def render_schedule(persona: Persona, now: datetime) -> str:
    lines = [f"現在は{format_now(now)}（日本時間）。"]
    if persona.schedule_pattern:
        lines.append("あなたの普段の過ごし方:")
        lines.append(persona.schedule_pattern.strip())
    return "\n".join(lines)


def render_short_term_note(history: Sequence[HistoryItem], now: datetime | None = None) -> str:
    # 直近の会話そのものは system の後ろに user / assistant メッセージとして渡す（重複させない）
    if not history:
        return "（まだ会話はありません。これが二人の最初のやりとりです）"
    note = (
        f"直近{len(history)}件のやりとりは、この前の会話履歴（user = 相手 / assistant = あなた）"
        "のとおり。流れを踏まえて自然に続けてください。"
    )
    if now is not None:
        # 履歴のメッセージには時刻が無いため、前回から日が空いたことだけは明示する
        last = history[-1].created_at
        days = (now.astimezone(JST).date() - last.astimezone(JST).date()).days
        if days >= 1:
            note += (
                f"\n前回のやりとり（{format_date(last)}）から{days}日たっています。"
                "履歴の中の「明日」「来週」などは、その日を基準にした表現です。"
            )
    return note


# ---------------------------------------------------------------------------
# キャラクターエンジン v1.0 の動的セクション（Context Assembler の ContextBundle から描画する）
#   各関数の出力の文字数が Context Assembler のトークン予算（文字数で代用）の計測対象になる。
# ---------------------------------------------------------------------------

EMPTY_SECTION: Final[str] = "（特になし）"
_BUSYNESS_LABELS: Final[dict[int, str]] = {0: "ひま", 1: "ふつう", 2: "忙しい", 3: "手が離せない"}


def format_month_day(value: datetime) -> str:
    local = value.astimezone(JST)
    return f"{local.month}月{local.day}日（{_WEEKDAYS_JA[local.weekday()]}）"


def render_world(world: WorldState) -> str:
    """世界の時間（C1）。例: 2026年9月26日（土）21:30（日本時間）/ 秋・夜 / 休日 / 祝日: 秋分の日 / 行事: お月見"""
    parts = [f"{format_now(world.now)}（日本時間）", f"{world.season_ja}・{world.time_of_day_ja}"]
    if world.holiday_name:
        parts.append(f"祝日「{one_line(world.holiday_name)}」")
    elif world.is_day_off:
        parts.append("休日")
    else:
        parts.append("平日")
    if world.seasonal_labels_ja:
        parts.append("季節の行事: " + "・".join(one_line(label) for label in world.seasonal_labels_ja))
    return " / ".join(parts)


def render_state(state: CharacterStateSnapshot) -> str:
    """キャラの今の状態（C5 / C6）。忙しさは返答の長さの指針にだけ使う（課金の誘導には使わない）。"""
    where = f"（{one_line(state.location)}）" if state.location else ""
    lines = [f"- いましていること: {one_line(state.activity)}{where}"]
    if state.mood:
        lines.append(f"- 気分: {one_line(state.mood)}")
    busyness = _BUSYNESS_LABELS.get(state.busyness, "ふつう")
    lines.append(f"- 忙しさ: {busyness}。返答の仕方: {one_line(state.reply_style_hint)}")
    if state.next_event:
        lines.append(f"- このあと: {one_line(state.next_event)}")
    if state.recent_events:
        lines.append("- 最近の予定: " + " / ".join(one_line(e) for e in state.recent_events))
    return "\n".join(lines)


def render_state_fallback(persona: Persona) -> str:
    """カレンダーが無効・取得できないとき（評価ハーネスの素の LLM も含む）は、ペルソナの普段の過ごし方を渡す。"""
    if not persona.schedule_pattern:
        return EMPTY_SECTION
    return "あなたの普段の過ごし方（今の時間に合わせて自然に）:\n" + persona.schedule_pattern.strip()


def render_relationship_guidance(guidance: RelationshipGuidance) -> str:
    """ふたりの関係（A8）。段階の名前は内部の目安で、数値・段階そのものを相手に話さない。"""
    lines = [
        f"- 関係の段階（内部の目安。相手には言わない）: {guidance.stage_label_ja}",
        f"- 相手の呼び方: 「{one_line(guidance.call_user)}」",
        f"- 口調: {one_line(guidance.tone)}",
        f"- 好意の表し方: {one_line(guidance.affection)}",
    ]
    if guidance.topics:
        lines.append("- 話題にしやすいこと: " + "、".join(one_line(t) for t in guidance.topics))
    if guidance.examples:
        lines.append("- 話し方の例: " + " ".join(f"「{one_line(e)}」" for e in guidance.examples))
    lines.extend(f"- 気をつけること: {one_line(note)}" for note in guidance.notes)
    return "\n".join(lines)


def render_memory_item(memory: MemoryItem) -> str:
    """記憶1件（M5 / M10）。種類のラベルと記録日を付け、本文は1行にする（見出しの偽造を防ぐ）。"""
    prefix = f"[{MEMORY_KIND_LABELS_JA.get(memory.kind, memory.kind)}]"
    if memory.kind == "summary":
        prefix += SUMMARY_MARKER
    if memory.is_secret:
        prefix += SECRET_MARKER
    return f"- {prefix}{one_line(memory.content)}（{format_date(memory.created_at)}に記録）"


def render_memory_items(memories: Sequence[MemoryItem]) -> str:
    if not memories:
        return "（まだ特にない）"
    return "\n".join(render_memory_item(m) for m in memories)


def render_character_memory(memory: CharacterMemoryItem) -> str:
    label = "話したこと" if memory.kind == "self_statement" else "出来事"
    when = f" {format_month_day(memory.occurred_at)}" if memory.occurred_at is not None else ""
    return f"- [{label}{when}] {one_line(memory.content)}"


def render_character_memories(memories: Sequence[CharacterMemoryItem]) -> str:
    if not memories:
        return EMPTY_SECTION
    return "\n".join(render_character_memory(m) for m in memories)


def _relative_day(due: datetime, now: datetime) -> str:
    days = (due.astimezone(JST).date() - now.astimezone(JST).date()).days
    if days == 0:
        return "今日"
    if days == 1:
        return "明日"
    if days == -1:
        return "昨日"
    return f"{days}日後" if days > 0 else f"{-days}日前"


def render_promise(promise: PromiseItem, now: datetime) -> str:
    if promise.due_at is None or promise.due_precision == "unknown":
        when = "期日は未定"
    elif promise.due_precision == "datetime":
        local = promise.due_at.astimezone(JST)
        when = f"{format_month_day(promise.due_at)}{local:%H:%M}・{_relative_day(promise.due_at, now)}"
    elif promise.due_precision in ("week", "month"):
        span = "週" if promise.due_precision == "week" else "月"
        when = f"{format_month_day(promise.due_at)}の{span}ごろ"
    else:
        when = f"{format_month_day(promise.due_at)}・{_relative_day(promise.due_at, now)}"
    mentioned = "（もう話題にした）" if promise.status == "mentioned" else ""
    return f"- {one_line(promise.content)}（{when}）{mentioned}"


def render_promises(promises: Sequence[PromiseItem], now: datetime) -> str:
    if not promises:
        return EMPTY_SECTION
    return "\n".join(render_promise(p, now) for p in promises)


def default_call_user(persona: Persona) -> str:
    return persona.speech.second_person


def fit_chat_history(
    history: Sequence[HistoryItem],
    *,
    max_chars: int = HISTORY_MAX_CHARS,
    full_messages: int = HISTORY_FULL_MESSAGES,
    older_message_max_chars: int = HISTORY_OLDER_MESSAGE_MAX_CHARS,
) -> list[HistoryItem]:
    """応答生成に渡す履歴（古い順）を文字数の上限に収める（本文の合計 ≤ max_chars）。

    新しい側から数えて `full_messages` 件は全文のまま、それより古い発言は `older_message_max_chars` 字 + 「…」に
    切り詰める。合計が `max_chars` を超えるところで打ち切り、それより古い発言は渡さない。
    """
    kept: list[HistoryItem] = []
    total = 0
    for index, item in enumerate(reversed(history)):
        body = item.body
        if index >= full_messages and len(body) > older_message_max_chars:
            body = body[:older_message_max_chars] + _TRUNCATION_MARK
        if not kept and len(body) > max_chars:
            # 直前の1件だけで上限を超える（上限を小さく設定した場合など）→ 最新の文脈は切り詰めてでも残す
            body = body[: max(max_chars - len(_TRUNCATION_MARK), 0)] + _TRUNCATION_MARK
        if total + len(body) > max_chars:
            break
        total += len(body)
        kept.append(item if body == item.body else replace(item, body=body))
    kept.reverse()
    return kept


CONTEXT_OPEN: Final[str] = "〔今の状況〕"
CONTEXT_CLOSE: Final[str] = "〔/今の状況〕"
_CONTEXT_MARKER_RE: Final = re.compile(r"〔\s*/?\s*今の状況\s*〕")


def _defuse_context_markers(text: str) -> str:
    """相手の発言に〔今の状況〕の目印を書いてシステムの情報を偽造させない（目印だけを別の括弧に置き換える）。"""
    return _CONTEXT_MARKER_RE.sub(lambda m: m.group(0).replace("〔", "［").replace("〕", "］"), text)


def _merge_consecutive(messages: list[ChatMessage]) -> list[ChatMessage]:
    """同じ role が連続する場合は結合する（role の交互性を要求するプロバイダ対策）。"""
    merged: list[ChatMessage] = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"] and message["role"] != "system":
            merged[-1] = {
                "role": message["role"],
                "content": merged[-1]["content"] + "\n" + message["content"],
            }
        else:
            merged.append(message)
    return merged


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    def __init__(self, templates: Mapping[str, PromptTemplate]) -> None:
        missing = set(template_specs()) - set(templates)
        if missing:
            raise PromptTemplateError(f"テンプレートが見つかりません: {sorted(missing)}")
        self._templates = dict(templates)

    @classmethod
    def load_dir(cls, directory: Path) -> PromptBuilder:
        """`<name>.ja.txt` をすべて読み込む。検証仕様のあるもの（コア + 登録済み）は必須で、起動時に検証する。"""
        templates: dict[str, PromptTemplate] = {}
        names = set(template_specs())
        names.update(path.name.removesuffix(".ja.txt") for path in directory.glob("*.ja.txt"))
        for name in sorted(names):
            path = directory / f"{name}.ja.txt"
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PromptTemplateError(f"{path}: 読み込めません: {exc}") from exc
            templates[name] = parse_template(name, text)
        return cls(templates)

    def has_template(self, name: str) -> bool:
        return name in self._templates

    def render(self, name: str, values: Mapping[str, str]) -> list[ChatMessage]:
        """任意のテンプレートを描画する（各モジュールの分析・生成用プロンプト）。"""
        template = self._templates.get(name)
        if template is None:
            raise PromptTemplateError(f"テンプレートが見つかりません: {name}")
        return template.render(values)

    def chat_messages(
        self,
        persona: Persona,
        *,
        memories: Sequence[RetrievedMemory] = (),
        history: Sequence[HistoryItem],
        user_message: str,
        now: datetime,
        bundle: ContextBundle | None = None,
        history_max_chars: int = HISTORY_MAX_CHARS,
    ) -> list[ChatMessage]:
        """DM の応答生成に渡すメッセージ列。

        `bundle`（Context Assembler の出力）があれば、世界の時間・キャラの状態・ふたりの関係・記憶・キャラ側の記憶・
        約束をその内容で描画する（予算内に切り詰め済み）。無ければ MVP と同じ形（`memories` とペルソナの過ごし方）。
        順序は [system: 静的なペルソナ・守ること] → [直近の会話] → [user:〔今の状況〕+ 今回の発言]
        （DeepSeek のプレフィックスキャッシュが system と直近の会話まで効くように。動的な部分は最後）。
        """
        # 履歴は文字数の上限内に収める（プロンプトの大きさ = 応答の待ち時間・費用を利用者の入力量で青天井にしない）
        history = fit_chat_history(history, max_chars=history_max_chars)
        values = {
            "name": persona.name,
            "profile": persona.profile.strip(),
            "speech": render_speech(persona),
            "relationship": render_relationship(persona),
            "short_term": render_short_term_note(history, now),
            "first_person": persona.speech.first_person,
            "second_person": persona.speech.second_person,
            "schedule": render_schedule(persona, now),
            "now": format_now(now),
            "archetype": persona.archetype,
            "bio": persona.bio.strip(),
        }
        values.update(self._dynamic_sections(persona, memories=memories, bundle=bundle, now=now))
        rendered = self._templates["dm_system"].render(values)
        # DeepSeek のプレフィックスキャッシュが効く順（ADR 候補「プロンプトの順序」）:
        #   1. system = 静的なペルソナ・守ること（キャラごとに毎回同じ）
        #   2. 直近の会話（user / assistant。前回のリクエストの末尾まで同じ = キャッシュに当たる）
        #   3. 最新の user メッセージ = 〔今の状況〕（世界の時間・状態・関係・記憶・約束。毎回変わる）+ 今回の発言
        # テンプレートの `=== user ===` より後ろが 3 の〔今の状況〕（DeepSeek の chat template は system を
        # 先頭にまとめるため、動的な部分を後ろの system メッセージにはできない）。
        system, context = rendered[0], rendered[1]["content"] if len(rendered) > 1 else ""
        messages: list[ChatMessage] = [system]
        for item in history:
            role: Role = "user" if item.sender_type == "user" else "assistant"
            body = _defuse_context_markers(item.body) if role == "user" else item.body
            messages.append({"role": role, "content": body})
        latest = _defuse_context_markers(user_message)
        messages.append({"role": "user", "content": f"{context}\n{latest}" if context else latest})
        return _merge_consecutive(messages)

    @staticmethod
    def _dynamic_sections(
        persona: Persona,
        *,
        memories: Sequence[RetrievedMemory],
        bundle: ContextBundle | None,
        now: datetime,
    ) -> dict[str, str]:
        if bundle is None:
            return {
                "world": f"{format_now(now)}（日本時間）",
                "state": render_state_fallback(persona),
                "relationship_guidance": EMPTY_SECTION,
                "call_user": default_call_user(persona),
                "memories": render_memories(memories),
                "character_memories": EMPTY_SECTION,
                "promises": EMPTY_SECTION,
            }
        relationship = bundle.relationship
        return {
            "world": render_world(bundle.world),
            "state": render_state(bundle.state) if bundle.state is not None else render_state_fallback(persona),
            "relationship_guidance": (
                render_relationship_guidance(relationship) if relationship is not None else EMPTY_SECTION
            ),
            "call_user": relationship.call_user if relationship is not None else default_call_user(persona),
            "memories": render_memory_items(bundle.memory.memories),
            "character_memories": render_character_memories(bundle.memory.character_memories),
            "promises": render_promises(bundle.memory.promises, now),
        }

    def comment_reply_messages(
        self, persona: Persona, *, post_caption: str | None, comment_body: str
    ) -> list[ChatMessage]:
        values = {
            "name": persona.name,
            "bio": (persona.bio or persona.profile).strip(),
            "profile": persona.profile.strip(),
            "speech": render_speech(persona),
            "comment_style": persona.comment_style or "短く、親しみを込めて返す",
            "first_person": persona.speech.first_person,
            "second_person": persona.speech.second_person,
            "post_caption": one_line(post_caption or "") or "（キャプションなし）",
            # コメントは他の利用者が書いた文章。1行にまとめ、テンプレート側で「データとして扱う」ことを指示する
            "comment_body": one_line(comment_body),
        }
        return self._templates["comment_reply"].render(values)
