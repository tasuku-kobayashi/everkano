# 04. Python API

`apps/api`（FastAPI）。スキーマの正は **[docs/api/openapi.json](../api/openapi.json)**（FastAPI から生成、CI で最新か検査）と、Web 側の型
**`packages/shared/src/api.ts`**（Pydantic モデルとフィールド単位で一致することを `apps/api/tests/test_openapi_contract.py` で検証）。
ローカルでは http://localhost:8000/docs で Swagger UI を開ける（`APP_ENV=production` では無効）。実装の構成は [apps/api/README.md](../../apps/api/README.md)。

## 共通

| 項目             | 内容                                                                                                                              |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| 認証             | `Authorization: Bearer <Supabase のアクセストークン>`（`GET /health` 以外すべて）。検証は [ADR-0007](../adr/0007-jwt-verification-jwks-and-hs256.md) |
| 形式             | JSON（キーは snake_case）。日時は ISO 8601（UTC）                                                                                 |
| リクエスト ID    | すべてのレスポンスに `X-Request-ID`。リクエストで送れば（英数と `._-`、128 文字まで）それを使い、無ければ生成。監査ログの `request_id` と同じ |
| CORS             | `CORS_ALLOW_ORIGINS`（カンマ区切り）。メソッド GET / POST / PATCH / DELETE / OPTIONS、ヘッダー `Authorization` / `Content-Type` / `X-Request-ID`、公開ヘッダー `X-Request-ID` / `Retry-After`、Cookie は使わない |
| エラー           | `{"error": {"code", "message", "request_id"}}`。`message` はそのまま表示できる日本語                                              |
| 所有者チェック   | 他人の会話・記憶、見えない投稿・キャラは **404**（存在を教えない。[ADR-0003](../adr/0003-api-db-connection-asyncpg.md)）        |
| 退会済み         | 有効なトークンでも `profiles.deleted_at` があれば 403 `account_deleted`                                                            |

## エンドポイント

| メソッド・パス                | 成功        | レート制限（ユーザー単位 / 分）      | 概要                                                                                                                  |
| ----------------------------- | ----------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `GET /health`                 | 200         | —                                    | 死活監視（認証不要）。`{status: ok \| degraded, version, env, llm_mode, embedding_mode, db: ok \| error}`              |
| `POST /conversations`         | 200         | —                                    | `{character_id}` → (自分, キャラ) の会話を取得または作成。新規時はペルソナの `greeting` をキャラの最初のメッセージとして保存し `greeting_message` で返す（冪等） |
| `POST /chat`                  | 200         | `RATE_LIMIT_CHAT_PER_MINUTE`（20）   | `{character_id, conversation_id, message}` → `{message_id, reply, memories_used, memories_created, user_message, character_message, moderated}`。処理は [02-data-flow.md](02-data-flow.md#6-dm-を送るpost-chat13-ステップ) |
| `GET /memories?character_id=` | 200         | —                                    | 自分とそのキャラの記憶（重要度 desc → 新しい順、最大 500 件）`{memories: MemoryDTO[]}`                                |
| `POST /memories`              | 201         | —                                    | `{character_id, content, importance?, tags?}` → `MemoryDTO`（`is_user_edited = true`、重要度の既定 0.7）            |
| `PATCH /memories/{id}`        | 200         | —                                    | `{content?, importance?, tags?}`（1 つ以上必須）→ `MemoryDTO`（`is_user_edited = true`。本文が変わったら再埋め込み）  |
| `DELETE /memories/{id}`       | 204         | —                                    | 記憶を削除                                                                                                            |
| `POST /comments`              | 201         | `RATE_LIMIT_COMMENTS_PER_MINUTE`（10、generate と共有） | `{post_id, body, parent_comment_id?}` → `{comment, reply_scheduled}`。確率で投稿者キャラの返信をバックグラウンド生成（Realtime で届く） |
| `POST /comments/generate`     | 200         | 同上                                 | `{post_id, parent_comment_id}` → `{comment: CommentDTO \| null}`（出力が Gate #1 で差し止められたら null）。現在の Web の画面からは使っていない |

`MemoryDTO` = `{id, character_id, content, importance, tags, is_user_edited, source_message_id, created_at, updated_at}`、
`CommentDTO` = `{id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body, created_at}`、
`MessageDTO` = `{id, conversation_id, sender_type, body, created_at}`。

## 入力の制限（422 `validation_error`）

| 項目                           | 制限                                                                                           |
| ------------------------------ | ---------------------------------------------------------------------------------------------- |
| `ChatRequest.message`          | 前後の空白を除いて 1〜2000 文字                                                                |
| `CreateCommentRequest.body`    | 1〜500 文字                                                                                    |
| 記憶の `content`               | 1〜500 文字                                                                                    |
| 記憶の `importance`            | 0.0〜1.0                                                                                       |
| 記憶の `tags`                  | 10 個まで、各 1〜20 文字、重複は除去（UI が使うのは `secret`。`summary` は自動要約）            |
| 本文すべて                     | 改行・タブ以外の制御文字（U+0000〜U+001F、U+007F）とサロゲートは拒否                           |
| UUID                           | 形式不正は 422（「〇〇の形式が正しくありません。」）                                            |

メッセージは項目名入りの日本語（例: 「メッセージは2000文字以内で入力してください。」）。

## エラーコード

| HTTP | `code`               | 主な発生条件                                                                                        | Web の扱い                                              |
| ---- | -------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| 401  | `unauthorized`       | トークン無し・期限切れ・署名 / `aud` / `iss` 不正、HS256 未設定                                      | ログイン画面へ（React Query のグローバル処理）          |
| 403  | `forbidden`          | トークンは正しいが `profiles` 行が無い                                                              | エラー表示                                              |
| 403  | `account_deleted`    | 退会済み                                                                                            | サインアウトしてログイン画面へ                          |
| 404  | `not_found`          | 他人の / 存在しない会話・記憶・投稿・コメント、無効なキャラ、未定義のパス（405 も `not_found`）      | エラー表示                                              |
| 422  | `validation_error`   | 入力の形式・長さ・制御文字、PATCH の項目なし、キャラ自身のコメントへの返信要求                      | メッセージをトースト                                    |
| 422  | `moderation_blocked` | コメント・記憶の本文が Gate #1 でヒット（DM はヒットしても 200 + 定型文）                           | メッセージをトースト（保存されていない）                |
| 429  | `rate_limited`       | レート制限超過（`Retry-After` 秒付き）                                                              | 「少し時間をおいてから…」                               |
| 503  | `llm_unavailable`    | LLM の失敗（リトライ後）・`/chat` の締め切り超過（何も保存しない）、記憶の追加・更新時の埋め込み失敗 | 送信失敗表示 + 再送                                     |
| 500  | `internal_error`     | 想定外の例外（ログに traceback。レスポンスにも `X-Request-ID`）                                      | エラー表示                                              |

Web 側だけのエラー（`apps/web/lib/api/errors.ts`）: `network_error`（「通信できませんでした。電波の良い場所で再度お試しください」）、
`timeout`、`aborted`。

## タイムアウトと締め切り

| 値                                   | 既定    | 場所                                                         |
| ------------------------------------ | ------- | ------------------------------------------------------------ |
| Web の API 呼び出し（既定）          | 15 秒   | `DEFAULT_TIMEOUT_MS`（`apps/web/lib/api/client.ts`）          |
| Web の `/chat`・`/comments/generate` | 45 秒   | `CHAT_TIMEOUT_MS`                                            |
| API の `/chat` 全体の締め切り        | 38 秒   | `CHAT_DEADLINE_SECONDS`（**必ず 45 秒より短く**。[ADR-0019](../adr/0019-chat-deadline.md)） |
| LLM / 埋め込み 1 回あたり            | 30 秒   | `LLM_TIMEOUT_SECONDS`（リトライ `LLM_MAX_RETRIES` = 2）       |
| DB のクエリ                          | 30 秒   | asyncpg の `command_timeout`                                 |

## レート制限

- ユーザー（JWT の `sub`）× バケットのスライディングウィンドウ（60 秒）。プロセス内メモリで数えるので、**マシンを N 台にすると実質 N 倍**
  （[ADR-0018](../adr/0018-in-process-rate-limit.md)）。
- 値は環境変数で変更し、API を再起動する（Fly.io なら `fly.toml` の `[env]` に追加してデプロイ、または `fly secrets set`）。
- OpenAPI では全認証付きエンドポイントに 429 が載っているが、実際に制限しているのは `/chat` と `/comments`・`/comments/generate` だけ。
  また `POST /memories`・`PATCH /memories/{id}` の 503（埋め込み失敗）は OpenAPI に載っていない。

## 型・ドキュメントの更新

エンドポイントを追加・変更したら（手順の詳細は [08-dev-guide.md](08-dev-guide.md#エンドポイントを追加する)）:

```bash
# Pydantic モデル（apps/api/app/models/）と packages/shared/src/api.ts を合わせ、契約テストの期待値を更新してから
pnpm --filter @everkano/api openapi         # docs/api/openapi.json を再生成
pnpm --filter @everkano/api openapi:check   # 差分が無いことの確認（CI と同じ）
(cd apps/api && uv run pytest tests/test_openapi_contract.py -q)
```
