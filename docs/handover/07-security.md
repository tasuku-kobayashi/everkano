# 07. セキュリティ

脅威モデルの要約と、ハードルール（特に H4・H7）の守り方、認可の検証方法。認証・データアクセスの設計は
[ADR-0002](../adr/0002-data-access-split.md)・[ADR-0003](../adr/0003-api-db-connection-asyncpg.md)・[ADR-0007](../adr/0007-jwt-verification-jwks-and-hs256.md)。

## 守るもの

| 資産                                   | 置き場所                                        | 重要度 | 備考                                                    |
| -------------------------------------- | ----------------------------------------------- | ------ | ------------------------------------------------------- |
| DM の会話・記憶（「二人だけの秘密」を含む） | `messages` / `memories`                      | 高     | 最も機微。本人以外（他ユーザー・未ログイン）に見せない   |
| 監査ログ（会話本文・プロンプト全文）   | `audit_logs`                                    | 高     | クライアントから一切アクセス不可                         |
| 有料投稿の本体画像                     | `post_private_assets` + B2                      | 中     | 決済未実装のため誰にも見せない                           |
| キャラの内部設定                       | `characters.system_prompt` / `persona_key`、YAML | 中     | 列 grant でクライアント非公開（YAML はリポジトリにある） |
| 強い資格情報                           | `DATABASE_URL`、service_role key、LLM / 埋め込み / Bunny / SMTP のキー | 高 | 環境変数・各サービスのシークレットのみ（H7）      |
| ユーザーのメールアドレス               | `auth.users`                                    | 中     | 他ユーザーには見えない（コメントは `user_xxxxxx` で匿名表示） |

## 脅威と対策（要約）

| 脅威                                                   | 対策                                                                                                                                                              | 検証                                                                 |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| 他ユーザーの DM・記憶を読む（REST / Realtime）          | 全テーブル RLS、ポリシーは `to authenticated` のみ、列 grant、`revoke all` + 明示 grant                                                                          | pgTAP（`05_dm_isolation` ほか）、E2E `rls.spec.ts`                   |
| 他ユーザーの会話・記憶を API で操作する                 | API の全クエリを検証済み `user_id` でスコープ、他人のリソースは 404                                                                                               | 統合テスト（`test_chat_ownership`、`test_memories_crud_and_ownership`）、E2E |
| 未ログインで Realtime を購読して DM の件数・時刻を知る  | anon に主キー列だけ grant して RLS の評価経路に乗せる（[ADR-0016](../adr/0016-realtime-anon-primary-key-grant.md)）                                               | `00_privileges` / `06_anon_and_private_tables`                       |
| クライアントから直接テキストを書き込み、モデレーション・監査を回避 | `messages` / `comments` / `memories` / `conversations` に INSERT 権限を与えない（書き込みは API のみ）                                                     | `00_privileges`（許可リスト）                                        |
| 偽造・期限切れ・他プロジェクトの JWT                    | 署名（JWKS / HS256）・`exp`・`aud`・`iss`・`role`・匿名ユーザー拒否                                                                                             | `tests/test_security.py`                                             |
| 退会したユーザーが使い続ける                            | API は毎回 `profiles.deleted_at` を確認（403）、Web はログイン時と `AccountGuard` でサインアウト、退会の取り消しはトリガーで禁止 | `test_deleted_profile_is_rejected`、`01_profiles`                    |
| アカウントの事前乗っ取り（パスワード付き signup）       | Confirm email 必須 + 確認時に確認前のパスワードを破棄（[ADR-0017](../adr/0017-discard-unverified-password.md)）                                                    | `08_auth_password_hardening`、`infra/supabase/tests/auth/signup_hardening.py` |
| 6 桁コードの総当たり                                    | Supabase Auth のレート制限（Token verifications）を緩めない、OTP の有効期限 1 時間                                                                                | 設定の確認（[supabase-auth.md](supabase-auth.md)）                   |
| 有料投稿の本体を取得する                                | 本体は `post_private_assets`（ポリシー・grant 無し）、プレビューとは推測できない別キー                                                                            | E2E `paid.spec.ts`（URL が通信・DOM に出ない）、`02_characters_posts` |
| LLM の乱用（コスト）                                    | ユーザー単位のレート制限（`/chat` 20 / 分、コメント 10 / 分）                                                                                                      | `test_rate_limit`                                                    |
| 有害・違法な出力（未成年・実在人物・暴言）              | Gate #1（入出力）、キャラ別 NG ワード、全キャラ成人の検証（起動時・CI）                                                                                           | `test_moderation.py`、`pnpm personas:validate`                       |
| プロンプトインジェクションでキャラの設定を引き出す      | システムプロンプトに制約を記載。出力は Gate #1 で検査。**それ以上の対策は無い**（キャラ設定は機密情報として扱っていない）                                          | —                                                                    |
| 入力による障害（巨大な本文・NUL 文字）                  | 長さ制限、制御文字の拒否（入口で 422）                                                                                                                            | `test_control_characters_are_rejected_before_any_work` ほか          |
| XSS                                                    | React のエスケープ（`dangerouslySetInnerHTML` 不使用）。CSP は未設定（[ADR-0015](../adr/0015-no-csp-in-mvp.md)）                                                  | —                                                                    |
| クリックジャッキング・MIME 推測                         | `X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy`、`Permissions-Policy`                                                               | —                                                                    |
| オープンリダイレクト（ログイン後の `next`）             | `sanitizeNextPath`（同一オリジンのパスだけ許可）                                                                                                                  | `apps/web/lib/auth/auth.test.ts`                                     |
| 端末に残るデータ                                        | Service Worker は HTML・API・Supabase の応答をキャッシュしない。ログアウトで React Query のキャッシュを破棄                                                       | E2E `pwa.spec.ts`（キャッシュは同一オリジンの静的ファイルのみ）       |

## ハードルールの守り方

### H4: NSFW 画像を Vercel / Supabase Storage に置かない

- 画像は Backblaze B2（オリジン）+ Bunny.net（CDN）。DB には URL かオブジェクトキーだけを置く（[ADR-0011](../adr/0011-storage-adapter-and-bunny-token-auth.md)）。
- Supabase Storage は無効（`infra/supabase/config.toml` の `[storage] enabled = false`）。コードに Storage の API 呼び出しは無い。
- `next/image` は使わない（Vercel の画像最適化を通らない）。`/media/*` は 302 リダイレクトだけで、画像のバイト列を Vercel で中継しない。
- キャラ・投稿の画像の実体はリポジトリにコミットしない（シードは外部のプレースホルダ URL）。リポジトリにある画像はアプリのアイコン（`apps/web/public`）と、
  受け入れ確認のスクリーンショット（`docs/acceptance/screenshots`。写っている画像はテスト用に生成したダミー）だけ。

### H7: シークレットをコミットしない

- `.env.example` にはキー名とローカル開発用の公開既定値だけ（キー・シークレット系は空）。実際の値は `apps/web/.env.local` / `apps/api/.env`
  （`.gitignore` 済み、`pnpm setup:env` は権限 600 で作成）と、各サービスの環境変数・secrets にだけ置く。
- **`scripts/check-secrets.sh`**（CI の checks ジョブ、`pnpm check:secrets`）がコミットされ得る全ファイルを検査する: 秘密鍵、`sk-` 形式、
  Supabase の `sb_secret_` と JWT（service_role / anon）、AWS / GitHub / Slack / Google / Stripe の形式、Sentry DSN、Bunny / B2 のキーへの代入、
  名前が `SECRET` / `PASSWORD` / `API_KEY` 等の変数への文字列代入、パスワード付きの接続文字列、`.env*` や `*.pem` のファイル自体。
  値そのものは出力しない（CI ログへの二次漏えい防止）。2026-09-25 時点で 380 ファイル・検出なし。
- Web のクライアントに渡るのは `NEXT_PUBLIC_*`（公開してよい値）だけ。`BUNNY_TOKEN_AUTH_KEY` は `lib/env.server.ts`（`import "server-only"`）で
  読み、クライアントには「設定されているか」だけを `NEXT_PUBLIC_MEDIA_SIGNED` で渡す。
- service_role key はアプリで使わない。`setup-env.sh` も service_role key・secret key・JWT secret を書き出さない。
- API のシークレットは pydantic の `SecretStr` で持ち、ログに出さない。起動ログには「HS256 が有効か」などの有無だけを出す。

### その他のハードルール

| ルール | 守り方 |
| ------ | ------ |
| H1 スマホ専用 | 480px のアプリシェル。E2E `layout.spec.ts` |
| H2 投稿はキャラのみ | 投稿タブ・投稿 API・ファイル入力なし。`posts` への書き込み権限なし。`check-scope.sh`・E2E `layout.spec.ts` |
| H3 決済なし | 「購入する（準備中）」のモーダルのみ。`check-scope.sh` |
| H5 API は Vercel 外 | Fly.io（Docker） |
| H6 構造化ログ | `audit_logs` + stdout JSON（[ADR-0013](../adr/0013-audit-log.md)） |
| H8 Cloudflare 不使用 | CDN は Bunny.net。コード・設定に Cloudflare の依存なし |

## RLS テストスイート

| スイート                                                   | 内容                                                                                                   | 実行                                      |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| pgTAP（`infra/supabase/tests/database/`、9 ファイル / 173 件） | 権限マトリクスの許可リスト、テーブルごとの RLS、DM の分離（A13）、anon、トリガー、パスワード破棄        | `pnpm db:test`（CI の api-db ジョブ）     |
| Auth の設定テスト（`infra/supabase/tests/auth/signup_hardening.py`） | Confirm email・パスワード付き signup でセッションが出ない・事前乗っ取りのシナリオ          | 手動（起動中のローカル Supabase が必要）   |
| API の統合テスト（`apps/api/tests/integration/`）           | 所有者チェック（他人の会話・記憶は 404）、退会・プロフィール無し、レート制限、モデレーション              | `pnpm test`（CI）                          |
| E2E（`apps/web/e2e/rls.spec.ts`）                          | 2 アカウント: supabase-js で他人の会話・メッセージ・記憶・プロフィール・DM 一覧が 0 件、直接 INSERT 拒否、API で 404、トークン無しで 401、画面にも出ない | 手動（[08-dev-guide.md](08-dev-guide.md#テスト)） |

テーブル・列・関数を追加すると `00_privileges.test.sql` の許可リストが失敗するので、意図どおりか確認して更新し、挙動のテストも足す
（書き方は [infra/supabase/tests/README.md](../../infra/supabase/tests/README.md)）。

## 個人データの扱い

- DM・記憶・監査ログに、ユーザーが書いた内容（個人情報を含み得る）がそのまま入る。監査ログにはプロンプト全文も入る（`AUDIT_LOG_PROMPTS`）。
- 会話は LLM / 埋め込みの提供元（OpenRouter → DeepSeek、OpenAI 互換の埋め込み API）に送られる。利用規約・データの取り扱い（学習への利用の有無など）を
  事業側で確認し、プライバシーポリシーに反映すること（**未対応**）。
- 保存期間・削除依頼への対応（[物理削除の手順](06-operations.md#ユーザーの物理削除)）・監査ログの扱いは事業側と決める（未決）。
- Sentry は `send_default_pii=false`。

## 既知のギャップ

- CSP 未設定（[ADR-0015](../adr/0015-no-csp-in-mvp.md)）。
- API の DB 接続が `postgres` ロール（専用ロールで最小権限にするのが望ましい）。
- レート制限はマシンごと。IP 単位の制限・WAF は無い（Cloudflare は H8 で不使用。Fly.io / Bunny の機能で追加を検討）。
- Bunny の署名 URL はユーザーに紐付かない（有効期限内は URL を知っていれば取得できる）。
- Realtime の DELETE イベントは RLS が適用されず、主キーだけが全購読者に届く。
- 依存関係の脆弱性の自動検出（Dependabot / `pip-audit` 等）は未設定。ロックファイル（`pnpm-lock.yaml` / `uv.lock`）と CI の frozen install で再現性だけ担保している。
- セキュリティ診断（ペネトレーションテスト）は未実施。
