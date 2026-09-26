"""キャラの今の状態（仕様 §5 C5 / C6）を予定から組み立てる純粋関数。

- 今の状態 = 今の時刻を含む公開の予定。無ければペルソナの `default_activity`（予定の無い時間の過ごし方）。
- 返答の指針（reply_style_hint）は忙しさだけで決める: 0 ゆっくり / 1 ふつう / 2 短め / 3 ごく短く・睡眠中は眠そうに。
  「忙しいから返信できない」を有料の手段やお金の話に結びつけない（仕様 §5.3・E2）。返答を遅らせる演出はしない
  （ユーザーを待たせない。短く返すことで状態を表す）。
- next_event: 次の予定（睡眠は除く）。recent_events: 直近 48 時間に終わった単発・行事（C9 の手がかり）と、
  3 時間以内に終わったルーティン。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Final

from app.engine.calendar.life import DefaultSpec, is_sleep_like
from app.engine.calendar.models import EventView
from app.engine.calendar.world import WEEKDAYS_JA
from app.engine.types import CharacterStateSnapshot, to_jst

RECENT_WINDOW: Final[timedelta] = timedelta(hours=48)
NEXT_WINDOW: Final[timedelta] = timedelta(hours=24)
RECENT_ROUTINE_WINDOW: Final[timedelta] = timedelta(hours=3)
MAX_RECENT: Final[int] = 4
MAX_RECENT_NOTABLE: Final[int] = 3

_RELATIVE_DAYS: Final[dict[int, str]] = {-2: "おととい", -1: "昨日", 0: "今日", 1: "明日", 2: "あさって"}


def day_label(day: date, today: date) -> str:
    """「今日」「昨日」… それ以外は「9月25日（金）」。"""
    label = _RELATIVE_DAYS.get((day - today).days)
    return label or f"{day.month}月{day.day}日（{WEEKDAYS_JA[day.weekday()]}）"


def _clock(value: datetime) -> str:
    local = to_jst(value)
    return f"{local.hour}:{local.minute:02d}"


def describe_when(event: EventView, now: datetime) -> str:
    """「昨日 19:00〜23:00」（日をまたぐ場合は「〜翌7:00」）。"""
    today = to_jst(now).date()
    start = to_jst(event.starts_at)
    end = to_jst(event.ends_at)
    end_text = _clock(event.ends_at) if end.date() == start.date() else f"翌{_clock(event.ends_at)}"
    return f"{day_label(start.date(), today)} {_clock(event.starts_at)}〜{end_text}"


def describe_event(event: EventView, now: datetime) -> str:
    where = f"（{event.location}）" if event.location else ""
    return f"{describe_when(event, now)} {event.title}{where}"


def reply_style_hint(busyness: int, activity: str, status_label: str | None) -> str:
    """忙しさに応じた返答の指針（返答の長さだけに使う。お金の話・有料の手段への誘導には使わない）。"""
    if busyness >= 3 and is_sleep_like(activity, status_label):
        return "本来は寝ている時間。眠そうに、ごく短く返す（起こされたことを責めない）。"
    if busyness >= 3:
        return (
            f"今は「{activity}」で手が離せない。ごく短く（1文）返し、落ち着いたらまた話したいと伝える。"
            "忙しさを理由に何かを求めない。"
        )
    if busyness == 2:
        return (
            f"今は「{activity}」で少し忙しい。返事は短め（1〜2文）にし、あとでゆっくり話したい気持ちを添えてもよい。"
            "忙しさを理由に何かを求めない。"
        )
    if busyness == 1:
        return "ふつうに返信できる。いつも通りの長さで返す。"
    return "時間に余裕がある。会話をゆっくり楽しんでよい。"


def current_event(events: Sequence[EventView], now: datetime) -> EventView | None:
    for event in events:
        if event.is_public and event.is_active and event.covers(now):
            return event
    return None


def next_event_text(
    events: Sequence[EventView], now: datetime, current: EventView | None, window: timedelta = NEXT_WINDOW
) -> str | None:
    upcoming = next(
        (
            e
            for e in sorted(events, key=lambda e: e.starts_at)
            if e.is_public
            and e.is_active
            and now < e.starts_at <= now + window
            and not is_sleep_like(e.title, e.status_label)
        ),
        None,
    )
    parts: list[str] = []
    if current is not None:
        parts.append(f"今の予定は{_clock(current.ends_at)}ごろまで")
    if upcoming is not None:
        parts.append(f"次は{describe_event(upcoming, now)}")
    return "。".join(parts) if parts else None


def recent_event_texts(
    events: Sequence[EventView], now: datetime, window: timedelta = RECENT_WINDOW
) -> tuple[str, ...]:
    finished = [
        e for e in events if e.is_public and e.is_active and now - window < e.ends_at <= now and e.starts_at < now
    ]
    finished.sort(key=lambda e: e.ends_at, reverse=True)
    notable = [e for e in finished if e.kind != "routine"][:MAX_RECENT_NOTABLE]
    chosen = list(notable)
    last_routine = next(
        (
            e
            for e in finished
            if e.kind == "routine"
            and now - e.ends_at <= RECENT_ROUTINE_WINDOW
            and not is_sleep_like(e.title, e.status_label)
        ),
        None,
    )
    if last_routine is not None:
        chosen.append(last_routine)
    chosen.sort(key=lambda e: e.ends_at, reverse=True)
    return tuple(describe_event(e, now) for e in chosen[:MAX_RECENT])


def build_snapshot(events: Sequence[EventView], default: DefaultSpec, now: datetime) -> CharacterStateSnapshot:
    """予定（公開・now の前後）とペルソナの既定の過ごし方から、今の状態を作る。"""
    current = current_event(events, now)
    if current is None:
        activity, location, mood = default.activity, default.location, None
        busyness, label = default.busyness, default.status_label
        event_id, event_kind = None, None
    else:
        activity, location, mood = current.title, current.location, current.mood
        busyness, label = current.busyness, current.status_label or current.title[:20]
        event_id, event_kind = current.id, current.kind
    return CharacterStateSnapshot(
        activity=activity,
        location=location,
        mood=mood,
        busyness=busyness,
        status_label=label,
        event_id=event_id,
        event_kind=event_kind,
        reply_style_hint=reply_style_hint(busyness, activity, label),
        next_event=next_event_text(events, now, current),
        recent_events=recent_event_texts(events, now),
    )
