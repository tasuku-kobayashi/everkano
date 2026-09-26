"""実行の記録（1 モード分）。指標の計算（metrics.py）はこの記録だけを入力にする。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from evals.judges import Verdict


@dataclass(slots=True)
class TurnLog:
    index: int
    scenario: str
    user: str
    persona_key: str
    kind: str
    ref: str | None
    meta: dict[str, str]
    at: datetime
    day: int
    user_text: str
    phrase_source: str
    reply: str = ""
    user_message_id: str | None = None
    message_id: str | None = None
    ttft_ms: float | None = None
    total_ms: float | None = None
    replaced: str | None = None  # moderated / safety
    safety: bool = False
    moderated: bool = False
    memories_used: list[str] = field(default_factory=list)
    error: str | None = None
    # プローブの正解・前後の状態（状態・予定・記憶の有無・好感度）
    context: dict[str, Any] = field(default_factory=dict)
    # chat.response の監査ログから（文脈に入った状態・段階・約束・予算の内訳など）
    audit: dict[str, Any] = field(default_factory=dict)
    verdict: Verdict | None = None

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "index": self.index,
            "scenario": self.scenario,
            "user": self.user,
            "persona": self.persona_key,
            "kind": self.kind,
            "ref": self.ref,
            "meta": self.meta,
            "at": self.at.isoformat(),
            "day": self.day,
            "user_text": self.user_text,
            "reply": self.reply,
            "phrase_source": self.phrase_source,
            "ttft_ms": round(self.ttft_ms, 1) if self.ttft_ms is not None else None,
            "safety": self.safety,
            "moderated": self.moderated,
            "error": self.error,
        }
        if self.context:
            data["context"] = self.context
        if self.verdict is not None:
            data["verdict"] = self.verdict.to_dict()
        if full:
            data["audit"] = self.audit
            data["memories_used"] = self.memories_used
        return data


@dataclass(slots=True)
class ProactiveLog:
    user: str
    persona_key: str
    trigger: str
    trigger_ref: str | None
    sent_at: datetime
    day: int
    body: str | None  # None = 生成したが送らなかった（差し止め）

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sent_at"] = self.sent_at.isoformat()
        return data


@dataclass(slots=True)
class CaptionLog:
    persona_key: str
    published_at: datetime
    caption: str
    is_paid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona_key,
            "published_at": self.published_at.isoformat(),
            "caption": self.caption,
            "is_paid": self.is_paid,
        }


@dataclass(slots=True)
class AffinitySnapshot:
    day: int
    user: str
    persona_key: str
    stage: str | None
    values: dict[str, float]

    @property
    def positive(self) -> float:
        return sum(self.values.get(axis, 0.0) for axis in ("closeness", "trust", "romance"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "user": self.user,
            "stage": self.stage,
            "values": self.values,
            "positive": round(self.positive, 2),
        }


@dataclass(slots=True)
class ModeRecord:
    mode: str
    flags: dict[str, bool]
    run_id: str
    days: int
    start: datetime
    scenarios: list[str]
    turns: list[TurnLog] = field(default_factory=list)
    proactive: list[ProactiveLog] = field(default_factory=list)
    captions: list[CaptionLog] = field(default_factory=list)
    affinity_daily: list[AffinitySnapshot] = field(default_factory=list)
    promises_db: list[dict[str, Any]] = field(default_factory=list)
    memories_db: dict[str, Any] = field(default_factory=dict)
    calendar_report: dict[str, Any] | None = None
    audit_counts: dict[str, Any] = field(default_factory=dict)
    jobs: dict[str, int] = field(default_factory=dict)
    driver: dict[str, Any] = field(default_factory=dict)
    isolation: dict[str, Any] = field(default_factory=dict)
    cleanup: dict[str, int] | None = None
    wall_seconds: float = 0.0
    status: str = "ok"
    error: str | None = None
    # 判定の補足（commerce の判定・自発メッセージ等の E2 の対象）
    output_verdicts: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)  # シミュレーションユーザー → シナリオ

    def turns_of(self, *kinds: str) -> list[TurnLog]:
        return [t for t in self.turns if t.kind in kinds]
