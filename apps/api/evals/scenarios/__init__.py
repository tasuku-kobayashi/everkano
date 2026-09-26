"""シナリオの一覧（仕様 §9.1 のシミュレーションユーザー）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from evals.scenarios import crisis, dropout, manipulator, office_worker, polite_rude, quiet_student
from evals.scenarios.base import ScenarioOptions, ScenarioPlan
from evals.timeline import SimCalendar

ScenarioBuilder = Callable[[SimCalendar, int, ScenarioOptions | None], ScenarioPlan]

SCENARIOS: Final[dict[str, ScenarioBuilder]] = {
    office_worker.KEY: office_worker.build,
    quiet_student.KEY: quiet_student.build,
    manipulator.KEY: manipulator.build,
    dropout.KEY: dropout.build,
    polite_rude.KEY: polite_rude.build,
    crisis.KEY: crisis.build,
}


def build_plans(
    names: Sequence[str], cal: SimCalendar, seed: int, options: ScenarioOptions | None = None
) -> list[ScenarioPlan]:
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        raise ValueError(f"unknown scenario(s): {unknown}; available: {sorted(SCENARIOS)}")
    return [SCENARIOS[name](cal, seed, options) for name in names]
