# ADR-0026: マジックリンクは確認画面を表示し、POST で初めてログインする

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §5.1 / [ADR-0012](0012-pwa-and-login-magic-link-otp.md)（「ログイン」の手順 2 のリンクの扱いを置き換え）・[ADR-0017](0017-discard-unverified-password.md) /
  実装: `apps/web/app/auth/confirm/page.tsx`, `apps/web/app/auth/confirm/verify/route.ts`, `apps/web/components/auth/confirm-login-form.tsx`,
  `apps/web/lib/auth/csrf.ts`, `apps/web/lib/auth/confirm-params.ts`, `apps/web/e2e/auth.spec.ts`, `infra/supabase/templates/magic_link.html`

## コンテキスト

ADR-0012 では、メールのリンク `/auth/confirm?token_hash=...` を Route Handler が **GET のまま** `verifyOtp` してセッション Cookie を発行していた。
この方式には 2 つの問題がある。

- **メールのセキュリティスキャナーの先読み**: 企業向けのメールサービス（Outlook の Safe Links など）は受信したメールのリンクを先に開いて検査する。
  GET でトークンを消費すると、本人がリンクを開いた時点ではトークンが使用済みになり、ログインできない（`/login?error=link`）。
- **ログイン CSRF**: 攻撃者が自分宛てに発行したマジックリンクを他人に踏ませると、その人は黙って攻撃者のアカウントでログインした状態になる
  （以後の DM や記憶が攻撃者のアカウントに書き込まれる）。

## 決定

- `GET /auth/confirm` は **確認画面（「everkano にログインしますか？」）を表示するだけ** にし、トークンを検証しない。URL にトークンが含まれるため
  リファラーを送らない（`<meta name="referrer" content="no-referrer">`）。別のアカウントでログイン中なら、切り替わることを画面に表示する。
- 画面の「ログインする」ボタンが `POST /auth/confirm/verify`（`application/x-www-form-urlencoded`: `token_hash` / `type` / `next`）を送り、
  そこで初めて `verifyOtp({ token_hash, type })` → Cookie 発行 → 退会済みの確認 → `next` へリダイレクトする。
- `POST /auth/confirm/verify` は **同じオリジンのページからのフォーム送信だけ** を受け付ける（`Sec-Fetch-Site: same-origin`。このヘッダーを送らない
  古いブラウザは `Origin` を自分のオリジンと比較。どちらも無ければ拒否）。拒否・パラメータ不正・検証失敗は `/login?error=link`。
- メールのリンクは `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&redirect_to={{ .RedirectTo | urlquery }}` にする
  （以前の `&next=/` から変更。**ホスト版 Supabase の Email Templates も同じ内容に更新する**。docs/handover/supabase-auth.md §3）。
  ログイン後の遷移先（ログイン前に開こうとしていたページ）は、ログイン画面が `emailRedirectTo = <SITE_URL>/auth/callback?next=<パス>` で送り、
  GoTrue が許可リスト（Redirect URLs / Site URL）で検証した値が `redirect_to` としてリンクに入る。確認画面は `redirect_to` の中の `next` を
  `sanitizeNextPath` で同一オリジンの相対パスに限って使う（`redirect_to` のオリジンは使わない。オープンリダイレクトにならない）。無ければ直接の `?next=`、
  それも無ければ `/`。確認画面のフォームは検証済みの `next` を POST する。リンクが無効・期限切れでも `next` は `/login?error=link&next=` に引き継ぐ。
  テンプレートには「このコードやリンクは誰にも教えないでください。everkano からコードを尋ねることはありません。」を加える（コードを聞き出す詐欺への注意）。
- 6 桁コードでのログイン（ホーム画面の PWA 用）と `/auth/callback`（PKCE の `code`）は ADR-0012 のまま。

## 結果・トレードオフ

- スキャナーがリンクを先読みしてもトークンは消費されず、本人がボタンを押したときにログインできる。他人のリンクを踏まされても、確認画面で止まる
  （別のアカウントでログイン中なら切り替わることが表示される）。E2E（`auth.spec.ts`）で「開いただけではログインせず、トークンも消費しない」
  「別のアカウントでログイン中は切り替わりを明示する」を検証している。
- ログインのタップが 1 回増える。
- 確認画面の表示は Supabase にトークンを問い合わせないので、無効・期限切れのトークンでも確認画面は出る（ボタンを押した時点で `/login?error=link`）。

## 代替案

- **GET のまま、User-Agent でスキャナーを除外する**: スキャナーは通常のブラウザの UA を名乗ることが多く、確実に見分けられない。ログイン CSRF も防げない。
- **JavaScript で自動送信する中間ページ**: スキャナーの一部は JavaScript を実行するため先読みを防げず、ログイン CSRF も防げない。
- **6 桁コードだけにする**: リンクの方が操作が少なく、Android や PC では便利。リンクとコードの両方を残す（ADR-0012）。
