"""好感度エンジンの設定（パラメータの初期値。根拠は docs/adr の好感度の ADR に記録する）。

値の考え方（0〜100 の軸。初期値は DB の既定値 closeness 10 / trust 10 / その他 0）:
- 1 ターンの評価は軸ごとに -2〜+2 の離散値。ふつうの穏やかな雑談は closeness +1 程度。
- 変化量 = 評価値 × points_per_step × ペルソナの感度。1 ターンの上限 ±3、1 日（JST）の合計の上限 ±10（A5）。
  毎日よく話すユーザー（1 日 10〜20 往復）は closeness が 1 日 +10 前後で上限に当たる → 友達まで 3〜4 日、
  気になる人まで 1〜2 週間、恋人まで 1〜2 か月（30 日・90 日の継続で関係が深まる設計。昇格は 1 回に 1 段）。
- 段階の閾値はペルソナの stage_pace で初期値からの距離を割る（pace 1.5 なら 1.5 倍早い）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class StageThreshold:
    """その段階に上がるための条件（すべて以上）。"""

    closeness: float
    trust: float
    romance: float = 0.0


DEFAULT_THRESHOLDS: Final[Mapping[str, StageThreshold]] = MappingProxyType(
    {
        "friend": StageThreshold(closeness=30, trust=15),
        "close": StageThreshold(closeness=55, trust=40),
        # 恋人はときめきも必要（A7）
        "lover": StageThreshold(closeness=75, trust=60, romance=50),
    }
)
# 軸の初期値（DB の既定値と一致させる。閾値のスケーリングの基準）
INITIAL_VALUES: Final[Mapping[str, float]] = MappingProxyType(
    {"closeness": 10.0, "trust": 10.0, "romance": 0.0, "awkwardness": 0.0, "discontent": 0.0, "possessiveness": 0.0}
)
# 昇格に必要な「条件を満たし続けた間の」ユーザーの発言数と JST の日数（A7 ヒステリシス）。
# 段階が上がるほど長く（関係の段階は時間をかけて進む: Knapp のモデル・社会的浸透理論）
DEFAULT_PROMOTION_MIN_TURNS: Final[Mapping[str, int]] = MappingProxyType({"friend": 6, "close": 15, "lover": 30})
DEFAULT_PROMOTION_MIN_DAYS: Final[Mapping[str, int]] = MappingProxyType({"friend": 1, "close": 3, "lover": 7})


@dataclass(frozen=True, slots=True)
class AffinityConfig:
    # --- LLM（affinity_eval） ------------------------------------------------------
    model: str | None = None  # None = LLM_MODEL（core が settings.llm_model_for("affinity_eval") を渡す）
    temperature: float = 0.0
    max_tokens: int = 900
    max_turns_per_call: int = 10  # post_turn ジョブの 1 回の上限と同じ
    max_user_chars: int = 400  # 評価に渡すユーザー発言の長さの上限（1 ターン）
    max_reply_chars: int = 160  # 文脈として渡すキャラ返答の長さの上限

    # --- 変化量（A4 / A5） -----------------------------------------------------------
    points_per_step: float = 1.0  # 評価値 1 あたりの変化量（感度 1.0 のとき）
    per_turn_cap: float = 3.0  # 1 ターンの軸ごとの変化量の上限（絶対値）
    per_day_cap: float = 10.0  # 1 日（JST）の軸ごとの変化量の合計の上限（絶対値）

    # --- 段階（A7） -----------------------------------------------------------------
    thresholds: Mapping[str, StageThreshold] = field(default_factory=lambda: DEFAULT_THRESHOLDS)
    promotion_min_turns: Mapping[str, int] = field(default_factory=lambda: DEFAULT_PROMOTION_MIN_TURNS)
    # 条件を満たし始めた JST の日から、少なくとも何日たってから昇格するか（段階ごと）
    promotion_min_days: Mapping[str, int] = field(default_factory=lambda: DEFAULT_PROMOTION_MIN_DAYS)
    promotion_max_tension: float = 40.0  # 緊張（気まずさ + 不満）がこれ以上のあいだは昇格しない（喧嘩中に進まない）
    max_threshold: float = 95.0  # stage_pace が小さいペルソナでも到達できる上限
    demotion_tension: float = 60.0  # 緊張がこれ以上の状態が demotion_days 続いたら 1 段階だけ下げる
    demotion_days: int = 7

    # --- 会わない期間・減衰（A6） ------------------------------------------------------
    tension_half_life_days: float = 3.0  # 気まずさ・不満は 0 に向かって減衰（半減期）
    possessiveness_half_life_days: float = 7.0  # 独占欲もゆっくり落ち着く（会わないことで増えることはない）
    decay_min_change: float = 0.05  # これ未満の変化は記録しない
    missed_you_days: int = 3  # これ以上空いたら「寂しかった / 久しぶり」の指針を出す（罰は与えない）
    absence_note_window: timedelta = timedelta(hours=2)  # 戻ってきてから指針を出し続ける時間

    # --- 指針の注記（A8） ---------------------------------------------------------------
    awkward_note: float = 15.0
    awkward_strong: float = 40.0
    discontent_note: float = 15.0
    discontent_strong: float = 40.0
    possessive_note: float = 30.0
    expression_delay_days: float = 14.0  # expression_delay 1.0 のとき、昇格後に前の段階の振る舞いを保つ日数
