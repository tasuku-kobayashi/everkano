# ホスト版 Supabase の Auth 設定チェックリスト

ローカルの Auth 設定は `infra/supabase/config.toml` の `[auth]` 以下に書いてあるが、**ホスト版（本番 / staging）には自動で反映されない**。
プロジェクトを作ったら、ダッシュボードで下表のとおりに設定する。設定を変えたときは、config.toml とこの表の両方を更新すること。

> `supabase config push` は使わない。config.toml の `site_url`（`http://localhost:3000`）などローカル用の値まで本番に書き込まれる。
> 差分の確認だけなら `supabase config diff --workdir infra`（リンク済みのプロジェクトと比較。Site URL などの差は正しい）。

ダッシュボードのメニュー名は Supabase 側の変更で変わることがある（2026 年 9 月時点の名称）。

ローカルの Supabase も `config.toml` を起動時にだけ読む。変更したら `pnpm db:stop && pnpm db:start` で再起動する（DB のデータは残る。
`db:reset` とは違う）。2026-09-26 の変更（`otp_expiry = 900`・`secure_password_change = true`・パスワード変更の通知・Redirect URLs の `**`）は、
この手順を実行するまで起動中のローカルの Auth には反映されない（作成環境では他の作業のため再起動しておらず、未確認）。

## 1. URL

| 項目                                                 | 値                                                                                     | config.toml                  |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------- |
| Authentication → URL Configuration → Site URL        | `https://<Web の本番ドメイン>`（Vercel の `NEXT_PUBLIC_SITE_URL` と同じ）              | `site_url`                   |
| Authentication → URL Configuration → Redirect URLs   | `https://<Web の本番ドメイン>/auth/callback**`（末尾の `**` でクエリ `?next=` 付きも許可。プレビュー環境も使うならその URL も追加） | `additional_redirect_urls`   |

- メールのリンクは `{{ .SiteURL }}/auth/confirm?...` で組み立てるため、**Site URL が Web のドメインでないとリンクが壊れる**。
- `signInWithOtp` の `emailRedirectTo` は `<SITE_URL>/auth/callback?next=<ログイン前に開こうとしていたページ>`（`apps/web/lib/auth/redirect.ts` の `emailRedirectUrl`）。
  メールのリンクはこの値を `redirect_to` として運び、Web の `/auth/confirm` が `next` を取り出して、ログイン後に共有リンク（`/posts/<id>` など）へ戻す。
  Redirect URLs で許可されない（かつ Site URL とホスト名が違う）と Site URL に置き換えられ、ログイン後は常にホームへ着地する。

## 2. サインイン方式（Authentication → Sign In / Providers）

| 項目                                  | 値                   | config.toml                                  |
| ------------------------------------- | -------------------- | -------------------------------------------- |
| Allow new users to sign up            | ON                   | `[auth] enable_signup = true`                |
| Allow anonymous sign-ins              | OFF                  | `enable_anonymous_sign_ins = false`          |
| Allow manual linking                  | OFF                  | `enable_manual_linking = false`              |
| Email プロバイダー                    | 有効                 | `[auth.email] enable_signup = true`          |
| Confirm email                         | **ON**               | `[auth.email] enable_confirmations = true`   |
| Secure email change                   | ON                   | `double_confirm_changes = true`              |
| Secure password change                | **ON**               | `secure_password_change = true`              |
| Email OTP Length                      | **6**                | `otp_length = 6`                             |
| Email OTP Expiration                  | **900 秒**（15 分）  | `otp_expiry = 900`                           |
| その他のプロバイダー（Google 等）     | すべて無効           | —                                            |

- **OTP 長は必ず 6**。ログイン画面のコード入力欄は 6 桁固定（`apps/web/components/auth/login-form.tsx` の `OTP_LENGTH`）。
- **OTP の有効期限を変えたら**、ログイン画面がコード入力待ちの状態を端末に保存する期間 `PENDING_LOGIN_TTL_MS`（`apps/web/lib/auth/pending-login.ts`、15 分）も合わせる。
- パスワードは使わない。既存ユーザーのパスワードの設定・変更はマイグレーションのトリガー（`on_auth_user_password_update`）が無効にする
  （`supabase db push` で入る。ダッシュボードの設定ではない）。Secure password change はその多層防御（[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md)）。
- **Confirm email を OFF にしない**。OFF だと `POST /auth/v1/signup` にパスワード付きで他人のメールアドレスを登録するだけで
  セッションが発行され、アカウントの事前乗っ取りや使い捨てアカウントの量産ができてしまう（[ADR-0017](../adr/0017-discard-unverified-password.md)、config.toml のコメント参照）。

## 3. メールテンプレート（Authentication → Emails → Templates）

**Magic Link** と **Confirm signup** の 2 つを、どちらも次の内容にする（新規ユーザーの初回は Confirm signup が送られる）。

| 項目    | 値                                                                  |
| ------- | ------------------------------------------------------------------- |
| Subject | `everkano ログイン用リンク`                                          |
| Body    | `infra/supabase/templates/magic_link.html` の内容をそのまま貼り付ける |

本文に次の **両方** が含まれていることを確認する。既定のテンプレート（`{{ .ConfirmationURL }}` のみ）のままだと、
ホーム画面に追加した PWA（iOS）からログインできない（[ADR-0012](../adr/0012-pwa-and-login-magic-link-otp.md)）。

- `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&redirect_to={{ .RedirectTo | urlquery }}`（リンク。
  `redirect_to` がログイン後の遷移先を運ぶ。以前の `&next=/` のままだと、ログイン後は常にホームへ着地する）
- `{{ .Token }}`（6 桁の確認コード）
- 「このコードやリンクは誰にも教えないでください。everkano からコードを尋ねることはありません。」（コードを聞き出す詐欺への注意）

あわせて **Password changed**（パスワード変更の通知。Authentication → Emails → Notifications など）を有効にし、件名
`everkano パスワード設定の要求がありました`・本文 `infra/supabase/templates/password_changed_notification.html` にする
（config.toml の `[auth.email.notification.password_changed]`。パスワードは設定されないが、要求があったことを本人に知らせる）。

## 4. メール送信（Authentication → Emails → SMTP Settings）

- **カスタム SMTP を設定する**（SendGrid / Amazon SES / Resend 等）。Supabase 既定の送信サーバーはチームメンバー宛てにしか届かず、
  送信数の上限も非常に低いため、一般ユーザーのログインに使えない。ローカルでは Mailpit（http://127.0.0.1:54324）が受ける。
- 送信元アドレスのドメインに SPF / DKIM を設定する（迷惑メール判定でログインできない問い合わせを防ぐ）。
- SMTP のパスワードは Supabase ダッシュボードにだけ保存し、リポジトリには書かない（H7）。

## 5. レート制限・セッション

| 項目                                                     | 値・方針                                                                 | config.toml                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| Authentication → Rate Limits → Emails sent               | カスタム SMTP の上限と想定ユーザー数に合わせる（ローカルは 100 / 時）     | `[auth.rate_limit] email_sent`                  |
| Rate Limits → Token verifications                         | 既定より緩めない（6 桁コードの総当たり対策）                             | `token_verifications = 30`                      |
| Rate Limits → Sign-ups and sign-ins                       | 既定のまま（ローカルは 30 / 5 分・IP）                                    | `sign_in_sign_ups = 30`                         |
| アクセストークンの有効期限（JWT expiry）                  | 3600 秒                                                                  | `jwt_expiry = 3600`                             |
| Refresh token rotation / reuse interval                   | ON / 10 秒                                                               | `enable_refresh_token_rotation`, `refresh_token_reuse_interval` |
| Attack Protection（Bot and Abuse Protection）の CAPTCHA   | **当面 OFF**。Web のログイン画面が hCaptcha の `captchaToken` を送るようになってから hCaptcha で ON にする（先に ON にすると誰もログインメールを要求できなくなる。Turnstile は Cloudflare なので使わない / H8） | `[auth.captcha]`（コメントアウト） |

- CAPTCHA が無い間は、anon key だけで任意のアドレス宛てのログインメールを要求し続けて、プロジェクト全体の送信枠（Emails sent）を使い切れる
  （全員のログインメールが届かなくなる）。Auth のログで `/auth/v1/otp` の 429（`over_email_send_rate_limit`）を監視し、Emails sent は SMTP の実際の
  送信能力に合わせる（受け入れているリスク。[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md)）。

## 6. JWT の署名方式と API の設定

- 新しいプロジェクト（非対称鍵 = ES256 / RS256）: API は `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` から公開鍵を取得するので、
  追加の設定は不要。
- 旧方式（共有鍵 = HS256）のプロジェクト: Project Settings → JWT の「JWT Secret」を API のシークレット
  `SUPABASE_JWT_SECRET` に設定する（`fly secrets set --config apps/api/fly.toml SUPABASE_JWT_SECRET=...`）。
- API の `SUPABASE_URL` は `https://<project-ref>.supabase.co`（staging / production では https 必須。起動時に検証される）。
  カスタムドメインを使う場合など、トークンの `iss` が `{SUPABASE_URL}/auth/v1` と異なるときは `SUPABASE_JWT_ISSUER` を設定する。

## 7. 設定後の確認

1. staging の Web でメールアドレスを入力し、届いたメールに「ログインする」ボタンと 6 桁のコードの両方があること。
2. ボタンのリンク先が `https://<Web のドメイン>/auth/confirm?token_hash=...&type=email&redirect_to=...` で、タップすると確認画面
   「everkano にログインしますか？」が開き、「ログインする」を押すとログインできること（開いただけではログインしない。[ADR-0026](../adr/0026-magic-link-confirm-page.md)）。
3. もう一度ログインを要求し、今度は 6 桁コードを入力してログインできること（iOS はホーム画面に追加した PWA からも確認）。
4. ログアウトした状態で投稿の共有リンク（`https://<Web のドメイン>/posts/<id>`）を開いてログインし、メールのリンクから
   その投稿に戻ること（ホームに着地する場合はテンプレートの `redirect_to` と Redirect URLs を確認）。
5. 初めて使うメールアドレスでも同じメール（Confirm signup テンプレート）が届き、ログインできること。
6. ログイン後、DM を 1 往復送って API が 200 を返すこと（401 なら JWT の方式・`SUPABASE_URL`・`SUPABASE_JWT_ISSUER` を確認）。
