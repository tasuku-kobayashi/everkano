# ADR-0002: データアクセス分離（読み取りは Supabase RLS 直結・ユーザー由来テキストの書き込みは API 経由）

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §2・§6・§10・H6 / [ADR-0003](0003-api-db-connection-asyncpg.md) / 実装: `infra/supabase/migrations/20260925000000_init.sql`（grant・policy）, `apps/web/lib/queries/`, `apps/web/lib/api/`

## コンテキスト

- 仕様書 §6 は全テーブルの RLS 有効化、§10 は DM・コメントの入出力に対する Gate #1 モデレーション、H6 はすべての会話・生成・判定の
  構造化ログ（`audit_logs`）を求めている。
- フィード・投稿・コメント・DM 履歴の読み取りは量が多く、Realtime（DM とコメントの同期）も使う。すべてを Python API 経由にすると
  API がボトルネックになり、Realtime の利点も失われる。
- 一方、ユーザーが書いたテキストをクライアントから直接 INSERT させると、モデレーションと監査ログを必ず通すことを保証できない。

## 決定

1. **読み取り**（フィード・投稿・コメント・キャラ・いいね・会話・メッセージ・DM 一覧）は、ブラウザから supabase-js でユーザーの
   セッションを使って **Supabase（PostgREST / Realtime）に直接** 行う。RLS と列単位の grant で守る。
2. **クライアントが直接行ってよい書き込み** は、ユーザー由来の自由テキストを含まない軽微なものに限る:
   - `likes` の INSERT / DELETE（本人の行のみ。公開済み投稿のみ）
   - `profiles` の `display_name` / `deleted_at`（退会）の UPDATE（本人の行のみ。退会の取り消しはトリガーで禁止 → [ADR-0004](0004-schema-changes-from-spec.md)）
   - 既読位置の更新（RPC `mark_conversation_read`）
   - 自分のコメントの DELETE（削除は DB トリガーが `audit_logs` に `comment.delete` を記録）
3. **ユーザー由来テキストの書き込みはすべて Python API 経由**: DM（`POST /chat`）、コメント（`POST /comments`）、記憶の追加・編集・削除
   （`/memories*`）、会話の作成（`POST /conversations`。初回挨拶の保存を含む）。API が Gate #1 と監査ログを必ず通す。
   クライアント（`authenticated`）には `messages` / `comments` / `memories` / `conversations` への INSERT 権限を付与しない。
4. メモリパネルの一覧は、クライアントにも `memories` の SELECT 権限（`embedding` 以外）はあるが、API の並び順（重要度 → 新しい順）に
   揃えるため `GET /memories` を使う。
5. クライアントに見せない列・テーブルは grant で閉じる:
   - `characters` は `PUBLIC_CHARACTER_COLUMNS`（`id, handle, name, avatar_url, bio, follower_count, is_active, created_at`）だけ SELECT 可。
     `system_prompt` / `persona_key` はサーバー専用で、`select('*')` は 42501 になる（必ず列を列挙する）。
   - `memories.embedding` は SELECT 不可。
   - `post_private_assets`（有料投稿の本体）と `audit_logs` はクライアントから一切アクセス不可（[ADR-0006](0006-paid-post-private-assets.md)）。
6. 他人のプロフィールはクライアントから読めないため、他人のコメントの作成者は `user_` + `author_user_id` の先頭 6 桁で匿名表示する。

## 結果・トレードオフ

- 権限の全体像はマイグレーションの grant / policy が正で、`infra/supabase/tests/database/00_privileges.test.sql` が許可リストとの一致を
  検査する（CI）。テーブル・列・関数を追加したら grant・ポリシー・pgTAP テストをセットで更新する。
- API は RLS をバイパスするので、所有者チェックを API 側で必ず書く（[ADR-0003](0003-api-db-connection-asyncpg.md)）。
- いいね（`likes`）はテキストを含まないため監査ログの対象外にしている。DD で必要になったら DB トリガーで記録できる。
- 書き込み経路が 2 系統（supabase-js / API）になるため、Web 側の実装ルールを `apps/web/README.md` に明記した。

## 代替案

- **すべて API 経由**: RLS に頼らずに済むが、読み取りの遅延と API の負荷が増え、Realtime を活かせない。不採用。
- **書き込みもクライアント直 + DB トリガーでモデレーション**: 語彙リストや LLM 出力の判定、プロンプト全文を含む監査ログを DB 関数に
  持たせるのは保守が難しく、キャラの返答生成には結局 API が必要。不採用。
- **API も RLS を効かせる（ユーザーの JWT で接続）**: [ADR-0003](0003-api-db-connection-asyncpg.md) を参照。
