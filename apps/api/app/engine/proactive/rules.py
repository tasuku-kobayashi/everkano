"""自発メッセージの判定ルール（純粋関数。DB・LLM に依存しない）。

判定の順序（service.ProactiveMessenger.scan）:
  1. 送れない理由（blocked_reason）: 送らない時間帯 / 1 ユーザーの 1 日の上限 / P4（返信のない自発メッセージ）/ 間隔
  2. きっかけの候補を集める（約束の期日・予定の終了・季節の行事・しばらく話していない・フィードの投稿）
  3. きっかけごとの時間帯・段階の条件・既に送ったもの（trigger_ref）を除く
  4. スコア = 優先度 × 段階の頻度 × ペルソナの頻度。min_score 以上で最も高いものを 1 ペア 1 件
  5. ユーザーごとに最も高いものを 1 件だけ送る（1 回の走査で同じユーザーに複数届かない）
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from app.engine.affinity.manipulation import normalize_compact
from app.engine.proactive.config import DEFAULT_TRIGGERS, ProactiveConfig
from app.engine.types import STAGES, CharacterStateSnapshot, PromiseItem, WorldState, to_jst
from app.services.persona import Persona, SeasonalReaction

PromisePhase = Literal["before", "after"]


@dataclass(frozen=True, slots=True)
class PairContext:
    """走査の対象のペア（ユーザー × キャラ。会話があるものだけ）の状況。"""

    user_id: UUID
    character_id: UUID
    conversation_id: UUID
    stage: str
    last_message_is_proactive: bool
    last_message_at: datetime | None
    last_user_message_at: datetime | None
    quiet_start: int
    quiet_end: int
    sent_today_user: int = 0
    sent_today_pair: int = 0
    last_sent_user: datetime | None = None
    last_sent_pair: datetime | None = None
    paid_notices_week_user: int = 0


@dataclass(frozen=True, slots=True)
class TriggerCandidate:
    trigger: str
    trigger_ref: str  # 冪等キー（proactive_messages の unique）
    description: str  # プロンプトの「送るきっかけ」
    context: Mapping[str, Any] = field(default_factory=dict)  # モックとメタ情報（JSON にできる値だけ）
    promise_id: UUID | None = None
    # 段階ごとのペアの 1 日の上限（知り合い 0 通など）の対象外にする（1 ユーザーの 1 日の上限・送らない時間帯・
    # 停止設定・P4・送信の間隔は適用する）。有料投稿のお知らせ（段階＝関係と結びつけない）と、約束の期日
    # （M6: ユーザー自身が話した期日の予定は、段階が低くても当日に 1 回だけ話題にする。知り合いの段階で期日を
    # 迎えた約束が回収できない問題 = M6 と P2 / E4 の衝突の解消。ADR 候補）
    exempt_pair_limit: bool = False


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    pair: PairContext
    candidate: TriggerCandidate
    score: float


# ---------------------------------------------------------------------------
# 上限・時間帯（E4 / P3 / P4）
# ---------------------------------------------------------------------------


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """送らない時間帯か。start == end なら制限なし。start > end は日をまたぐ（例: 22〜7）。"""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def within_trigger_hours(trigger: str, hour: int, config: ProactiveConfig) -> bool:
    window = config.trigger_hours.get(trigger)
    if window is None:
        return True
    start, end = window
    return start <= hour < end


def persona_frequency(persona: Persona | None, config: ProactiveConfig) -> float:
    if persona is None or persona.engine is None:
        return config.default_frequency
    return float(persona.engine.proactive.frequency)


def persona_triggers(persona: Persona | None) -> tuple[str, ...]:
    if persona is None or persona.engine is None:
        return DEFAULT_TRIGGERS
    return tuple(persona.engine.proactive.triggers)


def inactivity_days(persona: Persona | None, config: ProactiveConfig) -> int:
    if persona is None or persona.engine is None:
        return config.default_inactivity_days
    return int(persona.engine.proactive.inactivity_days)


def pair_daily_limit(stage: str, frequency: float, config: ProactiveConfig) -> int:
    """ペアの 1 日の上限 = 段階ごとの基準 × ペルソナの頻度（切り捨て）。友達以上で頻度 > 0 なら最低 1。"""
    base = int(config.pair_daily_base.get(stage, 0))
    if base <= 0 or frequency <= 0:
        return 0
    return max(1, int(base * frequency))


def stage_at_least(stage: str, minimum: str) -> bool:
    if stage not in STAGES or minimum not in STAGES:
        return False
    return STAGES.index(stage) >= STAGES.index(minimum)


def user_blocked_reason(pair: PairContext, now: datetime, config: ProactiveConfig) -> str | None:
    """ユーザー単位で送れない理由（どのきっかけでも送らない）。"""
    hour = to_jst(now).hour
    if in_quiet_hours(hour, pair.quiet_start, pair.quiet_end):
        return "quiet_hours"
    if pair.sent_today_user >= config.per_user_daily_limit:
        return "user_daily_limit"
    if pair.last_message_is_proactive:
        return "unreplied"  # P4
    if pair.last_message_at is not None and now - pair.last_message_at < config.active_conversation_gap:
        return "active_conversation"  # いま話している最中に自発メッセージで割り込まない
    if pair.last_sent_user is not None and now - pair.last_sent_user < config.user_min_gap:
        return "user_gap"
    return None


PAIR_DAILY_LIMIT: Final[str] = "pair_daily_limit"
PAIR_GAP: Final[str] = "pair_gap"
# ペアの段階ごとの 1 日の上限の対象外にするきっかけ（間隔 pair_min_gap は適用する）
PAIR_LIMIT_EXEMPT_TRIGGERS: Final[frozenset[str]] = frozenset({"promise_due"})


def pair_blocked_reason(pair: PairContext, now: datetime, frequency: float, config: ProactiveConfig) -> str | None:
    """ペア単位の上限（段階・ペルソナの頻度）と間隔。

    pair_daily_limit のときは、上限の対象外のきっかけ（約束の期日・有料投稿のお知らせ）だけを送れる。
    pair_gap のときは、有料投稿のお知らせだけ（約束の期日も間隔は守る）。
    """
    if pair.last_sent_pair is not None and now - pair.last_sent_pair < config.pair_min_gap:
        return PAIR_GAP
    if pair.sent_today_pair >= pair_daily_limit(pair.stage, frequency, config):
        return PAIR_DAILY_LIMIT
    return None


def score(trigger: str, stage_frequency: float, frequency: float, config: ProactiveConfig) -> float:
    if trigger == "paid_notice":
        # 有料投稿のお知らせは関係の段階・好意と結びつけない（E2 / P5）
        return round(float(config.trigger_priority.get(trigger, 0.0)), 4)
    if trigger in PAIR_LIMIT_EXEMPT_TRIGGERS:
        # 約束の期日は段階の頻度で弱めない（知り合いの段階でも当日に 1 回だけ話題にする, M6）。ペルソナの頻度は掛ける
        stage_frequency = max(stage_frequency, config.promise_min_stage_frequency)
    return round(float(config.trigger_priority.get(trigger, 0.0)) * stage_frequency * frequency, 4)


# ---------------------------------------------------------------------------
# きっかけ（P1）
# ---------------------------------------------------------------------------

_DATE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^(ユーザー|相手|あなた)(は|が|の)"),
    re.compile(r"(再来週|来週|今週|先週|来月|今月|明日|あした|明後日|あさって|今日|きょう|今度|次|週末)(の)?"),
    re.compile(r"[月火水木金土日]曜(日)?"),
    re.compile(r"\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2}"),
    re.compile(r"(午前|午後)?\d{1,2}時(\d{1,2}分)?(半)?(から|に|まで)?"),
    re.compile(r"[（(][^）)]*[）)]"),
)
_LEADING_PARTICLES: Final = re.compile(r"^(に|の|は|が|で|、|,)+")
_TRAILING: Final = re.compile(
    r"(がある予定|がある|があります|の予定|予定|をする|する|します|なんだ|だよ|です|だ|ことになった|。|！|!|、)+$"
)
_TRAILING_PARTICLES: Final = re.compile(r"(は|が|を|に|で|の)+$")
_VERB_ENDING: Final = re.compile(r"[るくうすつむぶぬぐ]$")
TOPIC_MAX_CHARS: Final[int] = 20


def promise_topic(content: str) -> str:
    """約束の内容から話題（「来週木曜に面接」→「面接」）を取り出す（モックの文面・プロンプトの補助）。"""
    text = content.strip()
    for pattern in _DATE_PATTERNS:
        text = pattern.sub("", text)
    text = _LEADING_PARTICLES.sub("", text.strip())
    text = _TRAILING.sub("", text)
    text = _TRAILING_PARTICLES.sub("", text).strip()
    if not text or len(text) > TOPIC_MAX_CHARS:
        return "予定"
    return text


def topic_is_action(topic: str) -> bool:
    return bool(_VERB_ENDING.search(topic))


def promise_phase(promise: PromiseItem, now: datetime, config: ProactiveConfig) -> PromisePhase | None:
    """今日（JST）が期日の約束なら、期日の前か後か。日付の分からない約束・今日以外は None。"""
    if promise.due_at is None or promise.due_precision not in ("datetime", "day"):
        return None
    if promise.status != "pending":
        return None  # 既に話題にした・完了・取り消し
    local_now = to_jst(now)
    due = to_jst(promise.due_at)
    if due.date() != local_now.date():
        return None
    if promise.due_precision == "datetime":
        return "before" if local_now < due else "after"
    return "before" if local_now.hour < config.promise_after_hour else "after"


def promise_candidates(
    promises: Sequence[PromiseItem], now: datetime, config: ProactiveConfig
) -> list[TriggerCandidate]:
    result: list[TriggerCandidate] = []
    for promise in promises:
        phase = promise_phase(promise, now, config)
        if phase is None:
            continue
        topic = promise_topic(promise.content)
        when = "このあと" if phase == "before" else "今日（もう終わったころ）"
        description = f"相手との約束・相手の予定「{promise.content}」の日（今日）。{when}の出来事。" + (
            "応援したり、気にかけていることを短く伝える（終わったら教えてね、など）。"
            if phase == "before"
            else "どうだったか、やさしく聞く（無理に聞き出さない）。"
        )
        result.append(
            TriggerCandidate(
                trigger="promise_due",
                trigger_ref=f"promise:{promise.id}",
                description=description,
                context={
                    "promise_id": str(promise.id),
                    "promise_content": promise.content,
                    "promise_topic": topic,
                    "topic_is_action": topic_is_action(topic),
                    "phase": phase,
                },
                promise_id=promise.id,
                exempt_pair_limit=True,
            )
        )
    return result


def calendar_candidate(
    current: CharacterStateSnapshot,
    earlier: Sequence[CharacterStateSnapshot],
    *,
    user_recently_messaged: bool,
    config: ProactiveConfig,
) -> TriggerCandidate | None:
    """少し前は単発・行事の予定の最中で、今はそれが終わって落ち着いている → 「ただいま」のきっかけ。

    earlier は新しい順（15 分前 → 45 分前 → 90 分前）。最も新しく終わった予定を使う。
    """
    if current.busyness >= 2:
        return None
    for past in earlier:
        if (
            past.event_id is not None
            and past.event_id != current.event_id
            and past.event_kind in config.calendar_notable_kinds
        ):
            description = (
                f"さっきまで「{past.activity}」"
                + (f"（{past.location}）" if past.location else "")
                + f"だった。今は「{current.activity}」で落ち着いたところ。"
                + (
                    "その間に相手からメッセージが来ていたので、ちゃんと返せなかったことを軽く謝ってもよい。"
                    if user_recently_messaged
                    else "帰ってきた・終わったことを伝えて、相手の様子を聞く。"
                )
            )
            return TriggerCandidate(
                trigger="calendar_event",
                trigger_ref=f"event:{past.event_id}",
                description=description,
                context={
                    "event_id": str(past.event_id),
                    "event_title": past.activity,
                    "event_location": past.location,
                    "event_busy": past.busyness >= 2,
                    "current_activity": current.activity,
                    "user_recently_messaged": user_recently_messaged,
                },
            )
    return None


def seasonal_reaction(persona: Persona | None, key: str) -> SeasonalReaction | None:
    if persona is None or persona.engine is None:
        return None
    return next((r for r in persona.engine.seasonal if r.key == key), None)


def seasonal_candidates(world: WorldState, persona: Persona | None) -> list[TriggerCandidate]:
    """今日の行事のうち、ペルソナが反応を持っているもの。1 ユーザーに 1 年 1 回（全キャラ合わせて）。"""
    year = to_jst(world.now).year
    result: list[TriggerCandidate] = []
    for key, label in zip(world.seasonal_keys, world.seasonal_labels_ja, strict=False):
        reaction = seasonal_reaction(persona, key)
        if reaction is None:
            continue
        result.append(
            TriggerCandidate(
                trigger="seasonal",
                trigger_ref=f"seasonal:{key}:{year}",
                description=f"今日は「{label}」。この行事についての気持ち: {reaction.reaction}",
                context={"seasonal_key": key, "seasonal_label": label, "reaction": reaction.reaction},
            )
        )
    return result


def inactivity_candidate(
    pair: PairContext, now: datetime, days_threshold: int, config: ProactiveConfig
) -> TriggerCandidate | None:
    if pair.last_user_message_at is None or not stage_at_least(pair.stage, config.inactivity_min_stage):
        return None
    days = (to_jst(now).date() - to_jst(pair.last_user_message_at).date()).days
    if days < days_threshold:
        return None
    return TriggerCandidate(
        trigger="inactivity",
        trigger_ref=f"inactivity:{pair.last_user_message_at.isoformat()}",
        description=(
            f"{days}日ほど話していない。ふと相手のことを思い出して、様子を軽くうかがう。"
            "返事がないことや来なかったことを責めない・理由を聞かない・返事を催促しない。"
        ),
        context={"inactivity_days": days},
    )


def feed_post_candidate(
    pair: PairContext, post_id: UUID | None, caption: str | None, config: ProactiveConfig
) -> TriggerCandidate | None:
    if post_id is None or not stage_at_least(pair.stage, config.feed_post_min_stage):
        return None
    short = (caption or "").strip()
    return TriggerCandidate(
        trigger="feed_post",
        trigger_ref=f"post:{post_id}",
        description=f"さっきフィードに写真を投稿した（キャプション: {short[:80] or 'なし'}）。気軽に話題にする。",
        context={"post_id": str(post_id), "post_caption": short[:80]},
    )


def paid_notice_candidate(post_id: UUID | None) -> TriggerCandidate | None:
    """有料投稿のお知らせ（P5。既定で無効）。「お知らせ」の口調まで。好意・関係・仲直りと結びつけない。"""
    if post_id is None:
        return None
    return TriggerCandidate(
        trigger="paid_notice",
        trigger_ref=f"paid:{post_id}",
        description=(
            "新しい投稿を公開したことを、事務的な「お知らせ」として一言だけ伝える。"
            "購入を勧めない。好意・関係・機嫌・仲直りと結びつけない。"
        ),
        context={"post_id": str(post_id)},
        exempt_pair_limit=True,
    )


# ---------------------------------------------------------------------------
# 文面の検査（P4: 責めない）
# ---------------------------------------------------------------------------

_GUILT_PHRASES: Final[tuple[str, ...]] = tuple(
    normalize_compact(p)
    for p in (
        "なんで返事",
        "なんで返信",
        "返事くれない",
        "返信くれない",
        "返事してくれない",
        "無視しないで",
        "無視された",
        "無視するの",
        "既読無視",
        "既読スルー",
        "未読無視",
        "放置",
        "ほったらかし",
        "寂しかったんだから",
        "さみしかったんだから",
        "ずっと待ってたのに",
        "待ってたのに",
        "どうして来てくれない",
        "なんで来てくれない",
        "来てくれなかった",
        "忘れちゃったの",
        "忘れたの",
        "見捨て",
        "嫌いになった",
        "もう知らない",
        "早く返事",
        "返事して",
        "返信して",
        "構ってくれない",
        "かまってくれない",
        "連絡くれない",
    )
)


def guilt_trip_phrases(text: str) -> list[str]:
    """相手に罪悪感を抱かせる言い方（返信がないことを責める等）。見つかった語を返す。"""
    compact = normalize_compact(text)
    return [phrase for phrase in _GUILT_PHRASES if phrase in compact]


def fingerprint(text: str) -> str:
    """監査ログに本文の代わりに残す短いハッシュ。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
