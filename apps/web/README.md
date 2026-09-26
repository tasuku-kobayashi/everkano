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
  auth/confirm/         マジックリンクの着地点（確認画面。GET ではログインしない）
  auth/confirm/verify/  確認画面の「ログインする」の送信先（POST・同一オリジンのみ → verifyOtp）
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
    dev/ui/             開発用 UI カタログ + Python API 疎通確認（page.dev.tsx: `next dev` のときだけ存在。本番ビルドに含まれない）
components/
  ui/                   汎用 UI（下記）
  auth/ account/ pwa/   基盤の画面部品
  feed/ post/ profile/ search/ dm/ memory/   各機能
lib/
  env.ts env.server.ts  環境変数の検証（env.ts は全画面のバンドルに入るため zod を使わない）
  supabase/             client（ブラウザ）/ server（RSC・Route Handler）/ middleware（セッション更新）
  api/                  Python API の型付きクライアント（client.ts）・SSE の逐次パーサー（sse.ts）・応答の正規化（normalize.ts）
  storage/              StorageAdapter（passthrough / bunny）+ サーバー専用の署名
  auth/                 アカウント取得・サインアウト・リダイレクト・エラー文言
  queries/keys.ts       TanStack Query のクエリキー
  query-client.ts       TanStack Query の既定設定（networkMode: always・再試行の判定は query-retry.ts）
  feed-refresh.ts       ホームフィードの再読み込み（Home タブ / ロゴの再タップ・プルリフレッシュ・復帰時）
  home-scroll.ts        ホームのスクロール位置（別のタブから Home タブで戻ったときに復元）
  format.ts             相対時刻・件数の日本語表記
  text.ts               絵文字を壊さない先頭文字・文字数
  navigation.ts         アプリ内の「戻る」+ 端末の「戻る」でシート・モーダルを閉じる履歴管理
                        （pushState / replaceState を包み、シートを閉じる history.go(-n) の着地まで Next.js の履歴の書き込みを保留する）
middleware.ts           セッション更新 + 未ログインは /login へ（判定は lib/auth/route-gate.ts）
public/                 manifest.json / sw.js / icons（生成物）/ favicon.ico
```

## 実装ルール（機能担当向け）

- **データ取得**: 読み取りは `getSupabaseBrowserClient()`（RLS 適用）で直接。`characters` は必ず
  `PUBLIC_CHARACTER_COLUMNS`（`@everkano/shared`）で列を指定する（`select('*')` は権限エラー）。
- **書き込み**: ユーザーが書くテキスト（DM・コメント・メモリ・会話作成）は必ず `api.*`（`lib/api`）経由。
  いいね（`likes`）と既読（RPC `mark_conversation_read`）のみ Supabase へ直接書いてよい。
- **エラー**: `api.*` は失敗時に `ApiError { status, code, message, retryAfterSeconds? }` を投げる。`message` は
  そのまま表示できる日本語。`useToast().error(getErrorMessage(error))` のように使う。401 / 403 account_deleted は
  React Query のグローバル onError がログイン画面へ遷移させる。supabase-js の `{ error }`（プレーンなオブジェクト）は
  `if (error) throw toAppError(error);` で ApiError にそろえる（通信失敗は network_error になり、表示と再試行の判定が
  Python API と同じになる）。
- **先読み**: 画面の `useQuery` は `xxxQueryOptions()`（`postQueryOptions` / `commentsQueryOptions` / `characterQueryOptions` など）を使い、
  リンクの `onPointerDown` から同じ設定で先読みする（`lib/queries/prefetch.ts` の `prefetchPostDetail` / `prefetchCharacterProfile`、
  DM は `lib/queries/dm.ts` の `prefetchDmConversation`）。先読みは読み取りだけにし、副作用のある API（会話の作成など）は呼ばない。
- **画像**: `next/image` は使わない。`<CdnImage>` / `<Avatar>`（内部で StorageAdapter を使用）を使う。
- **ヘッダー**: 各ページの先頭に `<AppHeader variant="logo|back|title" />` を置く（sticky・safe-area 込み）。
- **固定フッター**: `/posts/[postId]` と `/dm/[characterId]` はタブバーが消える。入力欄は
  `fixed bottom-0 inset-x-0 mx-auto max-w-[480px] pb-safe` で配置し、`useBottomBarHeight(高さ)` でトースト位置を合わせる。
- **色**: `bg-ig-bg` `text-ig-text` `text-ig-secondary` `border-ig-separator` などのトークンを使えばダークモードは自動。
  Instagram の青 `ig-blue`（#0095f6）と赤 `ig-red`（#ff3040）は **塗り・アイコン専用**（白地で 3.2:1 / 3.7:1 しかない）。
  エラー文言・送信失敗の案内・破壊的操作のラベル・テキストボタン・リンクなど **読ませる文字** は
  `text-ig-red-text` / `text-ig-blue-text`（ライト / ダークとも 4.5:1 以上）を使う。プライマリボタンの白文字 × 青の塗りは
  Instagram の見た目を優先した意図的な例外（ADR-0021）。
- **ランドマーク・見出し**: (main) 配下の本文は `MainShell` の `<main>` の中。ページで `<main>` を出さない。
  各画面に h1 を 1 つ（`AppHeader` の title。logo は非表示の「ホーム」、back + children は `heading` で指定）。
- **ボトムシート**: title 付きのシートには右上に「閉じる」が出る（スクリーンリーダーには Esc も背景タップも無いため）。
  最下段に「キャンセル」行を持つシートだけ `showClose={false}` にしてよい。
- **UI 文言は日本語**、コードの識別子は英語。エラー・案内の文は句点「。」で終え（Python API の message と同じ）、
  時間をおいた再試行は「しばらくしてから再度お試しください。」にそろえる。通信失敗（fetch の失敗）は API の停止でも
  起きるので、原因を端末の電波と決めつけない。既定の文言は `lib/api/errors.ts` の `API_ERROR_MESSAGES`（単体テストで検査）。
- **ホームのスクロール位置**: 別のタブからタブバーの Home タブで戻ると、前に読んでいた位置から表示する
  （`lib/home-scroll.ts`。ブラウザの「戻る」はブラウザが復元）。`app/(main)/loading.tsx` の `data-route-loading` は
  「まだフィードが無い」印なので外さない。
- **端末の「戻る」とシート**: `BottomSheet` / `Modal` は開くと同じ URL の履歴を 1 つ積む（`lib/navigation.ts`）。
  `window.history.pushState` / `replaceState` を直接呼ぶコードは書かない（呼ぶ場合も `lib/navigation.ts` のガードを通るので、
  シートを閉じる処理の着地まで保留されることを前提にする）。シートの操作で画面遷移するときは `onClose()` → `router.push()` の順でよい。
- **スケルトンの寸法**: 読み込み後の要素と同じ高さにする（違うと、読み込み完了でその下がずれ、スクロール中はスクロール位置も変わる）。
  `Skeleton shape="text"` の既定の高さ（`h-3`）は、`className` で `h-*` / `size-*` を指定すれば付かない。
- **ホスト要素の直下に RSC の `children` を置かない**: クライアントコンポーネントで、レイアウトから渡された `children` を `<main>` などの
  直下に置くと、本番のハイドレーション中に中断・再開したとき React #418 になる（`components/ui/main-shell.tsx` の `RouteContent` 参照）。
  関数コンポーネントで挟む。
- `/dev/ui` に全 UI 部品の見本がある（`next dev` のときだけ。開発専用のページは `page.dev.tsx` と名付ける）。

### キャラクターエンジン v1.0 の画面の約束

- **「AIキャラクター」バッジ（E3）**: キャラの名前・ハンドルを出す所には必ず `<AiBadge />`（`components/ui/ai-badge.tsx`）を
  添える（フィード・投稿詳細・コメント・プロフィール・検索・DM 一覧・DM ヘッダー・会話の先頭。ストーリーズは `variant="compact"`）。
  `aria-label` で読み上げを上書きするリンクは、ラベルにも「AIキャラクター」（`AI_BADGE_LABEL`）を入れる。
- **DM の送信は `api.streamChat`**（`POST /chat/stream`・SSE）。`delta` を受信中の吹き出しに追記し、`done` の保存済みメッセージに
  同じ key で置き換える（`components/dm/stream-state.ts`・`use-send-message.ts`）。ストリーミングを使えない環境・
  `/chat/stream` の無い API では `POST /chat` にフォールバックする。タイムアウトは接続から `done` まで 45 秒。
- **E6 相談窓口のカード**: 安全対応をした返答（`messages.safety_triggered = true`。受信中は `replace` の `reason: "safety"`）の
  下に出し続ける（閉じる操作なし）。印はサーバーが返答の行に残すので、履歴の読み込み・別の端末でも同じ返答の下に出る。
  窓口の一覧は `GET /safety/resources`（`lib/queries/safety.ts` の `useSafetyResources`。返答を受け取った端末では
  `ChatResponse.safety.resources` を先にキャッシュへ入れる）。一覧を読み込めないときも 119 番の案内と、返答の本文に入っている窓口は出る。
- **自発メッセージ（is_proactive）**は通常の吹き出しと同じ表示（返信を急かす特別な表示をしない）。設定は /me と DM の「i」。
- **好感度の数値・関係の段階はどこにも表示しない（A11）**。DM ヘッダーの 2 行目は `character_states.status_label`（無ければ「アクティブ」）。
- 「〇〇があなたのことを覚えました」は Realtime（`memories` の INSERT）で出す（記憶は返答の後に非同期で作られる）。

## 認証フロー

1. `/login` でメールアドレス → `signInWithOtp`（`emailRedirectTo = SITE_URL/auth/callback?next=<ログイン前に開こうとしていたページ>`。
   `lib/auth/redirect.ts` の `emailRedirectUrl`）
2. メール（`infra/supabase/templates/magic_link.html`）に `/auth/confirm?token_hash=…&type=email&redirect_to=<emailRedirectTo>` のリンクと 6 桁コード。
   `/auth/confirm` は `redirect_to` の中の `next` を取り出し（`lib/auth/confirm-params.ts`。同一オリジンの相対パスだけ）、ログイン後にそのページへ戻す
3. リンク → `/auth/confirm` は確認画面（「everkano にログインしますか？」）を表示するだけ。「ログインする」で
   `POST /auth/confirm/verify`（同一オリジンのフォーム送信のみ受け付ける）が `verifyOtp` → Cookie 発行 → `next` へ。
   JS が動いていれば fetch（`Accept: application/json`）で送り、応答の `{ location }` へ `location.replace()` で移る
   （`lib/auth/confirm-submit.ts`）。確認画面（トークン入りの URL）を履歴に残さず、ログイン後の「戻る」で使用済みの
   リンクの確認画面へ戻らないようにするため。JS が無い場合は通常のフォーム送信（303）のまま動く（その場合だけ確認画面が履歴に残る）。
   GET でログインしないのは、メールのセキュリティスキャナーの先読みでトークンが消費されるのと、他人のリンクを
   踏まされて黙ってその人のアカウントに切り替わる（ログイン CSRF）のを防ぐため。別のアカウントでログイン中なら
   確認画面で切り替わることを表示する。
   コード → `/login` 画面で `verifyOtp({ email, token, type: "email" })`（iOS のホーム画面 PWA は Safari と Cookie を共有しないため）。
   コード入力待ちの状態（メールアドレス・送信時刻。コードは保存しない）は localStorage に 15 分（= otp_expiry）保存し
   （`lib/auth/pending-login.ts`）、メールアプリへ切り替えている間に iOS がアプリを再起動しても入力画面から続けられる。
   送信間隔の制限（`over_email_send_rate_limit`）に当たった場合も、送信済みのコードの入力画面へ進める
4. どちらも `profiles.deleted_at` を確認し、退会済みならサインアウトして `/login?error=withdrawn`
5. `middleware.ts` が全リクエストでセッションを更新し、未ログインは `/login?next=…` へ
6. ログイン後に使えなくなったセッション（ユーザー削除・利用停止 = Supabase Auth の ban・退会）は AccountGuard が
   **この端末のセッションを破棄してから** `/login?error=session|banned|withdrawn` へ送る。middleware はこれらの
   `?error=` のときはログイン済みでも `/` へ戻さない（Cookie の JWT は期限まで有効に見えるため、戻すと
   `/` ⇄ `/login` のリダイレクトが無限に続く）
7. ログアウト（`signOutAndRedirect`）: supabase-js はサーバーへの失効要求（`/auth/v1/logout`）が通信失敗でも
   この端末のセッションを消す。その場合は失敗と表示せずにログイン画面へ進む（この端末からはログアウト済み）。
   セッションが残っているときだけ「ログアウトできませんでした」と表示する。退会（`scope: "global"`）の失敗は呼び出し元へ返す

## PWA

- `public/manifest.json`（standalone / アイコン 192・512・maskable）と `public/sw.js`（手書き）。
- SW: ページ遷移はネットワーク優先（失敗時 `/offline`）、`/_next/static` はキャッシュ優先（200 件を超えたら古い順に削除）、
  `/icons`・`manifest.json` はキャッシュを返しつつ裏で取り直す。Supabase / API / CDN（他オリジン）と `/auth/*`・`/media/*` は
  一切キャッシュしない。
- SW は `/sw.js?v=<ビルドID>` で登録する（`NEXT_PUBLIC_BUILD_ID`。next.config.ts が Vercel のデプロイ ID 等から導出）。
  デプロイのたびに新しい SW が入り、`/offline` を取り直して前のビルドのオフライン用キャッシュを消す。
  キャッシュの構成（名前・方針）を変えたときだけ `sw.js` の `CACHE_POLICY` を上げる。
- オフライン時、SW は開こうとした画面の URL のまま `/offline` の内容を返す。「再読み込み」と通信の復帰（`online` イベント）は
  その URL を読み込み直す（`/offline` を直接開いた場合だけホームへ）。
- アイコンは `pnpm --filter @everkano/web icons` で生成（依存なしの PNG エンコーダー）。
- ワードマーク（`components/ui/wordmark.tsx`）は Grand Hotel（SIL OFL 1.1）のグリフを opentype.js で輪郭化した
  インライン SVG。Web フォントは読み込まない。
