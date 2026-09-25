# 受け入れ基準（§13）E2E 検証結果

- 実施日: 2026-09-25（JST 16:37 実行、所要 1 分 53 秒）
- 対象: 作業ツリー（`main` ブランチ + 未コミットの修正を含む）、Web は **本番ビルド**（`next build` → `next start`）
- テストコード: [`apps/web/e2e/`](../../apps/web/e2e/)（手順は [`apps/web/e2e/README.md`](../../apps/web/e2e/README.md)）
- 結果: **53 passed / 0 failed / 1 skipped**（skip はスクリーンショット撮影の android 分。撮影は iphone のみ）
- 生の出力: [`raw/`](raw/)（Playwright の出力・テスト一覧 JSON・会話ログ・audit_logs のサンプル・その他のゲート）
- スクリーンショット: [`screenshots/`](screenshots/)（22 画面 × ライト / ダーク、iPhone 相当 1170×2532、合計 12.1 MB）

## 環境

| 項目       | 値                                                                                                         |
| ---------- | ---------------------------------------------------------------------------------------------------------- |
| OS         | Linux 6.18（サンドボックス。外部インターネットは原則遮断）                                                 |
| Node / pnpm | Node 22.22.2 / pnpm 10.33.0                                                                               |
| ブラウザ   | Playwright 1.56.1 + Chromium 141.0.7390.37（`/opt/pw-browsers/chromium-1194`）                             |
| 端末（エミュレーション） | `iphone`: 390×844 @3x・タッチ・iOS Safari の UA / `android`: 412×915 @2.625x・タッチ・Pixel 7 の UA。どちらも `ja-JP`・`Asia/Tokyo` |
| Web        | Next.js 15.5.26 本番ビルド（`http://localhost:3000`）。Service Worker は本番ビルドで登録される            |
| API        | FastAPI（`http://localhost:8000`）、`LLM_MODE=mock`・`EMBEDDING_MODE=hash`・`APP_ENV=local`                  |
| DB / Auth  | ローカル Supabase（CLI 2.117.0、Postgres 17.6、シード: キャラ 10 体 / 投稿 50 件）、メールは Mailpit          |

## 実行したコマンド

```bash
# API
cd apps/api
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres SUPABASE_URL=http://127.0.0.1:54321 \
LLM_MODE=mock EMBEDDING_MODE=hash CORS_ALLOW_ORIGINS=http://localhost:3000 APP_ENV=local \
  uv run uvicorn app.main:app --port 8000

# Web（本番ビルド）
cd apps/web
NEXT_PUBLIC_SITE_URL=http://localhost:3000 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm build
NEXT_PUBLIC_SITE_URL=http://localhost:3000 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm start -p 3000

# E2E（全テスト + スクリーンショットを docs に保存）
cd apps/web
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium-1194/chrome-linux/chrome \
E2E_SCREENSHOTS_DIR=../../docs/acceptance/screenshots pnpm e2e

# その他のゲート（リポジトリのルート）
bash scripts/test-db.sh                 # RLS / 権限 / トリガーの pgTAP
(cd apps/api && uv run pytest -q)       # API の単体・統合テスト
bash scripts/check-secrets.sh           # A14
bash scripts/check-scope.sh             # A16
pnpm --filter @everkano/web lint typecheck test
```

## 受け入れ基準ごとの結果

| #   | 基準                                                   | 結果                        | 根拠（テスト / 確認内容）                                                                                                                                                                                                                                                                         |
| --- | ------------------------------------------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1  | スマホ実機でレイアウト崩れがない                       | **代替確認のみ**（実機は未） | `layout.spec.ts`: 390px / 412px で全 10 画面 + 有料タブ + 長い単語を含む DM に横はみ出しなし、入力欄はすべて 16px 以上（iOS の自動ズーム防止）、タブバーは画面幅内。スクリーンショット 44 枚を目視確認。**実機（iOS Safari / Android Chrome）での撮影は未実施** |
| A2  | マジックリンクでログインできる                         | ✅ pass                     | `auth.spec.ts`: 新規メールアドレスで `/login` → Mailpit に届いたメールの `/auth/confirm?token_hash=…` リンクでログイン、別の新規アドレスで 6 桁コードでログイン。どちらも `profiles` 自動作成を SQL で確認。誤ったコードはエラー、使用済みリンクは `/login?error=link`、未ログインは `/login?next=` へ |
| A3  | ホームフィードが無限スクロールで表示される             | ✅ pass                     | `feed.spec.ts`: 1 ページ目 10 件 → スクロールで 2 ページ目以降 → 「すべて確認済みです」まで。表示順が DB の `published_at DESC, id DESC` と完全一致・重複なし。画像は画面幅の正方形。詳細から戻るとスクロール位置が保たれる                                                              |
| A4  | 個別投稿でコメント一覧と投稿欄が動作する               | ✅ pass                     | `post-detail.spec.ts`: コメントが時系列昇順（返信はスレッドの下）で DB と一致、入力欄から投稿（`POST /comments` 201・DB に保存）、**REST の再取得を遮断した状態で** 投稿者キャラの自動返信が Realtime で表示、自分のコメント削除（返信も消える）。Gate #1 で拒否（422）は保存されずトースト |
| A5  | プロフィールで「無料」「有料」タブが切り替わる         | ✅ pass                     | `profile.spec.ts`: 投稿数・フォロワー・自己紹介・「DMする」、無料タブ = `is_paid=false` の件数、有料タブ = `is_paid=true` の件数、3 列グリッド、`aria-selected` の切替。投稿のアバター → プロフィール                                                                                      |
| A6  | 有料投稿がぼかし＋鍵で表示され、タップで「準備中」モーダル | ✅ pass                 | `paid.spec.ts`: フィード・投稿詳細・プロフィールの有料タブの 3 か所で、CSS ぼかし（`filter: blur(≥8px)`）+ 鍵 + 「有料コンテンツ」/ 価格、タップでモーダル（価格は DB と一致）→「購入する（準備中）」でトースト「課金機能は現在準備中です」、画面遷移なし。本体画像（`post_private_assets`）の URL は通信にも DOM にも出ない |
| A7  | キャラプロフィールから「DMする」で DM 画面に遷移する   | ✅ pass                     | `profile.spec.ts`: 「DMする」→ `/dm/<characterId>`、ヘッダーにキャラ名、初回の挨拶メッセージ（DB と一致）、DM 一覧にも表示                                                                                                                                                                    |
| A8  | DM で送信するとキャラが文脈に合った返答を返す          | ✅ pass（モック LLM）       | `dm.spec.ts`: 10 往復すべてで「入力中…」表示 → 返答表示、挨拶には「こんばんは」、おやすみには「おやすみ」で返す、10 種以上の異なる返答、DB に挨拶 + 20 件が交互に保存、再読み込みしても表示、吹き出しは自分 = 右 / キャラ = 左。会話ログ: [`raw/e2e-dm-memory-transcripts.txt`](raw/e2e-dm-memory-transcripts.txt)。**本物の LLM の返答品質は staging で確認すること** |
| A9  | 以前話した内容を踏まえた返答が返る（メモリ）           | ✅ pass（モック LLM）       | `memory.spec.ts`: 1 往復目「来週、大阪に出張するんだ」→ 記憶が作られ「美咲があなたのことを覚えました」→ 別の話題 9 往復 → 「大阪でおすすめの場所ある？」への返答が `memories_used` にその記憶を含み「…大阪に出張するって言ってたけど、あれからどう？」                                    |
| A10 | メモリパネルで記憶の追加・削除、削除後は返答に反映されない | ✅ pass（モック LLM）   | `memory.spec.ts`: パネルで追加（優先度: 高・二人だけの秘密、DB で `is_user_edited=true`・`secret`）→ 返答がその記憶に触れる → パネルで削除（確認ダイアログ → 204・DB から消える）→ 同じ質問への返答は触れず `memories_used` にも含まれない。再読み込み後のパネルにも出ない                 |
| A11 | PWA としてホーム画面に追加でき、スタンドアロン起動する | **前提のみ確認**（実機は未） | `pwa.spec.ts`: manifest（`display: standalone`・`start_url`・`scope`・`lang`・192/512/maskable アイコンが実在し PNG の寸法も一致）、**Chromium がマニフェストを検出**（`Page.getAppManifest`）、`<head>` に manifest / apple-touch-icon（180×180）/ `apple-mobile-web-app-title` / `viewport-fit=cover`、Service Worker が登録され再読み込み後にページを制御、キャッシュは同一オリジンのみ、オフラインで `/offline`。**ホーム画面への追加とスタンドアロン起動は実機で確認すること** |
| A12 | `audit_logs` にチャットのリクエスト／レスポンス        | ✅ pass                     | `audit.spec.ts`: 画面から 2 往復 → SQL で `chat.request` 2 件・`chat.response` 2 件・`conversation.create` 1 件。`request_id` で対になり、`message`・`reply`・`message_id`・`user_message_id`・`model`・`latency_ms`・`memories_used` が送受信内容と一致。サンプル: [`raw/audit-logs-sample.json`](raw/audit-logs-sample.json) |
| A13 | RLS により他ユーザーの会話・メモリが取得できない       | ✅ pass                     | `rls.spec.ts`（2 アカウント）: B のトークンの supabase-js で A の conversations / messages / memories / profiles / `list_dm_threads` は 0 件、messages への直接 INSERT は拒否、memories の DELETE は 0 件、他人の会話の既読化は無効、anon も 0 件（A 本人は読める = ポジティブコントロール）。B のトークンの API: `/chat`・`PATCH/DELETE /memories/{A の id}` は 404、一覧に A の記憶なし、トークンなしは 401。画面でも B の DM・メモリパネルに A の内容は出ない。加えて `scripts/test-db.sh`（pgTAP 173 件）成功 |
| A14 | シークレットがコードに含まれていない                   | ✅ pass                     | `bash scripts/check-secrets.sh` → OK（[`raw/check-secrets.txt`](raw/check-secrets.txt)）                                                                                                                                                                                                      |
| A15 | README の手順でゼロから環境構築できる                   | 対象外（人手）              | 別担当者による確認が必要                                                                                                                                                                                                                                                                         |
| A16 | 決済・画像生成・TTS のコードが存在しない               | ✅ pass                     | `bash scripts/check-scope.sh` → OK（許可行 2: 仕様のモーダル文言の説明コメントと、E2E の「ファイル選択が無い」ことを確かめるセレクタ）。`layout.spec.ts`（H2）: 下部タブは 4 つ（投稿タブなし）、全画面にファイル選択・投稿作成ボタン / リンクなし、`/new` 等は存在せず、API に `POST /posts` なし |
| —   | ダークモード（§4.1）                                   | ✅ pass                     | `dark-mode.spec.ts`: 端末設定がダークのとき全 9 画面 + メモリパネルで背景 `#000`・文字 `#f5f5f5`・白い大きな面が残っていない（検査関数はライトで白い面を検出できることを確認済み）                                                                                                        |

## spec ファイルごとの結果（iphone / android）

| spec                  | テスト数 | iphone | android |
| --------------------- | -------- | ------ | ------- |
| `auth.spec.ts`        | 3        | pass   | pass    |
| `feed.spec.ts`        | 2        | pass   | pass    |
| `post-detail.spec.ts` | 2        | pass   | pass    |
| `profile.spec.ts`     | 3        | pass   | pass    |
| `paid.spec.ts`        | 3        | pass   | pass    |
| `dm.spec.ts`          | 1        | pass   | pass    |
| `memory.spec.ts`      | 2        | pass   | pass    |
| `pwa.spec.ts`         | 3        | pass   | pass    |
| `audit.spec.ts`       | 1        | pass   | pass    |
| `rls.spec.ts`         | 1        | pass   | pass    |
| `layout.spec.ts`      | 2        | pass   | pass    |
| `dark-mode.spec.ts`   | 2        | pass   | pass    |
| `screenshots.spec.ts` | 1        | pass   | skip    |

同じ構成で全体を 5 回実行した。1 回目は `layout.spec.ts`（H2）の待ち方の不備で 1 件失敗（テスト側を修正）、2〜5 回目はすべて成功（4・5 回目は最終版のテストコード。上の結果は 4 回目）。

## その他のゲート

| コマンド                                            | 結果                                              |
| --------------------------------------------------- | ------------------------------------------------- |
| `bash scripts/test-db.sh`                           | 9 ファイル / 173 tests すべて成功（[`raw/test-db.txt`](raw/test-db.txt)） |
| `cd apps/api && uv run pytest -q`                   | 233 passed（[`raw/api-pytest.txt`](raw/api-pytest.txt)）                  |
| `bash scripts/check-secrets.sh`                     | OK（[`raw/check-secrets.txt`](raw/check-secrets.txt)）                    |
| `bash scripts/check-scope.sh`                       | OK（[`raw/check-scope.txt`](raw/check-scope.txt)）                        |
| `pnpm --filter @everkano/web lint typecheck test`   | lint / typecheck エラーなし、vitest 163 passed（[`raw/web-quality-gates.txt`](raw/web-quality-gates.txt)） |

## 今回の検証で見つけて修正した不具合

1. **動的ページでマニフェストがブラウザに検出されない（A11）** — Next.js 15.2 以降のメタデータのストリーミングにより、
   `/login`・`/`・`/posts/*` などの動的ページでは `<link rel="manifest">`・apple-touch-icon などが `<body>` 側に出力され、
   Chromium の `Page.getAppManifest` がマニフェストを返さなかった（静的な `/offline` では検出される）。この状態では
   Android でホーム画面に追加してもスタンドアロン起動しない。`apps/web/next.config.ts` に `htmlLimitedBots: /.*/` を設定し、
   すべての UA でメタデータを `<head>` に出力するよう修正。`pwa.spec.ts` で回帰を検出する。
2. **メモリパネルで記憶を追加（またはフォームをキャンセル）すると Esc でシートを閉じられなくなる** — 追加フォームを閉じると
   フォーカスしていたボタンが消え、フォーカスがシートの外（body）に落ちていた。`components/memory/memory-panel.tsx` で
   フォームを閉じたら「覚えてほしいことを追加」ボタンへフォーカスを戻すよう修正（削除時は既に対処済みだった）。

## 既知の制約・未解決

- **端末はエミュレーション**: Chromium に iPhone / Pixel の画面サイズ・DPR・UA・タッチを設定したもので、WebKit（iOS Safari）
  そのものではない。safe-area（ノッチ・ホームインジケーター）は 0 として描画される。A1 / A11 は実機での確認が別途必要。
- **外部画像はスタブ**: シードの画像 URL（picsum.photos / api.dicebear.com）はこの環境から到達できないため、テスト内で
  URL から色を決めた SVG に差し替えている。スクリーンショットの画像はダミー。
- **LLM はモック**: A8〜A10 の返答内容の検証はモック LLM（ペルソナの口調例と検索された記憶から決定的に組み立てる）が前提。
  本物の LLM（`LLM_MODE=live`）の文脈理解・口調は staging で確認すること。
- **Service Worker**: PWA のテスト以外では Service Worker をブロックして実行している（キャッシュがテストの前提を変えないため）。
- **本番ビルドでまれに React のハイドレーションエラー #418（未解決・動作への影響なし）**: 動的ルート（`/dm/[characterId]`・`/c/[handle]`）を
  フルロードしたとき、CPU 負荷が高い状況（E2E の並列実行など）で数十〜百回に 1 回程度 `Minified React error #418` がコンソールに出る。
  React がクライアントで描画し直すため表示は正常で、テストも成功する。静的ルート（`/`・`/dm`）では発生しない。
  追加調査で、`loading.tsx` をすべて外しても `htmlLimitedBots` を外しても再現することを確認した（どちらも原因ではない）。
  ストリーミング SSR とハイドレーションのタイミングに依存しており、原因は未特定（Next.js / React の更新時に再確認する）。
- **テストデータ**: テストユーザーはテストごとに作成・削除する（`audit_logs` の行は監査ログとして残る）。
  ログイン画面の検証以外は、Supabase Auth のメール送信レート制限を避けるため、管理 API の `generate_link` のトークンを
  アプリの `/auth/confirm` に渡してログインしている（メールのリンクと同じルート）。

## スクリーンショット一覧（iphone、`<番号>-<画面>-<light|dark>.png`）

| #   | 画面                                    | #   | 画面                                   |
| --- | --------------------------------------- | --- | -------------------------------------- |
| 01  | ログイン（メールアドレス入力）          | 12  | 検索（発見グリッド）                   |
| 02  | ログイン（確認コード入力）              | 13  | 検索結果                               |
| 03  | ホームフィード（ストーリーズ + 投稿）   | 14  | DM 一覧                                |
| 04  | フィードの有料投稿（ぼかし + 鍵）       | 15  | DM 会話                                |
| 05  | 有料投稿のロックモーダル                | 16  | DM 会話（入力中インジケーター）        |
| 06  | 「課金機能は現在準備中です」トースト    | 17  | メモリパネル                           |
| 07  | 投稿詳細                                | 18  | メモリパネル（追加フォーム）           |
| 08  | 投稿詳細のコメント（自分 + キャラの返信） | 19  | 記憶の削除確認                         |
| 09  | 投稿詳細（有料）                        | 20  | マイページ                             |
| 10  | キャラプロフィール（無料タブ）          | 21  | オフライン                             |
| 11  | キャラプロフィール（有料タブ）          | 22  | 存在しない投稿                         |
