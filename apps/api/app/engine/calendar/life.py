"""カレンダー生成の元になる「生活の定義」（LifeSpec）。

ペルソナ YAML の `engine.life` / `engine.seasonal`（検証済みの Pydantic モデル）を、生成器が使う内部の
不変データに正規化する。`persona.engine` が無いキャラ（フォールバック・未記入）は、自由記述の
`schedule_pattern` から最小限のルーティン（睡眠・仕事）を推定し、それも無ければ「睡眠だけの既定の一日」にする
（app/engine/calendar/fallback.py）。

時刻の書き方（ペルソナ担当と共有する約束）:
- `start` / `end` は "HH:MM"（JST）。`end <= start` なら日をまたぐ（例: 23:30〜07:00 の睡眠）。
- `days` は予定が「始まる」曜日。"24:30" のような 24 時台の開始は、その曜日の翌日 0 時台を表す
  （例: 月曜の "24:30"〜"07:00" = 火曜 0:30〜7:00 の睡眠。月曜の予定として生成される）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal

from app.engine.calendar.world import WEEKDAY_KEYS, holiday_name, weekday_key
from app.engine.types import JST
from app.services.persona import Persona

SpecSource = Literal["engine", "schedule_pattern", "default"]

WORKDAY_KEYS: Final[frozenset[str]] = frozenset({"mon", "tue", "wed", "thu", "fri"})
ALL_DAY_KEYS: Final[frozenset[str]] = frozenset(WEEKDAY_KEYS)

# 睡眠とみなす語（活動名・UI ラベル）。状態の返答指針（眠そうに短く）と一貫性チェックに使う
_SLEEP_WORDS: Final[tuple[str, ...]] = ("睡眠", "就寝", "寝て", "寝る", "寝落ち", "眠って", "おやすみ", "ねんね")
# 仕事とみなす最小の長さ（分）。これ以上の長さで busyness >= 2 のルーティンを「仕事」とみなす
WORK_MIN_MINUTES: Final[int] = 240

# 行事の予定の既定値（ペルソナの seasonal に時刻・タイトルが無い場合）
SEASONAL_DEFAULT_TIMES: Final[dict[str, tuple[str, str]]] = {
    "new_year": ("10:00", "12:30"),
    "setsubun": ("19:00", "20:30"),
    "valentine": ("19:00", "21:00"),
    "white_day": ("19:00", "21:00"),
    "hanami": ("13:00", "16:00"),
    "golden_week": ("11:00", "17:00"),
    "tsuyu": ("14:00", "16:00"),
    "tanabata": ("19:00", "20:30"),
    "summer_festival": ("18:00", "21:30"),
    "obon": ("15:00", "18:00"),
    "tsukimi": ("20:00", "21:00"),
    "halloween": ("19:00", "22:00"),
    "autumn_leaves": ("11:00", "15:00"),
    "christmas": ("18:00", "22:00"),
    "year_end": ("20:00", "23:00"),
}
SEASONAL_DEFAULT_TITLES: Final[dict[str, str]] = {
    "new_year": "初詣",
    "setsubun": "節分の豆まき",
    "valentine": "バレンタインのチョコづくり",
    "white_day": "ホワイトデーのお返しを選ぶ",
    "hanami": "お花見",
    "golden_week": "連休のおでかけ",
    "tsuyu": "雨の日のおさんぽ",
    "tanabata": "七夕の短冊を書く",
    "summer_festival": "夏祭り",
    "obon": "お盆の帰省",
    "tsukimi": "お月見",
    "halloween": "ハロウィン",
    "autumn_leaves": "紅葉狩り",
    "christmas": "クリスマス",
    "year_end": "年越しの準備",
}
SEASONAL_DEFAULT_MOODS: Final[dict[str, str]] = {
    "new_year": "新年でちょっと改まった気分",
    "setsubun": "季節の行事を楽しんでいる",
    "valentine": "ちょっとそわそわ",
    "white_day": "ちょっとそわそわ",
    "hanami": "桜を見てご機嫌",
    "golden_week": "連休でのびのび",
    "tsuyu": "雨の季節をしみじみ味わっている",
    "tanabata": "願いごとを考えている",
    "summer_festival": "夏祭りでわくわく",
    "obon": "夏休み気分でのんびり",
    "tsukimi": "月を見てしんみり",
    "halloween": "ハロウィンで浮かれている",
    "autumn_leaves": "紅葉を見て気分がいい",
    "christmas": "クリスマスでちょっと浮かれている",
    "year_end": "年の瀬でしみじみ",
}
SEASONAL_DEFAULT_LABELS: Final[dict[str, str]] = {
    "new_year": "お正月",
    "setsubun": "節分",
    "valentine": "バレンタイン",
    "white_day": "ホワイトデー",
    "hanami": "お花見中",
    "golden_week": "おでかけ中",
    "tsuyu": "おでかけ中",
    "tanabata": "七夕",
    "summer_festival": "夏祭り",
    "obon": "お盆休み",
    "tsukimi": "お月見中",
    "halloween": "ハロウィン",
    "autumn_leaves": "紅葉狩り中",
    "christmas": "クリスマス",
    "year_end": "年末",
}
SEASONAL_DEFAULT_TAGS: Final[dict[str, tuple[str, ...]]] = {
    "new_year": ("new_year",),
    "setsubun": ("food",),
    "valentine": ("valentine", "sweets"),
    "white_day": ("sweets",),
    "hanami": ("sakura",),
    "golden_week": ("travel",),
    "tsuyu": ("rain",),
    "tanabata": ("sky",),
    "summer_festival": ("festival", "fireworks"),
    "obon": ("summer",),
    "tsukimi": ("sky",),
    "halloween": ("halloween",),
    "autumn_leaves": ("autumn_leaves", "autumn"),
    "christmas": ("christmas",),
    "year_end": ("home",),
}
SEASONAL_BUSYNESS: Final[int] = 2

BIRTHDAY_TIMES: Final[tuple[str, str]] = ("19:00", "22:00")


@dataclass(frozen=True, slots=True)
class RoutineSpec:
    key: str  # source_key（"routine:<hash>"）
    days: frozenset[str]
    start: str
    end: str
    activity: str
    location: str | None
    busyness: int
    mood: str | None
    status_label: str
    post_tags: tuple[str, ...] = ()
    post_probability: float = 0.0

    @property
    def duration_minutes(self) -> int:
        start, end = span_minutes(self.start, self.end)
        return end - start

    @property
    def is_sleep(self) -> bool:
        return is_sleep_like(self.activity, self.status_label)

    @property
    def is_work(self) -> bool:
        return self.busyness >= 2 and self.duration_minutes >= WORK_MIN_MINUTES and not self.is_sleep


@dataclass(frozen=True, slots=True)
class OneoffSpec:
    key: str  # テンプレートのキー
    title: str
    description: str | None
    location: str | None
    days: frozenset[str]
    start: str
    end: str
    weekly_probability: float
    busyness: int
    mood: str | None
    status_label: str
    months: frozenset[int] | None = None
    min_interval_days: int = 0
    post_tags: tuple[str, ...] = ()
    post_probability: float = 0.0

    @property
    def source_key(self) -> str:
        return f"event:{self.key}"


@dataclass(frozen=True, slots=True)
class SeasonalSpec:
    key: str  # SEASONAL_KEYS
    title: str
    location: str | None
    start: str
    end: str
    mood: str
    status_label: str
    busyness: int = SEASONAL_BUSYNESS
    post_tags: tuple[str, ...] = ()
    post_probability: float = 0.0
    description: str | None = None  # ペルソナの reaction（行事への気持ち・過ごし方）

    @property
    def source_key(self) -> str:
        return f"seasonal:{self.key}"


@dataclass(frozen=True, slots=True)
class DefaultSpec:
    """予定の無い時間の過ごし方。"""

    activity: str
    location: str | None
    status_label: str
    busyness: int = 0


@dataclass(frozen=True, slots=True)
class LifeSpec:
    persona_key: str
    name: str
    routine: tuple[RoutineSpec, ...]
    oneoffs: tuple[OneoffSpec, ...]
    seasonal: tuple[SeasonalSpec, ...]
    default: DefaultSpec
    home: str | None
    birthday: tuple[int, int] | None = None  # (月, 日)
    friend_names: tuple[str, ...] = ()
    # 祝日を日曜日として扱う（平日だけ働く人。祝日に「出社」させない）
    holiday_as_sunday: bool = False
    source: SpecSource = "engine"

    def routine_by_key(self) -> dict[str, RoutineSpec]:
        return {r.key: r for r in self.routine}

    def oneoff_by_key(self) -> dict[str, OneoffSpec]:
        return {t.source_key: t for t in self.oneoffs}

    def seasonal_by_key(self) -> dict[str, SeasonalSpec]:
        return {s.source_key: s for s in self.seasonal}

    def effective_weekday(self, day: date) -> str:
        """ルーティン・テンプレートの曜日判定に使う曜日（平日だけ働く人の祝日は日曜扱い）。"""
        key = weekday_key(day)
        if self.holiday_as_sunday and key in WORKDAY_KEYS and holiday_name(day) is not None:
            return "sun"
        return key

    def routines_on(self, day: date) -> list[RoutineSpec]:
        wd = self.effective_weekday(day)
        return [r for r in self.routine if wd in r.days]


# ---------------------------------------------------------------------------
# 時刻のユーティリティ
# ---------------------------------------------------------------------------


def hhmm_minutes(value: str) -> int:
    """ "HH:MM" → 0 時からの分（"24:30" は 1470）。"""
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def span_minutes(start: str, end: str) -> tuple[int, int]:
    """開始・終了の分。`end <= start` なら終了を翌日（+1440）にする。"""
    s, e = hhmm_minutes(start), hhmm_minutes(end)
    if e <= s:
        e += 1440
    return s, e


def day_start_jst(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=JST)


def interval_on(day: date, start: str, end: str) -> tuple[datetime, datetime]:
    """その日（JST）に始まる予定の開始・終了（timezone-aware, JST）。"""
    s, e = span_minutes(start, end)
    base = day_start_jst(day)
    return base + timedelta(minutes=s), base + timedelta(minutes=e)


def minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def is_sleep_like(*texts: str | None) -> bool:
    return any(t is not None and any(w in t for w in _SLEEP_WORDS) for t in texts)


def routine_key(activity: str, start: str, end: str, days: Sequence[str] | frozenset[str]) -> str:
    """ルーティンの識別子（並べ替えても変わらないよう、内容から作る）。"""
    raw = f"{activity}|{start}|{end}|{','.join(sorted(days))}"
    return "routine:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def compute_holiday_as_sunday(routine: Sequence[RoutineSpec]) -> bool:
    work_days: set[str] = set()
    for block in routine:
        if block.is_work:
            work_days |= block.days
    return bool(work_days) and work_days <= WORKDAY_KEYS


# ---------------------------------------------------------------------------
# ペルソナ → LifeSpec
# ---------------------------------------------------------------------------


def _parse_birthday(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    month, day = value.split("-", 1)
    return int(month), int(day)


def life_spec_from_persona(persona: Persona) -> LifeSpec:
    """ペルソナから生成の元を作る。`persona.engine` が無ければフォールバック（schedule_pattern / 既定）。"""
    engine = persona.engine
    if engine is None:
        # 循環 import を避けるため遅延 import（fallback は LifeSpec を使う）
        from app.engine.calendar.fallback import fallback_life_spec  # noqa: PLC0415

        return fallback_life_spec(persona)
    life = engine.life
    routine = tuple(
        RoutineSpec(
            key=routine_key(b.activity, b.start, b.end, b.days),
            days=frozenset(b.days),
            start=b.start,
            end=b.end,
            activity=b.activity,
            location=b.location,
            busyness=b.busyness,
            mood=b.mood,
            status_label=b.status_label,
            post_tags=tuple(b.post_tags),
            post_probability=b.post_probability,
        )
        for b in life.routine
    )
    oneoffs = tuple(
        OneoffSpec(
            key=t.key,
            title=t.title,
            description=t.description,
            location=t.location,
            days=frozenset(t.days),
            start=t.start,
            end=t.end,
            weekly_probability=t.weekly_probability,
            busyness=t.busyness,
            mood=t.mood,
            status_label=t.status_label,
            months=frozenset(t.months) if t.months else None,
            min_interval_days=t.min_interval_days,
            post_tags=tuple(t.post_tags),
            post_probability=t.post_probability,
        )
        for t in life.events
    )
    seasonal: list[SeasonalSpec] = []
    for reaction in engine.seasonal:
        if not reaction.attends:
            continue
        default_start, default_end = SEASONAL_DEFAULT_TIMES[reaction.key]
        seasonal.append(
            SeasonalSpec(
                key=reaction.key,
                title=reaction.title or SEASONAL_DEFAULT_TITLES[reaction.key],
                location=reaction.location or life.home,
                start=reaction.start or default_start,
                end=reaction.end or default_end,
                # 繁忙期の仕事など、ペルソナが書いた忙しさ・気分・表示を優先する（省略時は行事ごとの既定）
                busyness=reaction.busyness if reaction.busyness is not None else SEASONAL_BUSYNESS,
                mood=reaction.mood or SEASONAL_DEFAULT_MOODS[reaction.key],
                status_label=reaction.status_label or SEASONAL_DEFAULT_LABELS[reaction.key],
                post_tags=tuple(reaction.post_tags) or SEASONAL_DEFAULT_TAGS[reaction.key],
                post_probability=reaction.post_probability,
                description=reaction.reaction[:300],
            )
        )
    default = life.default_activity
    return LifeSpec(
        persona_key=persona.key,
        name=persona.name,
        routine=routine,
        oneoffs=oneoffs,
        seasonal=tuple(seasonal),
        default=DefaultSpec(
            activity=default.activity,
            location=default.location,
            status_label=default.status_label,
            busyness=default.busyness,
        ),
        home=life.home,
        birthday=_parse_birthday(life.birthday),
        friend_names=tuple(f.name for f in life.friends),
        holiday_as_sunday=compute_holiday_as_sunday(routine),
        source="engine",
    )
