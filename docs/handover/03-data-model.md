# 03. データモデル

正は `infra/supabase/migrations/20260925000000_init.sql`（1 ファイル）。Web 用の型は `packages/shared/src/database.types.ts`（`pnpm db:types` で生成、
CI でずれを検出）。仕様書 §6 との差分と理由は [ADR-0004](../adr/0004-schema-changes-from-spec.md)。

## ER 図

```mermaid
erDiagram
  auth_users ||--|| profiles : "id（on delete cascade）"
  profiles ||--o{ likes : "user_id"
  posts ||--o{ likes : "post_id"
  characters ||--o{ posts : "character_id"
  posts ||--o| post_private_assets : "post_id"
  posts ||--o{ comments : "post_id"
  comments ||--o{ comments : "parent_comment_id"
  profiles |o--o{ comments : "author_user_id"
  characters |o--o{ comments : "author_character_id"
  profiles ||--o{ conversations : "user_id"
  characters ||--o{ conversations : "character_id"
  conversations ||--o{ messages : "conversation_id"
  profiles ||--o{ memories : "user_id"
  characters ||--o{ memories : "character_id"
  messages |o--o{ memories : "source_message_id"

  profiles {
    uuid id PK
    text display_name
    timestamptz deleted_at "退会（論理削除）"
  }
  characters {
    uuid id PK
    text handle UK
    text persona_key "サーバー専用"
    text system_prompt "サーバー専用"
    int follower_count
    boolean is_active
  }
  posts {
    uuid id PK
    uuid character_id FK
    text image_url "有料はプレビュー"
    boolean is_paid
    int price_tokens
    timestamptz published_at "未来 = 予約投稿"
  }
  post_private_assets {
    uuid post_id PK
    text image_url "有料投稿の本体"
  }
  comments {
    uuid id PK
    uuid post_id FK
    uuid parent_comment_id FK
    text author_type "user または character"
    text body
  }
  conversations {
    uuid id PK
    uuid user_id FK
    uuid character_id FK
    timestamptz user_last_read_at
    timestamptz summary_cursor
  }
  messages {
    uuid id PK
    uuid conversation_id FK
    text sender_type "user または character"
    text body
    timestamptz created_at "clock_timestamp()"
  }
  memories {
    uuid id PK
    uuid user_id FK
    uuid character_id FK
    text content
    numeric importance "0.00 から 1.00"
    text_array tags "secret / summary"
    vector embedding "1536 次元"
    boolean is_user_edited
  }
  audit_logs {
    bigint id PK
    text event_type
    uuid user_id "外部キーなし"
    jsonb payload
  }
```

## テーブル

`[追加]` は仕様書 §6 に無いもの。`created_at` は全テーブルにあり、既定値は `now()`（`messages` だけ `clock_timestamp()`）。

### profiles — ユーザー（`auth.users` の拡張）

| 列             | 型          | 説明                                                                              |
| -------------- | ----------- | --------------------------------------------------------------------------------- |
| `id`           | uuid PK     | `auth.users.id`（`on delete cascade`）                                            |
| `display_name` | text        | 初期値はメールアドレスの `@` より前（30 文字に切り詰め）。`/me` で変更可。**check: NULL または 1〜30 文字**（コードポイント数。クライアントが直接 UPDATE できるため DB でも制限） |
| `deleted_at`   | timestamptz | 退会日時（論理削除）。非 NULL なら退会済み。本人は設定のみ可・取り消し不可         |

### characters — AI キャラクター

| 列                       | 型          | 説明                                                                           |
| ------------------------ | ----------- | ------------------------------------------------------------------------------ |
| `id`                     | uuid PK     | シードは固定 UUID `00000000-0000-4000-8000-0000000000c1`〜`…c10`               |
| `handle`                 | text UK     | `^[a-z0-9_.]{2,30}$`。URL `/c/[handle]`                                        |
| `name` / `avatar_url` / `bio` | text   | 表示名 / 絶対 URL またはオブジェクトキー / 自己紹介                             |
| `persona_key`            | text        | `packages/personas/<persona_key>.yaml`。**クライアント非公開**                  |
| `system_prompt`          | text        | YAML が読めない場合のフォールバック。**クライアント非公開**                    |
| `follower_count` [追加]  | int         | 表示専用の静的値（フォロー機能は無い）                                          |
| `is_active`              | boolean     | false でキャラ・投稿・コメントが見えなくなる（削除の代わりに使う）              |

### posts — キャラの投稿（ユーザーは投稿できない。H2）

| 列                                  | 型          | 説明                                                                                   |
| ----------------------------------- | ----------- | -------------------------------------------------------------------------------------- |
| `character_id`                      | uuid FK     | `characters`（cascade）                                                                |
| `image_url`                         | text        | 無料は本体、有料は **プレビュー**（本体とは推測できない別キー）                         |
| `caption`                           | text        |                                                                                        |
| `is_paid` / `price_tokens`          | boolean / int | 有料なら `price_tokens > 0`（check）                                                 |
| `like_count` / `comment_count`      | int         | トリガーで ±1（クライアントは直接更新できない）                                          |
| `published_at`                      | timestamptz | 公開日時。**未来なら RLS で公開時刻まで不可視（予約投稿）**                            |

索引: `(published_at desc)`、`(character_id, is_paid)`。

### post_private_assets [追加] — 有料投稿の本体

`post_id`（PK / FK cascade）、`image_url`。RLS 有効・ポリシー無し・grant 無し = クライアントから到達不能（[ADR-0006](../adr/0006-paid-post-private-assets.md)）。

### likes

`(user_id, post_id)` PK。索引 `(post_id)`。

### comments — ユーザー or キャラのコメント

| 列                              | 型      | 説明                                                                                      |
| ------------------------------- | ------- | ----------------------------------------------------------------------------------------- |
| `post_id`                       | uuid FK | `posts`（cascade）                                                                        |
| `parent_comment_id` [追加]      | uuid FK | 返信先（`comments`、cascade）                                                             |
| `author_type`                   | text    | `user` / `character`                                                                      |
| `author_user_id` / `author_character_id` | uuid FK | どちらか一方だけ非 NULL（check）。`on delete set null` との矛盾は [ADR-0004](../adr/0004-schema-changes-from-spec.md) |
| `body`                          | text    | 1〜1000 文字（API は 500 文字まで）                                                        |

索引: `(post_id, created_at)`、`(parent_comment_id)`、`(author_user_id) where author_user_id is not null`（ユーザーの物理削除で使う）。

### conversations — DM の会話（ユーザー × キャラで 1 件）

| 列                          | 型          | 説明                                                                    |
| --------------------------- | ----------- | ----------------------------------------------------------------------- |
| `user_id` / `character_id`  | uuid FK     | `unique (user_id, character_id)`                                        |
| `last_message_at`           | timestamptz | `messages` の INSERT でトリガーが更新                                    |
| `user_last_read_at` [追加]  | timestamptz | これより新しいキャラ発言が未読。RPC `mark_conversation_read` で更新     |
| `summary_cursor` [追加]     | timestamptz | 中期要約の処理済み位置（この時刻以前は要約済み）                        |

索引: `(user_id, last_message_at desc)`。

### messages — DM のメッセージ

`conversation_id`（FK cascade）、`sender_type`（`user` / `character`）、`body`（1〜4000 文字。API は 2000 文字まで）、
`created_at`（`clock_timestamp()`。同じトランザクション内でも順序が付く）。索引 `(conversation_id, created_at)`。

### memories — 長期メモリ

| 列                        | 型                          | 説明                                                                                  |
| ------------------------- | --------------------------- | ------------------------------------------------------------------------------------- |
| `user_id` / `character_id` | uuid FK                    | 誰の・どのキャラとの記憶か                                                             |
| `content`                 | text                        | 1〜1000 文字（API は 500 文字まで）                                                     |
| `importance`              | numeric(3,2)                | 0.00〜1.00。検索結果の再ランクに使う                                                    |
| `tags`                    | text[]                      | `secret`（二人だけの秘密）/ `summary`（中期要約。利用者は新たに付けられない）           |
| `embedding`               | extensions.vector(1536)     | **クライアント非公開**。HNSW 索引（DM 応答では使わない。[ADR-0005](../adr/0005-vector-index-and-exact-memory-search.md)） |
| `source_message_id`       | uuid FK                     | 抽出元のユーザー発言（`set null`）                                                      |
| `is_user_edited`          | boolean                     | true = ユーザーが追加・編集した記憶。自動処理で上書きしない                            |
| `updated_at` [追加]       | timestamptz                 | トリガーで更新                                                                          |

索引: `(user_id, character_id, created_at desc)`、`(source_message_id) where source_message_id is not null`（メッセージ削除時の `set null` 用。
無いとユーザーの物理削除でメッセージ 1 件ごとに `memories` 全体を走査する）、HNSW `(embedding vector_cosine_ops)`。
ペアあたりの件数の上限は API が守る（`MEMORY_MAX_PER_CHARACTER`、[ADR-0024](../adr/0024-memory-capacity-per-pair.md)）。

### audit_logs — 監査ログ（H6）

`id` bigserial、`event_type`、`user_id`、`character_id`（外部キー無し。ユーザー削除後も残る）、`payload` jsonb、`created_at`。
索引 `(event_type, created_at desc)`、`(user_id, created_at desc)`。イベント種別と payload は [ADR-0013](../adr/0013-audit-log.md)。

## 権限マトリクス（ロール別）

`public` スキーマは `revoke all` の後、必要なものだけを明示的に grant している（今後作るテーブル・関数も既定で非公開）。
**全テーブルで RLS 有効**。ポリシーはすべて `to authenticated`。

| テーブル / 関数            | anon                                  | authenticated（ブラウザ）                                                                                   | postgres（Python API・SQL Editor） | service_role |
| -------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------- | ------------ |
| `profiles`                 | —                                     | SELECT（本人）、UPDATE `display_name` / `deleted_at`（本人。退会の取り消しはトリガーで拒否）                 | すべて（RLS バイパス）             | すべて       |
| `characters`               | —                                     | SELECT 公開列のみ（`id, handle, name, avatar_url, bio, follower_count, is_active, created_at`）、`is_active` の行 | すべて                          | すべて       |
| `posts`                    | —                                     | SELECT（`published_at <= now()` かつ有効キャラ）                                                            | すべて                             | すべて       |
| `post_private_assets`      | —                                     | —                                                                                                           | すべて                             | すべて       |
| `likes`                    | —                                     | SELECT / INSERT / DELETE（本人の行。INSERT は見える投稿のみ）                                               | すべて                             | すべて       |
| `comments`                 | SELECT `id` のみ（ポリシー無し = 0 行。Realtime 用。[ADR-0016](../adr/0016-realtime-anon-primary-key-grant.md)） | SELECT（見える投稿かつ `created_at <= now()`）、DELETE（本人の user コメント）   | すべて                             | すべて       |
| `conversations`            | —                                     | SELECT（本人）、UPDATE `user_last_read_at`（本人）                                                          | すべて                             | すべて       |
| `messages`                 | SELECT `id` のみ（0 行。Realtime 用） | SELECT（本人の会話）                                                                                        | すべて                             | すべて       |
| `memories`                 | —                                     | SELECT `embedding` 以外の列（本人）                                                                         | すべて                             | すべて       |
| `audit_logs`               | —                                     | —                                                                                                           | すべて                             | すべて       |
| `list_dm_threads()`        | —                                     | EXECUTE（security invoker = RLS）                                                                           | ○                                  | ○            |
| `mark_conversation_read(uuid)` | —                                 | EXECUTE（security invoker。本人の会話だけ更新）                                                             | ○                                  | ○            |

- クライアントには `messages` / `comments` / `memories` / `conversations` / `posts` / `characters` への INSERT 権限が無い
  （ユーザー由来テキストは Python API 経由。[ADR-0002](../adr/0002-data-access-split.md)）。
- `service_role` はアプリのコードでは使っていない（E2E テストのユーザー作成・運用者の管理 API 用）。キーは Web にも API にも渡さない。
- この表は `infra/supabase/tests/database/00_privileges.test.sql` の許可リストで機械的に検査している（CI）。

## トリガー

| トリガー                                      | テーブル / タイミング            | 関数（security）                           | 内容                                                             |
| --------------------------------------------- | -------------------------------- | ------------------------------------------ | ---------------------------------------------------------------- |
| `on_auth_user_created`                        | `auth.users` AFTER INSERT        | `handle_new_user()`（definer）             | `profiles` を作成                                                 |
| `on_auth_user_email_verified`                 | `auth.users` BEFORE UPDATE       | `discard_unverified_password()`            | メールのトークンで確認済みになる時にパスワードを破棄（[ADR-0017](../adr/0017-discard-unverified-password.md)） |
| `on_auth_user_password_update`                | `auth.users` BEFORE UPDATE OF `encrypted_password` | `ignore_password_update()`   | 既存ユーザーのパスワードの設定・変更を無効にする（元の値に戻す。消去は許可。Postgres のログに LOG。[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md)） |
| `profiles_guard_withdrawal`                   | `profiles` BEFORE UPDATE         | `guard_profile_withdrawal()`               | `authenticated` / `anon` による `deleted_at` の変更・取り消しを 42501 で拒否 |
| `likes_sync_post_like_count`                  | `likes` AFTER INSERT / DELETE    | `sync_post_like_count()`（definer）        | `posts.like_count` ±1                                             |
| `comments_sync_post_comment_count`            | `comments` AFTER INSERT / DELETE | `sync_post_comment_count()`（definer）     | `posts.comment_count` ±1                                          |
| `comments_audit_delete`                       | `comments` AFTER DELETE          | `audit_comment_delete()`（definer）        | `audit_logs` に `comment.delete`                                  |
| `messages_sync_conversation_last_message_at`  | `messages` AFTER INSERT          | `sync_conversation_last_message_at()`（definer） | `conversations.last_message_at` を更新                     |
| `memories_touch_updated_at`                   | `memories` BEFORE UPDATE         | `touch_updated_at()`                       | `updated_at = now()`                                              |

すべての関数は `set search_path = ''` で、クライアントからは実行できない（トリガー経由のみ）。

## RPC

| 関数                                            | 返り値                                                                                                                                                 | 用途                      |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------- |
| `list_dm_threads()`                             | `conversation_id, character_id, character_handle, character_name, character_avatar_url, last_message_body, last_message_sender_type, last_message_at, unread_count`（新しい順） | DM 一覧・タブバーの未読バッジ |
| `mark_conversation_read(p_conversation_id uuid)` | void                                                                                                                                                  | 会話を開いた / 新着を表示したときの既読 |

## Realtime

- publication `supabase_realtime` に `messages` と `comments` を追加している。
- Web が購読するもの: `messages` の INSERT（DM 画面は `conversation_id=eq.<id>`、DM 一覧は一覧に出ている自分の会話の `conversation_id=in.(...)`。
  最大 100 件。[ADR-0030](../adr/0030-web-data-fetching-dm-and-prefetch.md)）、`comments` の INSERT / DELETE（`post_id` で絞る）。
- RLS に一致する行だけが配信される。DELETE イベントは Realtime の仕様で RLS が適用されず、主キーだけが全購読者に届く。

## シードデータ

`infra/supabase/seed.sql`（`packages/personas` から生成。[ADR-0020](../adr/0020-content-seed-generation.md)）: キャラ 10 体、投稿 50 件（有料 10、予約投稿 6）、
`post_private_assets` 10 件、コメント 142 件。`published_at` / `created_at` は投入時刻からの相対値。

## 型の再生成

```bash
pnpm db:types        # supabase gen types typescript --local → packages/shared/src/database.types.ts（Prettier 整形込み）
pnpm db:types:check  # 生成結果とコミット済みファイルの比較（CI）
```
