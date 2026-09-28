"""ペルソナ → LifeSpec（生成の元になる「生活の定義」を作る入口）。

`persona.engine`（検証済みの Pydantic モデル）があればそれを正規化し、無ければ `schedule_pattern` からの
推定（fallback.py）に任せる。life.py（データ型）→ fallback.py → このモジュールの一方向の依存にして、
循環 import を作らない。
"""

from __future__ import annotations

from app.engine.calendar.fallback import fallback_life_spec
from app.engine.calendar.life import (
    SEASONAL_BUSYNESS,
    SEASONAL_DEFAULT_LABELS,
    SEASONAL_DEFAULT_MOODS,
    SEASONAL_DEFAULT_TAGS,
    SEASONAL_DEFAULT_TIMES,
    SEASONAL_DEFAULT_TITLES,
    DefaultSpec,
    LifeSpec,
    OneoffSpec,
    RoutineSpec,
    SeasonalSpec,
    compute_holiday_as_sunday,
    routine_key,
)
from app.services.persona import Persona


def _parse_birthday(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    month, day = value.split("-", 1)
    return int(month), int(day)


def life_spec_from_persona(persona: Persona) -> LifeSpec:
    """ペルソナから生成の元を作る。`persona.engine` が無ければフォールバック（schedule_pattern / 既定）。"""
    engine = persona.engine
    if engine is None:
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
