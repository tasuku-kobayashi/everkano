"""好感度エンジン（仕様 §6。app.engine.types.AffinityService の実装）。

E1 / A9（構造で守る）: このパッケージが読むのは、呼び出し側から渡される会話の本文（TurnRecord）と、
affinity_states / affinity_history / characters（ペルソナの特定）だけ。
課金・購入・投稿・いいね・トークン残高のテーブルやモジュールは import・参照しない
（tests/engine/affinity/test_e1_structure.py が import と SQL を検査する）。

処理の流れ（evaluate_turns。post_turn ジョブから、返答の後に非同期で呼ばれる）:
  1. 評価済み（evaluated_until 以前）のターンを除く。安全対応（E6）・Gate #1 で差し止めたターンは評価しない
  2. ルール層（manipulation.py）: 操作・プロンプトインジェクションの形のターンは変化 0 + 監査（A10）。LLM にも渡さない
  3. 残りのターンを隔離された LLM 呼び出し（affinity_eval）で採点（-2〜+2）→ 感度でスケール → 1 ターンの上限
  4. 行ロックの中で 1 日の上限（A5）→ 0〜100 → 段階のヒステリシス（A7）→ 状態・履歴を保存
  5. 監査: affinity.update / affinity.stage_change / affinity.manipulation_detected / affinity.skipped / llm.error
  LLM の一時的な障害（再試行できるエラー・通信の失敗）は AffinityEngineUnavailableError で失敗させ、ジョブの再実行で採点し直す。
  拒否（4xx）・不正な出力（1 回の再試行の後）は変化 0 で評価済みにしない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.db import Connection, Pool
from app.core.logging import get_logger
from app.engine.affinity.config import AffinityConfig
from app.engine.affinity.evaluator import (
    PURPOSE,
    EvalOutcome,
    EvaluationError,
    build_input,
    evaluate,
    load_template,
)
from app.engine.affinity.guidance import build_guidance
from app.engine.affinity.manipulation import detect_manipulation
from app.engine.affinity.model import (
    AffinityRecord,
    apply_daily,
    apply_evaluation,
    cap_stage,
    max_stage_of,
    sensitivity_of,
    stage_pace_of,
    sum_deltas,
    turn_delta,
)
from app.engine.affinity.ports import AuditLog
from app.engine.types import (
    AFFINITY_AXES,
    STAGES,
    AffinityUpdateResult,
    RelationshipGuidance,
    TurnRecord,
)
from app.services.llm import LLMClient, LLMError
from app.services.persona import Persona, PersonaRepository
from app.services.types import CharacterRecord

logger = get_logger("engine.affinity")

REASON_MAX_CHARS: Final[int] = 500

# --- SQL（E1: affinity_states / affinity_history / characters だけ） ------------------------------------
_STATE_COLUMNS: Final[str] = (
    "closeness, trust, romance, awkwardness, discontent, possessiveness, stage, stage_changed_at, stage_candidate, "
    "stage_candidate_since, stage_candidate_turns, tension_high_since, last_interaction_at, daily_date, daily_delta, "
    "evaluated_until, user_turns, last_decayed_at, absence_days, absence_return_at"
)
_CHARACTER_COLUMNS: Final[str] = (
    "ch.id, ch.handle, ch.name, ch.avatar_url, ch.bio, ch.persona_key, ch.system_prompt, ch.is_active"
)

_LOAD_SQL: Final[str] = f"""
select {_CHARACTER_COLUMNS}, a.stage is not null as has_state,
       {", ".join("a." + c.strip() for c in _STATE_COLUMNS.split(","))}
  from public.characters ch
  left join public.affinity_states a on a.character_id = ch.id and a.user_id = $1
 where ch.id = $2
"""  # noqa: S608 - 列名は定数

_INSERT_DEFAULT_SQL: Final[str] = """
insert into public.affinity_states (user_id, character_id, created_at, updated_at)
values ($1, $2, $3, $3)
on conflict (user_id, character_id) do nothing
"""

_LOCK_SQL: Final[str] = f"""
select {_STATE_COLUMNS} from public.affinity_states
 where user_id = $1 and character_id = $2
 for update
"""  # noqa: S608 - 列名は定数

_UPDATE_SQL: Final[str] = """
update public.affinity_states
   set closeness = $3, trust = $4, romance = $5, awkwardness = $6, discontent = $7, possessiveness = $8,
       stage = $9, stage_changed_at = $10, stage_candidate = $11, stage_candidate_since = $12,
       stage_candidate_turns = $13, tension_high_since = $14, daily_date = $15, daily_delta = $16,
       evaluated_until = $17, user_turns = $18, last_decayed_at = $19
 where user_id = $1 and character_id = $2
"""

_HISTORY_SQL: Final[str] = """
insert into public.affinity_history
  (user_id, character_id, before, after, delta, stage_before, stage_after, reason, evaluator,
   manipulation_detected, source_message_ids, created_at)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
returning id
"""

# A6: 久しぶりの会話（JST の暦日で missed_you_days 日以上空いた）を記録する。罰は与えない
_TOUCH_SQL: Final[str] = """
insert into public.affinity_states as a (user_id, character_id, last_interaction_at, created_at, updated_at)
values ($1, $2, $3, $3, $3)
on conflict (user_id, character_id) do update set
  absence_days = case
    when a.last_interaction_at is not null
     and (($3 at time zone 'Asia/Tokyo')::date - (a.last_interaction_at at time zone 'Asia/Tokyo')::date) >= $4
    then least(($3 at time zone 'Asia/Tokyo')::date - (a.last_interaction_at at time zone 'Asia/Tokyo')::date, 32767)
    else a.absence_days end,
  absence_return_at = case
    when a.last_interaction_at is not null
     and (($3 at time zone 'Asia/Tokyo')::date - (a.last_interaction_at at time zone 'Asia/Tokyo')::date) >= $4
    then $3 else a.absence_return_at end,
  last_interaction_at = greatest(coalesce(a.last_interaction_at, $3), $3)
"""

# 日次処理の対象: 緊張・独占欲・昇格候補・緊張の継続があるペアと、ペルソナの段階の上限（max_stage）より上にいる
# ペア（YAML の上限を後から下げた場合に戻す）。$2 = 上限のあるキャラの id、$3 = 段階の並び、$4 = 上限の段階の番号
_MAINTENANCE_TARGETS_SQL: Final[str] = """
select user_id, character_id from public.affinity_states
 where (awkwardness > 0 or discontent > 0 or possessiveness > 0
        or stage_candidate is not null or tension_high_since is not null
        or coalesce(
             array_position($3::text[], stage) - 1 > ($4::int[])[array_position($2::uuid[], character_id)], false
           ))
   and ($1::uuid[] is null or user_id = any($1::uuid[]))
 order by user_id, character_id
"""

_CHARACTER_SQL: Final[str] = f"select {_CHARACTER_COLUMNS} from public.characters ch where ch.id = $1"  # noqa: S608
_ALL_CHARACTERS_SQL: Final[str] = f"select {_CHARACTER_COLUMNS} from public.characters ch"  # noqa: S608
# user_ids を渡したとき: そのユーザーと好感度の状態があるキャラだけ（全キャラのペルソナを読まない）
_PAIR_CHARACTERS_SQL: Final[str] = f"""
select {_CHARACTER_COLUMNS} from public.characters ch
 where ch.id in (select distinct character_id from public.affinity_states where user_id = any($1::uuid[]))
"""  # noqa: S608

_MAINTENANCE_UPDATE_SQL: Final[str] = """
update public.affinity_states
   set awkwardness = $3, discontent = $4, possessiveness = $5,
       stage = $6, stage_changed_at = $7, stage_candidate = $8, stage_candidate_since = $9,
       stage_candidate_turns = $10, tension_high_since = $11, last_decayed_at = $12
 where user_id = $1 and character_id = $2
"""


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if value is not None else default


def record_from_row(row: asyncpg.Record | Mapping[str, Any]) -> AffinityRecord:
    daily = row["daily_delta"] or {}
    return AffinityRecord(
        values={axis: _num(row[axis]) for axis in AFFINITY_AXES},
        stage=row["stage"],
        stage_changed_at=row["stage_changed_at"],
        stage_candidate=row["stage_candidate"],
        stage_candidate_since=row["stage_candidate_since"],
        stage_candidate_turns=int(row["stage_candidate_turns"] or 0),
        tension_high_since=row["tension_high_since"],
        last_interaction_at=row["last_interaction_at"],
        daily_date=row["daily_date"],
        daily_delta={str(k): float(v) for k, v in dict(daily).items()},
        evaluated_until=row["evaluated_until"],
        user_turns=int(row["user_turns"] or 0),
        last_decayed_at=row["last_decayed_at"],
        absence_days=row["absence_days"],
        absence_return_at=row["absence_return_at"],
    )


def _stage_change_cause(record: AffinityRecord, persona: Persona | None, *, default: str) -> str:
    """段階の変化の理由（監査ログ）。ペルソナの上限（max_stage）を超えていたペアを戻した場合は max_stage。"""
    return "max_stage" if cap_stage(record.stage, max_stage_of(persona)) != record.stage else default


def _character_from_row(row: asyncpg.Record | Mapping[str, Any]) -> CharacterRecord:
    return CharacterRecord(
        id=row["id"],
        handle=row["handle"],
        name=row["name"],
        avatar_url=row["avatar_url"],
        bio=row["bio"],
        persona_key=row["persona_key"],
        system_prompt=row["system_prompt"],
        is_active=row["is_active"],
    )


class AffinityEngineUnavailableError(RuntimeError):
    """LLM の一時的な障害で採点できなかった（何も書き込んでいない）。post_turn ジョブが後で再実行する。"""


class AffinityEngine:
    """好感度エンジン。コンストラクタで依存を受け取り、時刻（now）は各メソッドの引数で受け取る。"""

    def __init__(
        self,
        *,
        pool: Pool,
        llm: LLMClient,
        audit: AuditLog,
        personas: PersonaRepository,
        prompts_dir: Path,
        config: AffinityConfig | None = None,
    ) -> None:
        self._pool = pool
        self._llm = llm
        self._audit = audit
        self._personas = personas
        self._config = config or AffinityConfig()
        self._template = load_template(prompts_dir)

    @property
    def config(self) -> AffinityConfig:
        return self._config

    # ------------------------------------------------------------------
    # 読み取り
    # ------------------------------------------------------------------

    async def _load(
        self, conn: Connection, user_id: UUID, character_id: UUID
    ) -> tuple[CharacterRecord | None, AffinityRecord | None]:
        row = await conn.fetchrow(_LOAD_SQL, user_id, character_id)
        if row is None:
            return None, None
        character = _character_from_row(row)
        record = record_from_row(row) if row["has_state"] else None
        return character, record

    def _persona(self, character: CharacterRecord | None) -> Persona | None:
        return self._personas.for_character(character) if character is not None else None

    async def guidance(self, *, user_id: UUID, character_id: UUID, now: datetime) -> RelationshipGuidance:
        async with self._pool.acquire() as conn:
            character, record = await self._load(conn, user_id, character_id)
        return build_guidance(self._persona(character), record, now=now, config=self._config)

    async def stage_of(self, *, user_id: UUID, character_id: UUID) -> str:
        """現在の段階（自発メッセージの判定・評価ハーネス用）。行が無ければ acquaintance。

        ペルソナの上限（max_stage）までに収める。
        """
        async with self._pool.acquire() as conn:
            character, record = await self._load(conn, user_id, character_id)
        stage = record.stage if record is not None and record.stage in STAGES else "acquaintance"
        return cap_stage(stage, max_stage_of(self._persona(character)))

    # ------------------------------------------------------------------
    # 会話の記録（返答の後、リクエストの処理の中で呼ばれる。軽い 1 文）
    # ------------------------------------------------------------------

    async def touch_interaction(self, *, user_id: UUID, character_id: UUID, now: datetime) -> None:
        await self._pool.execute(_TOUCH_SQL, user_id, character_id, now, self._config.missed_you_days)

    # ------------------------------------------------------------------
    # 評価（post_turn ジョブ）
    # ------------------------------------------------------------------

    async def evaluate_turns(
        self, *, user_id: UUID, character_id: UUID, turns: Sequence[TurnRecord], now: datetime
    ) -> AffinityUpdateResult:
        config = self._config
        own = sorted(
            (t for t in turns if t.user_id == user_id and t.character_id == character_id),
            key=lambda t: t.occurred_at,
        )
        if not own:
            return AffinityUpdateResult(skipped_reason="no_turns")
        async with self._pool.acquire() as conn:
            character, stored = await self._load(conn, user_id, character_id)
        if character is None:
            return AffinityUpdateResult(skipped_reason="character_not_found")
        persona = self._persona(character)
        before_stage = stored.stage if stored is not None else "acquaintance"
        evaluated_until = stored.evaluated_until if stored is not None else None
        pending = [t for t in own if evaluated_until is None or t.occurred_at > evaluated_until]
        if not pending:
            return AffinityUpdateResult(
                stage_before=before_stage, stage_after=before_stage, skipped_reason="already_evaluated"
            )

        safety = [t for t in pending if t.safety_triggered]
        moderated = [t for t in pending if t.moderated and not t.safety_triggered]
        usable = [t for t in pending if not (t.safety_triggered or t.moderated)]
        flagged: list[tuple[TurnRecord, tuple[str, ...]]] = []
        clean: list[TurnRecord] = []
        for turn in usable:
            detection = detect_manipulation(turn.user_text)
            if detection.detected:
                flagged.append((turn, detection.labels))
            else:
                clean.append(turn)
        # 1 回の評価は新しい側から max_turns_per_call 件まで（古い分は評価済みとして扱う）
        overflow = clean[: max(len(clean) - config.max_turns_per_call, 0)]
        clean = clean[len(overflow) :]
        conversation_ids = sorted({str(t.conversation_id) for t in pending})

        if flagged:
            await self._audit.log(
                "affinity.manipulation_detected",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "conversation_ids": conversation_ids,
                    "message_ids": [t.user_message_id for t, _ in flagged],
                    "labels": sorted({label for _, labels in flagged for label in labels}),
                    "turns": len(flagged),
                },
                at=now,
            )

        outcome: EvalOutcome | None = None
        if clean:
            data = build_input(persona, character.name, [(t.user_text, t.reply_text) for t in clean], config)
            try:
                outcome = await evaluate(self._llm, self._template, data, config)
            except LLMError as exc:
                await self._llm_error(user_id, character_id, clean, now, error=str(exc), status=exc.status_code)
                if exc.retryable or exc.status_code is None:
                    # 一時的な障害: evaluated_until を進めずに失敗させ、ジョブの再実行で同じターンを採点し直す
                    # （最後の試行でも失敗したら post_turn はこの段階を諦める）
                    raise AffinityEngineUnavailableError(f"affinity evaluation LLM unavailable: {exc}") from exc
                return AffinityUpdateResult(
                    stage_before=before_stage,
                    stage_after=before_stage,
                    manipulation_detected=bool(flagged),
                    error="llm_error",
                )
            except EvaluationError as exc:
                await self._llm_error(
                    user_id,
                    character_id,
                    clean,
                    now,
                    error=f"invalid_output: {exc}",
                    raw=exc.raw,
                    model=exc.model,
                    usage=exc.usage,
                )
                return AffinityUpdateResult(
                    stage_before=before_stage,
                    stage_after=before_stage,
                    manipulation_detected=bool(flagged),
                    error="invalid_output",
                )

        # --- 変化量（1 ターンの上限まで）
        sensitivity = sensitivity_of(persona)
        per_turn: list[dict[str, float]] = []
        reasons: list[str] = []
        turn_details: list[dict[str, Any]] = []
        if outcome is not None:
            for index, turn in enumerate(clean):
                score = outcome.scores[index]
                delta = turn_delta(score.scores(), sensitivity, config)
                per_turn.append(delta)
                if score.reason and score.reason not in reasons:
                    reasons.append(score.reason)
                turn_details.append(
                    {
                        "message_id": turn.user_message_id,
                        "scores": score.scores(),
                        "delta": delta,
                        "reason": score.reason,
                    }
                )
        requested = sum_deltas(per_turn)
        if flagged:
            reasons.append("操作の試みを検知（変化なし）")
        if safety:
            reasons.append("安全対応のターンは評価しない")

        processed = [*clean, *overflow, *(t for t, _ in flagged), *safety, *moderated]
        newest = max(t.occurred_at for t in processed)
        evaluator = f"llm:{outcome.model}" if outcome is not None else "rule"
        source_ids: list[UUID] = []
        for t in sorted(processed, key=lambda t: t.occurred_at):
            source_ids.extend([t.user_message_id, t.character_message_id])

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(_INSERT_DEFAULT_SQL, user_id, character_id, now)
            row = await conn.fetchrow(_LOCK_SQL, user_id, character_id)
            if row is None:  # pragma: no cover - 直前に作成している
                raise RuntimeError("affinity state row missing")
            record = record_from_row(row)
            if record.evaluated_until is not None and newest <= record.evaluated_until:
                # 同時に別のジョブが評価した
                return AffinityUpdateResult(
                    stage_before=record.stage, stage_after=record.stage, skipped_reason="already_evaluated"
                )
            updated, applied = apply_evaluation(
                record,
                requested,
                now=now,
                evaluated_turns=len(clean),
                newest=newest,
                pace=stage_pace_of(persona),
                config=config,
                max_stage=max_stage_of(persona),
            )
            await self._write_state(conn, user_id, character_id, updated)
            history_id = await conn.fetchval(
                _HISTORY_SQL,
                user_id,
                character_id,
                record.snapshot(),
                updated.snapshot(),
                applied,
                record.stage,
                updated.stage,
                "・".join(reasons)[:REASON_MAX_CHARS] or None,
                evaluator,
                bool(flagged),
                source_ids,
                now,
            )

        payload: dict[str, Any] = {
            "history_id": history_id,
            "conversation_ids": conversation_ids,
            "message_ids": [t.user_message_id for t in processed],
            "requested": requested,
            "applied": applied,
            "before": record.snapshot(),
            "after": updated.snapshot(),
            "stage_before": record.stage,
            "stage_after": updated.stage,
            "stage_candidate": updated.stage_candidate,
            "turns_evaluated": len(clean),
            "turns_overflow": len(overflow),
            "turns_manipulation": len(flagged),
            "turns_skipped_safety": len(safety),
            "turns_skipped_moderated": len(moderated),
            "evaluator": evaluator,
            "purpose": PURPOSE,
            "turn_details": turn_details,
        }
        if outcome is not None:
            payload.update(
                {
                    "model": outcome.model,
                    "usage": outcome.usage,
                    "latency_ms": outcome.latency_ms,
                    "attempts": outcome.attempts,
                }
            )
        await self._audit.log("affinity.update", user_id=user_id, character_id=character_id, payload=payload, at=now)
        if safety or moderated:
            await self._audit.log(
                "affinity.skipped",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "reason": "safety" if safety else "moderated",
                    "message_ids": [t.user_message_id for t in [*safety, *moderated]],
                    "history_id": history_id,
                },
                at=now,
            )
        if updated.stage != record.stage:
            await self._stage_change_audit(
                user_id,
                character_id,
                before=record,
                after=updated,
                history_id=history_id,
                now=now,
                cause=_stage_change_cause(record, persona, default="evaluation"),
            )

        skipped_reason: str | None = None
        if not clean and not flagged:
            skipped_reason = "safety" if safety else "moderated"
        return AffinityUpdateResult(
            applied=applied,
            stage_before=record.stage,
            stage_after=updated.stage,
            manipulation_detected=bool(flagged),
            skipped_reason=skipped_reason,
        )

    async def _write_state(self, conn: Connection, user_id: UUID, character_id: UUID, r: AffinityRecord) -> None:
        await conn.execute(
            _UPDATE_SQL,
            user_id,
            character_id,
            r.value("closeness"),
            r.value("trust"),
            r.value("romance"),
            r.value("awkwardness"),
            r.value("discontent"),
            r.value("possessiveness"),
            r.stage,
            r.stage_changed_at,
            r.stage_candidate,
            r.stage_candidate_since,
            r.stage_candidate_turns,
            r.tension_high_since,
            r.daily_date,
            dict(r.daily_delta),
            r.evaluated_until,
            r.user_turns,
            r.last_decayed_at,
        )

    async def _llm_error(
        self,
        user_id: UUID,
        character_id: UUID,
        turns: Sequence[TurnRecord],
        now: datetime,
        *,
        error: str,
        status: int | None = None,
        raw: str | None = None,
        model: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        logger.warning(
            "affinity evaluation failed",
            extra={"fields": {"user_id": str(user_id), "character_id": str(character_id), "error": error[:300]}},
        )
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={
                "purpose": PURPOSE,
                "error": error[:500],
                "status_code": status,
                "raw": raw,
                "model": model,
                "usage": usage,
                "message_ids": [t.user_message_id for t in turns],
                "conversation_ids": sorted({str(t.conversation_id) for t in turns}),
            },
            at=now,
        )

    async def _stage_change_audit(
        self,
        user_id: UUID,
        character_id: UUID,
        *,
        before: AffinityRecord,
        after: AffinityRecord,
        history_id: int | None,
        now: datetime,
        cause: str,
    ) -> None:
        direction = "promote" if STAGES.index(after.stage) > STAGES.index(before.stage) else "demote"
        await self._audit.log(
            "affinity.stage_change",
            user_id=user_id,
            character_id=character_id,
            payload={
                "from": before.stage,
                "to": after.stage,
                "direction": direction,
                "cause": cause,
                "history_id": history_id,
                "values": after.snapshot(),
                "tension": round(after.tension, 2),
            },
            at=now,
        )

    # ------------------------------------------------------------------
    # 日次処理（スケジューラ affinity.daily。JST 04:00）
    # ------------------------------------------------------------------

    async def apply_daily_maintenance(self, *, now: datetime, user_ids: Sequence[UUID] | None = None) -> int:
        """緊張の軸・独占欲の減衰（A6）と、日数の経過による段階の遷移（A7）。更新したペアの数を返す。

        user_ids を渡すとそのユーザーだけを対象にする（評価ハーネス・テスト用。本番のスケジューラは渡さない）。
        """
        config = self._config
        personas: dict[UUID, Persona | None] = {}
        capped_ids: list[UUID] = []
        capped_ranks: list[int] = []
        rows = (
            await self._pool.fetch(_PAIR_CHARACTERS_SQL, list(user_ids))
            if user_ids is not None
            else await self._pool.fetch(_ALL_CHARACTERS_SQL)
        )
        for row in rows:
            persona = self._persona(_character_from_row(row))
            personas[row["id"]] = persona
            max_stage = max_stage_of(persona)
            if max_stage != STAGES[-1]:
                capped_ids.append(row["id"])
                capped_ranks.append(STAGES.index(max_stage))
        targets = await self._pool.fetch(
            _MAINTENANCE_TARGETS_SQL,
            list(user_ids) if user_ids is not None else None,
            capped_ids,
            list(STAGES),
            capped_ranks,
        )
        updated_count = 0
        for target in targets:
            user_id: UUID = target["user_id"]
            character_id: UUID = target["character_id"]
            try:
                changed = await self._maintain_pair(user_id, character_id, now, personas, config)
            except (asyncpg.PostgresError, OSError) as exc:
                logger.error(
                    "affinity maintenance failed for pair",
                    extra={"fields": {"user_id": str(user_id), "character_id": str(character_id), "error": repr(exc)}},
                )
                continue
            if changed:
                updated_count += 1
        return updated_count

    async def _maintain_pair(
        self,
        user_id: UUID,
        character_id: UUID,
        now: datetime,
        personas: dict[UUID, Persona | None],
        config: AffinityConfig,
    ) -> bool:
        async with self._pool.acquire() as conn:
            if character_id not in personas:
                row = await conn.fetchrow(_CHARACTER_SQL, character_id)
                personas[character_id] = self._persona(_character_from_row(row)) if row is not None else None
            persona = personas[character_id]
            async with conn.transaction():
                row = await conn.fetchrow(_LOCK_SQL, user_id, character_id)
                if row is None:
                    return False
                record = record_from_row(row)
                updated, change = apply_daily(
                    record, now=now, pace=stage_pace_of(persona), config=config, max_stage=max_stage_of(persona)
                )
                if updated == record:
                    return False
                await conn.execute(
                    _MAINTENANCE_UPDATE_SQL,
                    user_id,
                    character_id,
                    updated.value("awkwardness"),
                    updated.value("discontent"),
                    updated.value("possessiveness"),
                    updated.stage,
                    updated.stage_changed_at,
                    updated.stage_candidate,
                    updated.stage_candidate_since,
                    updated.stage_candidate_turns,
                    updated.tension_high_since,
                    updated.last_decayed_at,
                )
                history_id: int | None = None
                if change or updated.stage != record.stage:
                    history_id = await conn.fetchval(
                        _HISTORY_SQL,
                        user_id,
                        character_id,
                        record.snapshot(),
                        updated.snapshot(),
                        change,
                        record.stage,
                        updated.stage,
                        "時間の経過による減衰・段階の見直し",
                        "decay",
                        False,
                        [],
                        now,
                    )
        if change:
            await self._audit.log(
                "affinity.decay",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "history_id": history_id,
                    "delta": change,
                    "before": record.snapshot(),
                    "after": updated.snapshot(),
                },
                at=now,
            )
        if updated.stage != record.stage:
            await self._stage_change_audit(
                user_id,
                character_id,
                before=record,
                after=updated,
                history_id=history_id,
                now=now,
                cause=_stage_change_cause(record, persona, default="daily"),
            )
        return True
