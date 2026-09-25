# ADR-0025: API → DB の TLS 必須化と、API の入口での失敗の分類（本文サイズ上限・認証サーバー障害）

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §2・§15・H7 / [ADR-0003](0003-api-db-connection-asyncpg.md)・[ADR-0007](0007-jwt-verification-jwks-and-hs256.md)（本 ADR で追補） /
  実装: `apps/api/app/core/config.py`（`_database_tls_errors` / `database_sslmode`）, `apps/api/app/core/middleware.py`（本文サイズ上限）,
  `apps/api/app/core/security.py`（`JwksCache` / `get_current_user`）, `apps/api/README.md`「DB への接続（TLS）」, `apps/api/fly.toml`（`[[files]]`）

## コンテキスト

- API は RLS をバイパスする `postgres` ロールで、DM・記憶・監査ログを Fly.io（nrt）から Supabase へ公衆網越しに運ぶ（ADR-0003）。
  asyncpg の既定の `sslmode=prefer` は **証明書を検証せず、TLS を張れなければ平文に落ちる**。接続文字列に `sslmode` を書き忘れると、
  それに気付く手段が無かった。
- API はリクエスト本文の大きさを制限しておらず、認証の前に巨大な本文を読み込ませてメモリを消費させることができた。
- 認証サーバー（JWKS）に接続できないと、トークンの検証がすべて 401 になっていた。Web は 401 を「セッション切れ」としてログアウトさせるため、
  Supabase Auth の一時的な障害で **ログイン中の全ユーザーがログアウト** させられる。

## 決定

- **DB の TLS**: `APP_ENV` が `staging` / `production` で、`DATABASE_URL` のホストがループバック（`localhost` / `127.0.0.1` / `::1` 等）以外なら、
  `sslmode` が `require` / `verify-ca` / `verify-full` のいずれか（URL の `sslmode` の最後の値、無ければ環境変数 `PGSSLMODE`）でなければ
  **起動しない**（設定の検証エラー。文面に接続文字列・パスワードを含めない）。推奨は `verify-full`（Supabase のルート証明書で証明書と
  ホスト名を検証する。証明書は Fly.io の secret から `[[files]]` で `/app/certs/supabase-ca.crt` に置く）。実際の値は起動ログの
  `database_sslmode` で確認できる。Supabase 側でも「Enforce SSL on incoming connections」と Network Restrictions を有効にする
  （[07-security.md](../handover/07-security.md)）。
- **本文サイズの上限**: `MAX_REQUEST_BODY_BYTES`（既定 64 KiB、1024〜10485760）を超える本文は、読み込む前に 413 `validation_error` を返す
  （`Content-Length` を見て即座に、無い場合は読みながら数えて打ち切る。認証より前に効く）。正規の最大は `/chat` の 2000 文字（UTF-8 で約 8 KB）。
- **認証サーバーの障害**: JWKS を取得できず検証に使える鍵が無い（または未知の `kid` で再取得に失敗した）間は、トークンの正否を判定できないので
  401 ではなく **503 `internal_error` + `Retry-After: 5`**（「ただいまログイン状態を確認できません。しばらくしてから再度お試しください。」）を返す。
  Web は 401 と `account_deleted` のときだけログアウトさせるので、503 ではログアウトしない。失敗の直後 5 秒は再取得せずに即座に失敗させる
  （待ち行列で 1 件ずつ JWKS のタイムアウトを待たせない）。起動時に JWKS を先に取得しておく（失敗しても起動は続ける）。

## 結果・トレードオフ

- `sslmode` の書き忘れはデプロイ時（起動の失敗）に分かる。ループバックはサイドカーのプロキシ等を想定して対象外。ローカル（`APP_ENV=local`）も対象外。
  `require` は暗号化だけで接続先を検証しないため、経路上のなりすましは防げない（`verify-full` の用意までの暫定）。
- 巨大な本文でメモリを使わせる攻撃を入口で止める。上限を超える正規のリクエストは無い（`/chat` の本文の上限は API のスキーマで別に検証）。
- 認証サーバーの障害中は API を使う操作（DM の送信・コメント・メモリの編集）が 503 になるが、ログアウトはさせない。Supabase を直接読む画面
  （フィード・DM の履歴など）は Supabase 側の状態に従う。

## 代替案

- **`sslmode` を API が強制的に付け足す**: 接続文字列の意図（`verify-full` の証明書の場所など）を黙って書き換えることになる。明示を求めて起動を止める方を選んだ。
- **本文の上限をリバースプロキシ（Fly.io）だけで設定する**: Fly.io の設定に依存し、他のホストに移すと抜ける。アプリ側で持つ。
- **JWKS の障害時に期限切れのキャッシュ鍵で検証を続ける**: 鍵があれば既にそうしている（TTL 切れでも取得に失敗したら手元の鍵を使う）。鍵が 1 つも無い・
  未知の `kid` のときに限り 503 にする。
