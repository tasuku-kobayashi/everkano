# ADR-0017: メール確認前パスワードの破棄と Confirm email 必須（事前乗っ取り対策）

- ステータス: 採用（既存ユーザーのパスワード設定の無効化・コードの有効期限・CAPTCHA の方針を [ADR-0033](0033-auth-hardening-password-otp-captcha.md) で追補）
- 日付: 2026-09-25
- 関連: [ADR-0012](0012-pwa-and-login-magic-link-otp.md) / 実装: マイグレーションの `discard_unverified_password()` と `on_auth_user_email_verified`, `infra/supabase/config.toml`（`[auth.email] enable_confirmations = true`）, `infra/supabase/tests/database/08_auth_password_hardening.test.sql`, `infra/supabase/tests/auth/signup_hardening.py`

## コンテキスト

- ログインはマジックリンク / 6 桁コードだけでパスワードは使わない。しかし Supabase Auth の `POST /auth/v1/signup` は email + password を
  誰でも受け付ける（anon key は公開値）。
- 攻撃者が他人のメールアドレスでパスワード付きの未確認ユーザーを先に作り、本人が後からマジックリンクでログイン（= メール確認）すると、
  同じユーザーに攻撃者のパスワードが残る（GoTrue は確認時にパスワードを消さない）。攻撃者は以後パスワードでログインできる（アカウントの事前乗っ取り）。
- Confirm email が OFF（自動確認）だと、signup の応答でそのままセッションが発行され、確認前のアカウントの量産もできる。

## 決定

- **Confirm email を ON にする**（ローカル: `config.toml` の `[auth.email] enable_confirmations = true`、ホスト版: ダッシュボード）。
- `auth.users` の BEFORE UPDATE トリガー `on_auth_user_email_verified` で、**メールで送ったトークン（確認メール = `confirmation_sent_at`、
  マジックリンク / 再設定 = `recovery_sent_at`）によって未確認 → 確認済みになる瞬間に `encrypted_password` を NULL にする**。
- 管理 API（service role）で `email_confirm: true` を指定して作ったユーザー（テスト用）はメールのトークンを経由しない（sent_at が NULL）ため対象外で、
  E2E・統合テストのパスワードログインは引き続き使える。

## 結果・トレードオフ

- 攻撃者が先に作ったパスワードは、本人がメールで確認した時点で無効になる。pgTAP（`08_auth_password_hardening`、8 件）と、起動中のローカル
  Auth に対するシナリオテスト（`python3 infra/supabase/tests/auth/signup_hardening.py`）で確認できる。
- `auth` スキーマ（Supabase 管理）にトリガーを置いている。Supabase のバージョンアップで `auth.users` の列名・挙動が変わった場合は、
  上のテストで回帰を確認する。
- ホスト版で Confirm email を OFF にすると、この対策は効かない（[supabase-auth.md](../handover/supabase-auth.md) に明記）。
- パスワードでのログイン自体を将来使う場合は、この前提（パスワードは確認時に破棄）を見直す新しい ADR が必要。

## 代替案

- **パスワードサインアップを無効にする設定だけに頼る**: マジックリンクも同じ Email プロバイダを使うため、プロバイダを有効にしたまま
  password 付きの signup だけを止める設定は（2026 年 9 月時点で）見当たらなかった。「Allow new users to sign up」を OFF にすると新規登録そのものができなくなる。
- **Auth Hook（before user created）で password 付きの signup を拒否**: ホスト版の設定と関数のデプロイが増える。トリガーの方が自己完結する。
