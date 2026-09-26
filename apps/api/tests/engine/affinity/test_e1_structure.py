"""E1 / A9: 課金・購入・トークン消費は好感度に一切影響しない — を「構造」で検査する。

- app/engine/affinity/ の import は許可リストのモジュールだけ（投稿・いいね・課金・コメントのモジュールを読まない）
- SQL が参照するテーブルは affinity_states / affinity_history / characters だけ
- 識別子（変数・引数・属性・関数名）に購入・有料・投稿などの語が無い
- 評価の入力（AffinityEvalInput / EvalTurn）・evaluate_turns の入力（TurnRecord）に課金のデータのフィールドが無い
- マイグレーションで affinity_* から posts / post_private_assets / likes への外部キーが無い
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import fields
from pathlib import Path

import pytest

from app.engine import affinity as affinity_package
from app.engine.affinity.evaluator import AffinityEvalInput, EvalTurn
from app.engine.affinity.service import AffinityEngine
from app.engine.types import TurnRecord
from tests.conftest import REPO_ROOT

PACKAGE_DIR = Path(affinity_package.__file__).resolve().parent
MIGRATIONS_DIR = REPO_ROOT / "infra" / "supabase" / "migrations"

ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # 標準ライブラリ
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "json",
        "pathlib",
        "re",
        "time",
        "types",
        "typing",
        "unicodedata",
        "uuid",
        # サードパーティ
        "asyncpg",
        "pydantic",
        # アプリ（会話の本文・ペルソナ・LLM・監査・DB 接続だけ）
        "app.core.db",
        "app.core.logging",
        "app.engine.types",
        "app.services.audit",
        "app.services.llm",
        "app.services.persona",
        "app.services.prompt",
        "app.services.types",
    }
)
ALLOWED_TABLES: frozenset[str] = frozenset({"affinity_states", "affinity_history", "characters"})
FORBIDDEN_TABLES: frozenset[str] = frozenset({"posts", "post_private_assets", "likes", "comments", "purchases"})
# 識別子を snake_case の語に分けたとき、どれかがこれに一致したら違反
FORBIDDEN_WORDS: frozenset[str] = frozenset(
    {
        "paid",
        "purchase",
        "purchases",
        "purchased",
        "payment",
        "payments",
        "billing",
        "price",
        "prices",
        "post",
        "posts",
        "like",
        "likes",
        "wallet",
        "balance",
        "coin",
        "coins",
        "gift",
        "gifts",
        "spend",
        "spent",
        "charge",
        "revenue",
        "subscription",
        "premium",
    }
)
_SQL_KEYWORDS_RE = re.compile(r"\b(select|insert|update|delete)\b", re.IGNORECASE)
# 「on conflict ... do update set」の set はテーブル名ではない
_TABLE_RE = re.compile(r"\b(?:from|join|into|update|table)\s+(?!set\b)(?:public\.)?([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _modules() -> list[Path]:
    files = sorted(PACKAGE_DIR.glob("*.py"))
    assert files, "affinity package not found"
    return files


def _trees() -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))) for path in _modules()]


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # 相対 import は使っていない（念のため）
            names.add(node.module or "")
    return names


def _identifiers(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg) or (isinstance(node, ast.keyword) and node.arg):
            names.add(node.arg)
    return names


def _sql_strings(tree: ast.Module) -> list[str]:
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            strings.append(
                "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            )
    return [s for s in strings if _SQL_KEYWORDS_RE.search(s) and re.search(r"\b(from|into|update)\b", s, re.I)]


def test_affinity_imports_only_allowed_modules() -> None:
    for path, tree in _trees():
        for name in _imports(tree):
            if name.startswith("app.engine.affinity"):
                continue
            assert name in ALLOWED_IMPORTS, f"{path.name}: import {name!r} is not allowed (E1 / A9)"


def test_affinity_sql_touches_only_allowed_tables() -> None:
    seen: set[str] = set()
    for path, tree in _trees():
        for sql in _sql_strings(tree):
            tables = {t.lower() for t in _TABLE_RE.findall(sql)}
            seen |= tables
            assert tables <= ALLOWED_TABLES, f"{path.name}: SQL references {sorted(tables - ALLOWED_TABLES)}"
            lowered = sql.lower()
            for table in FORBIDDEN_TABLES | {"price_tokens", "is_paid"}:
                assert not re.search(rf"\b{table}\b", lowered), f"{path.name}: SQL mentions {table}"
    # 検査が空振りしていないこと（SQL を実際に見つけている）
    assert {"affinity_states", "affinity_history", "characters"} <= seen


def test_affinity_identifiers_have_no_commerce_words() -> None:
    for path, tree in _trees():
        for identifier in _identifiers(tree):
            words = {w for w in re.split(r"_+|(?<=[a-z])(?=[A-Z])", identifier) if w}
            lowered = {w.lower() for w in words}
            assert not (lowered & FORBIDDEN_WORDS), f"{path.name}: identifier {identifier!r} looks commerce-related"


def test_evaluator_input_has_no_commerce_fields() -> None:
    assert set(AffinityEvalInput.model_fields) == {"persona_name", "persona_notes", "possessiveness_enabled", "turns"}
    assert set(EvalTurn.model_fields) == {"index", "user", "character"}
    assert AffinityEvalInput.model_config.get("extra") == "forbid"
    assert EvalTurn.model_config.get("extra") == "forbid"
    for model in (AffinityEvalInput, EvalTurn):
        for name in model.model_fields:
            assert not (set(name.split("_")) & FORBIDDEN_WORDS | {"token", "tokens"} & set(name.split("_")))


def test_turn_record_is_conversation_only() -> None:
    names = {f.name for f in fields(TurnRecord)}
    assert names == {
        "conversation_id",
        "user_id",
        "character_id",
        "user_message_id",
        "character_message_id",
        "user_text",
        "reply_text",
        "occurred_at",
        "moderated",
        "safety_triggered",
    }
    for name in names:
        assert not (set(name.split("_")) & (FORBIDDEN_WORDS | {"token", "tokens"}))


@pytest.mark.parametrize("method", ["evaluate_turns", "guidance", "touch_interaction", "apply_daily_maintenance"])
def test_public_methods_take_no_commerce_arguments(method: str) -> None:
    signature = inspect.signature(getattr(AffinityEngine, method))
    for name in signature.parameters:
        assert not (set(name.split("_")) & (FORBIDDEN_WORDS | {"token", "tokens"})), (method, name)


def test_constructor_takes_no_commerce_dependencies() -> None:
    signature = inspect.signature(AffinityEngine.__init__)
    assert set(signature.parameters) == {"self", "pool", "llm", "audit", "personas", "prompts_dir", "config"}


def _create_table_bodies(sql: str, prefix: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for match in re.finditer(rf"create table public\.({prefix}\w*)\s*\((.*?)\n\);", sql, re.DOTALL | re.IGNORECASE):
        bodies[match.group(1)] = match.group(2)
    return bodies


def test_no_foreign_keys_from_affinity_tables_to_commerce_tables() -> None:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations
    found_tables: set[str] = set()
    for path in migrations:
        sql = path.read_text(encoding="utf-8")
        for table, body in _create_table_bodies(sql, "affinity_").items():
            found_tables.add(table)
            for forbidden in FORBIDDEN_TABLES:
                assert not re.search(rf"references\s+public\.{forbidden}\b", body, re.I), (path.name, table)
        # 後から追加された外部キー（alter table affinity_* ... references posts 等）も無いこと
        for statement in re.findall(r"alter table public\.affinity_\w+.*?;", sql, re.DOTALL | re.IGNORECASE):
            for forbidden in FORBIDDEN_TABLES:
                assert not re.search(rf"references\s+public\.{forbidden}\b", statement, re.I), path.name
    assert {"affinity_states", "affinity_history"} <= found_tables


def test_checker_detects_violations() -> None:
    """検査そのものが違反を見逃さないこと（空振りの防止）。"""
    source = (
        "from app.models.comments import CommentDTO\n"
        "import app.services.payments\n"
        'SQL = "select p.price_tokens from public.posts p join public.likes l on l.post_id = p.id"\n'
        "paid_total = 1\n"
        "def apply(purchase_amount: int) -> None: ...\n"
    )
    tree = ast.parse(source)
    assert {"app.models.comments", "app.services.payments"} <= _imports(tree) - ALLOWED_IMPORTS
    sqls = _sql_strings(tree)
    assert sqls
    assert {"posts", "likes"} <= {t.lower() for t in _TABLE_RE.findall(sqls[0])}
    identifiers = _identifiers(tree)
    flagged = {i for i in identifiers if {w.lower() for w in i.split("_") if w} & FORBIDDEN_WORDS}
    assert {"paid_total", "purchase_amount"} <= flagged
