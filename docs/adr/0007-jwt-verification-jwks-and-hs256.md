# ADR-0007: JWT 検証（JWKS 非対称鍵 + 旧 HS256）

- ステータス: 採用（認証サーバー障害時の 503 を [ADR-0025](0025-db-tls-and-api-entry-failures.md)、JWKS 専用の HTTP クライアントを [ADR-0028](0028-llm-input-budgets-and-summary-retries.md) で追補）
- 日付: 2026-09-25
- 関連: 仕様書 §2「認証: Supabase JWT を `Authorization: Bearer` で受け取り検証」 / 実装: `apps/api/app/core/security.py`, `apps/api/tests/test_security.py`

## コンテキスト

- Python API はブラウザから Supabase のアクセストークン（JWT）を受け取り、ユーザーを特定する（[ADR-0003](0003-api-db-connection-asyncpg.md) の
  所有者チェックの起点）。
- 新しい Supabase プロジェクト（とローカルの Supabase CLI）はアクセストークンを **非対称鍵（ES256）** で署名し、公開鍵を
  `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` で公開する。既存のホスト版プロジェクトには **旧方式（HS256・共有鍵）** のものもある。
- リクエストごとに Supabase Auth へ問い合わせる（`/auth/v1/user`）と、DM の遅延と Auth のレート制限の両方に効く。

## 決定

- API 内でトークンをローカル検証する（PyJWT）:
  - `alg` が **ES256 / RS256**: JWKS から `kid` の公開鍵を取得。`JWKS_CACHE_TTL_SECONDS`（既定 600 秒）キャッシュし、未知の `kid`
    （鍵のローテーション直後）は 30 秒のクールダウン付きで再取得する。取得に失敗したら既存のキャッシュを使い続ける。
  - `alg` が **HS256**: `SUPABASE_JWT_SECRET` が設定されている場合だけ受け付ける（旧方式のプロジェクト用）。未設定なら 401。
  - それ以外のアルゴリズム（`none` など）は拒否。
- 検証項目: 署名、`exp`（許容誤差 30 秒）、`aud` = `SUPABASE_JWT_AUDIENCE`（`authenticated`）、必須クレーム `exp` / `sub` / `aud`、
  `iss`（含まれる場合）= `SUPABASE_JWT_ISSUER`（未設定なら `{SUPABASE_URL}/auth/v1`）、`role`（含まれる場合）= `authenticated`、
  `is_anonymous` が true なら拒否、`sub` が UUID。
- 検証後に `profiles.deleted_at` を毎回確認する。プロフィール無し → 403 `forbidden`、退会済み → 403 `account_deleted`
  （どちらも `audit_logs` に `auth.failure`）。
- トークン不正（401）は理由（`token_expired` / `invalid_issuer` など）を **stdout にだけ** `auth.failure` として出し、DB には書かない
  （不正なトークンの連打で `audit_logs` を汚さないため）。
- Docker から `host.docker.internal` 経由でローカル Supabase を見る場合は、トークンの `iss`（`http://127.0.0.1:54321/auth/v1`）と
  `SUPABASE_URL` が一致しないので `SUPABASE_JWT_ISSUER` で合わせる（`docker-compose.yml` で設定済み）。

## 結果・トレードオフ

- ホスト版が ES256 でも HS256 でも同じコードで動く。HS256 のプロジェクトでは `SUPABASE_JWT_SECRET` を Fly.io の secrets に入れる。
- 非対称鍵のローテーションは自動で追従する（最大 `JWKS_CACHE_TTL_SECONDS`、未知の `kid` なら即時）。HS256 の共有鍵を変えた場合は
  API の secrets も更新する必要がある（[06-operations.md](../handover/06-operations.md#シークレットのローテーション)）。
- ローカル検証なので、ログアウトしたセッションのアクセストークンも `exp`（`jwt_expiry` = 3600 秒）までは有効。退会・プロフィール削除は
  DB の確認で即時に拒否できる。
- 起動ログの `jwt_hs256_enabled` で HS256 を受け付ける設定かどうか確認できる。ES256 経路は偽の JWKS を使った単体テストで検証している。

## 代替案

- **リクエストごとに `auth.getUser()`（Supabase Auth に問い合わせ）**: 失効を即時反映できるが、DM ごとに往復が増え、Auth のレート制限も受ける。不採用。
- **HS256 だけに対応**: 新しいプロジェクト（非対称鍵）で動かない。共有鍵を API に配る必要もある。
- **API ゲートウェイで検証**: 構成要素が増える。MVP では API 内で完結させる。
