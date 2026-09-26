# everkano（Project P MVP）

AI キャラクターだけが投稿する Instagram 型 SNS と、そのキャラクターと 1 対 1 で話せる DM（記憶を持つコンパニオン）を一体にした、
**スマートフォン専用の PWA**。仕様は「開発依頼書 Project P MVP v0.1」（以下、仕様書）に従う。

- 投稿するのは AI キャラクター 10 体（全員 20 歳以上の成人）だけ。ユーザーはフィードの閲覧・いいね・コメント・DM ができる。
- DM のキャラは会話を覚える（短期・中期・長期のメモリ）。記憶はユーザーがメモリパネルで追加・編集・削除できる。
- **キャラクターエンジン v1.0**（追加仕様「記憶 × カレンダー × 好感度」）: 返答の後に記憶・約束を自動で整理し（記憶エンジン v2）、キャラは日本時間の予定に沿って
  生活して状態・投稿が変わり（カレンダー）、関わり方で関係が深まり（好感度。ユーザーには見せない）、キャラから自発的にメッセージが届く。DM の返答はストリーミングで表示する。
  評価ハーネスで 30 日・90 日の利用を早送りして品質を測る（[docs/eval/](docs/eval/README.md)）。設計は [ADR-0035](docs/adr/0035-character-engine-architecture.md)〜[ADR-0049](docs/adr/0049-prompt-order-and-prefix-cache.md)。
- 有料投稿はぼかし + 鍵 + 「購入する（準備中）」まで。決済・画像生成・TTS・ユーザー投稿・通知・管理画面は実装しない（仕様書 §12。CI で検査）。
- 会話・生成・判定はすべて監査ログ（`audit_logs`）に残る（DD 対応）。

**目次**: [構成](#リポジトリ構成) / [必要なツール](#必要なツール) / [ローカル環境構築](#ローカル環境構築ゼロから) / [テスト](#テスト) /
[デプロイ](#デプロイ) / [環境変数](#環境変数) / [LLM と埋め込みのモード](#llm-と埋め込みのモードmock--live) / [ドキュメント](#ドキュメント)

## リポジトリ構成

仕様書 §3 の構成に沿っている。`# [追加]` は仕様書の構成に無く、実装で足したもの。

```
everkano/
├── apps/
│   ├── web/                              # Next.js 15（App Router）+ TypeScript + Tailwind CSS v4。Vercel にデプロイ
│   │   ├── app/
│   │   │   ├── (auth)/login/              # ログイン（メール → マジックリンク / 6 桁コード）
│   │   │   ├── (main)/
│   │   │   │   ├── page.tsx               # ホームフィード
│   │   │   │   ├── posts/[postId]/        # 個別投稿 + コメント
│   │   │   │   ├── c/[handle]/            # キャラプロフィール
│   │   │   │   ├── dm/                    # DM 一覧
│   │   │   │   ├── dm/[characterId]/      # DM 会話 + メモリパネル
│   │   │   │   ├── search/                # [追加] キャラ検索（§4.2 のタブ）
│   │   │   │   ├── me/                    # [追加] 自分のプロフィール（§5.7）
│   │   │   │   └── dev/ui/                # [追加] 開発用 UI カタログ（page.dev.tsx。next dev のときだけ。本番ビルドに含めない）
│   │   │   ├── auth/confirm/, auth/confirm/verify/, auth/callback/  # [追加] マジックリンクの確認画面とログイン（POST）/ PKCE の着地点
│   │   │   ├── media/[...key]/            # [追加] Bunny.net トークン認証の署名 URL へ 302
│   │   │   ├── offline/                   # [追加] Service Worker のオフラインページ
│   │   │   ├── layout.tsx
│   │   │   └── globals.css
│   │   ├── components/                    # feed / post / profile / dm / ui（汎用）+ [追加] memory / search / auth / account / pwa
│   │   ├── lib/                           # supabase / api（Python API クライアント）/ storage（StorageAdapter）+ [追加] queries / auth / env
│   │   ├── middleware.ts                  # [追加] セッション更新・未ログインは /login へ
│   │   ├── e2e/                           # [追加] Playwright の受け入れテスト
│   │   └── public/                        # manifest.json / icons + [追加] sw.js（手書きの Service Worker）
│   └── api/                              # Python 3.12 + FastAPI。Fly.io にデプロイ（Docker）
│       ├── app/
│       │   ├── main.py
│       │   ├── worker.py                  # [追加] ジョブのワーカー・スケジューラの常駐プロセス（python -m app.worker）
│       │   ├── container.py               # [追加] サービスの組み立て・レート制限・エンジンの配線
│       │   ├── engine/                    # [追加] キャラクターエンジン（pipeline / context_assembler / jobs / scheduler / memory / calendar / affinity / proactive / safety）
│       │   ├── routers/                   # chat / comments / health + [追加] conversations / memories / promises / proactive / safety
│       │   ├── services/                  # llm / persona / memory / moderation + [追加] chat / comments / embedding / prompt / audit / rate_limit ほか
│       │   ├── models/                    # Pydantic（packages/shared/src/api.ts と一致）
│       │   └── core/                      # config / security（JWT 検証）/ logging + [追加] db / errors / middleware / http / observability（Sentry）
│       ├── evals/                         # [追加] 評価ハーネス（本番のイメージには入らない。docs/eval/）
│       ├── scripts/                       # [追加] OpenAPI の出力・ペルソナの検証・記憶の再埋め込み
│       ├── tests/                         # [追加] 単体テスト + 統合テスト（ローカル Supabase）+ engine/ + evals/
│       ├── Dockerfile
│       ├── fly.toml                       # [追加] Fly.io の設定
│       ├── package.json                   # [追加] turbo から uv を呼ぶためのラッパー
│       └── pyproject.toml
├── packages/
│   ├── shared/                           # 型定義（DB 型 = supabase gen types の生成物、API 型 = api.ts）
│   ├── personas/                         # キャラ YAML（10 体）+ [追加] seed/feed.yaml とシードの生成・検証スクリプト
│   └── prompts/                          # プロンプトテンプレート（DM・記憶の分析・中期要約・好感度の評価・自発メッセージ・キャプション・コメント返信）+ [追加] safety/（E6 の相談窓口）
├── infra/supabase/
│   ├── config.toml                       # [追加] ローカル Supabase の設定（Auth・メールテンプレートの参照）
│   ├── migrations/                       # スキーマ・RLS・grant・トリガー・RPC（DB の契約）
│   ├── seed.sql                          # シード（packages/personas から自動生成。直接編集しない）
│   ├── seed_engine.sql                   # [追加] エンジン用のシード（予定からの投稿の画像プール）
│   ├── templates/                        # [追加] ログインメール（リンク + 6 桁コード）
│   └── tests/                            # [追加] pgTAP（RLS・権限）と Auth 設定のテスト
├── docs/
│   ├── adr/                              # 設計判断の記録（ADR-0001〜0049）
│   ├── eval/                             # [追加] 評価ハーネスの使い方・判定のプロンプト・結果の推移
│   ├── api/                              # OpenAPI（FastAPI から生成。CI で最新か検査）
│   ├── handover/                         # 引き継ぎ資料
│   └── acceptance/                       # [追加] 受け入れ検証レポート・納品前の検査の報告・E2E 結果・スクリーンショット
├── scripts/                              # [追加] env 生成・pgTAP 実行・シークレット / スコープ外機能 / DB 型の検査
├── .github/                              # [追加] CI（workflows/ci.yml）と PR テンプレート
├── .env.example                          # 環境変数の一覧（キー名とローカルの既定値のみ）
├── docker-compose.yml                    # ローカル用（API だけをコンテナで。Supabase は Supabase CLI で起動）
├── turbo.json / pnpm-workspace.yaml / package.json
└── README.md
```

全体の構成図とデータの流れは [docs/handover/01-architecture.md](docs/handover/01-architecture.md)。

## 必要なツール

| ツール                          | バージョン                                                                  | 用途                                                                                         |
| ------------------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Node.js                         | 22 系（`.nvmrc`）                                                           | Web・モノレポのツール                                                                        |
| pnpm                            | 10（`package.json` の `packageManager` = 10.33.0。`corepack enable` で揃う）  | パッケージ管理。`.npmrc` の `engine-strict` により Node 22 / pnpm 10 未満ではインストールできない |
| Python + uv                     | Python 3.12（`apps/api/.python-version`）/ uv 0.8 以上                       | Python API。Python 3.12 が無ければ uv が自動で用意する                                        |
| Docker                          | Docker Desktop / Docker Engine                                              | ローカル Supabase（Supabase CLI がコンテナで起動する）。API をコンテナで動かす場合も          |
| Supabase CLI                    | 2.x（CI と検証は 2.117.0）                                                  | ローカル Supabase の起動・型生成・本番へのマイグレーション                                   |
| psql（PostgreSQL クライアント） | 任意のバージョン                                                            | pgTAP テスト（`pnpm db:test`）                                                                |
| bash 4.4 以上 / GNU grep        | —                                                                           | `scripts/`（macOS は `brew install bash grep`）                                              |

E2E テストには Playwright の Chromium（`pnpm --filter @everkano/web exec playwright install chromium`）も使う。デプロイには Supabase・Vercel・
Fly.io（`flyctl`）・LLM 提供元（OpenRouter または DeepSeek）のアカウントが必要。

## ローカル環境構築（ゼロから）

外部サービスのキーは不要（LLM は `mock`、埋め込みは `hash` モードで動く）。以下はこのリポジトリの作成環境で実行して確認した手順。

```bash
git clone <リポジトリの URL> everkano && cd everkano
corepack enable                 # packageManager の pnpm 10.33 を使う
pnpm install

# 1. ローカル Supabase（Postgres / Auth / PostgREST / Realtime / Mailpit / Studio）を起動する
#    初回はイメージの取得に数分かかる。マイグレーションと seed.sql（キャラ 10 体・投稿 50 件・コメント）が自動で適用される
pnpm db:start                   # = supabase start --workdir infra

# 2. env ファイルを作る（supabase status の値で .env.example を埋める）
#    apps/web/.env.local（NEXT_PUBLIC_* / BUNNY_*）と apps/api/.env（それ以外）ができる
pnpm setup:env

# 3. Python API（ターミナル A）
cd apps/api
uv sync                         # .venv を作って依存を入れる
uv run uvicorn app.main:app --reload --port 8000
#   または、リポジトリのルートでコンテナとして: docker compose up --build api

# 4. Web（ターミナル B、リポジトリのルートで）
pnpm --filter @everkano/web dev # http://localhost:3000
```

`pnpm dev` で Web（:3000）と API（:8000）を turbo で同時に起動することもできる。

### 動作確認

1. `curl http://localhost:8000/health` → `{"status":"ok", ..., "llm_mode":"mock","embedding_mode":"hash","db":"ok"}`
2. ブラウザで http://localhost:3000 を開く。**スマホ専用の UI** なので、開発者ツールのデバイス表示（例: 390×844）にする。
3. ログイン画面でメールアドレス（何でもよい。実際には送信されない）を入力 → **Mailpit（http://127.0.0.1:54324）** に届いたメールの
   「ログインする」を開いて確認画面の「ログインする」を押すか、メールの 6 桁コードをログイン画面に入力する。
4. ホーム → キャラのプロフィール → 「DMする」で DM を送ると、モックの LLM がキャラの口調で返す（文ごとに表示される）。「来週、大阪に出張するんだ」のように
   予定を話すと、**会話が止まって約 3 分後**（返答の後のジョブの待ち `ENGINE_POST_TURN_DELAY_SECONDS` = 180 秒。手元ですぐ見たいなら `apps/api/.env` で 1 にする）に
   記憶と約束が作られ「覚えました」が出る（ヘッダーの「i」でメモリパネル）。後で「大阪」の話をすると触れてくる。DM ヘッダーの 2 行目にはキャラの今の状況（予定）が出る。

| ローカルの URL                                              | 内容                                                                  |
| ----------------------------------------------------------- | --------------------------------------------------------------------- |
| http://localhost:3000                                       | Web                                                                   |
| http://localhost:8000/docs                                  | API の Swagger UI                                                     |
| http://127.0.0.1:54321                                      | Supabase（Auth / REST / Realtime）                                    |
| postgresql://postgres:postgres@127.0.0.1:54322/postgres     | Postgres                                                              |
| http://127.0.0.1:54323                                      | Supabase Studio                                                       |
| http://127.0.0.1:54324                                      | Mailpit（ローカルのログインメールはすべてここに届く）                 |

### うまくいかないとき

- `pnpm setup:env` が「既に存在するため何も書き込みませんでした」で止まる → `pnpm setup:env --force`（元のファイルは `*.bak.<日時>` に退避される）。
- `pnpm db:start` がイメージの取得で失敗する → `SUPABASE_INTERNAL_IMAGE_REGISTRY=mirror.gcr.io pnpm db:start`。
- `docker compose up --build api` がベースイメージの取得で失敗する（Docker Hub のレート制限）→ `PYTHON_IMAGE=mirror.gcr.io/library/python:3.12.14-slim-trixie@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 docker compose up --build api`（digest は `apps/api/Dockerfile` の `PYTHON_IMAGE` と同じ）。
- Web が「環境変数が不正です」で止まる → `apps/web/.env.local` を確認（`pnpm setup:env` で作り直せる）。
- ポートが使われている → 3000 / 8000 / 54321〜54324 を使っているプロセスを止める。
- ローカルの DB を最初の状態に戻したい / シードの投稿が古くなった（投稿時刻は投入時刻が基準）→ `pnpm db:reset`（**ローカルの全データが消える**）。
- スマホ実機での確認（ホーム画面への追加など）には HTTPS が必要。ローカルの `http://<PC の IP>` では PWA として動かないので staging で行う。

## テスト

| 対象                  | コマンド                                                                                         | CI  |
| --------------------- | ------------------------------------------------------------------------------------------------ | --- |
| まとめて              | `pnpm lint && pnpm typecheck && pnpm test`（ESLint・ruff・Prettier / tsc・mypy strict / vitest・pytest） | ✓   |
| Web                   | `pnpm --filter @everkano/web lint` / `typecheck` / `test` / `build`                               | ✓   |
| API                   | `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy app evals && uv run pytest` | ✓   |
| DB（RLS・権限）       | `pnpm db:test`（pgTAP。ローカル Supabase が必要）、`pnpm db:types:check`（DB 型のずれ）           | ✓   |
| キャラクターエンジン  | `cd apps/api && uv run pytest tests/engine -q`（モジュールごとは [08-dev-guide.md](docs/handover/08-dev-guide.md#エンジンのテスト)）、`uv run mypy app evals` | ✓   |
| 評価ハーネス          | `cd apps/api && uv run python -m evals.run --days 30`（mock。結果は `docs/eval/`。[docs/eval/README.md](docs/eval/README.md)） | —（手動） |
| E2E（受け入れ基準）   | API と Web の本番ビルドを起動してから `pnpm --filter @everkano/web e2e`（手順は [apps/web/e2e/README.md](apps/web/e2e/README.md)） | main への push・毎晩・手動 |
| リポジトリの検査      | `pnpm check:secrets`（A14。履歴は `bash scripts/check-secrets.sh --history <範囲>`）/ `pnpm check:scope`（A16。回帰テスト `bash scripts/tests/check-scope.test.sh`）/ `pnpm personas:validate` / `pnpm format:check` / `pnpm --filter @everkano/api openapi:check` / `pnpm audit --audit-level high` | ✓   |

- API の統合テストはローカル Supabase に自前のデータを作って削除する。DB に接続できないとき、ローカルでは **skip** になるので、`pnpm db:start` 済みで
  実行し、`skipped` の件数を確認する（接続先は `TEST_DATABASE_URL`）。`REQUIRE_TEST_DB=1`（CI では既定で有効）なら skip せずに失敗する。
- CI（[.github/workflows/ci.yml](.github/workflows/ci.yml)）は PR と main への push で 3 ジョブ（checks / web / api-db）を実行する。E2E（`e2e` ジョブ）は
  main への push・毎晩の定期実行・手動実行のときだけ動く。シークレットは使わない。
- 件数（2026-09-26、キャラクターエンジン v1.0 の統合後）: vitest 432 件、pytest 1,539 件（うちエンジン 1,074・評価ハーネス 54）、pgTAP 286 件、E2E 16 spec / 172 件（2 端末）。
  MVP の受け入れ検証の結果は [docs/acceptance/report.md](docs/acceptance/report.md)、エンジンの評価の結果は [docs/eval/README.md](docs/eval/README.md)。

## デプロイ

| 役割                 | サービス                                    | 設定                                                                                       |
| -------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------ |
| DB / Auth / Realtime | Supabase（ホスト版）                        | `infra/supabase/`、[docs/handover/supabase-auth.md](docs/handover/supabase-auth.md)        |
| Python API + worker  | Fly.io（東京 `nrt`）。同じイメージの 2 つのプロセスグループ `app`（API）と `worker`（エンジンのジョブ・定期実行） | `apps/api/fly.toml`、`apps/api/Dockerfile`                                                  |
| Web                  | Vercel                                      | Root Directory `apps/web`                                                                  |
| 画像                 | Bunny.net CDN（オリジン Backblaze B2）       | Web の `NEXT_PUBLIC_STORAGE_DRIVER=bunny`（シードのプレースホルダ画像のままなら `passthrough`） |

> **注意**: 以下のデプロイ手順は、外部サービスに接続できない作成環境では実行できていない（コマンドの形は各 CLI のヘルプと突き合わせ済み）。
> 初回は staging で確認してから本番に適用すること。シークレットは各サービスの環境変数・secrets にだけ置く（H7）。

**順番**: 1. Supabase → 2. API（Fly.io）→ 3. Web（Vercel）→ 4. 画像（B2 + Bunny）→ 5. URL の相互設定 → 6. 動作確認。
Web の本番 URL（`https://<プロジェクト>.vercel.app` または独自ドメイン）を先に決めておくと、各所で同じ値を使える。

### 1. Supabase

1. ダッシュボードでプロジェクトを作る（リージョンは Tokyo。Postgres は 17 系 = `config.toml` の `major_version`）。
2. マイグレーションとシードを適用する（リポジトリのルートで。DB のパスワードを聞かれる）:

   ```bash
   supabase login
   supabase link --project-ref <project-ref> --workdir infra
   supabase db push --workdir infra --dry-run          # 適用されるものを確認
   supabase db push --workdir infra --include-seed     # 初回だけ --include-seed（キャラ・投稿・コメント）
   ```

   `seed.sql` は固定 UUID の INSERT なので投入は 1 回だけ（`--include-seed` はキャラクターエンジンの画像プール `seed_engine.sql` も入れる。URL は開発用のプレースホルダ）。
   以後のマイグレーションは `supabase db push --workdir infra`。
   投稿の時刻は投入時刻が基準で、予約投稿は時間とともにフィードに現れる。以後の投稿の追加は [06-operations.md](docs/handover/06-operations.md#投稿を追加する)。
3. **Auth をダッシュボードで `config.toml` と揃える**（必須。一覧は [supabase-auth.md](docs/handover/supabase-auth.md)）:
   - URL Configuration: Site URL = `https://<Web のドメイン>`、Redirect URLs に `https://<Web のドメイン>/auth/callback**`（`?next=` 付きを許可）
   - Email: **Confirm email を ON**、**Secure password change を ON**、**Email OTP Length = 6**、有効期限 900 秒
   - Email Templates: **Magic Link と Confirm signup の両方** を `infra/supabase/templates/magic_link.html` の内容にする（リンク + 6 桁コード。
     既定のテンプレートのままだとホーム画面の PWA からログインできない）
   - Email の通知: **Password changed** を有効にし、`infra/supabase/templates/password_changed_notification.html` の内容にする
   - SMTP: カスタム SMTP を設定する（Supabase 既定の送信サーバーは一般ユーザーに使えない）
   - CAPTCHA（Attack Protection）は **当面 OFF**（Web の対応後に hCaptcha で ON。先に ON にするとログインできなくなる。[ADR-0033](docs/adr/0033-auth-hardening-password-otp-captcha.md)）
   - `supabase config push` は使わない（ローカル用の `site_url` などまで本番に書き込まれる）
4. JWT の署名鍵: 新しいプロジェクト（非対称鍵 ES256）なら API は JWKS から公開鍵を自動取得するので設定不要。旧方式（HS256）のプロジェクトなら
   Project Settings → JWT の JWT Secret を API の `SUPABASE_JWT_SECRET` に設定する（[ADR-0007](docs/adr/0007-jwt-verification-jwks-and-hs256.md)）。
5. 控える値: Project URL、anon key（または publishable key）、DB の接続文字列（Direct connection、または Supavisor の session mode）。

### 2. API（Fly.io）

リポジトリの **ルート** で実行する（Docker のビルドコンテキストに `packages/personas` と `packages/prompts` が必要なため）。

```bash
fly auth login
fly apps create everkano-api        # 名前は apps/api/fly.toml の app。使われていたら fly.toml も書き換える

fly secrets set --config apps/api/fly.toml --stage \
  DATABASE_URL='postgresql://...?sslmode=verify-full&sslrootcert=/app/certs/supabase-ca.crt' \
  SUPABASE_URL='https://<project-ref>.supabase.co' \
  LLM_API_KEY='<OpenRouter または DeepSeek の API キー>' \
  CORS_ALLOW_ORIGINS='https://<Web のドメイン>'
#   必要に応じて: SUPABASE_JWT_SECRET（旧 HS256 のみ）/ EMBEDDING_API_KEY / SENTRY_DSN

fly deploy --config apps/api/fly.toml --dockerfile apps/api/Dockerfile
fly scale count app=1 worker=1 --config apps/api/fly.toml   # worker のマシンが無ければ（fly status で確認）
curl https://everkano-api.fly.dev/health
```

- `fly.toml` の `[processes]` で、同じイメージを `app`（uvicorn）と `worker`（`python -m app.worker`。キャラクターエンジンの返答後のジョブと定期実行）の 2 つの
  プロセスグループで動かす。`[env]` の `ENGINE_WORKER_ENABLED=false`・`ENGINE_SCHEDULER_ENABLED=false` で API はジョブを動かさず、`worker` はこの値に関係なく
  ワーカーとスケジューラを起動する。**worker が止まると記憶・約束・好感度・予定・自発メッセージが止まる**（返答は届く）（[ADR-0036](docs/adr/0036-engine-job-queue-and-scheduler.md)）。
- **公開前に** E6 の相談窓口（`packages/prompts/safety/resources.ja.yaml`）の番号・受付時間を公式サイトで確かめる（未検証の値。[06-operations.md](docs/handover/06-operations.md#相談窓口の番号の確認公開前定期)）。

- シークレット以外は `apps/api/fly.toml` の `[env]`（既定: `APP_ENV=staging`・`LLM_MODE=live`・`EMBEDDING_MODE=hash`）。本番は `APP_ENV = "production"`
  にする（`LLM_MODE=mock` は起動時に拒否され、`/docs` も無効になる）。
- `APP_ENV` が staging / production のとき、`SUPABASE_URL`（https 必須）や `CORS_ALLOW_ORIGINS` がローカルのまま、`LLM_MODE=live` で `LLM_API_KEY` が
  無い、などは起動時の検証で止まる（登録漏れの検出）。
- Supavisor の transaction mode（:6543）で接続するなら `DATABASE_STATEMENT_CACHE_SIZE = "0"`。
- `DATABASE_URL` は TLS 必須（ループバック以外の DB で `sslmode` が `require` / `verify-ca` / `verify-full` でない、または `PGSSLMODE` も無いと起動しない）。
  `verify-full` にはルート証明書をマシンに置く必要がある（`fly.toml` の `[[files]]`。手順は [apps/api/README.md](apps/api/README.md) の「DB への接続（TLS）」）。
  証明書を用意するまでの暫定は `?sslmode=require`。
- レート制限はプロセス内なので、台数を増やすと実質の上限も台数倍になる（[ADR-0018](docs/adr/0018-in-process-rate-limit.md)）。

### 3. Web（Vercel）

1. Vercel で GitHub のリポジトリをインポートし、Project Settings を次にする:
   - **Root Directory**: `apps/web`（「Include source files outside of the Root Directory in the Build Step」は有効のまま。`packages/shared` を使うため）
   - Framework Preset: Next.js（Install / Build Command は既定のまま）、Node.js Version: 22.x
2. Environment Variables（Production と Preview。詳細は[環境変数](#環境変数)）:

   | 変数                                                 | 値                                                                                  |
   | ---------------------------------------------------- | ----------------------------------------------------------------------------------- |
   | `ENABLE_EXPERIMENTAL_COREPACK`                       | `1`（`packageManager` の pnpm 10.33 を使わせる。`engine-strict` のため pnpm 9 では失敗する） |
   | `NEXT_PUBLIC_SUPABASE_URL`                           | `https://<project-ref>.supabase.co`                                                 |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY`                      | anon key（または publishable key）                                                  |
   | `NEXT_PUBLIC_API_BASE_URL`                           | `https://everkano-api.fly.dev`                                                      |
   | `NEXT_PUBLIC_SITE_URL`                               | `https://<Web のドメイン>`（Supabase の Site URL と同じ）                            |
   | `NEXT_PUBLIC_STORAGE_DRIVER`                         | `passthrough`（シードのプレースホルダ画像のまま）または `bunny`                       |
   | `NEXT_PUBLIC_CDN_BASE_URL`                           | `bunny` のとき: Pull Zone の URL（例 `https://everkano.b-cdn.net`）                   |
   | `BUNNY_TOKEN_AUTH_KEY` / `BUNNY_TOKEN_TTL_SECONDS`   | トークン認証を使う場合（サーバー専用。`NEXT_PUBLIC_` を付けない）                    |

   `NEXT_PUBLIC_*` はビルド時に埋め込まれるので、変えたら再デプロイする。
3. Deploy。`Unsupported engine` で失敗したら、ビルドログの pnpm / Node のバージョンと上の 2 つの設定を確認する。

### 4. 画像（Backblaze B2 + Bunny.net）

H4 により画像は Vercel / Supabase Storage に置かない。シードのプレースホルダ画像のまま運用するなら不要（`passthrough`）。

1. B2 にバケットを作り（非公開推奨）、画像を置く。有料投稿はプレビューと本体を **互いに推測できない別のキー** にする（[ADR-0006](docs/adr/0006-paid-post-private-assets.md)）。
2. Bunny.net で Pull Zone を作ってオリジンを B2 にする（非公開バケットならオリジンの S3 互換認証に B2 のアプリケーションキー）。リサイズ・ぼかしの
   パラメータを使うなら Bunny Optimizer を有効にする。URL を署名付きにするなら Token Authentication を有効にし、キーを Vercel の `BUNNY_TOKEN_AUTH_KEY` に。
3. DB の `characters.avatar_url` / `posts.image_url` / `post_private_assets.image_url` をオブジェクトキーに更新し、Vercel を `NEXT_PUBLIC_STORAGE_DRIVER=bunny` にして再デプロイ。
   手順の詳細は [06-operations.md](docs/handover/06-operations.md#画像を本番の-cdn-に移す)、仕組みは [ADR-0011](docs/adr/0011-storage-adapter-and-bunny-token-auth.md)。

### 5. URL の相互設定

- Supabase の Site URL / Redirect URLs が Web のドメインになっているか。
- API の `CORS_ALLOW_ORIGINS` に Web のオリジンが入っているか（変更は `fly secrets set --config apps/api/fly.toml CORS_ALLOW_ORIGINS=...`、再起動される）。
- Vercel の Preview（別の URL）でもログインと DM を使うなら、その URL も Redirect URLs と `CORS_ALLOW_ORIGINS` に足す。

### 6. 動作確認

1. `curl https://<API>/health` が `"db":"ok"`・`"llm_mode":"live"`。
2. スマホで Web を開き、メールのリンクと 6 桁コードの両方でログインできる（[supabase-auth.md の 7](docs/handover/supabase-auth.md#7-設定後の確認)）。
3. DM を送って返答が来る。SQL Editor で `select event_type, created_at from public.audit_logs order by id desc limit 10;` に `chat.request` / `chat.response`。
4. 受け入れ基準の残り（実機・実 LLM）は [docs/acceptance/report.md](docs/acceptance/report.md) のチェックリストで確認する。

## 環境変数

キー名とローカルの既定値は [.env.example](.env.example)（**値の入ったシークレットは書かない**）。ローカルでは `pnpm setup:env` が
`apps/web/.env.local`（`NEXT_PUBLIC_*` / `BUNNY_*`）と `apps/api/.env`（それ以外）に分けて書き出す。Web は `lib/env.ts`（公開値。全画面のバンドルに入るため
zod を使わない手書きの検証）/ `lib/env.server.ts`（サーバー専用。zod）、API は `app/core/config.py`（pydantic-settings）で起動時に検証し、不正なら起動しない
（API の検証エラーには入力値を出さない）。

### Web（Vercel / `apps/web/.env.local`）

| 変数                            | 必須                  | 例 / 既定                              | 説明                                                                                              |
| ------------------------------- | --------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `NEXT_PUBLIC_SUPABASE_URL`      | 必須                  | `http://127.0.0.1:54321`               | Supabase の URL                                                                                    |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | 必須                  | （`pnpm db:status` の `ANON_KEY`）      | anon key または publishable key（公開値）                                                          |
| `NEXT_PUBLIC_API_BASE_URL`      | 必須                  | `http://localhost:8000`                | Python API のベース URL                                                                            |
| `NEXT_PUBLIC_SITE_URL`          | 本番では設定          | `http://localhost:3000`                | マジックリンクの戻り先（`emailRedirectTo`）。未設定ならブラウザの origin                            |
| `NEXT_PUBLIC_STORAGE_DRIVER`    | 任意                  | `passthrough`                          | `passthrough`（DB の URL をそのまま）/ `bunny`（オブジェクトキー → CDN）                           |
| `NEXT_PUBLIC_CDN_BASE_URL`      | `bunny` のとき必須    | `https://everkano.b-cdn.net`           | Bunny.net Pull Zone の URL                                                                         |
| `BUNNY_TOKEN_AUTH_KEY`          | 任意（サーバー専用）  | 空                                     | 設定すると画像は `/media/*` 経由で署名 URL にリダイレクト（8 文字以上）                            |
| `BUNNY_TOKEN_TTL_SECONDS`       | 任意（サーバー専用）  | `3600`                                 | 署名 URL の有効期限（60〜604800 秒）                                                               |
| `NEXT_PUBLIC_ENABLE_SW`         | 任意                  | 空                                     | `1` で開発サーバーでも Service Worker を登録（本番ビルドでは常に登録）                             |
| `NEXT_PUBLIC_BUILD_ID`          | 任意（通常は設定しない） | 自動                                 | Service Worker の登録 URL（`/sw.js?v=`）に付けるデプロイごとの ID。未設定なら `VERCEL_DEPLOYMENT_ID` → `VERCEL_GIT_COMMIT_SHA` → `GITHUB_SHA` → ビルド時刻（`next.config.ts`）。設定するならデプロイごとに変える（[ADR-0032](docs/adr/0032-service-worker-versioning.md)） |

`NEXT_PUBLIC_MEDIA_SIGNED` は `next.config.ts` が `BUNNY_TOKEN_AUTH_KEY` の有無から自動で設定する（手で設定しない）。

### API（Fly.io / `apps/api/.env`）

| 変数                                            | 必須                                     | 既定                                                     | 説明                                                                                              |
| ----------------------------------------------- | ---------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `APP_ENV`                                       | 任意                                     | `local`                                                  | `local` / `staging` / `production`。production は `LLM_MODE=mock` を禁止し `/docs` を無効化        |
| `LOG_LEVEL`                                     | 任意                                     | `INFO`                                                   | 監査ログの stdout 複製は常に出る                                                                   |
| `DATABASE_URL`                                  | 必須（secret）                           | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` | Postgres（`postgres` ロール。RLS をバイパス → [ADR-0003](docs/adr/0003-api-db-connection-asyncpg.md)）。staging / production でループバック以外の DB なら `?sslmode=verify-full&sslrootcert=...`（最低 `require`、または `PGSSLMODE`）が無いと起動しない（[apps/api/README.md](apps/api/README.md) の「DB への接続（TLS）」） |
| `DATABASE_STATEMENT_CACHE_SIZE`                 | 任意                                     | `100`                                                    | Supavisor の transaction mode では `0`                                                             |
| `DATABASE_POOL_MIN_SIZE` / `DATABASE_POOL_MAX_SIZE` | 任意                                 | `1` / `10`                                               | 接続プール（台数 × 最大値が Supabase の上限内に）                                                   |
| `SUPABASE_URL`                                  | 必須（staging / production は https）    | `http://127.0.0.1:54321`                                 | JWKS の取得元と `iss` の既定                                                                       |
| `SUPABASE_JWT_SECRET`                           | 旧 HS256 のプロジェクトのみ（secret）    | 空                                                       | 設定時のみ HS256 を受け付ける                                                                      |
| `SUPABASE_JWT_AUDIENCE`                         | 任意                                     | `authenticated`                                          | `aud` の期待値                                                                                     |
| `SUPABASE_JWT_ISSUER`                           | 任意                                     | 空（= `{SUPABASE_URL}/auth/v1`）                          | `iss` が異なる場合（Docker から `host.docker.internal` 経由など）                                  |
| `JWKS_CACHE_TTL_SECONDS`                        | 任意                                     | `600`                                                    | 公開鍵のキャッシュ秒数                                                                             |
| `CORS_ALLOW_ORIGINS`                            | 必須（staging / production）             | `http://localhost:3000,...`                              | Web のオリジン（カンマ区切り）。production で `*` は不可                                            |
| `LLM_MODE`                                      | 任意                                     | `mock`                                                   | `live` / `mock`（[下記](#llm-と埋め込みのモードmock--live)）                                        |
| `LLM_BASE_URL`                                  | 任意                                     | `https://openrouter.ai/api/v1`                           | DeepSeek 直なら `https://api.deepseek.com/v1`                                                      |
| `LLM_API_KEY`                                   | `live` のとき必須（secret）              | 空                                                       |                                                                                                   |
| `LLM_MODEL`                                     | 任意                                     | `deepseek/deepseek-chat`                                 | DeepSeek 直なら `deepseek-chat`                                                                    |
| `LLM_MODEL_ANALYSIS` / `LLM_MODEL_PROACTIVE` / `LLM_MODEL_CAPTION` | 任意                  | 空（= `LLM_MODEL`）                                      | 用途別のモデル: 記憶の分析・要約・好感度の評価 / 自発メッセージ / 予定からの投稿のキャプション（[ADR-0035](docs/adr/0035-character-engine-architecture.md)） |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS`            | 任意                                     | `0.8` / `400`                                            | DM 返答の生成パラメータ                                                                            |
| `LLM_MOCK_STREAM_DELAY_MS`                      | 任意                                     | `0`                                                      | `LLM_MODE=mock` のストリーミングで数文字ごとに入れる待ち（0〜5000。表示・レイテンシの確認用）       |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES`       | 任意                                     | `30` / `2`                                               | LLM 1 回あたりのタイムアウト / 429・5xx・タイムアウト時のリトライ回数（埋め込みには使わない）      |
| `LLM_HTTP_REFERER` / `LLM_APP_TITLE`            | 任意                                     | 空 / `everkano`                                          | OpenRouter のときだけ送るヘッダー                                                                  |
| `EMBEDDING_MODE`                                | 任意                                     | `hash`                                                   | `live` / `hash`。**切り替えたら記憶の再埋め込みが必須**                                            |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL`        | 任意                                     | `https://api.openai.com/v1` / `text-embedding-3-small`    | OpenAI 互換の `/embeddings`                                                                        |
| `EMBEDDING_API_KEY`                             | `live` のとき必須（secret）              | 空                                                       |                                                                                                   |
| `EMBEDDING_DIMENSIONS`                          | 任意                                     | `1536`                                                   | DB の `vector(1536)` と一致必須                                                                    |
| `EMBEDDING_TIMEOUT_SECONDS` / `EMBEDDING_MAX_RETRIES` | 任意                               | `5` / `1`                                                | 埋め込み API 1 回あたりのタイムアウト / リトライ回数。`/chat` の記憶検索は間に合わなければ省略して返答する（`CHAT_DEADLINE_SECONDS` の半分未満） |
| `MEMORY_SHORT_TERM_TURNS`                       | 任意                                     | `30`                                                     | 短期メモリのターン数（×2 件）                                                                      |
| `MEMORY_SUMMARY_TRIGGER_TURNS`                  | 任意                                     | `50`                                                     | 未要約がこの ×2 件を超えたら中期要約                                                               |
| `MEMORY_IMPORTANCE_THRESHOLD`                   | 任意                                     | `0.6`                                                    | 記憶を保存する最低重要度                                                                           |
| `MEMORY_RETRIEVAL_TOP_K`                        | 任意                                     | `5`                                                      | 未使用（MVP の検索件数。エンジンの記憶の件数は `MemoryConfig`）                                                                                          |
| `MEMORY_DEDUP_SIMILARITY`                       | 任意                                     | `0.92`                                                   | 重複とみなすコサイン類似度                                                                         |
| `MEMORY_MAX_PER_CHARACTER`                      | 任意                                     | `500`                                                    | ユーザー × キャラあたりの記憶の上限。ユーザーの追加は上限で 422、自動抽出・要約は重要度の低い自動記憶と入れ替える |
| `RATE_LIMIT_CHAT_PER_MINUTE`                    | 任意                                     | `20`                                                     | ユーザー単位（プロセス内）                                                                         |
| `RATE_LIMIT_COMMENTS_PER_MINUTE`                | 任意                                     | `10`                                                     | `/comments` と `/comments/generate` の合計                                                          |
| `RATE_LIMIT_MEMORIES_PER_MINUTE`                | 任意                                     | `30`                                                     | `POST` / `PATCH /memories`（埋め込み API を呼ぶ）                                                   |
| `MAX_REQUEST_BODY_BYTES`                        | 任意                                     | `65536`                                                  | リクエスト本文の上限（1024〜10485760）。超えたら本文を読まずに 413 `validation_error`（認証より前） |
| `COMMENT_AUTO_REPLY_PROBABILITY`                | 任意                                     | `1.0`                                                    | コメントにキャラが自動返信する確率                                                                 |
| `AUDIT_LOG_PROMPTS`                             | 任意                                     | `true`                                                   | 監査ログにプロンプト全文を含める                                                                   |
| `CHAT_DEADLINE_SECONDS`                         | 任意                                     | `38`                                                     | `/chat`・`/chat/stream` の文脈の組み立て + 返答の生成の締め切り。Web のタイムアウト 45 秒より短くする（[ADR-0019](docs/adr/0019-chat-deadline.md)） |
| `CHAT_STREAM_HEARTBEAT_SECONDS`                 | 任意                                     | `10`                                                     | `POST /chat/stream` でイベントが無い間に送るキープアライブの間隔（秒）                            |
| `ENGINE_MEMORY_ENABLED` / `ENGINE_CALENDAR_ENABLED` / `ENGINE_AFFINITY_ENABLED` / `ENGINE_PROACTIVE_ENABLED` | 任意 | `true`                      | キャラクターエンジンの各仕組みの有効 / 無効（すべて false = 評価ハーネスの「素の LLM」） |
| `ENGINE_CONTEXT_TIMEOUT_SECONDS`                | 任意                                     | `1.5`                                                    | 文脈の組み立ての締め切り。間に合わない要素は省いて返答する（E8）                                  |
| `ENGINE_WORKER_ENABLED` / `ENGINE_SCHEDULER_ENABLED` | 任意（Fly.io は `fly.toml` で false） | `true`                                                  | ジョブのワーカー / 定期実行を API のプロセスの中で動かすか。本番は `worker` プロセスグループ（`python -m app.worker` はこの値に関係なく両方を動かす） |
| `ENGINE_WORKER_CONCURRENCY` / `ENGINE_WORKER_POLL_INTERVAL_SECONDS` | 任意                  | `2` / `1`                                                | ワーカーが同時に処理するジョブの数 / 空のときの確認間隔（秒）                                    |
| `ENGINE_JOB_MAX_ATTEMPTS` / `ENGINE_JOB_BACKOFF_BASE_SECONDS` / `ENGINE_JOB_BACKOFF_MAX_SECONDS` | 任意       | `5` / `30` / `3600`                                   | ジョブの再試行の回数と指数バックオフ（上限に達したら `dead`・audit `engine.job_dead`）             |
| `ENGINE_JOB_TIMEOUT_SECONDS` / `ENGINE_JOB_LOCK_TIMEOUT_SECONDS` / `ENGINE_JOB_RETENTION_DAYS` | 任意         | `300` / `600` / `7`                                    | 1 ジョブの実行時間の上限 / 実行中のまま止まったジョブを戻すまで / 完了したジョブを消すまでの日数   |
| `ENGINE_POST_TURN_DELAY_SECONDS` / `ENGINE_POST_TURN_MAX_TURNS` | 任意                       | `180` / `10`                                             | 返答の後の分析（記憶・約束・好感度）を何秒後にまとめて行うか（続けて話すと後ろへ。最大 6 倍）/ 1 回の最大ターン数（[ADR-0046](docs/adr/0046-engine-cost-and-latency.md)） |
| `ENGINE_SCHEDULER_POLL_INTERVAL_SECONDS`        | 任意                                     | `30`                                                     | 定期実行の期限を確認する間隔（リーダーは `pg_try_advisory_lock`）                                 |
| `ENGINE_CALENDAR_ENSURE_INTERVAL_SECONDS` / `ENGINE_CALENDAR_DAYS_AHEAD` / `ENGINE_CALENDAR_TICK_INTERVAL_SECONDS` | 任意 | `3600` / `7` / `300`                             | 予定の生成の間隔と何日先まで / 状態・予定の完了・投稿の間隔                                      |
| `ENGINE_PROACTIVE_SCAN_INTERVAL_SECONDS`        | 任意                                     | `600`                                                    | 自発メッセージの判定の間隔                                                                        |
| `ENGINE_AFFINITY_DAILY_HOUR_JST`                | 任意                                     | `4`                                                      | 好感度の日次処理（緊張の減衰など）の時刻（JST）                                                   |
| `ENGINE_SCHEDULE_NAMESPACE`                     | 任意（通常は空）                         | 空                                                       | 定期実行の記録とリーダーのロックの名前空間（評価ハーネス・テストが共有の DB で時計を早送りするとき） |
| `ENGINE_PROACTIVE_DAILY_LIMIT` / `ENGINE_PROACTIVE_QUIET_START` / `ENGINE_PROACTIVE_QUIET_END` | 任意          | `3` / `0` / `7`                                        | 自発メッセージの 1 ユーザー 1 日の上限（全キャラ合計）と、既定の送らない時間帯（JST。ユーザーが変更可。E4） |
| `ENGINE_PRICE_TABLE_JSON`                       | 任意                                     | 空（= DeepSeek V3 の価格）                               | 評価ハーネスの費用の推計の価格表（`{"<モデル>": {"input", "cached_input", "output"}}`、円 / 100 万トークン） |
| `SAFETY_RESOURCES_PATH`                         | 任意（`.env.example` ではコメントアウト） | リポジトリ内の `packages/prompts/safety/resources.ja.yaml`（Docker は `/srv/everkano/safety/...`） | E6 の相談窓口と返答文面の YAML（起動時に検証。**番号は公開前に要確認**）                         |
| `CLIENT_IP_HEADER`                              | 任意（Fly.io は `fly.toml` で設定済み）  | 空                                                       | ログに記録するクライアントIPの取得元（Fly.io: `Fly-Client-IP`）。X-Forwarded-For の先頭は偽装できるため使わない |
| `PERSONAS_DIR` / `PROMPTS_DIR`                  | 任意（`.env.example` ではコメントアウト） | リポジトリ内の `packages/`（Docker は `/srv/everkano/...`） | ペルソナ YAML / テンプレートの場所。空の値を書かないこと                                          |
| `SENTRY_DSN`                                    | 任意（secret）                           | 空                                                       | API の例外を Sentry に送る（Web は未対応）。トークン・本文・ローカル変数・ログのパンくずは送らない（[ADR-0034](docs/adr/0034-supply-chain-and-telemetry-minimization.md)） |

`.env.example` に無い変数: `FORWARDED_ALLOW_IPS`（uvicorn が X-Forwarded-* を信頼するプロキシ。Dockerfile の既定は `127.0.0.1`、`fly.toml` で `*`）、`PGSSLMODE`（`DATABASE_URL` に `sslmode` が無いときの TLS の指定）、
`TEST_DATABASE_URL` / `REQUIRE_TEST_DB`（pytest の統合テストの接続先 / 接続できないときに skip せず失敗させる）、`NEXT_DIST_DIR`（Web の出力先。dev サーバーの並行起動用。
既定以外にすると Next.js が `apps/web/tsconfig.json` を書き換えるので、その変更はコミットしない）、`NEXT_PUBLIC_BUILD_ID`（上表）、
`PYTHON_IMAGE` / `UV_IMAGE`（API のイメージのビルド引数。digest 固定）/ `API_DOCKER_*`（`docker-compose.yml`）、`E2E_*`（[apps/web/e2e/README.md](apps/web/e2e/README.md)）、`ENABLE_EXPERIMENTAL_COREPACK`（Vercel）。

## LLM と埋め込みのモード（mock / live）

| モード                 | 動き                                                                                                                                  | 使う場面                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `LLM_MODE=mock`        | 外部を呼ばない。ペルソナの口調例・一人称 / 呼び方・今の状況と、検索された記憶・約束から決定的に返答。記憶の分析・好感度の評価・自発メッセージ・キャプションは各モジュールのルールのモック | ローカル・CI・E2E・評価ハーネス（**本番は禁止**） |
| `LLM_MODE=live`        | OpenAI 互換の Chat Completions（OpenRouter 経由 DeepSeek-V3 / DeepSeek 直）。リトライ付き                                              | staging・本番                        |
| `EMBEDDING_MODE=hash`  | 文字 n-gram のハッシュで 1536 次元（外部を呼ばない。語彙の重なりを捉える程度）                                                          | ローカル・CI。キーが無い間の staging  |
| `EMBEDDING_MODE=live`  | OpenAI 互換の `/embeddings`（既定 `text-embedding-3-small`）                                                                           | 本番                                 |

- モックの返答は品質の確認にならない。**実際の会話の品質（文脈・口調・記憶の自然な想起）は staging（`live`）と評価ハーネスの live 実行で確認する**。
  live に切り替える前に `LLM_MODEL` と価格表を確かめる（[06-operations.md](docs/handover/06-operations.md#live-の-llm-に切り替える前モデルや価格が変わったとき)）。
- 埋め込みのモード・モデルを切り替えたら、直後に既存の記憶を再埋め込みする（`apps/api/scripts/reembed_memories.py`。
  [06-operations.md](docs/handover/06-operations.md#埋め込み設定の切り替え)）。
- 現在のモードは `GET /health` の `llm_mode` / `embedding_mode` で分かる。詳細は [ADR-0008](docs/adr/0008-llm-embedding-providers-and-mock.md)。

## ドキュメント

| ドキュメント                                                                | 内容                                                                      |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| [docs/README.md](docs/README.md)                                            | ドキュメントの一覧                                                        |
| [docs/handover/](docs/handover/README.md)                                   | 引き継ぎ資料（構成・データフロー・データモデル・API・メモリ / モデレーション・運用・セキュリティ・開発ガイド） |
| [docs/adr/](docs/adr/README.md)                                             | 設計判断の記録（ADR-0001〜0049。0035 以降がキャラクターエンジン v1.0）     |
| [docs/eval/](docs/eval/README.md)                                           | キャラクターエンジンの評価ハーネスの使い方・指標・結果の推移               |
| [docs/character-engine-report.md](docs/character-engine-report.md)          | キャラクターエンジン v1.0 の最終報告（全指標の結果・コスト・レイテンシ・残っている確認事項） |
| [docs/acceptance/report.md](docs/acceptance/report.md)                      | 受け入れ基準 A1〜A16 の検証結果と、残りの確認手順                          |
| [docs/acceptance/inspection-report.md](docs/acceptance/inspection-report.md) | 納品前の検査の報告（指摘の件数・修正前後の計測値・修正した項目・未対応の項目と推奨する対応） |
| [docs/api/openapi.json](docs/api/openapi.json)                              | Python API の OpenAPI                                                     |
| [apps/web/README.md](apps/web/README.md) / [apps/api/README.md](apps/api/README.md) | Web / API の構成と実装ルール                                      |
| [packages/personas/README.md](packages/personas/README.md) / [packages/prompts/README.md](packages/prompts/README.md) | キャラクター・シード / プロンプト                 |
| [infra/supabase/tests/README.md](infra/supabase/tests/README.md) / [scripts/README.md](scripts/README.md) | DB テスト / 補助スクリプト                                   |
| [LICENSE](LICENSE) / [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) / [SECURITY.md](SECURITY.md) | 権利表示（非公開）/ 依存パッケージのライセンス一覧 / 脆弱性の報告窓口と対応の目安 |

開発の約束（Conventional Commits、PR テンプレート、スキーマ変更・API 追加の手順、ブランチ保護）は [docs/handover/08-dev-guide.md](docs/handover/08-dev-guide.md)。
