# @everkano/web

everkano の Web フロントエンド（Next.js 15 App Router / React 19 / TypeScript strict / Tailwind CSS v4 / TanStack Query v5）。
スマートフォン専用の PWA（H1）で、Instagram モバイルアプリの見た目を再現する（仕様 §4）。

## コマンド

```bash
pnpm --filter @everkano/web dev         # http://localhost:3000
pnpm --filter @everkano/web build
pnpm --filter @everkano/web lint        # ESLint（flat config / eslint-config-next）
pnpm --filter @everkano/web typecheck   # next typegen（ルート型生成）+ tsc --noEmit
pnpm --filter @everkano/web test        # vitest（**/*.test.ts）
pnpm --filter @everkano/web e2e         # Playwright（サーバーを起動してから。e2e/README.md）
pnpm --filter @everkano/web icons       # public/icons を再生成（scripts/generate-icons.mjs）
```

### 複数の dev サーバーを並行起動する

`next.config.ts` の `distDir` は `NEXT_DIST_DIR` で切り替えられる（未設定なら `.next`）。
同じ作業ツリーで複数の dev サーバーを立てる場合は、ポートと distDir を分ける。

```bash
cd apps/web
NEXT_DIST_DIR=.next-browse pnpm exec next dev -p 3001
NEXT_DIST_DIR=.next-dm     pnpm exec next dev -p 3002
```

`.next-*` は `.gitignore` 済み。既定以外の distDir で起動すると Next.js が `tsconfig.json` の `include` に
`.next-xxx/types/**/*.ts` を自動追加する（整形も変わる）ので、その変更はコミットしないこと（`git checkout apps/web/tsconfig.json`）。

## 環境変数

`apps/web/.env.local`（gitignore 済み）に置く。キーの一覧と意味はリポジトリ直下の `.env.example`。
値は `lib/env.ts`（公開値 `NEXT_PUBLIC_*`）と `lib/env.server.ts`（サーバー専用 `BUNNY_*`）で zod 検証し、
不正ならルートレイアウトの描画時に分かりやすいエラーで停止する。

| 変数 | 用途 |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase（ローカル: `supabase status --workdir infra -o json` の `ANON_KEY`） |
| `NEXT_PUBLIC_API_BASE_URL` | Python API（FastAPI） |
| `NEXT_PUBLIC_SITE_URL` | マジックリンクの戻り先（未設定ならブラウザの origin） |
| `NEXT_PUBLIC_STORAGE_DRIVER` | `passthrough`（DB の URL をそのまま）/ `bunny`（オブジェクトキー → Bunny CDN） |
| `NEXT_PUBLIC_CDN_BASE_URL` | Bunny Pull Zone の URL（bunny 時必須） |
| `BUNNY_TOKEN_AUTH_KEY` / `BUNNY_TOKEN_TTL_SECONDS` | サーバー専用。設定時は `/media/*` が署名 URL へ 302 |
| `NEXT_PUBLIC_ENABLE_SW` | `1` なら開発中も Service Worker を登録（既定は本番のみ） |

## ディレクトリ

```
app/
  layout.tsx            ルート（lang=ja・metadata・viewport・Providers）
  providers.tsx         React Query / Toast / SW 登録 / 戻る履歴の追跡
  globals.css           デザイントークン（--ig-*）・ユーティリティ
  (auth)/login/         ログイン（メール → マジックリンク / 6 桁コード）
  auth/confirm/         マジックリンクの着地点（token_hash → verifyOtp）
  auth/callback/        PKCE コード交換（exchangeCodeForSession）
  media/[...key]/       Bunny Token Authentication の署名 URL へ 302（サーバー専用キー）
  offline/              Service Worker のオフラインページ
  (main)/               ログイン後の画面（MainShell = 480px 幅 + タブバー + AccountGuard）
    page.tsx            ホーム（B-1）
    posts/[postId]/     投稿詳細（B-2/B-3）※タブバー非表示
    c/[handle]/         キャラプロフィール（B-4）
    search/             検索（B-6）
    dm/                 DM 一覧（C-1）
    dm/[characterId]/   DM 会話（C-2）※タブバー非表示
    me/                 自分のプロフィール（§5.7）
    dev/ui/             開発用 UI カタログ（本番は 404）+ Python API 疎通確認
components/
  ui/                   汎用 UI（下記）
  auth/ account/ pwa/   基盤の画面部品
  feed/ post/ profile/ search/ dm/ memory/   各機能
lib/
  env.ts env.server.ts  環境変数の検証
  supabase/             client（ブラウザ）/ server（RSC・Route Handler）/ middleware（セッション更新）
  api/                  Python API の型付きクライアント
  storage/              StorageAdapter（passthrough / bunny）+ サーバー専用の署名
  auth/                 アカウント取得・サインアウト・リダイレクト・エラー文言
  queries/keys.ts       TanStack Query のクエリキー
  format.ts             相対時刻・件数の日本語表記
  navigation.ts         アプリ内の「戻る」
middleware.ts           セッション更新 + 未ログインは /login へ
public/                 manifest.json / sw.js / icons（生成物）/ favicon.ico
```

## 実装ルール（機能担当向け）

- **データ取得**: 読み取りは `getSupabaseBrowserClient()`（RLS 適用）で直接。`characters` は必ず
  `PUBLIC_CHARACTER_COLUMNS`（`@everkano/shared`）で列を指定する（`select('*')` は権限エラー）。
- **書き込み**: ユーザーが書くテキスト（DM・コメント・メモリ・会話作成）は必ず `api.*`（`lib/api`）経由。
  いいね（`likes`）と既読（RPC `mark_conversation_read`）のみ Supabase へ直接書いてよい。
- **エラー**: `api.*` は失敗時に `ApiError { status, code, message, retryAfterSeconds? }` を投げる。`message` は
  そのまま表示できる日本語。`useToast().error(getErrorMessage(error))` のように使う。401 / 403 account_deleted は
  React Query のグローバル onError がログイン画面へ遷移させる。
- **画像**: `next/image` は使わない。`<CdnImage>` / `<Avatar>`（内部で StorageAdapter を使用）を使う。
- **ヘッダー**: 各ページの先頭に `<AppHeader variant="logo|back|title" />` を置く（sticky・safe-area 込み）。
- **固定フッター**: `/posts/[postId]` と `/dm/[characterId]` はタブバーが消える。入力欄は
  `fixed bottom-0 inset-x-0 mx-auto max-w-[480px] pb-safe` で配置し、`useBottomBarHeight(高さ)` でトースト位置を合わせる。
- **色**: `bg-ig-bg` `text-ig-text` `text-ig-secondary` `border-ig-separator` などのトークンを使えばダークモードは自動。
- **UI 文言は日本語**、コードの識別子は英語。
- `/dev/ui` に全 UI 部品の見本がある。

## 認証フロー

1. `/login` でメールアドレス → `signInWithOtp`（`emailRedirectTo = SITE_URL/auth/callback`）
2. メール（`infra/supabase/templates/magic_link.html`）に `/auth/confirm?token_hash=…` のリンクと 6 桁コード
3. リンク → `/auth/confirm` が `verifyOtp` → Cookie 発行 → `next` へ
   コード → `/login` 画面で `verifyOtp({ email, token, type: "email" })`（iOS のホーム画面 PWA は Safari と Cookie を共有しないため）
4. どちらも `profiles.deleted_at` を確認し、退会済みならサインアウトして `/login?error=withdrawn`
5. `middleware.ts` が全リクエストでセッションを更新し、未ログインは `/login?next=…` へ

## PWA

- `public/manifest.json`（standalone / アイコン 192・512・maskable）と `public/sw.js`（手書き）。
- SW: ページ遷移はネットワーク優先（失敗時 `/offline`）、`/_next/static`・`/icons` はキャッシュ優先。
  Supabase / API / CDN（他オリジン）と `/auth/*`・`/media/*` は一切キャッシュしない。キャッシュ方針を変えたら
  `sw.js` の `VERSION` を上げる。
- アイコンは `pnpm --filter @everkano/web icons` で生成（依存なしの PNG エンコーダー）。
- ワードマーク（`components/ui/wordmark.tsx`）は Grand Hotel（SIL OFL 1.1）のグリフを opentype.js で輪郭化した
  インライン SVG。Web フォントは読み込まない。
