# ADR-0004: 仕様スキーマからの追加・変更点

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §6 / [ADR-0005](0005-vector-index-and-exact-memory-search.md) / [ADR-0006](0006-paid-post-private-assets.md) / [ADR-0016](0016-realtime-anon-primary-key-grant.md) / [ADR-0017](0017-discard-unverified-password.md) / 実装: `infra/supabase/migrations/20260925000000_init.sql`（仕様書に無いものには `[追加]` コメント）

## コンテキスト

仕様書 §6 のテーブル定義だけでは、画面仕様（未読バッジ・キャラの返信・有料投稿のロック表示）とメモリエンジン（§9 の中期要約）を
実装できない。また §6 の定義には、そのままでは矛盾する箇所（comments の check 制約と `on delete set null`）がある。
既存の列の意味は変えずに、追加・制約の強化・矛盾の扱いを決める必要がある。

## 決定

### 1. 追加したテーブル・列

| 対象                              | 内容                                                                        | 理由                                                                                                |
| --------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `characters.follower_count`       | `int not null default 0`（`>= 0`）。表示専用の静的値                         | プロフィールにフォロワー数を出す（§5.4）。フォロー機能はスコープ外（§12）なので集計しない           |
| `post_private_assets`（テーブル） | 有料投稿の本体画像。RLS 有効・ポリシー無し                                   | 本体 URL をクライアントに渡さない（[ADR-0006](0006-paid-post-private-assets.md)）                   |
| `comments.parent_comment_id`      | 返信先。`on delete cascade`（親を消すと返信も消える）                        | キャラがユーザーのコメントに返信する（[ADR-0014](0014-comments-via-api-and-auto-reply.md)）          |
| `conversations.user_last_read_at` | `not null default now()`                                                    | DM 一覧の未読バッジ（これより新しいキャラ発言 = 未読）                                               |
| `conversations.summary_cursor`    | 中期要約の処理済み位置（この時刻以前のメッセージは要約済み）                 | §9.1 の中期メモリ（[ADR-0009](0009-memory-engine.md)）                                              |
| `memories.updated_at`             | トリガーで自動更新                                                          | メモリパネルでの編集・自動更新の追跡                                                                |

### 2. 変更した定義・制約

| 対象                                 | 仕様書                  | 実装                                                                                              |
| ------------------------------------ | ----------------------- | ------------------------------------------------------------------------------------------------- |
| `messages.created_at` の既定値        | `now()`                 | `clock_timestamp()`。1 トランザクションでユーザー発言 → キャラ返答を保存しても順序が保たれる       |
| `memories.embedding` の索引           | ivfflat                 | HNSW（[ADR-0005](0005-vector-index-and-exact-memory-search.md)）                                  |
| `memories.embedding` の型             | `vector(1536)`          | `extensions.vector(1536)`（Supabase は拡張を `extensions` スキーマに入れる）                      |
| 既定値を持つ列                        | NULL 可                 | `not null`（`created_at`・`is_paid`・`like_count`・`tags`・`is_user_edited` など）                |
| `characters.handle`                   | 制約なし                | `check (handle ~ '^[a-z0-9_.]{2,30}$')`（URL `/c/[handle]` に使うため）                          |
| `posts`                               | —                       | `price_tokens >= 0`、`like_count >= 0`、`comment_count >= 0`、`check (not is_paid or price_tokens > 0)` |
| 本文の長さ                            | —                       | `comments.body` 1〜1000 文字、`messages.body` 1〜4000 文字、`memories.content` 1〜1000 文字（API はさらに短く制限。[04-api.md](../handover/04-api.md)） |
| `memories.importance`                 | `numeric(3,2)`          | 同じ型 + `check (0 <= importance <= 1)`                                                           |
| 追加の索引                            | —                       | `likes(post_id)`、`comments(parent_comment_id)`、`conversations(user_id, last_message_at desc)`、`memories(user_id, character_id, created_at desc)`、`audit_logs(user_id, created_at desc)` |

### 3. 追加した関数・RPC・トリガー

| 名前                                                             | 種別                          | 内容                                                                                              |
| ---------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------- |
| `list_dm_threads()`                                              | RPC（security invoker）       | 会話済みキャラ・最新メッセージ・未読数を 1 回で返す（RLS が効く）。DM 一覧を N+1 なしで描画する  |
| `mark_conversation_read(p_conversation_id uuid)`                 | RPC（security invoker）       | `user_last_read_at = now()`（本人の会話のみ）。クライアントに会話の任意列の UPDATE を許さない     |
| `on_auth_user_created` → `handle_new_user()`                     | `auth.users` AFTER INSERT     | `profiles` を自動作成（§5.1。`display_name` はメールの `@` より前）                                |
| `on_auth_user_email_verified` → `discard_unverified_password()`  | `auth.users` BEFORE UPDATE    | メール確認前に設定されたパスワードを破棄（[ADR-0017](0017-discard-unverified-password.md)）       |
| `profiles_guard_withdrawal` → `guard_profile_withdrawal()`       | `profiles` BEFORE UPDATE      | 退会の一方向化（下記 5）                                                                          |
| `likes_sync_post_like_count`                                     | `likes` AFTER INSERT/DELETE   | `posts.like_count` を ±1                                                                          |
| `comments_sync_post_comment_count`                               | `comments` AFTER INSERT/DELETE | `posts.comment_count` を ±1                                                                      |
| `comments_audit_delete` → `audit_comment_delete()`               | `comments` AFTER DELETE       | `audit_logs` に `comment.delete`（[ADR-0013](0013-audit-log.md)）                                  |
| `messages_sync_conversation_last_message_at`                     | `messages` AFTER INSERT       | `conversations.last_message_at` を更新                                                            |
| `memories_touch_updated_at`                                      | `memories` BEFORE UPDATE      | `updated_at = now()`                                                                               |

Realtime publication `supabase_realtime` に `messages` と `comments` を追加する（anon への主キー列 grant とセット。
[ADR-0016](0016-realtime-anon-primary-key-grant.md)）。権限は `revoke all` のうえで必要なものだけ明示的に grant し、
今後作るテーブル・関数も既定で非公開にする（`alter default privileges ... revoke`）。

### 4. comments の check 制約と `on delete set null` の矛盾

仕様書の `comments` は `author_user_id ... on delete set null` と
`check ((author_type = 'user' and author_user_id is not null and author_character_id is null) or (...character...))` を同時に持つ。
作成者が削除されると `set null` が check 制約に違反し、**その削除（`auth.users` → `profiles` の cascade を含む）全体がエラーで失敗する**。
キャラクターの削除でも、そのキャラが他の投稿に書いたコメントについて同じことが起きる。

- 仕様書の定義（外部キーと check 制約）は **そのまま残す**。
- 通常の退会は **論理削除**（`profiles.deleted_at`）なので、この矛盾は発生しない。
- ユーザーの **物理削除** は運用者の作業とし、**先にそのユーザーのコメントを削除してから** `auth.users` を削除する
  （手順: [06-operations.md](../handover/06-operations.md#ユーザーの物理削除)。削除は `comment.delete` として監査ログに残る）。
- キャラクターは削除せず `is_active = false` で非表示にする（RLS により本人・投稿・コメントがクライアントから見えなくなる）。

### 5. コメントの可視性（`created_at <= now()`）

`comments` の SELECT ポリシーは「公開投稿のコメント」に加えて `created_at <= now()` を条件にする。シードでは予約投稿
（未来の `published_at`）にもコメントを付けており、その `created_at` も未来になる。この条件により、予約投稿が公開された後、
コメントも時刻どおりに順に現れる（フィードが「生きている」感。仕様書 §18）。API が保存するコメントは `now()` なので即座に見える。

### 6. 退会の一方向化

`/me` の「退会する」はクライアントが `profiles.deleted_at` を設定する（列 grant で許可）。トリガー `guard_profile_withdrawal` は、
**一度設定された `deleted_at` を `authenticated` / `anon` が変更・解除することを禁止する**（42501）。退会済みユーザーは
ログイン時（`/auth/confirm`・`/auth/callback`）と `AccountGuard` でサインアウトされ、API も 403 `account_deleted` を返す。
復旧は本人確認のうえ運用者が `postgres` ロールで `deleted_at = null` に戻す（[06-operations.md](../handover/06-operations.md#退会ユーザーの復旧)）。

## 結果・トレードオフ

- `packages/shared/src/database.types.ts` は追加分も含めて生成される（`pnpm db:types`）。CI の `check-db-types.sh` がずれを検出する。
- 追加した列・関数の権限は `00_privileges.test.sql`、挙動は `04_comments` / `05_dm_isolation` / `07_triggers` / `08_auth_password_hardening`
  の pgTAP テストで検査している。
- 物理削除とキャラ削除には手順上の注意が要る（上記 4）。将来「退会時に物理削除する」方針にするなら、新しい ADR とマイグレーションで
  外部キーを `on delete cascade` にするか、削除用の関数を用意する。
- シードのコメントは投入時刻基準なので、日が経つと「予約」の効果は無くなる（[ADR-0020](0020-content-seed-generation.md)）。

## 代替案

- **comments の外部キーを `on delete cascade` に変更**: 仕様書のテーブル定義からの変更になる。物理削除は明示的な運用作業に限りたいので不採用。
- **check 制約を緩める（作成者 NULL の user コメントを許可）**: 「退会済みユーザー」のコメントとして残せるが、匿名表示の規則
  （`user_` + ID 先頭 6 桁）が成り立たず、表示側の分岐も増える。不採用。
- **未読数をクライアントで計算**: 会話ごとにメッセージを取得する必要があり、DM 一覧が重くなる。RPC に集約した。
- **退会の取り消しを RLS の `with check` で防ぐ**: `with check` は新しい行しか見られず「以前は退会済みだった」を判定できない。トリガーにした。
