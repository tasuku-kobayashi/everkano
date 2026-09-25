"""Pydantic モデル（OpenAPI）と packages/shared/src/api.ts の契約がフィールド単位で一致することを検証する。

api.ts を変更したら、ここの期待値と apps/api/app/models/*.py を合わせて更新すること。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.main import create_app
from tests.conftest import make_settings

# packages/shared/src/api.ts の interface 定義（フィールド名, 必須フィールド）
CONTRACT: dict[str, tuple[set[str], set[str]]] = {
    "HealthResponse": (
        {"status", "version", "env", "llm_mode", "embedding_mode", "db"},
        {"status", "version", "env", "llm_mode", "embedding_mode", "db"},
    ),
    "ConversationDTO": (
        {"id", "user_id", "character_id", "last_message_at", "user_last_read_at", "created_at"},
        {"id", "user_id", "character_id", "last_message_at", "user_last_read_at", "created_at"},
    ),
    "MessageDTO": (
        {"id", "conversation_id", "sender_type", "body", "created_at"},
        {"id", "conversation_id", "sender_type", "body", "created_at"},
    ),
    "CreateConversationRequest": ({"character_id"}, {"character_id"}),
    "CreateConversationResponse": (
        {"conversation", "created", "greeting_message"},
        {"conversation", "created", "greeting_message"},
    ),
    "ChatRequest": (
        {"character_id", "conversation_id", "message"},
        {"character_id", "conversation_id", "message"},
    ),
    "ChatResponse": (
        {"message_id", "reply", "memories_used", "memories_created", "user_message", "character_message", "moderated"},
        {"message_id", "reply", "memories_used", "memories_created", "user_message", "character_message", "moderated"},
    ),
    "MemoryDTO": (
        {
            "id",
            "character_id",
            "content",
            "importance",
            "tags",
            "is_user_edited",
            "source_message_id",
            "created_at",
            "updated_at",
        },
        {
            "id",
            "character_id",
            "content",
            "importance",
            "tags",
            "is_user_edited",
            "source_message_id",
            "created_at",
            "updated_at",
        },
    ),
    "ListMemoriesResponse": ({"memories"}, {"memories"}),
    "CreateMemoryRequest": ({"character_id", "content", "importance", "tags"}, {"character_id", "content"}),
    "UpdateMemoryRequest": ({"content", "importance", "tags"}, set()),
    "CommentDTO": (
        {
            "id",
            "post_id",
            "parent_comment_id",
            "author_type",
            "author_user_id",
            "author_character_id",
            "body",
            "created_at",
        },
        {
            "id",
            "post_id",
            "parent_comment_id",
            "author_type",
            "author_user_id",
            "author_character_id",
            "body",
            "created_at",
        },
    ),
    "CreateCommentRequest": ({"post_id", "body", "parent_comment_id"}, {"post_id", "body"}),
    "CreateCommentResponse": ({"comment", "reply_scheduled"}, {"comment", "reply_scheduled"}),
    "GenerateCommentRequest": ({"post_id", "parent_comment_id"}, {"post_id", "parent_comment_id"}),
    "GenerateCommentResponse": ({"comment"}, {"comment"}),
    "ApiErrorBody": ({"error"}, {"error"}),
    "ErrorDetail": ({"code", "message", "request_id"}, {"code", "message"}),
}

API_ERROR_CODES = {
    "unauthorized",
    "forbidden",
    "account_deleted",
    "not_found",
    "validation_error",
    "moderation_blocked",
    "rate_limited",
    "llm_unavailable",
    "internal_error",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return create_app(make_settings()).openapi()


@pytest.mark.parametrize("name", sorted(CONTRACT))
def test_schema_fields_match_ts_contract(schema: dict[str, Any], name: str) -> None:
    components = schema["components"]["schemas"]
    assert name in components, f"{name} missing from OpenAPI components"
    fields, required = CONTRACT[name]
    assert set(components[name]["properties"]) == fields
    # 応答モデルは全フィールド必須（null 許容は別）。リクエストの任意項目は required に含まれないこと
    assert required <= set(components[name].get("required", []))
    if name.endswith("Request"):
        assert set(components[name].get("required", [])) == required


def test_enums(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    assert set(components["ErrorDetail"]["properties"]["code"]["enum"]) == API_ERROR_CODES
    assert set(components["MessageDTO"]["properties"]["sender_type"]["enum"]) == {"user", "character"}
    assert set(components["CommentDTO"]["properties"]["author_type"]["enum"]) == {"user", "character"}
    assert set(components["HealthResponse"]["properties"]["llm_mode"]["enum"]) == {"live", "mock"}
    assert set(components["HealthResponse"]["properties"]["embedding_mode"]["enum"]) == {"live", "hash"}


def test_length_constraints(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
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
