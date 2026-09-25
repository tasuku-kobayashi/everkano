"""Pydantic モデル（OpenAPI）と packages/shared/src/api.ts の契約が一致することを検証する。

期待値は **api.ts を読んで** 作る（Python 側に写しを持たない）。api.ts だけを変更しても、
Pydantic 側だけを変更しても、このテストが落ちる。

検査すること:
- api.ts の `export interface` がすべて OpenAPI の components にあり、逆に components の DTO / Request /
  Response もすべて api.ts にある（ErrorDetail は ApiErrorBody.error のインライン型として突合）
- フィールド名の集合・必須（`?` の有無）が一致する
- 型が一致する（string / UUID / ISODateString / number / boolean / 配列 / 文字列リテラルの union / 他の interface）
- null 許容: 応答の必須フィールドは `| null` と OpenAPI の null 許容が完全に一致する。
  リクエストは「TS が null を送れるなら API も受け付ける」こと（API 側が寛容なのは可）
- `ApiErrorCode` と API のエラーコード（Pydantic・app/core/errors.py）が一致する
- `MEMORY_TAG_*` 定数が API 側の値と一致する
"""

from __future__ import annotations

import re
import typing
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.errors import DEFAULT_MESSAGES, ApiErrorCode
from app.main import create_app
from app.services.types import MEMORY_TAG_SECRET, MEMORY_TAG_SUMMARY
from tests.conftest import REPO_ROOT, make_settings

API_TS = REPO_ROOT / "packages" / "shared" / "src" / "api.ts"

# ---------------------------------------------------------------------------
# api.ts の簡易パーサ（このファイルが使う構文: フラットな interface・インライン object・union・配列）
# ---------------------------------------------------------------------------

Shape = tuple[Any, ...]


@dataclass(frozen=True)
class TsField:
    name: str
    optional: bool
    type: str
    inline: dict[str, TsField] | None = None


@dataclass(frozen=True)
class TsContract:
    interfaces: dict[str, dict[str, TsField]]
    aliases: dict[str, str]
    consts: dict[str, str]


_FIELD_RE = re.compile(r"\s*([A-Za-z_]\w*)(\?)?\s*:\s*")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def _matching_brace(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unbalanced braces in api.ts near: {text[start : start + 80]!r}")


def _parse_fields(body: str) -> dict[str, TsField]:
    fields: dict[str, TsField] = {}
    index = 0
    while True:
        match = _FIELD_RE.match(body, index)
        if match is None:
            break
        name, optional = match.group(1), match.group(2) == "?"
        index = match.end()
        if body[index] == "{":
            end = _matching_brace(body, index)
            field = TsField(name, optional, "{}", _parse_fields(body[index + 1 : end]))
            index = end + 1
        else:
            end = body.index(";", index)
            field = TsField(name, optional, " ".join(body[index:end].split()))
            index = end
        fields[name] = field
        while index < len(body) and body[index] in "; \n\t\r":
            index += 1
    rest = body[index:].strip()
    assert rest == "", f"api.ts: could not parse interface body near {rest[:80]!r}"
    return fields


def parse_api_ts(text: str) -> TsContract:
    text = _strip_comments(text)
    interfaces: dict[str, dict[str, TsField]] = {}
    for match in re.finditer(r"export interface (\w+)\s*\{", text):
        start = match.end() - 1
        interfaces[match.group(1)] = _parse_fields(text[start + 1 : _matching_brace(text, start)])
    aliases = {
        m.group(1): " ".join(m.group(2).split()).lstrip("| ").strip()
        for m in re.finditer(r"export type (\w+)\s*=\s*(.+?);", text, flags=re.DOTALL)
    }
    consts = {m.group(1): m.group(2) for m in re.finditer(r'export const (\w+)\s*=\s*"([^"]*)"', text)}
    return TsContract(interfaces=interfaces, aliases=aliases, consts=consts)


def _split_union(type_str: str) -> list[str]:
    return [part.strip() for part in type_str.split("|") if part.strip()]


def ts_shape(type_str: str, contract: TsContract) -> tuple[Shape, bool]:
    """TS の型 → (正規化した形, null 許容)。"""
    parts = _split_union(type_str)
    nullable = "null" in parts
    parts = [p for p in parts if p != "null"]
    if parts and all(p.startswith('"') and p.endswith('"') for p in parts):
        return ("enum", frozenset(p.strip('"') for p in parts)), nullable
    assert len(parts) == 1, f"unsupported TS union: {type_str!r}"
    base = parts[0]
    if base.endswith("[]"):
        item, _ = ts_shape(base[:-2], contract)
        return ("array", item), nullable
    primitives: dict[str, Shape] = {
        "string": ("string", None),
        "UUID": ("string", "uuid"),
        "ISODateString": ("string", "date-time"),
        "number": ("number",),
        "boolean": ("boolean",),
    }
    if base in primitives:
        return primitives[base], nullable
    if base in contract.aliases:
        shape, alias_nullable = ts_shape(contract.aliases[base], contract)
        return shape, nullable or alias_nullable
    if base in contract.interfaces:
        return ("ref", base), nullable
    raise AssertionError(f"unknown TS type in api.ts: {type_str!r}")


def openapi_shape(schema: dict[str, Any]) -> tuple[Shape, bool]:
    """OpenAPI のプロパティ → (正規化した形, null 許容)。"""
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        nullable = len(options) != len(schema["anyOf"])
        assert len(options) == 1, f"unsupported anyOf: {schema}"
        shape, inner_nullable = openapi_shape(options[0])
        return shape, nullable or inner_nullable
    if "$ref" in schema:
        return ("ref", schema["$ref"].rsplit("/", 1)[-1]), False
    if "enum" in schema:
        return ("enum", frozenset(schema["enum"])), False
    kind = schema.get("type")
    if kind == "string":
        return ("string", schema.get("format")), False
    if kind in ("number", "integer"):
        return ("number",), False
    if kind == "boolean":
        return ("boolean",), False
    if kind == "array":
        item, _ = openapi_shape(schema["items"])
        return ("array", item), False
    raise AssertionError(f"unsupported OpenAPI schema: {schema}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def contract() -> TsContract:
    return parse_api_ts(API_TS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return create_app(make_settings()).openapi()


def _components(schema: dict[str, Any]) -> dict[str, Any]:
    components: dict[str, Any] = schema["components"]["schemas"]
    return components


def _compare_object(
    name: str,
    ts_fields: dict[str, TsField],
    component_name: str,
    schema: dict[str, Any],
    contract: TsContract,
    *,
    is_request: bool,
) -> None:
    component = _components(schema)[component_name]
    properties: dict[str, Any] = component.get("properties", {})
    assert set(properties) == set(ts_fields), (
        f"{name}: field names differ (api.ts only: {sorted(set(ts_fields) - set(properties))}, "
        f"OpenAPI only: {sorted(set(properties) - set(ts_fields))})"
    )
    ts_required = {f.name for f in ts_fields.values() if not f.optional}
    assert set(component.get("required", [])) == ts_required, f"{name}: required fields differ from api.ts ('?')"
    for field in ts_fields.values():
        where = f"{name}.{field.name}"
        if field.inline is not None:
            api_shape, _ = openapi_shape(properties[field.name])
            assert api_shape[0] == "ref", f"{where}: api.ts has an inline object, OpenAPI must reference a component"
            _compare_object(where, field.inline, api_shape[1], schema, contract, is_request=is_request)
            continue
        ts, ts_nullable = ts_shape(field.type, contract)
        api, api_nullable = openapi_shape(properties[field.name])
        assert api == ts, f"{where}: type differs (api.ts {field.type!r} -> {ts}, OpenAPI -> {api})"
        if is_request:
            # クライアントが null を送れるなら API は受け付ける必要がある（API がより寛容なのは可）
            assert api_nullable or not ts_nullable, f"{where}: api.ts allows null but the API rejects it"
        elif not field.optional:
            assert api_nullable == ts_nullable, f"{where}: nullability differs (api.ts {field.type!r})"


def _compare_all(contract: TsContract, schema: dict[str, Any]) -> None:
    for name, fields in contract.interfaces.items():
        _compare_object(name, fields, name, schema, contract, is_request=name.endswith("Request"))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_parser_reads_the_whole_contract(contract: TsContract) -> None:
    # パーサが api.ts を読めていること（空集合同士の比較で素通りしないように）
    assert len(contract.interfaces) >= 17
    assert {"ChatRequest", "ChatResponse", "MemoryDTO", "ApiErrorBody"} <= set(contract.interfaces)
    assert contract.interfaces["ApiErrorBody"]["error"].inline is not None
    assert "ApiErrorCode" in contract.aliases


def test_every_interface_matches_openapi(contract: TsContract, schema: dict[str, Any]) -> None:
    components = _components(schema)
    # ErrorDetail は api.ts では ApiErrorBody.error のインライン型
    assert set(components) - {"ErrorDetail"} == set(contract.interfaces), (
        f"api.ts only: {sorted(set(contract.interfaces) - set(components))}, "
        f"OpenAPI only: {sorted(set(components) - {'ErrorDetail'} - set(contract.interfaces))}"
    )
    _compare_all(contract, schema)


def test_error_codes_match_everywhere(contract: TsContract, schema: dict[str, Any]) -> None:
    ts_codes, _ = ts_shape("ApiErrorCode", contract)
    assert ts_codes[0] == "enum"
    codes = set(ts_codes[1])
    assert set(_components(schema)["ErrorDetail"]["properties"]["code"]["enum"]) == codes
    assert set(typing.get_args(ApiErrorCode)) == codes
    assert set(DEFAULT_MESSAGES) == codes


def test_memory_tag_constants(contract: TsContract) -> None:
    assert contract.consts["MEMORY_TAG_SECRET"] == MEMORY_TAG_SECRET
    assert contract.consts["MEMORY_TAG_SUMMARY"] == MEMORY_TAG_SUMMARY


def test_parser_detects_drift(contract: TsContract, schema: dict[str, Any]) -> None:
    """api.ts 側だけの変更（必須化・改名・型変更）が検出されること。"""
    source = API_TS.read_text(encoding="utf-8")
    drifted = [
        source.replace("  user_message: MessageDTO;", "  user_message?: MessageDTO;"),
        source.replace("  memories_used: UUID[];", "  memories_used_ids: UUID[];"),
        source.replace("  moderated: boolean;", "  moderated: string;"),
        source.replace("  greeting_message: MessageDTO | null;", "  greeting_message: MessageDTO;"),
    ]
    for text in drifted:
        assert text != source
        with pytest.raises(AssertionError):
            _compare_all(parse_api_ts(text), schema)


def test_length_constraints(schema: dict[str, Any]) -> None:
    components = _components(schema)
    assert components["ChatRequest"]["properties"]["message"]["maxLength"] == 2000
    assert components["CreateCommentRequest"]["properties"]["body"]["maxLength"] == 500
    assert components["CreateMemoryRequest"]["properties"]["content"]["maxLength"] == 500


def test_paths(schema: dict[str, Any]) -> None:
    paths = schema["paths"]
    expected = {
        ("/health", "get"),
        ("/conversations", "post"),
        ("/chat", "post"),
        ("/memories", "get"),
        ("/memories", "post"),
        ("/memories/{memory_id}", "patch"),
        ("/memories/{memory_id}", "delete"),
        ("/comments", "post"),
        ("/comments/generate", "post"),
    }
    actual = {(path, method) for path, ops in paths.items() for method in ops}
    assert actual == expected
    # /health 以外は Bearer 認証
    for path, method in expected - {("/health", "get")}:
        assert paths[path][method].get("security"), f"{method} {path} must require auth"
    assert not paths["/health"]["get"].get("security")
