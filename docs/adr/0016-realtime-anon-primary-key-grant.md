# ADR-0016: Realtime 対象テーブルの anon への主キー列 grant

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §2「Realtime（DM とコメントの同期）」 / [ADR-0002](0002-data-access-split.md) / 実装: マイグレーション末尾の `grant select (id) on public.messages / public.comments to anon`, `infra/supabase/tests/database/00_privileges.test.sql`, `06_anon_and_private_tables.test.sql`

## コンテキスト

- DM（`messages`）とコメント（`comments`）の同期に Supabase Realtime の `postgres_changes` を使う。publication `supabase_realtime` に
  2 つのテーブルを追加している。
- Realtime は、購読者のロールが **主キー列の SELECT 権限を持たないと RLS を評価せず**、本文を除いた `Error 401: Unauthorized` のイベントを
  **全行分** そのまま配信する（`realtime.apply_rls` の仕様）。
- anon key は Web のバンドルに含まれる公開値なので、誰でも未ログインのまま `messages` を購読できる。このままでは全ユーザーの DM の
  件数・時刻（誰かが今話している、という事実）が漏れる。

## 決定

- Realtime の対象テーブルについて、**anon に主キー列（`id`）だけの SELECT を付与する**。anon 向けのポリシーは作らない（すべて `to authenticated`）。
  - これで RLS の評価経路に乗り、anon に一致するポリシーが無いためイベントは配信されない。REST（PostgREST）でも RLS により 0 行しか返らない。
- publication にテーブルを追加するときは、この anon への主キー列 grant も必ずセットで書く。`00_privileges.test.sql` が
  「publication のテーブルには anon の主キー列 grant がある」ことを検査する。

## 結果・トレードオフ

- 未ログインの購読者には何も届かない。ログインユーザーには RLS に一致する行（自分の会話のメッセージ、公開投稿のコメント）だけが届く。
- **DELETE イベントは Realtime の仕様で RLS が適用されず、全購読者に主キーだけが配信される**（本文は含まれない）。コメント削除の事実と ID は
  他のユーザーにも届く。
- anon に列権限があることは一見すると最小権限に反するため、マイグレーションとこの ADR に理由を書いた。

## 代替案

- **Realtime を使わずポーリング**: DM の即時性と、API のバックグラウンドで生成されるキャラの返信コメントの表示が遅れる。
- **Broadcast（サーバーから明示的に送る）に切り替え**: 送信側（API）の実装と認可の設計が必要。MVP では `postgres_changes` + RLS で足りる。
- **Realtime の Authorization（private channel）**: 設定と RLS ポリシーの追加が必要。将来 Broadcast / Presence を使うときに検討する。
