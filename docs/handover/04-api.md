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
| CORS             | `CORS_ALLOW_ORIGINS`（カンマ区切り）。メソッド GET / POST / PUT / PATCH / DELETE / OPTIONS、ヘッダー `Authorization` / `Content-Type` / `X-Request-ID`、公開ヘッダー `X-Request-ID` / `Retry-After`、Cookie は使わない |
| エラー           | `{"error": {"code", "message", "request_id"}}`。`message` はそのまま表示できる日本語                                              |
| 所有者チェック   | 他人の会話・記憶、見えない投稿・キャラは **404**（存在を教えない。[ADR-0003](../adr/0003-api-db-connection-asyncpg.md)）        |
| 退会済み         | 有効なトークンでも `profiles.deleted_at` があれば 403 `account_deleted`                                                            |

## エンドポイント

| メソッド・パス                | 成功        | レート制限（ユーザー単位 / 分）      | 概要                                                                                                                  |
| ----------------------------- | ----------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `GET /health`                 | 200         | —                                    | 死活監視（認証不要）。`{status: ok \| degraded, version, env, llm_mode, embedding_mode, db: ok \| error}`              |
| `POST /conversations`         | 200         | —                                    | `{character_id}` → (自分, キャラ) の会話を取得または作成。新規時はペルソナの `greeting` をキャラの最初のメッセージとして保存し `greeting_message` で返す（冪等） |
| `POST /chat/stream`           | 200（SSE）  | `RATE_LIMIT_CHAT_PER_MINUTE`（20。`/chat` と共有） | `/chat` と同じ入力で、返答を Server-Sent Events で順に返す（下の「`POST /chat/stream` のイベント」）。Web の DM はこちらを使う。処理は [02-data-flow.md](02-data-flow.md#6-dm-を送るpost-chatstreampost-chat) |
| `POST /chat`                  | 200         | 同上                                 | `{character_id, conversation_id, message}` → `ChatResponse = {message_id, reply, memories_used, memories_created（常に []）, user_message, character_message, moderated, safety}`。`/chat/stream` と同じ処理で、最後の結果だけを返す |
| `GET /memories?character_id=&include_superseded=` | 200 | —                         | 自分とそのキャラの記憶（有効なもの → 重要度 desc → 新しい順、最大 `MEMORY_MAX_PER_CHARACTER` + 50 件。要約は上限を超えて保存されることがあるため）`{memories: MemoryDTO[]}`。`include_superseded=true` で置き換えられた古い記憶（履歴）も返す |
| `POST /memories`              | 201         | `RATE_LIMIT_MEMORIES_PER_MINUTE`（30、PATCH と共有） | `{character_id, content, importance?, tags?, kind?}` → `MemoryDTO`（`is_user_edited = true`、重要度の既定 0.7、種類の既定 `fact`。`summary` のタグ・種類は 422）。ペアの記憶が `MEMORY_MAX_PER_CHARACTER`（500）件に達していたら 422 |
| `PATCH /memories/{id}`        | 200         | 同上                                 | `{content?, importance?, tags?, kind?}`（1 つ以上必須）→ `MemoryDTO`（`is_user_edited = true`。本文が変わったら再埋め込み。要約の種類は変えられない）。以後、自動処理はこの記憶を上書き・置き換えしない（E5） |
| `DELETE /memories/{id}`       | 204         | —                                    | 記憶を削除し、本文を持たない墓標を残す（自動抽出で作り直さない。E5）。この記憶から作られた未達の約束は取り消す |
| `GET /promises?character_id=&include_closed=` | 200 | —                             | 自分とそのキャラの約束。既定は未達（`pending` / `mentioned`）だけを期日の近い順（期日なしは最後）、最大 200 件。`include_closed=true` で完了・取り消し済みも `{promises: PromiseDTO[]}` |
| `PATCH /promises/{id}`        | 200         | —                                    | `{status: "done" \| "cancelled"}` → `PromiseDTO`。同じ状態なら何もしない。取り消すとカレンダーの予定も取り消し、元の記憶を履歴にする（`promise.status_change`） |
| `GET /proactive/settings`     | 200         | —                                    | 自発メッセージの設定 `{global: {enabled, quiet_start, quiet_end}, characters: [{character_id, enabled}]}`。行が無ければ既定（有効・送らない時間帯は `ENGINE_PROACTIVE_QUIET_START`〜`END`、既定 0〜7 時 JST）。キャラ別の設定が無いキャラは有効 |
| `PUT /proactive/settings`     | 200         | —                                    | `{enabled?, quiet_start?, quiet_end?}`（省略・null は変更しない。時は整数 0〜23、`start == end` なら制限なし。片方だけ指定したら、もう片方を今の値で埋めて両方を保存）→ 設定全体。audit `proactive.settings_update` |
| `PUT /proactive/settings/{character_id}` | 200 | —                                | `{enabled}` → 設定全体。有効なキャラ以外は 404 |
| `GET /safety/resources`       | 200         | —                                    | E6 の相談窓口の一覧 `{resources: SafetyResource[]}`（`ChatResponse.safety.resources` と同じ内容・順序。ログイン必須）。`messages.safety_triggered` の返答の下のカード用 |
| `POST /comments`              | 201         | `RATE_LIMIT_COMMENTS_PER_MINUTE`（10、generate と共有） | `{post_id, body, parent_comment_id?}` → `{comment, reply_scheduled}`。確率で投稿者キャラの返信をバックグラウンド生成（Realtime で届く） |
| `POST /comments/generate`     | 200         | 同上                                 | `{post_id, parent_comment_id}` → `{comment: CommentDTO \| null}`。**自分のコメントだけ**（他人のコメントは 404、キャラのコメントは 422）。キャラの返信はコメント 1 件につき 1 件で、既にあれば LLM を呼ばずにそれを返す（冪等）。出力が Gate #1 で差し止められた、または URL・ドメイン名を含む場合は null（[ADR-0027](../adr/0027-comment-reply-generation-limits.md)）。現在の Web の画面からは使っていない |

`MemoryDTO` = `{id, character_id, content, importance, tags, is_user_edited, source_message_id, created_at, updated_at, kind, status, superseded_by, superseded_at, last_referenced_at, reference_count}`、
`PromiseDTO` = `{id, character_id, content, due_at, due_precision, status, created_at, updated_at}`、
`CommentDTO` = `{id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body, created_at}`、
`MessageDTO` = `{id, conversation_id, sender_type, body, created_at, is_proactive, safety_triggered}`、
`SafetyInfo` = `{triggered, resources: SafetyResource[]}`、`SafetyResource` = `{name, phone, hours, url}`（`phone` / `hours` / `url` は null あり）。
`kind` は `fact` / `preference` / `episode` / `promise` / `emotion` / `relationship` / `summary`、`status` は `active` / `superseded`。

### `POST /chat/stream` のイベント

レスポンスは `text/event-stream`（[ADR-0037](../adr/0037-chat-streaming-sse.md)。型は `packages/shared/src/api.ts` の `ChatStreamEvent`）。各イベントは `event: <type>` と
`data: <1 行の JSON>` と空行。開始直後に `: ok`、イベントの無い間は `CHAT_STREAM_HEARTBEAT_SECONDS`（10 秒）ごとに `: keep-alive` のコメント行が届く。

| event | data | 意味 |
| --- | --- | --- |
| `delta` | `{text}` | 検査（Gate #1・キャラの NG ワード・E2 / E3）を通過した返答の続き。文単位 |
| `replace` | `{text, reason: "moderated" \| "safety"}` | 表示中の本文を置き換える（出力・入力が差し止められた / E6 の安全対応） |
| `done` | `ChatResponse` | 保存済みの最終結果。この後に接続が閉じる |
| `error` | `{code, message, request_id}` | 失敗（`llm_unavailable` など）。**何も保存していない**。この後に接続が閉じる |

```
: ok

event: delta
data: {"text":"おつかれ〜。"}

event: delta
data: {"text":"今日は定時で上がれた？"}

event: done
data: {"message_id":"…","reply":"おつかれ〜。今日は定時で上がれた？","memories_used":[],"memories_created":[],"user_message":{…},"character_message":{…},"moderated":false,"safety":null}
```

- 認証・所有者・レート制限・入力のエラーはストリームを始める前に、通常の JSON エラー（下の表の HTTP ステータス）で返る。
- クライアントが接続を切っても、返答の生成・保存は最後まで行われる（再送せずに履歴・Realtime で受け取る）。
- 記憶の分析は返答の後に非同期で行うため `memories_created` は常に空。新しい記憶は Realtime（`memories` の INSERT）で、会話が止まってから約 3 分後
  （`ENGINE_POST_TURN_DELAY_SECONDS` = 180 秒。話し続ければ最大 18 分後）に届く。

## 入力の制限（422 `validation_error`）

| 項目                           | 制限                                                                                           |
| ------------------------------ | ---------------------------------------------------------------------------------------------- |
| `ChatRequest.message`          | 前後の空白を除いて 1〜2000 文字                                                                |
| `CreateCommentRequest.body`    | 1〜500 文字                                                                                    |
| 記憶の `content`               | 1〜500 文字                                                                                    |
| 記憶の `importance`            | 0.0〜1.0                                                                                       |
| 記憶の `tags`                  | 10 個まで、各 1〜20 文字、重複は除去（UI が使うのは `secret`）。`summary` は自動要約専用で、利用者は新たに付けられない（既存の要約の記憶に付いたまま送り返すのは可） |
| 記憶の `kind`                  | `summary` 以外の種類。要約の記憶の種類は変えられない                                            |
| 約束の `status`（PATCH）       | `done` / `cancelled` だけ                                                                      |
| 送らない時間帯                 | `quiet_start` / `quiet_end` は整数の 0〜23（文字列・小数・真偽値は 422）。`enabled` は真偽値    |
| 本文すべて                     | 改行・タブ以外の制御文字（U+0000〜U+001F、U+007F）とサロゲートは拒否                           |
| リクエスト本文の大きさ         | `MAX_REQUEST_BODY_BYTES`（64 KiB）を超えると本文を読まずに **413** `validation_error`（認証より前） |
| 記憶の件数                     | ユーザー × キャラあたり `MEMORY_MAX_PER_CHARACTER`（500）件まで（`POST /memories`。[ADR-0024](../adr/0024-memory-capacity-per-pair.md)） |
| UUID                           | 形式不正は 422（「〇〇の形式が正しくありません。」）                                            |

メッセージは項目名入りの日本語（例: 「メッセージは2000文字以内で入力してください。」）。

## エラーコード

| HTTP | `code`               | 主な発生条件                                                                                        | Web の扱い                                              |
| ---- | -------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| 401  | `unauthorized`       | トークン無し・期限切れ・署名 / `aud` / `iss` 不正、HS256 未設定                                      | ログイン画面へ（React Query のグローバル処理。Web がログアウトさせるのは 401 と `account_deleted` だけ） |
| 403  | `forbidden`          | トークンは正しいが `profiles` 行が無い                                                              | エラー表示                                              |
| 403  | `account_deleted`    | 退会済み                                                                                            | サインアウトしてログイン画面へ                          |
| 404  | `not_found`          | 他人の / 存在しない会話・記憶・約束・投稿・コメント、他人のコメントへの `/comments/generate`、無効なキャラ（`PUT /proactive/settings/{character_id}` を含む）、未定義のパス（405 も `not_found`） | エラー表示（`/chat/stream` の 404 / 405 は `/chat` へフォールバックして確かめる） |
| 422  | `validation_error`   | 入力の形式・長さ・制御文字、PATCH の項目なし、キャラ自身のコメントへの返信要求、`summary` タグの指定、記憶の件数の上限（`POST /memories`） | メッセージをトースト                                    |
| 422  | `moderation_blocked` | コメント・記憶の本文が Gate #1 でヒット（DM はヒットしても 200 + 定型文）                           | メッセージをトースト（保存されていない）                |
| 429  | `rate_limited`       | レート制限超過（`Retry-After` 秒付き）                                                              | 「送信が多すぎます。しばらくしてから再度お試しください。」（DM 送信は Web 側の文言「送信が集中しています。…」）                               |
| 503  | `llm_unavailable`    | LLM の失敗（リトライ後）・`/chat` の締め切り超過（何も保存しない。`/chat/stream` では `error` イベント）、記憶の追加・更新時の埋め込み失敗 | 送信失敗表示 + 再送                                     |
| 503  | `internal_error`     | 認証サーバー（JWKS）に接続できずトークンを検証できない（`Retry-After: 5`。[ADR-0025](../adr/0025-db-tls-and-api-entry-failures.md)） | エラー表示（**ログアウトさせない**）                     |
| 500  | `internal_error`     | 想定外の例外（ログに traceback。レスポンスにも `X-Request-ID`）                                      | エラー表示                                              |

413（本文が大きすぎる）は `validation_error`（「送信内容が大きすぎます。内容を短くして再度お試しください。」）。

Web 側だけのエラー（`apps/web/lib/api/errors.ts`）: `network_error`（「通信できませんでした。接続を確認して、しばらくしてから再度お試しください。」）、
`timeout`、`aborted`。Supabase（PostgREST / Auth）のエラーも `toAppError` で同じ `ApiError` にそろえて表示・再試行を判定する。
トークンの更新（refresh）が通信失敗・Auth の 5xx で失敗した場合も `network_error`（ログアウトさせない）。

文言の決まり（API の `DEFAULT_MESSAGES` と Web の `API_ERROR_MESSAGES` で共通）: 文は句点「。」で終える。時間をおいた再試行の案内は
「しばらくしてから再度お試しください。」にそろえる。通信失敗は API・Supabase の停止でも起きるので、原因を端末の電波と決めつけない
（[ADR-0029](../adr/0029-web-network-failure-policy.md)）。

## タイムアウトと締め切り

| 値                                   | 既定    | 場所                                                         |
| ------------------------------------ | ------- | ------------------------------------------------------------ |
| Web の API 呼び出し（既定）          | 15 秒   | `DEFAULT_TIMEOUT_MS`（`apps/web/lib/api/client.ts`）          |
| Web の `/chat`・`/chat/stream`・`/comments/generate` | 45 秒 | `CHAT_TIMEOUT_MS` / `CHAT_STREAM_TIMEOUT_MS`（ストリームは接続から `done` まで） |
| API の `/chat`・`/chat/stream` の締め切り | 38 秒 | `CHAT_DEADLINE_SECONDS`（文脈の組み立て + 返答の生成。**必ず 45 秒より短く**。[ADR-0019](../adr/0019-chat-deadline.md)） |
| 文脈の組み立て（Context Assembler）  | 1.5 秒  | `ENGINE_CONTEXT_TIMEOUT_SECONDS`（間に合わない要素は省いて返答する） |
| SSE のキープアライブ                 | 10 秒   | `CHAT_STREAM_HEARTBEAT_SECONDS`                              |
| LLM 1 回あたり                       | 30 秒   | `LLM_TIMEOUT_SECONDS`（リトライ `LLM_MAX_RETRIES` = 2）       |
| 埋め込み 1 回あたり                  | 5 秒    | `EMBEDDING_TIMEOUT_SECONDS`（リトライ `EMBEDDING_MAX_RETRIES` = 1）。`/chat` の検索は間に合わなければ省略（[ADR-0022](../adr/0022-embedding-failures-and-audit-additions.md)） |
| `/memories` の埋め込み全体           | 10 秒   | `USER_MEMORY_EMBED_DEADLINE_SECONDS`（Web の 15 秒より前に 503 を返す）   |
| JWKS の取得                          | 5 秒    | `JwksCache`（失敗後 5 秒は再取得せずに 503）                  |
| DB のクエリ                          | 30 秒   | asyncpg の `command_timeout`                                 |
| LLM・埋め込みの HTTP 接続の確立 / 空き待ち | 5 秒 / 5 秒 | `app/core/http.py`（同時接続 200。`fly.toml` の `hard_limit` × 2 以上。[ADR-0028](../adr/0028-llm-input-budgets-and-summary-retries.md)） |
| Web → Supabase（REST / Auth）         | 15 秒   | `SUPABASE_FETCH_TIMEOUT_MS`（`apps/web/lib/supabase/fetch-timeout.ts`。再試行は React Query の 1 回だけ） |

## レート制限

- ユーザー（JWT の `sub`）× バケットのスライディングウィンドウ（60 秒）。プロセス内メモリで数えるので、**マシンを N 台にすると実質 N 倍**
  （[ADR-0018](../adr/0018-in-process-rate-limit.md)）。
- 値は環境変数で変更し、API を再起動する（Fly.io なら `fly.toml` の `[env]` に追加してデプロイ、または `fly secrets set`）。
- OpenAPI では全認証付きエンドポイントに 413 / 429 / 503 が載っているが、実際にレート制限しているのは `/chat`・`/chat/stream`（共有）、`/comments`・`/comments/generate`、
  `POST` / `PATCH /memories` だけ（約束・自発メッセージの設定・相談窓口には無い）。

## 型・ドキュメントの更新

エンドポイントを追加・変更したら（手順の詳細は [08-dev-guide.md](08-dev-guide.md#エンドポイントを追加する)）:

```bash
# Pydantic モデル（apps/api/app/models/）と packages/shared/src/api.ts を合わせ、契約テストの期待値を更新してから
pnpm --filter @everkano/api openapi         # docs/api/openapi.json を再生成
pnpm --filter @everkano/api openapi:check   # 差分が無いことの確認（CI と同じ）
(cd apps/api && uv run pytest tests/test_openapi_contract.py -q)
```
