# DB テスト（pgTAP）

`database/*.test.sql` は RLS・権限（grant）・トリガーの回帰テスト。受け入れ基準 **A13**
（RLS により他ユーザーの会話・メモリが取得できない）を 2 アカウントで機械的に検証する。

```bash
scripts/test-db.sh                           # psql だけで実行（Docker 不要。CI もこちら）
supabase test db --workdir infra             # pg_prove コンテナで実行（同じファイル）
```

| ファイル                              | 内容                                                                                                            |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `00_privileges.test.sql`              | 権限マトリクス: 全テーブル RLS 有効 / anon・PUBLIC に権限なし / authenticated のテーブル・列・関数権限が許可リストと一致 / ポリシーは authenticated 限定 / Realtime publication |
| `01_profiles.test.sql`                | 本人の行のみ参照・更新、`display_name` / `deleted_at` 以外は更新不可、INSERT / DELETE 不可                       |
| `02_characters_posts.test.sql`        | `system_prompt` / `persona_key` / `select *` は 42501、無効キャラ・予約投稿は不可視、posts への書き込み不可、`post_private_assets` 不可 |
| `03_likes.test.sql`                   | 本人のいいねのみ参照・作成・削除、なりすまし・予約投稿・無効キャラ投稿へのいいねは RLS 違反                      |
| `04_comments.test.sql`                | 公開投稿のコメントのみ参照、INSERT / UPDATE 不可、本人のコメントのみ削除（cascade 含め `comment.delete` 監査ログ） |
| `05_dm_isolation.test.sql`            | **A13**: 会話・メッセージ・メモリの分離、書き込み不可、`embedding` 不可、`list_dm_threads` の未読数、`mark_conversation_read` |
| `06_anon_and_private_tables.test.sql` | anon は全テーブル・RPC 不可、`post_private_assets` / `audit_logs` は authenticated も一切不可                     |
| `07_triggers.test.sql`                | profiles 自動作成、`like_count` / `comment_count`、`last_message_at`、`memories.updated_at`、ユーザー削除の cascade |

## 書き方の約束

- 1 ファイル = 1 トランザクション。`begin;` → `create extension if not exists pgtap with schema extensions;`
  → `select plan(n);` → テスト → `select * from finish();` → `rollback;`。
  **DB に何も残さない**（開発中の共有 DB に対して何度でも実行できるように）。
- フィクスチャは postgres ロールで作る（RLS をバイパス）。ユーザーは `auth.users` に直接 INSERT すると
  `on_auth_user_created` トリガーで `profiles` も作られる。ID は固定 UUID（`aaaaaaaa-...` = User A、`bbbbbbbb-...` = User B）。
  既存の seed データには依存しない。
- ユーザーになりすます（PostgREST がリクエストごとに行う設定と同じ）:

  ```sql
  set local role authenticated;
  set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';
  -- ... テスト ...
  reset role;   -- postgres に戻して実データを確認する
  ```

  未ログインは `set local role anon; set local request.jwt.claims to '{"role":"anon"}';`。
- 「0 行しか変わらない」ことは、操作を `lives_ok` で実行したあと `reset role` して postgres で実データが
  変わっていないことを確認する。権限エラーは `throws_ok(sql, '42501', 'permission denied for table xxx', ...)`、
  RLS の WITH CHECK 違反は `'new row violates row-level security policy for table "xxx"'` で区別する。
- テーブル・列・RPC を追加したら、`00_privileges.test.sql` の許可リストが失敗するので、意図どおりか確認して更新し、
  挙動のテストも追加すること。
- 既知の問題は `select todo_start('理由'); ... select todo_end();` で囲む（失敗しても CI は通り、修正されると
  `scripts/test-db.sh` が「TODO が成功した」と知らせる）。

## 既知の問題（TODO）

- `04_comments.test.sql` の最後: `audit_comment_delete()` が `request.jwt.claims` の空文字（同一セッションで
  一度 claims を設定した後の値）を `jsonb` にキャストできずに失敗する。修正案は
  `coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb`。
