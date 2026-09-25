# ADR-0033: 認証の追加の守り（パスワード設定の無効化・コードの有効期限 15 分・CAPTCHA は Web の対応後・利用停止の扱い）

- ステータス: 採用（CAPTCHA は未導入。一般公開の前に導入する）
- 日付: 2026-09-26
- 関連: 仕様書 §5.1 / [ADR-0012](0012-pwa-and-login-magic-link-otp.md)・[ADR-0017](0017-discard-unverified-password.md)（本 ADR で追補）・[ADR-0026](0026-magic-link-confirm-page.md) /
  実装: `infra/supabase/migrations/20260925000000_init.sql`（`ignore_password_update()` / `on_auth_user_password_update`）, `infra/supabase/config.toml`（`[auth.email]`・
  `[auth.rate_limit]`・`[auth.captcha]`・`[auth.email.notification.password_changed]`）, `infra/supabase/templates/password_changed_notification.html`,
  `infra/supabase/tests/database/08_auth_password_hardening.test.sql`, `infra/supabase/tests/auth/signup_hardening.py`,
  `apps/web/components/auth/account-guard.tsx`・`apps/web/lib/auth/account.ts`（`AccountBannedError`）, [docs/handover/supabase-auth.md](../handover/supabase-auth.md)

## コンテキスト

everkano はパスワードを使わない（メールのリンクか 6 桁コードだけ。ADR-0012）。納品前の検査で、Supabase Auth の既定の機能が次の抜け道になっていた。

- **パスワードの後付け**: Supabase Auth の `PUT /auth/v1/user {password}` は、有効なアクセストークン（1 時間）だけで確認なしにパスワードを設定できる。
  トークンが一度でも漏れる（XSS・共用端末・ブラウザ拡張など）と、攻撃者はパスワードを設定し、サインアウトやリフレッシュトークンの失効の後も
  そのパスワードでいつでもログインできる（恒久的な乗っ取り）。
- **6 桁コードの有効期限が 1 時間**: GoTrue の試行回数の制限は IP ごとだけで、ユーザーごとの上限は無い。有効期限が長いほど総当たりに使える時間が長い。
- **ボット対策が無い**: メール送信の上限（`email_sent`）はプロジェクト全体の値。CAPTCHA が無いと、anon key だけで任意のアドレス宛てのログインメールを
  要求し続けて枠を使い切れる（1 IP でも `sign_in_sign_ups` 30 / 5 分 = 360 / 時 > 既定の 100 / 時）。枠が尽きると全員のログインメールが届かなくなる
  （ログインできなくなる攻撃）。
- 運営が Supabase Auth でアカウントを利用停止（ban）にしたとき、Web はそれを扱っておらず、`/` ⇄ `/login` のリダイレクトが繰り返された（検査で 10 秒に 46 回）。

## 決定

- **既存ユーザーのパスワードの設定・変更を DB で無効にする**: `auth.users` の `encrypted_password` を「空でない別の値」に変える UPDATE を、トリガー
  `on_auth_user_password_update`（`ignore_password_update()`）が元の値に戻す。例外にはしない（GoTrue は 200 を返すが、パスワードは設定されない。
  GoTrue がハッシュ形式の更新で `encrypted_password` を書き換える場合にログイン自体を失敗させないため）。発生は Postgres のログに LOG で残す。
  パスワードの消去（NULL / 空文字。ADR-0017 の破棄を含む）と INSERT（管理 API で作るテスト用ユーザー）は対象外。
  多層防御として `secure_password_change = true`（最近ログインしていないセッションからの変更に再認証を要求）と、パスワードの設定が要求されたことを
  本人に知らせる通知メール（`password_changed`）を有効にする。**パスワードを使う機能を追加する場合は、このトリガーを削除するマイグレーションと ADR が必要**。
- **メールのコード・リンクの有効期限を 15 分**（`otp_expiry = 900`）にする。ログイン画面のコード入力待ちの保存期間（`PENDING_LOGIN_TTL_MS`）も 15 分に合わせる。
- **CAPTCHA（hCaptcha）は、Web のログイン画面が `captchaToken` を送るようになってから有効にする**。先に有効にすると誰もログインメールを要求できなくなる。
  Turnstile は Cloudflare のサービスなので使わない（H8）。それまでの間は、メール送信枠の枯渇によるログイン妨害を **受け入れるリスク** とし、Auth のログで
  `/auth/v1/otp` の 429（`over_email_send_rate_limit`）を監視する。`email_sent` は SMTP の実際の送信能力に合わせて設定する。
- **利用停止（ban）**: Web は Auth の `user_banned` を `AccountBannedError` として扱い、この端末のセッションを消してから `/login?error=banned`
  （「このアカウントは利用停止中です。」）へ送る。ユーザーの削除・無効なセッションも同様に `?error=session` へ送り、middleware はこれらの `?error=` のときは
  ログイン済みに見えても `/` へ戻さない（ループさせない）。
- ホスト版 Supabase の Auth 設定は `config.toml` から自動では反映されないため、[supabase-auth.md](../handover/supabase-auth.md) のチェックリストに上記を加える。

## 結果・トレードオフ

- アクセストークンが漏れても、被害はそのトークンの有効期限（最長 1 時間）とリフレッシュトークンの範囲に留まり、パスワードによる恒久的なログインはできない
  （pgTAP `08_auth_password_hardening` で、確認済みユーザーのパスワードの設定・変更が無効になることを検査）。
- パスワードを使うテスト用ユーザー（管理 API で作成）は引き続きパスワードでログインできる（INSERT は対象外）。管理 API でパスワードを **変更** することはできない。
- コードの有効期限が短くなったので、メールを開くのが 15 分より遅れた場合は送り直しが必要になる。
- CAPTCHA が入るまで、ログインメールの要求の大量送信で全員のログインを一時的に妨げられる（**残存リスク**。一般公開の前に Web 側の対応と合わせて導入する）。
- 利用停止しても、API はアクセストークンの有効期限（最長 1 時間）までは受け付ける（API は JWT の署名と `profiles.deleted_at` だけを確認する）。
  直ちに止める必要があれば、運用者が `profiles.deleted_at` も設定する（API は 403 `account_deleted` を返す。[06-operations.md](../handover/06-operations.md)）。
- ローカルの Supabase は `config.toml` の変更を再起動（`pnpm db:stop && pnpm db:start`）まで読み込まない。

## 代替案

- **Supabase の設定だけで防ぐ（`secure_password_change` のみ）**: 最近ログインしたセッション（トークンを盗んだ直後の攻撃者を含む）はそのまま変更できる。
- **トリガーで例外を投げる**: GoTrue のパスワードログイン（テスト用ユーザー）でハッシュの更新が失敗し、ログインそのものが失敗する。
- **CAPTCHA を先に有効にする**: Web が `captchaToken` を送っていないため、全員がログインできなくなる。
- **ban の代わりに退会（`deleted_at`）だけで止める**: 利用者には「退会済み」と表示され、運営の措置であることが伝わらない。Auth の ban はリフレッシュも止める。
