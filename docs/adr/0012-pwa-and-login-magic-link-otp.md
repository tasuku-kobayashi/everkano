# ADR-0012: PWA とログイン方式（マジックリンク + 6 桁コード）

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §2「PWA 対応」・§5.1・H1 / [ADR-0017](0017-discard-unverified-password.md) / 実装: `apps/web/components/auth/login-form.tsx`, `apps/web/app/auth/confirm/route.ts`, `apps/web/app/auth/callback/route.ts`, `infra/supabase/templates/magic_link.html`, `apps/web/public/manifest.json`, `apps/web/public/sw.js`, `apps/web/next.config.ts`

## コンテキスト

- 仕様書 §5.1: Supabase Auth のメール + マジックリンク（`signInWithOtp`）。匿名ログインは使わない。初回ログインで `profiles` を自動作成。
- 仕様書 §2 / A11: PWA としてホーム画面に追加でき、スタンドアロンで起動する。
- **iOS でホーム画面に追加した PWA は Safari と Cookie・ストレージを共有しない**。メールのリンクは Safari（やメールアプリ内ブラウザ）で開かれるため、
  リンクだけでは PWA 側がログイン状態にならない。
- Supabase 既定のマジックリンク（`{{ .ConfirmationURL }}`）は PKCE フローで、ログインを要求したブラウザに残る code_verifier が必要。
  メールを別のブラウザ・端末で開くと完了できない。

## 決定

### ログイン

1. `/login` でメールアドレスを入力 → `signInWithOtp({ email, options: { emailRedirectTo: SITE_URL + '/auth/callback' } })`。
2. メールテンプレート（`magic_link.html`。**Magic Link と Confirm signup の両方**に同じものを使う）に次の **両方** を入れる:
   - リンク `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&next=/` → Route Handler `/auth/confirm` がサーバー側で
     `verifyOtp({ token_hash, type })` してセッション Cookie を発行する（code_verifier に依存しないので、どのブラウザで開いてもよい）。
   - 6 桁の確認コード `{{ .Token }}` → `/login` 画面で `verifyOtp({ email, token, type: 'email' })`（6 桁入力で自動送信）。
     フォームは 6 桁固定（`OTP_LENGTH`）なので、Auth の OTP 長も 6 にする。
3. `/auth/callback`（`code` → `exchangeCodeForSession`）も残す（既定テンプレートなど PKCE のコードで戻ってきた場合用）。
4. どの経路でも、ログイン直後に `profiles.deleted_at` を確認し、退会済みならサインアウトして `/login?error=withdrawn`。
   ログイン後の画面では `AccountGuard` が同じ確認を行う。`middleware.ts` は全リクエストでセッションを更新し、未ログインは `/login?next=...` へ。
5. 新規ユーザーは Confirm email を ON にした状態で運用する（確認前のパスワードの破棄とセット。[ADR-0017](0017-discard-unverified-password.md)）。

### PWA

- `public/manifest.json`: `display: standalone`・`start_url` / `scope` = `/`・`orientation: portrait`・`lang: ja`・アイコン 192 / 512 / maskable 512。
- iOS 用: Next の `metadata.appleWebApp`（`apple-mobile-web-app-capable` / タイトル）、`apple-touch-icon`（180×180）、`viewport-fit=cover`、
  ライト / ダークの `theme-color`。
- **`htmlLimitedBots: /.*/`**（`next.config.ts`）: Next.js 15.2 以降は動的ページのメタデータを `<body>` 側にストリーミングするため、
  `<link rel="manifest">` や apple-touch-icon が `<head>` に無く、Chromium がマニフェストを検出しなかった（E2E で発見）。
  すべての UA でメタデータを `<head>` に出力する。
- Service Worker（`public/sw.js`）は手書き。ページ遷移はネットワーク優先で、オフライン時は `/offline`。HTML はキャッシュしない
  （ログイン中のページを端末に残さない）。`/_next/static` と `/icons` はキャッシュ優先。Supabase・API・CDN などの他オリジン、`/auth/*`・`/media/*` は扱わない。
  本番ビルド（または `NEXT_PUBLIC_ENABLE_SW=1`）のときだけ登録する。キャッシュ方針を変えたら `VERSION` を上げる。
- アイコンは `pnpm --filter @everkano/web icons`（依存なしの PNG エンコーダー）で生成する。

## 結果・トレードオフ

- iOS のホーム画面 PWA でも 6 桁コードでログインできる。リンクはどのブラウザで開いても完了する。
- **ホスト版 Supabase の設定を必ず揃える**（Site URL / Redirect URLs / Confirm email / OTP 長 6 / 2 つのテンプレート / SMTP）。既定の
  テンプレートのままだと 6 桁コードが届かず、リンクも `/auth/confirm` を通らない（[supabase-auth.md](../handover/supabase-auth.md)）。
- 6 桁コードは総当たりの対象になり得るため、Auth の Token verifications のレート制限を緩めない。
- Service Worker とホーム画面への追加には HTTPS が必要。ローカルの `http://<PC の IP>` では実機確認できないので、A1 / A11 の実機確認は staging で行う。
- 既知の小さな問題: 検証中に別の 6 桁コードを貼り付けると自動送信されないことがある（「コードでログイン」ボタンで送れる）。

## 代替案

- **リンクだけ（既定の `{{ .ConfirmationURL }}`）**: iOS のホーム画面 PWA でログインできない。
- **パスワードログイン**: 仕様書はマジックリンクを指定。パスワード管理・再設定の導線も増える。
- **OAuth（Google / Apple）**: 仕様書の範囲外。
- **next-pwa / Workbox**: App Router との組み合わせで設定が複雑になり、生成された SW の挙動（何をキャッシュするか）を監査しにくい。
  キャッシュ対象を厳密に絞りたいので手書きにした。
