# 受け入れ基準（A1〜A16）検証結果レポート

仕様書 §13 の受け入れ基準と §16 の提出物について、2026-09-26 時点の検証結果をまとめる（提出物 7）。数値は納品前の検査の指摘を修正した後の最終の再実行
（2026-09-26 0 時台 JST）の結果。検査の指摘と対応の一覧は [inspection-report.md](inspection-report.md)。

- 対象: リポジトリの作業ツリー（Web は本番ビルド）。LLM は `LLM_MODE=mock`、埋め込みは `EMBEDDING_MODE=hash`。
- 環境: ローカル Supabase（CLI 2.117.0 / Postgres 17.6、シード: キャラ 10 体・投稿 50 件）、Playwright 1.56.1 + Chromium 141 による
  スマホのエミュレーション（`iphone` 390×844 @3x / `android` 412×915 @2.625x、`ja-JP`・`Asia/Tokyo`）。
- E2E の詳細: [e2e-results.md](e2e-results.md)、生の出力: [raw/](raw/)、画面: [screenshots/](screenshots/)（22 画面 × ライト / ダーク）。
- **結論**: 自動で確認できる 13 項目（A2〜A10・A12〜A14・A16）は合格。**A1 と A11 は実機での確認、A15 は別担当者による実施が残っている**。
  A8〜A10 はモック LLM での合格で、**本物の LLM（staging）での確認も残っている**。

## 判定の一覧

| #   | 基準（仕様書 §13）                                               | 判定                          | 検証方法                                                                                                                                                                                                                                                                                                  | 根拠                                                                                                                  |
| --- | ---------------------------------------------------------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| A1  | スマホ実機（iOS Safari / Android Chrome）でレイアウト崩れがない  | **一部（実機は未実施）**      | Chromium のエミュレーション（390px / 412px）で、10 画面 + 有料タブ + 120 文字の単語を含む DM に横方向のはみ出しが無いこと、表示中の入力欄がすべて 16px 以上（iOS の自動ズーム防止）、タブバーが画面幅に収まることを検査。スクリーンショット 44 枚を目視確認 | `apps/web/e2e/layout.spec.ts`（iphone / android 合格）、[screenshots/](screenshots/)、[raw/e2e-playwright.txt](raw/e2e-playwright.txt)。**実機のスクリーンショットは無い** → [実機チェックリスト](#a1a11-実機チェックリスト) |
| A2  | マジックリンクでログインできる                                   | **合格**                      | 実際の `/login` 画面から新規メールアドレスで: (1) Mailpit に届いたメールの `/auth/confirm?token_hash=...&type=email` リンクを開き、確認画面の「ログインする」を押す（開いただけではログインせずトークンも消費しない。[ADR-0026](../adr/0026-magic-link-confirm-page.md)）、(2) メールの 6 桁コードを入力。どちらも `profiles` の自動作成を SQL で確認。誤ったコードはエラー、使用済みリンクは `/login?error=link`、未ログインは `/login?next=`、ログアウトも確認。共有リンク（`/posts/<id>`・`/dm/<id>`）から未ログインで開いた場合も、メールのリンク・6 桁コードのどちらでログインしても元のページへ戻る | `apps/web/e2e/auth.spec.ts`（5 テスト × 2 端末）                                                                      |
| A3  | ホームフィードが無限スクロールで表示される                       | **合格**                      | 1 ページ目 10 件 → スクロールで 20 件以上 → 「すべて確認済みです」まで。表示順が DB の `published_at DESC, id DESC` と完全一致・重複なし。画像は画面幅の正方形。詳細から戻るとスクロール位置を保持 | `apps/web/e2e/feed.spec.ts`（3 × 2）                                                                                  |
| A4  | 個別投稿でコメント一覧と投稿欄が動作する                         | **合格**                      | コメントが昇順（返信はスレッドの下）で DB と一致。入力欄からの投稿が `POST /comments` 201 で保存される。REST の再取得を遮断した状態でも投稿者キャラの自動返信（`parent_comment_id` = 自分のコメント）が Realtime で表示される。自分のコメントの削除で返信も消える。Gate #1 でヒットしたコメントは 422 `moderation_blocked`・トースト・保存されない | `apps/web/e2e/post-detail.spec.ts`（7 × 2）                                                                           |
| A5  | プロフィールで「無料」「有料」タブが切り替わる                   | **合格**                      | `/c/misaki_ol` の統計が DB と一致。無料タブ（既定）は `is_paid=false` の件数だけ・3 列グリッド、有料タブは `is_paid=true` の件数だけ。`aria-selected` の切り替え。投稿のアバターからプロフィールへ遷移 | `apps/web/e2e/profile.spec.ts`（A5 の 3 テスト、iphone / android）                                                     |
| A6  | 有料投稿がぼかし＋鍵で表示され、タップで「準備中」モーダルが出る | **合格**                      | フィードのカード・投稿詳細・プロフィールの有料タブの 3 か所で、8px 以上の CSS ぼかし + 鍵 + 「有料コンテンツ」または価格。タップで「この投稿は有料コンテンツです」（価格は DB と一致）→「購入する（準備中）」でトースト「課金機能は現在準備中です」、URL は変わらない。`post_private_assets` の URL は通信にも DOM にも出ない | `apps/web/e2e/paid.spec.ts`（3 × 2）、screenshots 04 / 05 / 06 / 09 / 11                                               |
| A7  | キャラプロフィールから「DMする」で DM 画面に遷移する             | **合格**                      | 「DMする」→ `/dm/<characterId>`。ヘッダーにキャラ名、DB と一致する挨拶（キャラのメッセージ 1 件）が表示され、`/dm` の一覧にも出る | `apps/web/e2e/profile.spec.ts`（A7、iphone / android）                                                                 |
| A8  | DM で送信するとキャラが文脈に合った返答を返す                    | **合格（モック LLM）**        | 画面から 10 往復。毎回「入力中」（`role=status`「美咲が入力中」）→ `POST /chat` の返答を表示。挨拶に「こんばんは」、おやすみに「おやすみ」を返す。異なる返答が 8 種類以上。DB に挨拶 + 20 件が交互に保存。再読み込み後も表示。自分の吹き出しは右・キャラは左 | `apps/web/e2e/dm.spec.ts`、[raw/e2e-dm-memory-transcripts.txt](raw/e2e-dm-memory-transcripts.txt)。**本物の LLM での品質は未確認** |
| A9  | 以前話した内容を踏まえた返答が返る（メモリ）                     | **合格（モック LLM）**        | 1 往復目「来週、大阪に出張するんだ」→ 記憶が作られ通知が出る → 別の話題 9 往復 →「大阪でおすすめの場所ある？」の返答の `memories_used` にその記憶が含まれ、返答が出張に触れる（「前に来週、大阪に出張するって言ってたけど、あれからどう？」）。メモリパネルにも表示 | `apps/web/e2e/memory.spec.ts`（A9 × 2）、API 統合テスト `test_memory_recall_after_ten_turns_and_forget_after_delete`   |
| A10 | メモリパネルで記憶の追加・削除ができ、削除後は返答に反映されない | **合格（モック LLM）**        | パネルで優先度「高」・「二人だけの秘密」の記憶を追加（DB で `is_user_edited=true`・`secret` タグ）→ 次の返答がその内容（3 月 3 日）に触れる → 確認ダイアログで削除（DELETE 204、DB から消える、トースト）→ 同じ質問への返答は触れず `memories_used` にも含まれない。再読み込み後のパネルにも出ない | `apps/web/e2e/memory.spec.ts`（A10 × 2）                                                                              |
| A11 | PWA としてホーム画面に追加でき、スタンドアロン起動する           | **一部（実機は未実施）**      | manifest（`display: standalone`・`start_url`・`scope`・`lang`、192 / 512 / maskable アイコンが 200 で PNG の寸法も一致）。Chromium の `Page.getAppManifest` が動的ページ `/login` でもマニフェストを検出。`<head>` に manifest・apple-touch-icon（180×180）・`apple-mobile-web-app-title`・`viewport-fit=cover`。Service Worker が登録され再読み込み後にページを制御、キャッシュは同一オリジンのみ、オフラインで `/offline`（「再読み込み」と通信の復帰で元の URL を読み込み直す） | `apps/web/e2e/pwa.spec.ts`（A11 の 3 テスト × 2。SW のキャッシュの上限・D-3 の圏外の 3 テストも同じファイル）。**ホーム画面への追加とスタンドアロン起動は実機で未確認** → [実機チェックリスト](#a1a11-実機チェックリスト) |
| A12 | `audit_logs` にチャットのリクエスト／レスポンスが記録されている  | **合格**                      | 画面から 2 往復 → SQL で `chat.request` 2 件・`chat.response` 2 件・`conversation.create` 1 件。対は `request_id` で一致し、`message`・`reply`・`message_id`・`user_message_id`・`conversation_id` が一致、`model`・`latency_ms`・`memories_used` あり | `apps/web/e2e/audit.spec.ts`（× 2）、[raw/audit-logs-sample.json](raw/audit-logs-sample.json)。確認 SQL は [06-operations.md](../handover/06-operations.md#監査ログaudit_logsの調べ方) |
| A13 | RLS により、他ユーザーの会話・メモリが取得できない               | **合格**                      | 2 アカウント（A = 所有者、B = 他人）。A のトークンでは自分の行が読める（ポジティブコントロール）。B のトークンの supabase-js では A の conversations / messages / memories / profile / `list_dm_threads` が 0 件、messages への INSERT は拒否、memories の DELETE は 0 件、`mark_conversation_read` で A の既読は変わらない、anon key も 0 件。API: B のトークンで A の会話への `POST /chat`・A の記憶への PATCH / DELETE は 404、`GET /memories` に A の記憶は出ない、トークン無しは 401。画面でも B の DM・メモリパネルに A の内容は出ない | `apps/web/e2e/rls.spec.ts`（× 2）、pgTAP [raw/test-db.txt](raw/test-db.txt)（10 ファイル / 195 件）、API 統合テスト        |
| A14 | シークレットがコードに含まれていない                             | **合格**                      | `bash scripts/check-secrets.sh`（新しいファイルの追加後に再実行）。CI の checks ジョブでも毎回実行 | [raw/check-secrets.txt](raw/check-secrets.txt)（459 ファイル、検出なし、exit 0）                                       |
| A15 | README の手順でゼロから環境構築できる                            | **未実施（別担当者が必要）**  | 仕様上、別の担当者が実施する項目。作成者側では README の各コマンドをこの環境で実行して確認済み（下記「README の手順の確認」） | [A15 の実施手順](#a15-の実施手順)                                                                                      |
| A16 | 決済・画像生成・TTS のコードが存在しない                         | **合格**                      | `bash scripts/check-scope.sh`（決済・画像生成・TTS / 音声・ユーザー投稿・通知・管理画面）。`layout.spec.ts`（H2）: 下部タブは ホーム / 検索 / メッセージ / プロフィール の 4 つだけ、どの画面にもファイル入力・投稿作成のボタン / リンクが無い、`/new`・`/create`・`/upload`・`/posts/new`・`/compose` は 404、API に `POST /posts` が無い | [raw/check-scope.txt](raw/check-scope.txt)（exit 0。許可行 2: 仕様のモーダル文言の説明コメントと、E2E の「ファイル入力が無い」ことを確かめるセレクタ。どちらも `scope-check: allow`） |

補足: ダークモード（仕様書 §4.1）も `dark-mode.spec.ts` で確認した（端末設定がダークのとき、全画面とメモリパネルで背景 `#000`・文字 `#f5f5f5`・白い面が残らない）。

## 自動テストの結果（2026-09-26）

| スイート                                      | 結果                                                                            | 出力                                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| E2E（Playwright、14 spec・72 テスト × iphone / android） | 143 passed / 1 skipped（スクリーンショット撮影の android 分）、約 4 分 8 秒       | [e2e-results.md](e2e-results.md)、[raw/e2e-playwright.txt](raw/e2e-playwright.txt) |
| pgTAP（`pnpm db:test`）                       | 10 ファイル / 195 件 すべて成功                                                 | [raw/test-db.txt](raw/test-db.txt)                                                   |
| API（`uv run pytest`、統合テストを含む）      | 389 passed                                                                      | [raw/api-pytest.txt](raw/api-pytest.txt)                                             |
| Web（lint / typecheck / vitest）              | エラーなし、vitest 35 ファイル / 351 件                                         | [raw/web-quality-gates.txt](raw/web-quality-gates.txt)                               |
| `check-secrets.sh` / `check-scope.sh`         | OK / OK                                                                         | [raw/](raw/)                                                                         |

修正の統合検証（2026-09-25 夜）では、`pnpm install --frozen-lockfile`・`pnpm format:check`・`pnpm lint`・`pnpm typecheck`・`pnpm test`（vitest 331・pytest 389）・
`pnpm db:test`（195）・`pnpm db:types:check`・`pnpm personas:validate`・`pnpm check:secrets`（+ 回帰テスト `scripts/tests/check-secrets.test.sh` 18 件）・
`pnpm check:scope`・`openapi:check`・`pnpm audit --prod`（既知の脆弱性なし）・actionlint（`.github/workflows/ci.yml`）・ShellCheck・`next build`・
API の Docker イメージのビルドがすべて成功し、E2E の全体（132 件）を本番ビルドに対して 3 回実行した（1 回目に見つけた不具合を修正し、2・3 回目は全件成功）。
最終の再実行（2026-09-26 0 時台）では、残りの指摘（共有リンクからのログイン後の遷移先・遷移先の先読み）の修正とテストの追加の後に、`pnpm lint`・`pnpm typecheck`・
`pnpm test`（vitest 351・pytest 389）・`pnpm db:test`（195）・ruff / mypy（41 ファイル）と E2E の全体（144 件。上の表）を実行してすべて成功した。

## README の手順の確認（A15 の事前確認）

作成者がこのサンドボックスで、ルートの README のコマンドを次のとおり実行した。**別担当者による新しいマシンでの確認の代わりにはならない。**

| 手順                                                       | 結果                                                                                                  |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `pnpm install --frozen-lockfile`                           | 成功（ロックファイルと一致）                                                                          |
| `pnpm db:start`                                            | 成功（起動済みのスタックに対して実行。初回起動のイメージ取得は未確認）                                |
| `pnpm setup:env`                                           | 空のディレクトリ（`OUT_DIR`）に対して成功。既存ファイルがあると終了コード 1 になることも確認           |
| `cd apps/api && uv sync && uv run uvicorn app.main:app`    | 成功。`/health` が `{"status":"ok","llm_mode":"mock","embedding_mode":"hash","db":"ok"}`             |
| `docker compose up --build api`                            | 成功（ベースイメージはミラー `PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim`）。コンテナの API でログイン〜DM まで確認。統合検証では digest 固定のベースイメージ（ミラー `mirror.gcr.io/library/python:3.12.14-slim-trixie@sha256:2f17fc04…`。`docker-compose.yml` の既定と同じ digest）で `docker build -f apps/api/Dockerfile` が成功し、コンテナの `/health` が `db: ok` を返すことを確認した（この環境では ghcr.io の uv イメージを取得できないため、uv は同じ版 0.8.17 のバイナリをローカルでイメージにして `UV_IMAGE` に指定） |
| `pnpm --filter @everkano/web dev`                          | 成功（`/login` 200、未ログインの `/` は `/login` へ 307）。`pnpm dev` は turbo の dry-run で 2 つのタスクを確認 |
| Mailpit でのログイン → DM                                  | スクリプトで確認: 件名「everkano ログイン用リンク」、`/auth/confirm?token_hash=...` のリンクと 6 桁コード、コードでログイン → `profiles` 作成 → `POST /conversations` → `POST /chat`（記憶が 1 件作成）→ `audit_logs` に `chat.request` / `chat.response` / `conversation.create` / `memory.create` |
| デプロイ（Supabase / Fly.io / Vercel）                      | **未実行**（外部サービスに接続できない環境のため）。`supabase db push --local --dry-run` でコマンドの形だけ確認 |

## A1・A11 実機チェックリスト

staging（HTTPS）で行う。Service Worker とホーム画面への追加には HTTPS が必要なので、ローカルの `http://<PC の IP>` では確認できない。

**準備**

- [ ] staging の Web / API / Supabase を用意し（[README のデプロイ](../../README.md#デプロイ)）、[supabase-auth.md](../handover/supabase-auth.md) の設定を済ませる
- [ ] 端末: iPhone（iOS の最新版と 1 つ前、Safari）と Android（Chrome の最新版）を各 1 台以上。可能ならノッチ / ホームインジケーター付きの iPhone
- [ ] ライトとダークの両方で撮影する（OS の設定で切り替え）

**A1: 全画面（各端末 × ライト / ダーク）** — E2E のスクリーンショットと同じ 22 画面:
ログイン / コード入力 / ホーム / ホームの有料投稿 / ロックモーダル / 準備中トースト / 投稿詳細 / コメント / 有料の投稿詳細 / プロフィール（無料・有料） /
検索 / 検索結果 / DM 一覧 / DM 会話 / 入力中 / メモリパネル / 記憶の追加 / 削除の確認 / マイページ / オフライン / 404。各画面で:

- [ ] 横スクロールが出ない。文字や画像が画面外にはみ出さない
- [ ] 下部タブバーと固定の入力欄がホームインジケーターに重ならない（safe-area）。ノッチの下にヘッダーが隠れない
- [ ] 入力欄をタップしても画面がズームしない。キーボードを出しても入力欄が隠れない（DM・コメント・ログイン）
- [ ] 画像が正方形で表示され、有料投稿はぼかし + 鍵
- [ ] ダークモードで白い面が残らない
- [ ] スクリーンショットを `docs/acceptance/device-screenshots/<ios|android>/<画面番号>-<light|dark>.png` に保存し、端末名・OS / ブラウザのバージョンをこのレポートに追記する

**A11: ホーム画面への追加とスタンドアロン起動**

- [ ] iOS Safari: 共有 →「ホーム画面に追加」→ アイコンと名前「everkano」が表示される
- [ ] iOS: アイコンから起動するとアドレスバーなしで開く（スタンドアロン）
- [ ] iOS: その PWA の中で、メールの **6 桁コード** でログインできる（PWA は Safari と Cookie を共有しないため、メールのリンクでは PWA 側はログインしない）
- [ ] Android Chrome: メニュー →「ホーム画面に追加」または「アプリをインストール」→ アイコンから起動するとスタンドアロンで開く
- [ ] Android: メールのリンクと 6 桁コードのどちらでもログインできる
- [ ] 両方: 機内モードで画面遷移すると `/offline` の画面が出て、「再読み込み」で復帰できる
- [ ] 両方: ステータスバーの色がライト / ダークに合っている

## A15 の実施手順

- [ ] このリポジトリに関わっていない担当者が、新しいマシン（macOS または Linux）で、ルートの [README.md](../../README.md) の「ローカル環境構築」だけを見て実施する
- [ ] 使ったマシン・OS・各ツールのバージョン・所要時間を記録する
- [ ] README の手順から外れた操作（追加で調べたこと・README の誤り）をすべて記録し、README を直す
- [ ] 合格条件: ローカル（`LLM_MODE=mock`）で、ログイン（Mailpit）→ フィード → プロフィール → DM を数往復 → メモリパネルで追加・削除、まで操作できること（A2〜A10 相当）

## 本物の LLM での確認（A8〜A10 の補完）

- [ ] staging（`LLM_MODE=live`、`LLM_API_KEY` 設定済み。可能なら `EMBEDDING_MODE=live` + 再埋め込み）で A8〜A10 を手動で行う
- [ ] 10 往復の会話ログと、`audit_logs` の `chat.response`（`model`・`latency_ms`・`memories_used`）を保存する（SQL は [06-operations.md](../handover/06-operations.md#監査ログaudit_logsの調べ方)）
- [ ] 口調がペルソナに合っているか、記憶への触れ方が自然か、AI であることを示唆していないかを確認する

## 提出物（仕様書 §16）

| #   | 提出物                                                    | 状態                           | 内容・残作業                                                                                                                         |
| --- | --------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | GitHub Private Repo（main ブランチ + 全 PR 履歴）         | **一部（依頼者の作業が必要）** | リポジトリとコミット履歴（Conventional Commits）はある。private 設定・main ブランチ保護（PR 必須・CI 必須）・PR 経由のマージはリポジトリ管理者が設定する（[08-dev-guide.md](../handover/08-dev-guide.md#main-ブランチの保護github-での設定)） |
| 2   | staging 環境のデプロイ済み URL（Vercel + FastAPI ホスト） | **未（依頼者の作業が必要）**   | Supabase / Vercel / Fly.io / LLM のアカウントと API キーが必要。手順は [README のデプロイ](../../README.md#デプロイ)。デプロイ後に URL をここに記載する |
| 3   | README.md（環境構築・起動・デプロイ手順）                  | 済                             | [README.md](../../README.md)                                                                                                         |
| 4   | `docs/adr/` の ADR 一式                                   | 済                             | [ADR-0001〜0034](../adr/README.md)                                                                                                   |
| 5   | `docs/handover/` の引き継ぎ資料                           | 済                             | [docs/handover/](../handover/README.md)（構成図・データフロー・データモデル・API・メモリとモデレーション・運用・セキュリティ・開発ガイド） |
| 6   | 実機スクリーンショット集（全画面）                        | **一部（実機は未）**           | エミュレーションのスクリーンショット 22 画面 × ライト / ダーク（[screenshots/](screenshots/)。画像はテスト用のダミーに差し替え）。実機での撮影は上のチェックリスト |
| 7   | 受け入れ基準 A1〜A16 の検証結果レポート                   | 済（本書）                     | A1 / A11 / A15 と実 LLM の確認が残る                                                                                                  |

## 検証中に見つけて修正した不具合

受け入れ検証の中で見つけたもの。納品前の検査（観点別の 77 件）の結果と対応は [inspection-report.md](inspection-report.md)。

1. **動的ページでマニフェストが検出されない（A11）**: Next.js 15.2 以降、動的ページ（`/login`・`/`・`/posts/*`・`/c/*`・`/dm/*`）のメタデータが `<body>` に
   ストリーミングされ、`<link rel="manifest">` や apple-touch-icon が `<head>` に無かった。Chromium の `Page.getAppManifest` は `/login` で空を返し
   （静的な `/offline` では検出）、Android でホーム画面に追加してもスタンドアロンにならない状態だった。`apps/web/next.config.ts` に
   `htmlLimitedBots: /.*/` を設定して修正し、`pwa.spec.ts` で回帰を検査している。
2. **記憶を追加（またはフォームをキャンセル）した後、Esc でメモリパネルを閉じられない**: フォームを閉じるとフォーカスしていたボタンが消え、
   フォーカスがシートの外に落ちていた。`apps/web/components/memory/memory-panel.tsx` でフォームを閉じたら「覚えてほしいことを追加」ボタンへ
   フォーカスを戻すよう修正。
3. **（統合検証）オフラインから復帰しても自動で読み込み直さないことがある**: オフラインページの JavaScript が動き出す前に通信が戻ると
   `online` イベントを取りこぼしていた。表示の直後にもサーバーに届くかを確かめて読み込み直すよう修正（`apps/web/app/offline/reload-button.tsx`）。
   詳細は [e2e-results.md](e2e-results.md#今回の検証で見つけて修正した不具合)。
4. **（統合検証）その他**: E2E のセレクタの陳腐化（ヘッダーの `banner` ロール）、`check-secrets.sh` の誤検知（テストのダミー値・Fly.io の `secret_name`）、
   表示名の長さが DB で制限されていなかった（クライアントが直接 UPDATE できるため、`profiles.display_name` に 1〜30 文字の check 制約を追加）。

## 未解決の問題・制約

| 項目                        | 内容                                                                                                                                                                                  |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ハイドレーションエラー #418 | 本番ビルドで動的ルート（`/dm/[characterId]`・`/c/[handle]`）を直接開いたとき、CPU 負荷が高い状況（E2E の並列実行など）で数十〜百回に 1 回程度。React がクライアントで描画し直すので表示と動作に影響は無い。静的ルート（`/`・`/dm`）では発生しない。検証済み: `loading.tsx` をすべて外しても、`htmlLimitedBots` を外しても再現するため、これらは原因ではない。ストリーミング SSR とハイドレーションのタイミングに依存する（負荷をかけないと再現しない）。原因は未特定 |
| E2E は CI で PR ごとには動かない | `.github/workflows/ci.yml` の `e2e` ジョブは main への push・毎晩・手動実行（Actions → CI → Run workflow）で動く。GitHub 上ではまだ一度も実行していない |
| 納品前の検査で未対応の項目 | CAPTCHA（ログインのボット対策）の導入、API → DB の証明書の検証（`verify-full`）の本番での設定、`LICENSE` の権利者の確定など。一覧と推奨する対応は [inspection-report.md](inspection-report.md#5-未対応の項目と推奨する対応) |
| エミュレーションの限界      | WebKit（iOS Safari）ではなく Chromium のエミュレーション。safe-area は 0 で描画される                                                                                                 |
| 画像                        | シードの外部プレースホルダ画像（picsum.photos / api.dicebear.com）はこの環境から取得できないため、テスト内で生成した SVG に差し替えている。スクリーンショットの画像はダミー             |
| LLM                         | A8〜A10 の返答内容の検証はモック LLM の決定的な挙動が前提。実 LLM の品質は未確認                                                                                                      |
| テストの監査ログ            | E2E のテストユーザーは実行後に削除されるが、`audit_logs` の行は残る（監査ログは消さない方針。ローカルで 1 回あたり約 20 行増える）                                                    |
