"""キャラクターエンジン v1.0 の共有型とモジュール間の契約（仕様 §3）。

各モジュール（memory / calendar / affinity / proactive / safety）と Context Assembler は、ここで定義する
値オブジェクトと Protocol だけを介してやり取りする。モジュールの内部実装に直接依存しないこと。

- 時刻はすべて timezone-aware な datetime（UTC）で受け渡す。キャラの世界は日本時間（JST）で動くため、
  表示・日付の判定には `to_jst()` を使う。
- 現在時刻は必ず `Clock` から得る（`datetime.now()` を直接呼ばない）。評価ハーネスは `ManualClock` で
  時間を早送りする（仕様 §9.1）。DB に保存する日時もアプリの時計から明示的に渡す。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

JST: Final = ZoneInfo("Asia/Tokyo")

# ---------------------------------------------------------------------------
# 時計（§9.1 時間の早送り）
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> datetime:
        """現在時刻（timezone-aware, UTC）。"""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """テスト・評価ハーネス用の手動時計。`advance()` で時間を進める。"""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware datetime")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware datetime")
        self._now = value.astimezone(UTC)

    def advance(self, delta: timedelta) -> datetime:
        self._now = self._now + delta
        return self._now


def to_jst(value: datetime) -> datetime:
    return value.astimezone(JST)


def jst_date(value: datetime) -> date:
    return value.astimezone(JST).date()


# ---------------------------------------------------------------------------
# 語彙（DB の check 制約・ペルソナ YAML と一致させる）
# ---------------------------------------------------------------------------

MemoryKind = Literal["fact", "preference", "episode", "promise", "emotion", "relationship", "summary"]
MEMORY_KINDS: Final[tuple[str, ...]] = (
    "fact",
    "preference",
    "episode",
    "promise",
    "emotion",
    "relationship",
    "summary",
)
MEMORY_KIND_LABELS_JA: Final[dict[str, str]] = {
    "fact": "事実",
    "preference": "好み",
    "episode": "出来事",
    "promise": "約束・予定",
    "emotion": "気持ち",
    "relationship": "ふたりの関係",
    "summary": "会話の要約",
}

PromiseStatus = Literal["pending", "mentioned", "done", "cancelled"]
DuePrecision = Literal["datetime", "day", "week", "month", "unknown"]

EventKind = Literal["routine", "oneoff", "seasonal", "promise"]
# 0=暇 1=ふつう 2=忙しい 3=手が離せない（睡眠など）
Busyness = Literal[0, 1, 2, 3]

AffinityAxis = Literal["closeness", "trust", "romance", "awkwardness", "discontent", "possessiveness"]
AFFINITY_AXES: Final[tuple[str, ...]] = (
    "closeness",
    "trust",
    "romance",
    "awkwardness",
    "discontent",
    "possessiveness",
)
POSITIVE_AXES: Final[tuple[str, ...]] = ("closeness", "trust", "romance")
TENSION_AXES: Final[tuple[str, ...]] = ("awkwardness", "discontent")

Stage = Literal["acquaintance", "friend", "close", "lover"]
STAGES: Final[tuple[str, ...]] = ("acquaintance", "friend", "close", "lover")
STAGE_LABELS_JA: Final[dict[str, str]] = {
    "acquaintance": "知り合い",
    "friend": "友達",
    "close": "気になる人",
    "lover": "恋人",
}

ProactiveTrigger = Literal["calendar_event", "promise_due", "seasonal", "inactivity", "feed_post", "paid_notice"]
PROACTIVE_TRIGGERS: Final[tuple[str, ...]] = (
    "calendar_event",
    "promise_due",
    "seasonal",
    "inactivity",
    "feed_post",
    "paid_notice",
)

# 季節・行事のキー（C4）。日付の定義は app/engine/calendar/ が持つ。ペルソナ YAML の engine.seasonal[].key と一致させる
SEASONAL_KEYS: Final[tuple[str, ...]] = (
    "new_year",  # 年始（1/1〜1/3）
    "setsubun",  # 節分（2/3 前後）
    "valentine",  # バレンタイン（2/14）
    "white_day",  # ホワイトデー（3/14）
    "hanami",  # 花見（3 月下旬〜4 月上旬）
    "golden_week",  # ゴールデンウィーク
    "tsuyu",  # 梅雨
    "tanabata",  # 七夕（7/7）
    "summer_festival",  # 夏祭り・花火（7〜8 月）
    "obon",  # お盆
    "tsukimi",  # お月見（中秋の名月）
    "halloween",  # ハロウィン（10/31）
    "autumn_leaves",  # 紅葉
    "christmas",  # クリスマス（12/24〜25）
    "year_end",  # 年末（12/28〜31）
)

# フィード投稿の画像タグ（C7。ENGINE_BRIEF §2.7）。次のすべてがこの 1 つの語彙を使う:
#   - 事前に用意した画像のプール post_image_pool.tags（infra/supabase/seed_engine.sql。全タグに画像があること）
#   - ペルソナ YAML の post_tags（packages/personas/scripts/engine_checks.py が検査する）
#   - キャプションの写真の説明・絵文字（app/engine/calendar/captions.py の TAG_INFO。全タグに説明があること）
#   - カレンダーの既定のタグ（行事・誕生日）と一貫性チェック（app/engine/calendar/）
# 語彙を変えるときは seed_engine.sql と TAG_INFO も合わせる（tests/engine/calendar/test_captions.py が検査する）。
TAG_VOCABULARY: Final[tuple[str, ...]] = (
    "cafe",
    "food",
    "sweets",
    "izakaya",
    "bar",
    "office",
    "home",
    "room",
    "book",
    "study",
    "gym",
    "running",
    "yoga",
    "travel",
    "sea",
    "mountain",
    "forest",
    "city",
    "night_city",
    "street",
    "shopping",
    "fashion",
    "cosmetics",
    "cooking",
    "music",
    "stage",
    "live",
    "karaoke",
    "game",
    "anime",
    "art",
    "flowers",
    "sakura",
    "rain",
    "summer",
    "festival",
    "fireworks",
    "autumn",
    "autumn_leaves",
    "snow",
    "christmas",
    "new_year",
    "valentine",
    "halloween",
    "pet",
    "sky",
    "sunset",
    "morning",
    "train",
    "library",
    "school",
    "park",
)

# ---------------------------------------------------------------------------
# 値オブジェクト
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorldState:
    """世界の時計（C1）。キャラ全員に共通。"""

    now: datetime  # UTC
    now_jst: datetime
    weekday_ja: str  # 「金」
    season: Literal["spring", "summer", "autumn", "winter"]
    season_ja: str  # 「秋」
    time_of_day_ja: str  # 早朝 / 朝 / 昼 / 夕方 / 夜 / 深夜
    holiday_name: str | None  # 祝日名（振替休日を含む）
    is_day_off: bool  # 土日祝
    seasonal_keys: tuple[str, ...]  # 今日が該当する行事（SEASONAL_KEYS）
    seasonal_labels_ja: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CharacterStateSnapshot:
    """キャラの今の状態（C5）と、プロンプトに渡す前後の予定。"""

    activity: str  # 「会社の同期と飲み会」
    location: str | None  # 「新宿の居酒屋」
    mood: str | None  # 「ほろ酔いで上機嫌」
    busyness: int  # 0..3
    status_label: str | None  # UI 用「飲み会中」
    event_id: UUID | None
    event_kind: str | None  # routine / oneoff / seasonal / promise
    reply_style_hint: str  # 忙しさに応じた返答の指針（「今は手短に」など。課金誘導には使わない）
    next_event: str | None = None  # 「23:00 ごろ帰宅」
    recent_events: tuple[str, ...] = ()  # 「昨日: 同期と飲み会（新宿）」など（C9 の手がかり）


@dataclass(frozen=True, slots=True)
class RelationshipGuidance:
    """好感度から導いた振る舞いの指針（A8）。数値そのものはプロンプトに入れない。"""

    stage: str  # STAGES
    stage_label_ja: str
    call_user: str  # 呼び方（名前が分からなければペルソナの既定）
    tone: str  # 口調
    affection: str  # 甘え方・好意の表し方
    topics: tuple[str, ...]
    examples: tuple[str, ...]
    notes: tuple[str, ...] = ()  # 「少し気まずさが残っている」「3日ぶりで寂しかった」など
    days_since_last_interaction: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """プロンプトに注入するユーザーについての記憶（M5）。"""

    id: UUID
    kind: str  # MEMORY_KINDS
    content: str
    importance: float
    tags: tuple[str, ...]
    created_at: datetime
    score: float | None = None  # ランキングの総合点（監査・評価用）
    is_user_edited: bool = False

    @property
    def is_secret(self) -> bool:
        return "secret" in self.tags


@dataclass(frozen=True, slots=True)
class CharacterMemoryItem:
    """キャラ側の記憶（M8 / C9）。"""

    id: UUID
    kind: str  # self_statement / event / fact
    content: str
    occurred_at: datetime | None
    is_shared: bool  # True = 全ユーザー共通（予定由来）


@dataclass(frozen=True, slots=True)
class PromiseItem:
    id: UUID
    content: str
    due_at: datetime | None
    due_precision: str
    status: str  # PromiseStatus


@dataclass(frozen=True, slots=True)
class MemoryContext:
    """Memory Engine が Context Assembler に返すもの。"""

    memories: tuple[MemoryItem, ...]
    character_memories: tuple[CharacterMemoryItem, ...]
    promises: tuple[PromiseItem, ...]  # 期日が近い・今日の約束（M6）
    retrieval_skipped: bool = False  # 埋め込み失敗などで意味検索を省略した


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """保存済みの 1 往復（非同期ジョブの入力）。"""

    conversation_id: UUID
    user_id: UUID
    character_id: UUID
    user_message_id: UUID
    character_message_id: UUID
    user_text: str
    reply_text: str
    occurred_at: datetime
    moderated: bool = False  # Gate #1 で差し止めた（抽出・好感度の評価から除外する）
    safety_triggered: bool = False  # E6 の安全対応をした（好感度を動かさない）


@dataclass(frozen=True, slots=True)
class SafetyResource:
    name: str
    phone: str | None
    hours: str | None
    url: str | None


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    """E6: 自傷・希死念慮のシグナルの判定。"""

    triggered: bool
    categories: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GuardResult:
    """出力の追加検査（E2: 購入と関係を結びつける発言の禁止 / E3: 実在の人間だという主張の禁止）。"""

    flagged: bool
    categories: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Context Assembler の出力（プロンプトの材料と、監査・評価のための内訳）。"""

    world: WorldState
    state: CharacterStateSnapshot | None
    relationship: RelationshipGuidance | None
    memory: MemoryContext
    budget_report: dict[str, int] = field(default_factory=dict)  # 要素ごとの文字数（トークン予算の実測）


@dataclass(frozen=True, slots=True)
class MemoryProcessResult:
    created: tuple[UUID, ...] = ()
    updated: tuple[UUID, ...] = ()
    superseded: tuple[UUID, ...] = ()  # 矛盾で置き換えられた古い記憶（M4）
    skipped_user_edited: int = 0
    skipped_tombstoned: int = 0
    promises_created: tuple[UUID, ...] = ()
    character_memories_created: tuple[UUID, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AffinityUpdateResult:
    applied: dict[str, float] = field(default_factory=dict)  # 軸ごとの実際の変化量（上限適用後）
    stage_before: str = "acquaintance"
    stage_after: str = "acquaintance"
    manipulation_detected: bool = False
    skipped_reason: str | None = None  # safety / moderated / no_turns など
    error: str | None = None


# ---------------------------------------------------------------------------
# モジュールの契約（Protocol）。実装は app/engine/<module>/service.py
#   すべてコンストラクタで Pool・LLMClient・AuditLogger・Settings などを受け取り、接続は内部で取得する。
#   now は必ず呼び出し側（Clock）から渡す。
# ---------------------------------------------------------------------------


class MemoryService(Protocol):
    async def retrieve_context(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        query_text: str,
        query_embedding: list[float] | None,
        now: datetime,
    ) -> MemoryContext: ...

    async def embed_query(
        self, text: str, *, user_id: UUID, character_id: UUID, conversation_id: UUID
    ) -> list[float] | None: ...

    async def mark_referenced(self, *, memory_ids: Sequence[UUID], now: datetime) -> None: ...

    async def process_turns(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        turns: Sequence[TurnRecord],
        now: datetime,
    ) -> MemoryProcessResult: ...

    async def maybe_summarize(
        self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, now: datetime
    ) -> None: ...

    async def due_promises(
        self, *, user_id: UUID, character_id: UUID, now: datetime, window: timedelta
    ) -> Sequence[PromiseItem]: ...

    async def mark_promise_mentioned(self, *, promise_id: UUID, now: datetime) -> None: ...


class CalendarService(Protocol):
    def world_state(self, now: datetime) -> WorldState: ...

    async def current_state(self, *, character_id: UUID, now: datetime) -> CharacterStateSnapshot: ...

    async def ensure_schedules(self, *, now: datetime, days_ahead: int) -> int: ...

    async def tick(self, *, now: datetime) -> None: ...

    async def sync_promise_events(
        self, *, user_id: UUID, character_id: UUID, promise_ids: Sequence[UUID], now: datetime
    ) -> None: ...


class AffinityService(Protocol):
    async def guidance(self, *, user_id: UUID, character_id: UUID, now: datetime) -> RelationshipGuidance: ...

    async def touch_interaction(self, *, user_id: UUID, character_id: UUID, now: datetime) -> None: ...

    async def evaluate_turns(
        self, *, user_id: UUID, character_id: UUID, turns: Sequence[TurnRecord], now: datetime
    ) -> AffinityUpdateResult: ...

    async def apply_daily_maintenance(self, *, now: datetime) -> int: ...


class ProactiveService(Protocol):
    async def scan(self, *, now: datetime) -> int:
        """送るべき自発メッセージを判定して送る。送った件数を返す。"""
        ...

    async def on_user_message(self, *, user_id: UUID, character_id: UUID, conversation_id: UUID, now: datetime) -> None:
        """ユーザーの発言で、未返信の自発メッセージに replied_at を付ける（P4）。"""
        ...


class SafetyService(Protocol):
    def assess(self, text: str) -> SafetyAssessment: ...

    def resources(self) -> Sequence[SafetyResource]: ...


class OutputGuard(Protocol):
    def check(self, text: str) -> GuardResult: ...


class JobQueue(Protocol):
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, object],
        *,
        run_at: datetime,
        dedupe_key: str | None = None,
    ) -> int | None:
        """ジョブを登録する。同じ kind + dedupe_key の未処理ジョブがあれば登録せず None。"""
        ...
