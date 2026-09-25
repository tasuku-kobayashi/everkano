# E2E テスト（受け入れ基準 §13 の自動検証）

Playwright（Chromium）で、スマホ相当の 2 端末（`iphone` = 390×844 @3x / `android` = 412×915 @2.625x、`ja-JP`・`Asia/Tokyo`）を
エミュレートして、仕様書 §13 の受け入れ基準のうち自動化できるものを検証する。実行結果は
[`docs/acceptance/e2e-results.md`](../../../docs/acceptance/e2e-results.md)。

| ファイル                | 対象                                                                                   |
| ----------------------- | -------------------------------------------------------------------------------------- |
| `auth.spec.ts`          | A2 マジックリンク（メールの `/auth/confirm` リンク）と 6 桁コードでのログイン / 共有リンク（`/posts/<id>`・`/dm/<id>`）→ ログイン → 元のページへ戻る（メールのリンクの `redirect_to` が `next` を運ぶ）/ リンクの先読み・ログイン CSRF 対策 / 削除・利用停止されたセッション（ループしない）/ ログアウト（失効要求が失敗しても端末からはログアウト）/ 退会（§5.7）と退会確認の初期フォーカス / コード入力中のアプリ再起動・送信間隔の制限でも続けられること |
| `feed.spec.ts`          | A3 ホームフィードの無限スクロール（2 ページ目以降・published_at DESC・重複なし）       |
| `post-detail.spec.ts`   | A4 コメント一覧（昇順）・投稿（API）・キャラの自動返信（Realtime）・Gate #1            |
| `profile.spec.ts`       | A5 「無料」「有料」タブ / A7 「DMする」→ DM 画面                                        |
| `paid.spec.ts`          | A6 有料投稿のぼかし + 鍵 + 「準備中」モーダルとトースト（フィード・投稿詳細・グリッド） |
| `dm.spec.ts`            | A8 DM 10 往復（毎回「入力中…」→ 返答）                                                 |
| `memory.spec.ts`        | A9 10 往復後の想起 / A10 メモリパネルでの追加・削除と、削除後に返答へ反映されないこと  |
| `pwa.spec.ts`           | A11 マニフェスト・アイコン・iOS 用 meta・Service Worker（ビルドごとの登録 URL・静的キャッシュの上限）・オフラインページ（「再読み込み」・通信復帰で開こうとした画面を読み込み直す）/ preconnect / D-3 圏外・通信停止時のエラー表示 |
| `prefetch.spec.ts`      | 遷移先のデータの先読み: リンクに触れた時点（遷移前）で遷移先のデータを取りに行き、遷移後は取り直さない（フィード → 投稿詳細・プロフィール / DM → プロフィール / DM 一覧の「おすすめ」→ 会話。会話の作成はしない） |
| `audit.spec.ts`         | A12 `audit_logs` の `chat.request` / `chat.response`（SQL で確認）                      |
| `rls.spec.ts`           | A13 2 アカウントで RLS（supabase-js）と API の所有者チェック（404）                     |
| `layout.spec.ts`        | A1 の代替（全画面で横はみ出しなし・入力欄 16px 以上）/ H2 投稿 UI が無いこと / ホームの再読み込み（Home タブ・ロゴ・プルリフレッシュ）とタブを切り替えて戻ったときのスクロール位置 / 空状態の見出しの折り返し / 端末の「戻る」とシート・モーダル / 絵文字の表示名 / LCP 画像 / `<main>` ランドマークと h1・シートの「閉じる」 |
| `dark-mode.spec.ts`     | ダークモード（端末設定に追従・白い面が残っていない）/ 読ませる文字のコントラスト 4.5:1 以上（ライト / ダーク） |
| `screenshots.spec.ts`   | 全画面のスクリーンショット（ライト / ダーク。`iphone` のみ）                            |

A1（実機）・A11 のスタンドアロン起動そのもの・A15（別担当者による環境構築）は実機 / 人手でしか確認できない。

## 前提

- ローカル Supabase が起動している（`pnpm db:start`。シード済み）
- Python API と Web（**本番ビルド**）が起動している。テストはサーバーを起動しない
- LLM はモック（`LLM_MODE=mock`）、Embedding は `hash`。A9 / A10 の返答内容の検証はモック LLM の決定的な返答を前提にしている
- 外部のプレースホルダー画像（picsum.photos / api.dicebear.com / placehold.co）はテスト内で SVG にスタブしている（オフラインでも動く）
- ログインメールのリンクのオリジンは `E2E_BASE_URL` ではなく Supabase Auth の Site URL（`infra/supabase/config.toml` の
  `site_url`。既定 `http://localhost:3000`）になる。`auth.spec.ts` はリンクのオリジンが `E2E_SITE_URL` であることを確かめたうえで、
  パス（`/auth/confirm?token_hash=...`）だけを `E2E_BASE_URL` の Web で開くので、Web を別のポートで動かしても A2 は通る

## 手順

```bash
# 0. 依存（ルートで 1 回）
pnpm install

# 1. Supabase（起動済みなら不要）
pnpm db:start

# 2. Python API（別ターミナル）
cd apps/api
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
SUPABASE_URL=http://127.0.0.1:54321 LLM_MODE=mock EMBEDDING_MODE=hash \
CORS_ALLOW_ORIGINS=http://localhost:3000 APP_ENV=local \
  uv run uvicorn app.main:app --port 8000

# 3. Web（本番ビルド。apps/web/.env.local は pnpm setup:env で作成済みのこと。別ターミナル）
cd apps/web
NEXT_PUBLIC_SITE_URL=http://localhost:3000 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm build
NEXT_PUBLIC_SITE_URL=http://localhost:3000 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm start -p 3000

# 4. E2E（別ターミナル）
pnpm --filter @everkano/web e2e                                  # 全部（iphone + android）
pnpm --filter @everkano/web e2e --project=iphone                 # iPhone 相当のみ
pnpm --filter @everkano/web e2e e2e/memory.spec.ts               # ファイル指定
pnpm --filter @everkano/web exec playwright show-report          # HTML レポート

# スクリーンショットを docs/acceptance/screenshots に更新する
cd apps/web
E2E_SCREENSHOTS_DIR=../../docs/acceptance/screenshots pnpm e2e --project=iphone e2e/screenshots.spec.ts
```

Chromium は Playwright のブラウザ（`pnpm --filter @everkano/web exec playwright install chromium`）を使う。
別の場所の Chromium を使う場合は `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/chrome` を指定する。

## 環境変数

| 変数                                                  | 既定                                                      | 説明                                                     |
| ----------------------------------------------------- | --------------------------------------------------------- | -------------------------------------------------------- |
| `E2E_BASE_URL`                                        | `http://localhost:3000`                                   | Web                                                      |
| `E2E_SITE_URL`                                        | `http://localhost:3000`                                   | Supabase Auth の Site URL（`infra/supabase/config.toml` の `site_url`）。ログインメールのリンクのオリジン |
| `E2E_API_URL`                                         | `http://localhost:8000`                                   | Python API                                               |
| `E2E_SUPABASE_URL`                                    | `http://127.0.0.1:54321`                                  | Supabase（Auth / REST）                                  |
| `MAILPIT_URL`                                         | `http://127.0.0.1:54324`                                  | ログインメールの受信箱                                   |
| `DATABASE_URL`                                        | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` | 検証用 SQL と後片付け                                    |
| `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY`     | `supabase status` から取得                                | テストユーザーの作成（管理 API）と supabase-js の検証    |
| `E2E_WORKERS`                                         | `3`                                                       | 並列数                                                   |
| `E2E_KEEP_USERS`                                      | 未設定                                                    | `1` でテストユーザーを削除しない（調査用）               |
| `E2E_SCREENSHOTS_DIR`                                 | `test-results/screenshots`                                | スクリーンショットの保存先                               |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE`                      | 未設定                                                    | Chromium の実行ファイル                                  |

## テストデータの扱い

- テストごとに新しいユーザーを作り（`e2e-<run id>-...@example.com`）、終わったら削除する。
  途中で落ちた場合も `global-teardown.ts` がその実行のユーザーをまとめて削除する。
  コメントを書いたユーザーは先にコメントを削除してから `auth.users` を削除する（ADR-0004）。
- ログイン画面の検証（`auth.spec.ts`）とスクリーンショット以外は、メールを送らずに管理 API の `generate_link` で
  発行したトークンをアプリの `/auth/confirm`（確認画面）に渡し、「ログインする」を押してログインする
  （Supabase Auth のメール送信レート制限を避けるため）。
- ホームの再読み込みのテスト（`layout.spec.ts`）は「今」公開の投稿を一時的に追加し、終了時に削除する。
- シードデータ（キャラクター・投稿・コメント）は読むだけ。コメントを書くテストは、並列実行でぶつからないよう
  プロジェクトごとに別の投稿を使う。
- `audit_logs` は削除しない（監査ログは消さない方針。テストユーザーの `user_id` の行が残る）。
