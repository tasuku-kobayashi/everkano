"""POST /conversations — 会話の取得または作成。

新規作成時はペルソナの `greeting`（無ければ「はじめまして、{name}だよ。」）を
キャラの最初のメッセージとして同じトランザクションで保存する。
会話・挨拶の日時は時計（Clock）の時刻で保存する（評価ハーネスの時間の早送りで順序が崩れないように）。
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from app.core.db import Pool
from app.core.errors import not_found
from app.core.security import CurrentUser
from app.engine.types import Clock, SystemClock
from app.models.dm import CreateConversationResponse
from app.services.audit import AuditLogger
from app.services.characters import (
    CONVERSATION_COLUMNS,
    MESSAGE_COLUMNS,
    conversation_dto,
    fetch_active_character,
    message_dto,
)
from app.services.persona import PersonaRepository

_SELECT_CONVERSATION_SQL: Final[str] = (
    f"select {CONVERSATION_COLUMNS} from public.conversations where user_id = $1 and character_id = $2"  # noqa: S608
)


class ConversationService:
    def __init__(
        self, *, pool: Pool, personas: PersonaRepository, audit: AuditLogger, clock: Clock | None = None
    ) -> None:
        self._pool = pool
        self._personas = personas
        self._audit = audit
        self._clock: Clock = clock or SystemClock()

    async def get_or_create(self, user: CurrentUser, character_id: UUID) -> CreateConversationResponse:
        async with self._pool.acquire() as conn:
            character = await fetch_active_character(conn, character_id)
            if character is None:
                raise not_found("キャラクターが見つかりません。")
            persona = self._personas.for_character(character)
            greeting_text = persona.greeting.strip() or f"はじめまして、{character.name}だよ。"
            now = self._clock.now()
            async with conn.transaction():
                inserted_id: UUID | None = await conn.fetchval(
                    """
                    insert into public.conversations
                      (user_id, character_id, created_at, last_message_at, user_last_read_at)
                    values ($1, $2, $3, $3, $3)
                    on conflict (user_id, character_id) do nothing
                    returning id
                    """,
                    user.id,
                    character.id,
                    now,
                )
                if inserted_id is None:
                    existing = await conn.fetchrow(_SELECT_CONVERSATION_SQL, user.id, character.id)
                    if existing is None:  # pragma: no cover - on conflict の直後なので存在する
                        raise not_found("会話が見つかりません。")
                    return CreateConversationResponse(
                        conversation=conversation_dto(existing), created=False, greeting_message=None
                    )
                greeting_row = await conn.fetchrow(
                    f"""
                    insert into public.messages (conversation_id, sender_type, body, created_at)
                    values ($1, 'character', $2, $3)
                    returning {MESSAGE_COLUMNS}
                    """,  # noqa: S608 - 列名は定数
                    inserted_id,
                    greeting_text,
                    now,
                )
                conversation_row = await conn.fetchrow(_SELECT_CONVERSATION_SQL, user.id, character.id)
        if greeting_row is None or conversation_row is None:  # pragma: no cover
            raise RuntimeError("conversation insert returned no row")
        greeting = message_dto(greeting_row)
        await self._audit.log(
            "conversation.create",
            user_id=user.id,
            character_id=character.id,
            payload={
                "conversation_id": inserted_id,
                "greeting_message_id": greeting.id,
                "greeting": greeting.body,
                "persona_key": persona.key,
                "persona_fallback": persona.is_fallback,
            },
            at=now,
        )
        return CreateConversationResponse(
            conversation=conversation_dto(conversation_row), created=True, greeting_message=greeting
        )
