"""評価専用のユーザー・キャラの作成と後片付け（共有の開発 DB を汚さない。EVAL_BRIEF）。

- キャラ: シード済みのキャラ（persona_key ごとに 1 体）を複製した評価専用のキャラ（handle = ev_<run>_<n>）。
  ペルソナ YAML は persona_key で同じものが使われる。予定・状態・投稿・キャラ側の記憶・好感度・約束・自発メッセージは
  すべてキャラ（とユーザー）への外部キーの on delete cascade で消える。シード済みのキャラの状態（character_states）には
  触れない。
- ユーザー: auth.users（email = eval-<run>-<user>@example.test）。profiles・会話・メッセージ・記憶は cascade で消える。
- 外部キーの無いもの（audit_logs・engine_jobs・engine_schedules）は ID・名前空間で消す。
- 実行期間は既定で 2030 年（現在の日付と重ならない）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from uuid import UUID

import asyncpg

from app.core.db import Pool
from app.services.persona import PersonaRepository

HANDLE_PREFIX: Final[str] = "ev_"
EMAIL_DOMAIN: Final[str] = "example.test"
_PLACEHOLDER_AVATAR: Final[str] = "https://placehold.co/400x400"
_RUN_ID_RE: Final = re.compile(r"^[a-z0-9]{4,12}$")


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def namespace_for(run_id: str) -> str:
    """engine_schedules の名前空間（ENGINE_SCHEDULE_NAMESPACE）。"""
    return f"eval-{run_id}"


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


@dataclass(frozen=True, slots=True)
class EvalCharacter:
    id: UUID
    persona_key: str
    name: str
    handle: str
    source_id: UUID | None  # 複製元（シード済みのキャラ）


@dataclass(slots=True)
class EvalWorld:
    run_id: str
    characters: dict[str, EvalCharacter] = field(default_factory=dict)  # persona_key → キャラ
    users: dict[str, UUID] = field(default_factory=dict)  # シミュレーションユーザーの key → user_id
    conversations: dict[str, UUID] = field(default_factory=dict)  # ユーザーの key → conversation_id

    @property
    def namespace(self) -> str:
        return namespace_for(self.run_id)

    @property
    def character_ids(self) -> list[UUID]:
        return [c.id for c in self.characters.values()]

    @property
    def user_ids(self) -> list[UUID]:
        return list(self.users.values())

    @property
    def conversation_ids(self) -> list[UUID]:
        return list(self.conversations.values())

    def email_for(self, user_key: str) -> str:
        return f"eval-{self.run_id}-{user_key.replace('_', '-')}@{EMAIL_DOMAIN}"


async def create_characters(
    pool: Pool, world: EvalWorld, persona_keys: Sequence[str], personas: PersonaRepository
) -> None:
    """persona_key ごとに、シード済みのキャラ（無ければペルソナ YAML の名前）を複製した評価専用のキャラを作る。"""
    rows = await pool.fetch(
        """
        select distinct on (persona_key) id, name, avatar_url, bio, persona_key, system_prompt
          from public.characters
         where persona_key = any($1::text[]) and is_active and handle not like 'ev\\_%'
         order by persona_key, created_at
        """,
        list(persona_keys),
    )
    seeded = {r["persona_key"]: r for r in rows}
    for index, key in enumerate(persona_keys):
        character_id = uuid.uuid4()
        handle = f"{HANDLE_PREFIX}{world.run_id}_{index}"
        row = seeded.get(key)
        persona = personas.get(key)
        name = row["name"] if row is not None else (persona.name if persona is not None else key)
        await pool.execute(
            """
            insert into public.characters (id, handle, name, avatar_url, bio, persona_key, system_prompt, is_active)
            values ($1, $2, $3, $4, $5, $6, $7, true)
            """,
            character_id,
            handle,
            name,
            row["avatar_url"] if row is not None else _PLACEHOLDER_AVATAR,
            row["bio"] if row is not None else "評価用",
            key,
            row["system_prompt"] if row is not None else f"あなたは{name}です。",
        )
        world.characters[key] = EvalCharacter(
            id=character_id,
            persona_key=key,
            name=name,
            handle=handle,
            source_id=row["id"] if row is not None else None,
        )


async def create_users(pool: Pool, world: EvalWorld, user_keys: Sequence[str]) -> None:
    for key in user_keys:
        user_id = uuid.uuid4()
        await pool.execute(
            """
            insert into auth.users
              (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
               raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
            values ('00000000-0000-0000-0000-000000000000', $1, 'authenticated', 'authenticated', $2, '', now(),
                    '{"provider":"email","providers":["email"]}', '{}', now(), now())
            """,
            user_id,
            world.email_for(key),
        )
        world.users[key] = user_id


@dataclass(frozen=True, slots=True)
class CleanupReport:
    characters: int
    users: int
    audit_logs: int
    jobs: int
    schedules: int

    def to_dict(self) -> dict[str, int]:
        return {
            "characters": self.characters,
            "users": self.users,
            "audit_logs": self.audit_logs,
            "jobs": self.jobs,
            "schedules": self.schedules,
        }


def _count(status: str) -> int:
    """asyncpg の execute の結果（'DELETE 3'）から件数を取り出す。"""
    parts = status.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def cleanup_run(conn: asyncpg.Connection[asyncpg.Record] | Pool, run_id: str) -> CleanupReport:
    """run_id で作ったものをすべて消す（キャラ・ユーザーの cascade + 監査ログ・ジョブ・定期実行の記録）。"""
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    handle_like = _like_escape(f"{HANDLE_PREFIX}{run_id}_") + "%"
    email_like = _like_escape(f"eval-{run_id}-") + "%@" + _like_escape(EMAIL_DOMAIN)
    character_ids: list[UUID] = [
        r["id"] for r in await conn.fetch("select id from public.characters where handle like $1", handle_like)
    ]
    user_ids: list[UUID] = [
        r["id"] for r in await conn.fetch("select id from auth.users where email like $1", email_like)
    ]
    conversation_ids: list[UUID] = [
        r["id"]
        for r in await conn.fetch(
            "select id from public.conversations where user_id = any($1::uuid[]) or character_id = any($2::uuid[])",
            user_ids,
            character_ids,
        )
    ]
    namespace = namespace_for(run_id)
    jobs = _count(
        await conn.execute(
            """
            delete from public.engine_jobs
             where dedupe_key = any($1::text[])
                or payload->>'user_id' = any($2::text[])
                or payload->>'character_id' = any($3::text[])
                or payload->>'conversation_id' = any($1::text[])
            """,
            [str(c) for c in conversation_ids],
            [str(u) for u in user_ids],
            [str(c) for c in character_ids],
        )
    )
    audit = _count(
        await conn.execute(
            """
            delete from public.audit_logs
             where user_id = any($1::uuid[]) or character_id = any($2::uuid[])
                or (event_type like 'engine.schedule%' and payload->>'task' like $3)
            """,
            user_ids,
            character_ids,
            _like_escape(namespace + ":") + "%",
        )
    )
    characters = _count(await conn.execute("delete from public.characters where id = any($1::uuid[])", character_ids))
    users = _count(await conn.execute("delete from auth.users where id = any($1::uuid[])", user_ids))
    schedules = _count(
        await conn.execute(
            "delete from public.engine_schedules where name like $1", _like_escape(namespace + ":") + "%"
        )
    )
    return CleanupReport(characters=characters, users=users, audit_logs=audit, jobs=jobs, schedules=schedules)


async def leftover_run_ids(conn: asyncpg.Connection[asyncpg.Record] | Pool) -> list[str]:
    """後片付けされていない評価の実行（--keep や中断）の run_id。"""
    ids: set[str] = set()
    for row in await conn.fetch("select handle from public.characters where handle like 'ev\\_%'"):
        match = re.match(rf"^{HANDLE_PREFIX}([a-z0-9]+)_\d+$", row["handle"])
        if match:
            ids.add(match.group(1))
    for row in await conn.fetch("select email from auth.users where email like 'eval-%@example.test'"):
        match = re.match(r"^eval-([a-z0-9]+)-", row["email"] or "")
        if match:
            ids.add(match.group(1))
    return sorted(ids)


async def rows_in_window(conn: Pool, start: datetime, end: datetime, world: EvalWorld) -> dict[str, int]:
    """実行期間の行のうち、この実行のものではない行の数（隔離の確認。他の評価・テストが同じ期間を使うと増える）。"""
    chars, users = world.character_ids, world.user_ids
    return {
        "character_events": int(
            await conn.fetchval(
                """select count(*) from public.character_events
                    where starts_at >= $1 and starts_at < $2 and not (character_id = any($3::uuid[]))""",
                start,
                end,
                chars,
            )
        ),
        "messages": int(
            await conn.fetchval(
                """select count(*) from public.messages m join public.conversations c on c.id = m.conversation_id
                    where m.created_at >= $1 and m.created_at < $2 and not (c.user_id = any($3::uuid[]))""",
                start,
                end,
                users,
            )
        ),
        "posts": int(
            await conn.fetchval(
                """select count(*) from public.posts
                    where published_at >= $1 and published_at < $2 and not (character_id = any($3::uuid[]))""",
                start,
                end,
                chars,
            )
        ),
    }
