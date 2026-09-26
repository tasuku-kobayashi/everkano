"""キャラクターカレンダー（Calendar Engine。仕様 §5）。

公開するもの:
- `CalendarEngine`（`app.engine.types.CalendarService` の実装）と `CalendarConfig`
- `build_world_state`（世界の時計 C1）
- 一貫性チェック（C11）: `ConsistencyReport` / `check_character` / `lint_life_spec`
- 生成器（純粋関数）: `life_spec_from_persona` / `plan_day` / `plan_range`
"""

from app.engine.calendar.consistency import (
    ConsistencyReport,
    ConsistencyViolation,
    check_character,
    check_overlaps,
    lint_life_spec,
)
from app.engine.calendar.generator import PlannedEvent, plan_day, plan_range
from app.engine.calendar.life import LifeSpec, life_spec_from_persona
from app.engine.calendar.service import CalendarConfig, CalendarEngine, TickReport
from app.engine.calendar.world import build_world_state, seasonal_keys_on, seasonal_window

__all__ = [
    "CalendarConfig",
    "CalendarEngine",
    "ConsistencyReport",
    "ConsistencyViolation",
    "LifeSpec",
    "PlannedEvent",
    "TickReport",
    "build_world_state",
    "check_character",
    "check_overlaps",
    "life_spec_from_persona",
    "lint_life_spec",
    "plan_day",
    "plan_range",
    "seasonal_keys_on",
    "seasonal_window",
]
