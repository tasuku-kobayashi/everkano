"""SQLite persistence (stdlib sqlite3, WAL). One short-lived connection per operation keeps it thread-safe.

Tables: characters / character_versions / character_references / images / jobs / scene_presets.
Lists and nested objects are stored as JSON text. Timestamps are ISO-8601 (UTC).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import (
    Character,
    CharacterVersion,
    ImageRecord,
    Job,
    JobProgress,
    LockedParams,
    Quality,
    Reference,
    ScenePreset,
    iso,
    parse_dt,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  is_synthetic INTEGER NOT NULL,
  adult_confirmed INTEGER NOT NULL,
  current_version INTEGER NOT NULL,
  generation_count INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS character_versions (
  character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  locked TEXT NOT NULL,
  thumbnail_path TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  PRIMARY KEY (character_id, version)
);
CREATE TABLE IF NOT EXISTS character_references (
  id TEXT PRIMARY KEY,
  character_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  position INTEGER NOT NULL,
  image_path TEXT NOT NULL,
  source_image_id TEXT,
  embedding TEXT NOT NULL,
  quality TEXT NOT NULL,
  is_primary INTEGER NOT NULL,
  FOREIGN KEY (character_id, version) REFERENCES character_versions(character_id, version) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  character_id TEXT,
  character_name TEXT,
  character_version INTEGER,
  job_id TEXT,
  path TEXT NOT NULL,
  thumbnail_path TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  seed INTEGER,
  params_snapshot TEXT NOT NULL,
  similarity REAL,
  similarity_status TEXT,
  is_adult INTEGER NOT NULL DEFAULT 1,
  favorite INTEGER NOT NULL DEFAULT 0,
  rating INTEGER,
  tags TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS images_character_created ON images(character_id, created_at);
CREATE INDEX IF NOT EXISTS images_created ON images(created_at);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  progress TEXT NOT NULL,
  request TEXT NOT NULL,
  result TEXT,
  result_image_ids TEXT NOT NULL DEFAULT '[]',
  error TEXT,
  character_id TEXT,
  api_key_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS scene_presets (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def _normalize_bound(value: str) -> str:
    """Rows store `iso(utcnow())` (`+00:00`, microseconds) and are compared as strings, so a client bound must have
    the same shape: parse (tz-less = UTC, `Z` accepted) and re-serialise. Unparsable input is passed through as-is."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _loads(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # ------------------------------------------------------------------ characters
    @staticmethod
    def _row_character(row: sqlite3.Row) -> Character:
        return Character(
            id=row["id"],
            name=row["name"],
            tags=_loads(row["tags"], []),
            description=row["description"],
            status=row["status"],
            is_synthetic=bool(row["is_synthetic"]),
            adult_confirmed=bool(row["adult_confirmed"]),
            current_version=int(row["current_version"]),
            generation_count=int(row["generation_count"]),
            last_used_at=parse_dt(row["last_used_at"]),
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
        )

    def insert_character(self, character: Character) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO characters (id, name, tags, description, status, is_synthetic, adult_confirmed,
                   current_version, generation_count, last_used_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    character.id,
                    character.name,
                    json.dumps(character.tags, ensure_ascii=False),
                    character.description,
                    character.status,
                    int(character.is_synthetic),
                    int(character.adult_confirmed),
                    character.current_version,
                    character.generation_count,
                    iso(character.last_used_at),
                    iso(character.created_at),
                    iso(character.updated_at),
                ),
            )

    def update_character(self, character: Character) -> None:
        """Write the editable fields only. Counters (generation_count / last_used_at) are owned by
        `record_generation`, which the job worker calls concurrently — a full-row write would lose them."""
        character.updated_at = utcnow()
        with self.transaction() as conn:
            conn.execute(
                """UPDATE characters SET name=?, tags=?, description=?, status=?, is_synthetic=?, adult_confirmed=?,
                   current_version=?, updated_at=? WHERE id=?""",
                (
                    character.name,
                    json.dumps(character.tags, ensure_ascii=False),
                    character.description,
                    character.status,
                    int(character.is_synthetic),
                    int(character.adult_confirmed),
                    character.current_version,
                    iso(character.updated_at),
                    character.id,
                ),
            )

    def set_current_version(self, character_id: str, version: int) -> None:
        """Switch the current version only (create_version / rollback / save_as_version); no other column is touched."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE characters SET current_version=?, updated_at=? WHERE id=?",
                (version, iso(utcnow()), character_id),
            )

    def get_character(self, character_id: str) -> Character | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
        return self._row_character(row) if row else None

    def list_characters(
        self, *, q: str | None = None, tag: str | None = None, sort: str = "recent", include_drafts: bool = False
    ) -> list[Character]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_drafts:
            clauses.append("status = 'active'")
        if q:
            clauses.append("(name LIKE ? OR tags LIKE ? OR description LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = {
            "recent": "COALESCE(last_used_at, updated_at) DESC, created_at DESC",
            "name": "name COLLATE NOCASE ASC",
            "generations": "generation_count DESC, created_at DESC",
        }.get(sort, "COALESCE(last_used_at, updated_at) DESC")
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM characters {where} ORDER BY {order}", params).fetchall()  # noqa: S608
        characters = [self._row_character(r) for r in rows]
        if tag:
            characters = [c for c in characters if tag in c.tags]
        return characters

    def delete_character(self, character_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM character_references WHERE character_id=?", (character_id,))
            conn.execute("DELETE FROM character_versions WHERE character_id=?", (character_id,))
            conn.execute("DELETE FROM characters WHERE id=?", (character_id,))

    def record_generation(self, character_id: str, count: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE characters SET generation_count = generation_count + ?, last_used_at=?, updated_at=? "
                "WHERE id=?",
                (count, iso(utcnow()), iso(utcnow()), character_id),
            )

    # ------------------------------------------------------------------ versions / references
    def insert_version(self, version: CharacterVersion) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO character_versions (character_id, version, locked, thumbnail_path, note, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    version.character_id,
                    version.version,
                    json.dumps(version.locked.to_dict(), ensure_ascii=False),
                    version.thumbnail_path,
                    version.note,
                    iso(version.created_at),
                ),
            )
            for ref in version.references:
                conn.execute(
                    """INSERT INTO character_references (id, character_id, version, position, image_path,
                       source_image_id, embedding, quality, is_primary) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        ref.id,
                        ref.character_id,
                        ref.version,
                        ref.position,
                        ref.image_path,
                        ref.source_image_id,
                        json.dumps(ref.embedding),
                        json.dumps(ref.quality.to_dict(), ensure_ascii=False),
                        int(ref.is_primary),
                    ),
                )

    def update_version_locked(self, character_id: str, version: int, locked: LockedParams) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE character_versions SET locked=? WHERE character_id=? AND version=?",
                (json.dumps(locked.to_dict(), ensure_ascii=False), character_id, version),
            )

    def _references(self, conn: sqlite3.Connection, character_id: str, version: int) -> list[Reference]:
        rows = conn.execute(
            "SELECT * FROM character_references WHERE character_id=? AND version=? ORDER BY position",
            (character_id, version),
        ).fetchall()
        return [
            Reference(
                id=r["id"],
                character_id=r["character_id"],
                version=int(r["version"]),
                position=int(r["position"]),
                image_path=r["image_path"],
                source_image_id=r["source_image_id"],
                embedding=[float(x) for x in _loads(r["embedding"], [])],
                quality=Quality.from_dict(_loads(r["quality"], {})),
                is_primary=bool(r["is_primary"]),
            )
            for r in rows
        ]

    def _row_version(self, conn: sqlite3.Connection, row: sqlite3.Row) -> CharacterVersion:
        return CharacterVersion(
            character_id=row["character_id"],
            version=int(row["version"]),
            locked=LockedParams.from_dict(_loads(row["locked"], {})),
            thumbnail_path=row["thumbnail_path"],
            note=row["note"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            references=self._references(conn, row["character_id"], int(row["version"])),
        )

    def current_versions(self, characters: list[Character]) -> dict[str, CharacterVersion]:
        """Current version (with references) of many characters in two queries instead of 2 per character."""
        if not characters:
            return {}
        keys = [(c.id, c.current_version) for c in characters]
        placeholders = " OR ".join("(character_id=? AND version=?)" for _ in keys)
        params = [x for key in keys for x in key]
        versions_sql = f"SELECT * FROM character_versions WHERE {placeholders}"  # noqa: S608 - placeholders only
        refs_sql = f"SELECT * FROM character_references WHERE {placeholders} ORDER BY position"  # noqa: S608
        with self.connect() as conn:
            version_rows = conn.execute(versions_sql, params).fetchall()
            ref_rows = conn.execute(refs_sql, params).fetchall()
        refs: dict[tuple[str, int], list[Reference]] = {}
        for r in ref_rows:
            refs.setdefault((r["character_id"], int(r["version"])), []).append(
                Reference(
                    id=r["id"],
                    character_id=r["character_id"],
                    version=int(r["version"]),
                    position=int(r["position"]),
                    image_path=r["image_path"],
                    source_image_id=r["source_image_id"],
                    embedding=[float(x) for x in _loads(r["embedding"], [])],
                    quality=Quality.from_dict(_loads(r["quality"], {})),
                    is_primary=bool(r["is_primary"]),
                )
            )
        out: dict[str, CharacterVersion] = {}
        for row in version_rows:
            key = (row["character_id"], int(row["version"]))
            out[row["character_id"]] = CharacterVersion(
                character_id=row["character_id"],
                version=int(row["version"]),
                locked=LockedParams.from_dict(_loads(row["locked"], {})),
                thumbnail_path=row["thumbnail_path"],
                note=row["note"],
                created_at=parse_dt(row["created_at"]) or utcnow(),
                references=refs.get(key, []),
            )
        return out

    def get_version(self, character_id: str, version: int) -> CharacterVersion | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM character_versions WHERE character_id=? AND version=?", (character_id, version)
            ).fetchone()
            return self._row_version(conn, row) if row else None

    def list_versions(self, character_id: str) -> list[CharacterVersion]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM character_versions WHERE character_id=? ORDER BY version", (character_id,)
            ).fetchall()
            return [self._row_version(conn, r) for r in rows]

    def get_reference(self, reference_id: str) -> Reference | None:
        with self.connect() as conn:
            r = conn.execute("SELECT * FROM character_references WHERE id=?", (reference_id,)).fetchone()
        if not r:
            return None
        return Reference(
            id=r["id"],
            character_id=r["character_id"],
            version=int(r["version"]),
            position=int(r["position"]),
            image_path=r["image_path"],
            source_image_id=r["source_image_id"],
            embedding=[float(x) for x in _loads(r["embedding"], [])],
            quality=Quality.from_dict(_loads(r["quality"], {})),
            is_primary=bool(r["is_primary"]),
        )

    # ------------------------------------------------------------------ images
    @staticmethod
    def _row_image(row: sqlite3.Row) -> ImageRecord:
        return ImageRecord(
            id=row["id"],
            kind=row["kind"],
            character_id=row["character_id"],
            character_name=row["character_name"],
            character_version=row["character_version"],
            job_id=row["job_id"],
            path=row["path"],
            thumbnail_path=row["thumbnail_path"],
            width=int(row["width"]),
            height=int(row["height"]),
            seed=row["seed"],
            params_snapshot=_loads(row["params_snapshot"], {}),
            similarity=row["similarity"],
            similarity_status=row["similarity_status"],
            is_adult=bool(row["is_adult"]),
            favorite=bool(row["favorite"]),
            rating=row["rating"],
            tags=_loads(row["tags"], []),
            created_at=parse_dt(row["created_at"]) or utcnow(),
            deleted_at=parse_dt(row["deleted_at"]),
        )

    def insert_image(self, image: ImageRecord) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO images (id, kind, character_id, character_name, character_version, job_id, path,
                   thumbnail_path, width, height, seed, params_snapshot, similarity, similarity_status, is_adult,
                   favorite, rating, tags, created_at, deleted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    image.id,
                    image.kind,
                    image.character_id,
                    image.character_name,
                    image.character_version,
                    image.job_id,
                    image.path,
                    image.thumbnail_path,
                    image.width,
                    image.height,
                    image.seed,
                    json.dumps(image.params_snapshot, ensure_ascii=False),
                    image.similarity,
                    image.similarity_status,
                    int(image.is_adult),
                    int(image.favorite),
                    image.rating,
                    json.dumps(image.tags, ensure_ascii=False),
                    iso(image.created_at),
                    iso(image.deleted_at),
                ),
            )

    def update_image_flags(self, image: ImageRecord) -> None:
        """favorite / rating / tags only (never the columns other code paths write)."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE images SET favorite=?, rating=?, tags=? WHERE id=?",
                (int(image.favorite), image.rating, json.dumps(image.tags, ensure_ascii=False), image.id),
            )

    def set_image_similarity(self, image_id: str, similarity: float | None, status: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE images SET similarity=?, similarity_status=? WHERE id=?", (similarity, status, image_id)
            )

    def soft_delete_images(self, image_ids: list[str]) -> int:
        if not image_ids:
            return 0
        placeholders = ",".join("?" for _ in image_ids)
        with self.transaction() as conn:
            cur = conn.execute(
                f"UPDATE images SET deleted_at=? WHERE deleted_at IS NULL AND id IN ({placeholders})",  # noqa: S608
                [iso(utcnow()), *image_ids],
            )
        return int(cur.rowcount)

    def get_image(self, image_id: str, *, include_deleted: bool = False) -> ImageRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not row:
            return None
        image = self._row_image(row)
        if image.deleted_at and not include_deleted:
            return None
        return image

    def get_images(self, image_ids: list[str]) -> list[ImageRecord]:
        if not image_ids:
            return []
        placeholders = ",".join("?" for _ in image_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM images WHERE id IN ({placeholders}) AND deleted_at IS NULL",  # noqa: S608
                image_ids,
            ).fetchall()
        by_id = {r["id"]: self._row_image(r) for r in rows}
        return [by_id[i] for i in image_ids if i in by_id]

    def list_images(
        self,
        *,
        character_id: str | None = None,
        kind: str | None = None,
        job_id: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        min_similarity: float | None = None,
        favorite: bool | None = None,
        tag: str | None = None,
        q: str | None = None,
        seed: int | None = None,
        face_method: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ImageRecord], int]:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if character_id:
            clauses.append("character_id=?")
            params.append(character_id)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if job_id:
            clauses.append("job_id=?")
            params.append(job_id)
        if created_from:
            clauses.append("created_at>=?")
            params.append(_normalize_bound(created_from))
        if created_to:
            clauses.append("created_at<=?")
            params.append(_normalize_bound(created_to))
        if min_similarity is not None:
            clauses.append("similarity>=?")
            params.append(min_similarity)
        if favorite is not None:
            clauses.append("favorite=?")
            params.append(int(favorite))
        if tag:
            clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if q:
            clauses.append(
                "(json_extract(params_snapshot, '$.prompt') LIKE ? "
                "OR json_extract(params_snapshot, '$.positive') LIKE ?"
                " OR character_name LIKE ? OR tags LIKE ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like, like])
        if seed is not None:
            clauses.append("seed=?")
            params.append(seed)
        if face_method:
            clauses.append("params_snapshot LIKE ?")
            params.append(f'%"face_method": "{face_method}"%')
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM images WHERE {where}", params).fetchone()[0])  # noqa: S608
            rows = conn.execute(
                f"SELECT * FROM images WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",  # noqa: S608
                [*params, limit, offset],
            ).fetchall()
        return [self._row_image(r) for r in rows], total

    def images_of_character(self, character_id: str, *, include_deleted: bool = False) -> list[ImageRecord]:
        """Every image row of a character (soft-deleted rows included on request: the purge path needs them)."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM images WHERE character_id=?", (character_id,)).fetchall()
        images = [self._row_image(r) for r in rows]
        return images if include_deleted else [i for i in images if i.deleted_at is None]

    def detach_images_from_character(self, character_id: str) -> int:
        """Images of a deleted character keep the name snapshot but lose the (dangling) character_id."""
        with self.transaction() as conn:
            cur = conn.execute("UPDATE images SET character_id=NULL WHERE character_id=?", (character_id,))
        return int(cur.rowcount)

    def hard_delete_images(self, image_ids: list[str]) -> None:
        if not image_ids:
            return
        placeholders = ",".join("?" for _ in image_ids)
        with self.transaction() as conn:
            conn.execute(f"DELETE FROM images WHERE id IN ({placeholders})", image_ids)  # noqa: S608

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _row_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            type=row["type"],
            status=row["status"],
            progress=JobProgress.from_dict(_loads(row["progress"], {})),
            request=_loads(row["request"], {}),
            result=_loads(row["result"], None),
            result_image_ids=_loads(row["result_image_ids"], []),
            error=row["error"],
            character_id=row["character_id"],
            api_key_id=row["api_key_id"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            started_at=parse_dt(row["started_at"]),
            finished_at=parse_dt(row["finished_at"]),
        )

    def insert_job(self, job: Job) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO jobs (id, type, status, progress, request, result, result_image_ids, error, character_id,
                   api_key_id, created_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id,
                    job.type,
                    job.status,
                    json.dumps(job.progress.to_dict()),
                    json.dumps(job.request, ensure_ascii=False),
                    json.dumps(job.result, ensure_ascii=False) if job.result is not None else None,
                    json.dumps(job.result_image_ids),
                    job.error,
                    job.character_id,
                    job.api_key_id,
                    iso(job.created_at),
                    iso(job.started_at),
                    iso(job.finished_at),
                ),
            )

    def update_job(self, job: Job) -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, progress=?, result=?, result_image_ids=?, error=?, started_at=?,
                   finished_at=? WHERE id=?""",
                (
                    job.status,
                    json.dumps(job.progress.to_dict()),
                    json.dumps(job.result, ensure_ascii=False) if job.result is not None else None,
                    json.dumps(job.result_image_ids),
                    job.error,
                    iso(job.started_at),
                    iso(job.finished_at),
                    job.id,
                ),
            )

    def get_job(self, job_id: str) -> Job | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_job(row) if row else None

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[Job]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_job(r) for r in rows]

    def queued_jobs_before(self, job: Job) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='queued' AND created_at < ?", (iso(job.created_at),)
            ).fetchone()
        return int(row[0])

    def mark_interrupted_jobs(self) -> int:
        """At startup: jobs left `running`/`queued` by a previous process cannot be resumed."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='error', error=?, finished_at=? WHERE status IN ('running','queued')",
                ("サーバーの再起動により中断されました。", iso(utcnow())),
            )
        return int(cur.rowcount)

    # ------------------------------------------------------------------ scene presets
    def upsert_scene_preset(self, preset: ScenePreset) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO scene_presets (id, data, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (preset.id, json.dumps(preset.to_dict(), ensure_ascii=False), iso(utcnow())),
            )

    def delete_scene_preset(self, preset_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM scene_presets WHERE id=?", (preset_id,))

    def list_scene_preset_overrides(self) -> list[ScenePreset]:
        with self.connect() as conn:
            rows = conn.execute("SELECT data FROM scene_presets ORDER BY updated_at").fetchall()
        return [ScenePreset.from_dict(_loads(r["data"], {})) for r in rows]
