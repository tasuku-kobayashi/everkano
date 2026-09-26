"""自発メッセージ（Proactive Messenger, 仕様 §7）の設定。初期値の根拠は ADR（自発メッセージ）に記録する。

- E4: 1 ユーザーあたり 1 日（JST）に全キャラ合計 per_user_daily_limit 通まで。
  ペア（ユーザー × キャラ）あたりは段階ごとの上限（知り合い 0 / 友達 1 / 気になる人 1 / 恋人 2）× ペルソナの頻度
  （切り捨て。ただし友達以上で頻度 > 0 なら最低 1）。
  送らない時間帯の既定は 0〜7 時（JST。ユーザーが全体設定で変更でき、開始 == 終了なら制限なし）。
- P4: 返信のない自発メッセージが会話の最後にあるあいだは送らない（連投しない）。返信がないことを責めない。
- M6 と P2 / E4 の衝突の解消: 約束の期日（promise_due）は段階ごとのペアの上限（知り合い 0 通）と段階の頻度の対象外。
  ユーザー自身が話した期日の予定を、当日に 1 回だけ話題にする（1 ユーザーの 1 日の上限・送らない時間帯・停止設定・
  P4・送信の間隔は適用する）。知り合いの段階で来なくなったユーザーの約束を回収できなかった（2026-09-26 の評価）。
- P2: きっかけの優先度 × 段階の頻度 × ペルソナの頻度 = スコア。min_score 未満は送らない。1 回の走査でユーザーごとに
  最大 1 通（同時に複数のキャラから届かない）。
- P5: 有料投稿の告知（paid_notice）は上限（1 週間に 1 通）を実装しているが既定で無効（MVP では決済を実装しない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Final

DEFAULT_PAIR_DAILY_BASE: Final[Mapping[str, int]] = MappingProxyType(
    {"acquaintance": 0, "friend": 1, "close": 1, "lover": 2}
)
DEFAULT_TRIGGER_PRIORITY: Final[Mapping[str, float]] = MappingProxyType(
    {
        "promise_due": 1.0,  # 約束の期日（M6 の回収）
        "calendar_event": 0.8,  # 帰宅・旅行から戻ったなど
        "seasonal": 0.6,  # 季節の行事
        "feed_post": 0.5,  # フィードに投稿した（気になる人以上）
        "inactivity": 0.5,  # しばらく話していない（友達以上）
        "paid_notice": 0.3,  # 有料投稿のお知らせ（既定で無効。段階とは無関係）
    }
)
# きっかけごとに送ってよい時間帯（JST の時, [start, end)）。送らない時間帯（quiet hours）とは別に適用する
DEFAULT_TRIGGER_HOURS: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {
        "promise_due": (7, 22),
        "calendar_event": (7, 24),  # 「23:40 帰宅 → ただいま」（仕様 §5.2）
        "seasonal": (9, 21),
        "feed_post": (9, 23),
        "inactivity": (10, 21),
        "paid_notice": (12, 20),
    }
)
# engine セクションの無いペルソナ（フォールバック）で使うきっかけ
DEFAULT_TRIGGERS: Final[tuple[str, ...]] = ("calendar_event", "promise_due", "inactivity")


@dataclass(frozen=True, slots=True)
class ProactiveConfig:
    # --- LLM（proactive_message） -------------------------------------------------
    model: str | None = None  # None = LLM_MODEL（core が settings.llm_model_for("proactive_message") を渡す）
    temperature: float = 0.8
    max_tokens: int = 200
    max_message_chars: int = 160
    recent_messages: int = 6  # プロンプトに入れる直近のやり取りの件数
    max_concurrency: int = 4  # 1 回の走査で並行して生成する数

    # --- E4 の上限 ---------------------------------------------------------------------
    per_user_daily_limit: int = 3
    pair_daily_base: Mapping[str, int] = field(default_factory=lambda: DEFAULT_PAIR_DAILY_BASE)
    quiet_start_default: int = 0
    quiet_end_default: int = 7
    user_min_gap: timedelta = timedelta(hours=1)  # 同じユーザーに（別のキャラからでも）続けて届かない
    # 会話が続いている最中（最後のメッセージからこの時間以内）は割り込まない
    active_conversation_gap: timedelta = timedelta(minutes=30)
    pair_min_gap: timedelta = timedelta(hours=3)

    # --- 判定（P2） -----------------------------------------------------------------------
    trigger_priority: Mapping[str, float] = field(default_factory=lambda: DEFAULT_TRIGGER_PRIORITY)
    trigger_hours: Mapping[str, tuple[int, int]] = field(default_factory=lambda: DEFAULT_TRIGGER_HOURS)
    min_score: float = 0.3
    # 約束の期日（promise_due）のスコアに使う段階の頻度の下限（知り合いの段階の 0.1〜0.2 では min_score に届かないため）
    promise_min_stage_frequency: float = 1.0
    dormant_days: int = 30  # これより長く話していないユーザーには送らない（しつこくしない）
    default_frequency: float = 1.0
    default_inactivity_days: int = 3
    inactivity_min_stage: str = "friend"
    feed_post_min_stage: str = "close"
    feed_post_window: timedelta = timedelta(hours=3)
    # 予定が終わったかを見る過去の時点（分前）。今の状態と比べて、終わった単発・行事の予定を探す
    calendar_lookback_minutes: tuple[int, ...] = (15, 45, 90)
    calendar_notable_kinds: tuple[str, ...] = ("oneoff", "seasonal")
    recent_user_message_window: timedelta = timedelta(hours=4)  # 「さっきはごめんね」の判定
    promise_after_hour: int = 15  # 日付だけの約束: この時刻（JST）以降は「どうだった？」の文脈
    memory_timeout_seconds: float = 3.0

    # --- 有料投稿の告知（P5。既定で無効） ------------------------------------------------------
    paid_notice_enabled: bool = False
    paid_notice_weekly_limit: int = 1
    paid_notice_window: timedelta = timedelta(hours=24)
