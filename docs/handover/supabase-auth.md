# ホスト版 Supabase の Auth 設定チェックリスト

ローカルの Auth 設定は `infra/supabase/config.toml` の `[auth]` 以下に書いてあるが、**ホスト版（本番 / staging）には自動で反映されない**。
プロジェクトを作ったら、ダッシュボードで下表のとおりに設定する。設定を変えたときは、config.toml とこの表の両方を更新すること。

> `supabase config push` は使わない。config.toml の `site_url`（`http://localhost:3000`）などローカル用の値まで本番に書き込まれる。
> 差分の確認だけなら `supabase config diff --workdir infra`（リンク済みのプロジェクトと比較。Site URL などの差は正しい）。

ダッシュボードのメニュー名は Supabase 側の変更で変わることがある（2026 年 9 月時点の名称）。

## 1. URL

| 項目                                                 | 値                                                                                     | config.toml                  |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------- |
| Authentication → URL Configuration → Site URL        | `https://<Web の本番ドメイン>`（Vercel の `NEXT_PUBLIC_SITE_URL` と同じ）              | `site_url`                   |
| Authentication → URL Configuration → Redirect URLs   | `https://<Web の本番ドメイン>/auth/callback`（プレビュー環境も使うならその URL も追加） | `additional_redirect_urls`   |

- メールのリンクは `{{ .SiteURL }}/auth/confirm?...` で組み立てるため、**Site URL が Web のドメインでないとリンクが壊れる**。
- `signInWithOtp` の `emailRedirectTo`（`<SITE_URL>/auth/callback`）が Redirect URLs に無いと、Site URL に置き換えられる。

## 2. サインイン方式（Authentication → Sign In / Providers）

| 項目                                  | 値                   | config.toml                                  |
| ------------------------------------- | -------------------- | -------------------------------------------- |
| Allow new users to sign up            | ON                   | `[auth] enable_signup = true`                |
| Allow anonymous sign-ins              | OFF                  | `enable_anonymous_sign_ins = false`          |
| Allow manual linking                  | OFF                  | `enable_manual_linking = false`              |
| Email プロバイダー                    | 有効                 | `[auth.email] enable_signup = true`          |
| Confirm email                         | **ON**               | `[auth.email] enable_confirmations = true`   |
| Secure email change                   | ON                   | `double_confirm_changes = true`              |
| Email OTP Length                      | **6**                | `otp_length = 6`                             |
| Email OTP Expiration                  | 3600 秒              | `otp_expiry = 3600`                          |
| その他のプロバイダー（Google 等）     | すべて無効           | —                                            |

- **OTP 長は必ず 6**。ログイン画面のコード入力欄は 6 桁固定（`apps/web/components/auth/login-form.tsx` の `OTP_LENGTH`）。
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

- `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&next=/`（リンク）
- `{{ .Token }}`（6 桁の確認コード）

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

## 6. JWT の署名方式と API の設定

- 新しいプロジェクト（非対称鍵 = ES256 / RS256）: API は `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` から公開鍵を取得するので、
  追加の設定は不要。
- 旧方式（共有鍵 = HS256）のプロジェクト: Project Settings → JWT の「JWT Secret」を API のシークレット
  `SUPABASE_JWT_SECRET` に設定する（`fly secrets set --config apps/api/fly.toml SUPABASE_JWT_SECRET=...`）。
- API の `SUPABASE_URL` は `https://<project-ref>.supabase.co`（staging / production では https 必須。起動時に検証される）。
  カスタムドメインを使う場合など、トークンの `iss` が `{SUPABASE_URL}/auth/v1` と異なるときは `SUPABASE_JWT_ISSUER` を設定する。

## 7. 設定後の確認

1. staging の Web でメールアドレスを入力し、届いたメールに「ログインする」ボタンと 6 桁のコードの両方があること。
2. ボタンのリンク先が `https://<Web のドメイン>/auth/confirm?token_hash=...` で、タップするとログインできること。
3. もう一度ログインを要求し、今度は 6 桁コードを入力してログインできること（iOS はホーム画面に追加した PWA からも確認）。
4. 初めて使うメールアドレスでも同じメール（Confirm signup テンプレート）が届き、ログインできること。
5. ログイン後、DM を 1 往復送って API が 200 を返すこと（401 なら JWT の方式・`SUPABASE_URL`・`SUPABASE_JWT_ISSUER` を確認）。
