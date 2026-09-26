# 08. 開発ガイド

環境構築はルートの [README.md](../../README.md)。ここでは、変更を入れるときの約束と手順をまとめる。

## 約束

| 対象       | ルール                                                                                                                                                                  |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 共通       | UI 文言は日本語、識別子は英語、コメントは日本語でよい。エラーは握りつぶさない（ログ + ユーザー向けメッセージ）。シークレットはコミットしない（新しい変数は `.env.example` にキー名だけ） |
| TypeScript | strict、`any` 禁止（やむを得ない場合は理由をコメント）。ESLint（`eslint-config-next`）+ Prettier（`prettier-plugin-tailwindcss`）                                          |
| Python     | 型ヒント必須、`ruff check` / `ruff format`（行 120）、`mypy --strict`（`app/`・`evals/`）。全エンドポイントに Pydantic スキーマ                                           |
| Web        | 読み取りは `getSupabaseBrowserClient()`（RLS）。`characters` は必ず `PUBLIC_CHARACTER_COLUMNS` で列を指定。supabase-js のエラーは `if (error) throw toAppError(error);`。ユーザーが書くテキストは必ず `api.*`（`lib/api`）経由。画面の `useQuery` は `xxxQueryOptions()` を使い、リンクの `onPointerDown` から同じ設定で先読みする（読み取りだけ）。画像は `<CdnImage>` / `<Avatar>`（`next/image` 禁止）。色はトークン（`bg-ig-bg` など。読ませる文字の青・赤は `text-ig-blue-text` / `text-ig-red-text`、`ig-blue` / `ig-red` は塗り・アイコン専用）。(main) の画面は自分で `<main>` を出さず、h1 を 1 つ。エラー文は句点で終え、再試行の案内は「しばらくしてから再度お試しください。」。開発専用のページは `page.dev.tsx`。詳細は [apps/web/README.md](../../apps/web/README.md#実装ルール機能担当向け)、[ADR-0029](../adr/0029-web-network-failure-policy.md)〜[ADR-0031](../adr/0031-web-ui-accessibility-and-dev-only-pages.md) |
| API        | **DB クエリは必ず検証済み `user_id` でスコープ**（他人のものは 404）。ユーザー由来テキストは Gate #1 を通す。重要な操作は監査ログに残す（payload に `request_id` は自動で入る） |
| DB         | テーブル・列・関数の追加は「RLS 有効化 + 最小権限の grant + ポリシー + pgTAP テスト」をセットで。`database.types.ts` を再生成                                             |
| エンジン   | `apps/api/app/engine/` のコードは `datetime.now()` を呼ばず、渡された `now`（`Clock`）を使い、DB に書く日時も明示する（評価ハーネスの早送り）。モジュール間は `engine/types.py` の Protocol だけでつなぐ。状態の変化は必ず監査ログ（`at=now`。E9）。好感度のパッケージには課金・投稿・いいねのモジュール・テーブルを持ち込まない（E1。構造テストが落ちる）。重い処理（LLM の分析）は返答の経路に入れず、ジョブにする（E8）。プロンプト・調整値を変えたら評価ハーネスを再実行して記録する |
| スコープ   | 決済・画像生成・TTS / 音声・ユーザー投稿・フォロー・通知・管理画面・多言語は作らない（仕様書 §12。`check-scope.sh` が検出）                                               |

## よく使うコマンド

| コマンド                                             | 内容                                                                  |
| ---------------------------------------------------- | --------------------------------------------------------------------- |
| `pnpm lint` / `pnpm typecheck` / `pnpm test`         | 全パッケージ（ESLint・ruff・Prettier / tsc・mypy / vitest・pytest）    |
| `pnpm format`                                        | Prettier で整形（`*.md` / `*.sql` / `*.yaml` / `apps/api` は対象外）   |
| `pnpm db:test` / `pnpm db:types` / `pnpm db:types:check` | pgTAP / DB 型の再生成 / 比較                                       |
| `pnpm check:secrets` / `pnpm check:scope`            | シークレット / スコープ外機能                                          |
| `pnpm personas:validate`                             | ペルソナ YAML・シードの検証                                            |
| `pnpm --filter @everkano/api openapi`                | `docs/api/openapi.json` を再生成（`openapi:check` で比較）             |
| `pnpm --filter @everkano/web e2e`                    | E2E（サーバーを起動してから。下記）                                    |
| `(cd apps/api && uv run python -m app.worker)`       | キャラクターエンジンのワーカーとスケジューラだけを動かす（下の「キャラクターエンジンの開発」） |
| `(cd apps/api && uv run python -m evals.run --days 30)` | 評価ハーネス（[docs/eval/README.md](../eval/README.md)）             |
| `(cd apps/api && uv run mypy app evals)`             | 型検査（CI・`pnpm typecheck` と同じ）                                  |
| `bash scripts/tests/check-scope.test.sh`             | `check-scope.sh` の回帰テスト（E1 / E2 の例外。[ADR-0044](../adr/0044-check-scope-compliance-allowlist.md)） |

## エンドポイントを追加する

例: `GET /characters/{id}/stats` を足す場合。上から順に。

1. **Pydantic モデル**: `apps/api/app/models/<領域>.py` にリクエスト / レスポンスを定義（`ApiModel` を継承、本文には `NoControlChars` と長さ制限）。
2. **サービス**: `apps/api/app/services/<領域>.py` に処理を書く。SQL は定数にし、**`where ... user_id = $n` で所有者を絞る**。
   ユーザー由来のテキストなら `Moderator.check()` → ヒット時は `moderation.flag` を記録して 422 `moderation_blocked`。
   監査ログが必要なら `AuditLogger.log()`（新しい `event_type` は `services/audit.py` の `AuditEventType` に追加し、[ADR-0013](../adr/0013-audit-log.md) の表も更新）。
   新しいサービスは `app/container.py` の `Services` / `build_services` に登録する。
3. **ルーター**: `apps/api/app/routers/<領域>.py`。認証は `CurrentUserDep`、レート制限が必要なら `Depends(RateLimit("<bucket>"))` 相当
   （`container.py` の `ChatRateLimitedUser` などの型を参照。バケットは `container.py` の `SlidingWindowRateLimiter` の辞書と `app/core/config.py` の設定に追加）。`responses=` に `ERROR_RESPONSES` / `NOT_FOUND_RESPONSE` を付ける。
   `app/main.py` の `create_app()` で `include_router`。
4. **共有の TS 型**: `packages/shared/src/api.ts` に同じ形（snake_case）の interface を追加。
5. **契約テスト**: `apps/api/tests/test_openapi_contract.py` は **`api.ts` を読んで** OpenAPI の components と突き合わせる（interface の過不足・フィールド名・必須・
   型・null 許容・エラーコード）ので、モデルの写しを書く必要は無い。新しいパスは同じファイルの `test_paths` の `expected` に追加する。
6. **テスト**: 単体テストと、`apps/api/tests/integration/` に統合テスト（**他人のリソースで 404 になるテストを必ず書く**。認証無しで 401 も）。
7. **OpenAPI**: `pnpm --filter @everkano/api openapi` で `docs/api/openapi.json` を再生成してコミット（CI が差分を検出する）。
8. **Web クライアント**: `apps/web/lib/api/client.ts` の `createApiClient` にメソッドを追加（`call<T>({ method, path, body, ... })`）し、
   ファイル末尾の既定エクスポート `api` にも追加。LLM を呼ぶものはタイムアウトを `CHAT_TIMEOUT_MS` に。`client.test.ts` にテスト。
9. **クエリ / 画面**: `apps/web/lib/queries/` に TanStack Query のフックを作り（キーは `queries/keys.ts`）、コンポーネントから使う。
   失敗時は `useToast().error(getErrorMessage(error))`。
10. **ドキュメント**: [04-api.md](04-api.md) の表、必要なら ADR。

確認: `pnpm lint && pnpm typecheck && pnpm test && pnpm --filter @everkano/api openapi:check`。

## DB スキーマを変える

```bash
supabase migration new <name> --workdir infra        # infra/supabase/migrations/<timestamp>_<name>.sql が作られる
# SQL を書く（RLS 有効化・grant・ポリシー・必要ならトリガー。既存のマイグレーションは書き換えない）
supabase migration up --local --workdir infra         # ローカル DB に未適用分だけ適用（作り直さない）
pnpm db:types                                         # packages/shared/src/database.types.ts を再生成
pnpm db:test                                          # 00_privileges の許可リストが失敗したら、意図どおりか確認して更新
```

- 最初から作り直して確認したいときは `pnpm db:reset`（**ローカルの全データが消える**。共有の開発 DB では使わない）。
- Realtime の publication にテーブルを足すときは、anon への主キー列 grant もセット（[ADR-0016](../adr/0016-realtime-anon-primary-key-grant.md)）。
- API の SQL（`apps/api/app/services/`）の列名も合わせる（型生成は Web 用のみ）。統合テストで検出される。
- 本番への適用は `supabase db push --workdir infra`（[06-operations.md](06-operations.md#デプロイとロールバック)）。

## ペルソナ・シード・プロンプトを変える

| 変えるもの                    | 手順                                                                                                                              |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| ペルソナ YAML / `seed/feed.yaml` | `pnpm --filter @everkano/personas seed:generate` → `pnpm personas:validate`（`engine:` セクションは `engine_checks.py` の規則も検査）。`seed.sql` は直接編集しない。`engine:` を変えたら `(cd apps/api && uv run pytest tests/engine/calendar/test_real_personas.py -q)` と評価ハーネス |
| プロンプト（`packages/prompts/templates/*.ja.txt`） | プレースホルダは [packages/prompts/README.md](../../packages/prompts/README.md)。`(cd apps/api && uv run pytest tests/test_prompt.py -q)`。DM・分析・評価のプロンプトを変えたら評価ハーネスを再実行して `docs/eval/` に記録する |
| 相談窓口（`packages/prompts/safety/resources.ja.yaml`） | 起動時に形式を検証する。番号・受付時間は公式サイトで確かめる（[06-operations.md](06-operations.md#相談窓口の番号の確認公開前定期)）。`(cd apps/api && uv run pytest tests/engine/core/test_safety.py -q)` |
| 画像プール（`infra/supabase/seed_engine.sql`） | 直接編集してよい（固定 UUID・`on conflict do nothing`）。タグは `TAG_VOCABULARY` の語彙だけ、全タグに共通の画像を 3 枚以上（`tests/engine/calendar/test_captions.py`・`test_tag_vocabulary.py`） |
| モデレーション語彙・E6・OutputGuard | [05-memory-and-moderation.md](05-memory-and-moderation.md#語を追加調整する)・[安全対応（E6）](05-memory-and-moderation.md#安全対応e6)                     |

YAML とテンプレートは API と worker の起動時に読み込まれるので、変更後は両方を再起動する（本番は再デプロイ）。

## テスト

| 種類            | 場所                                              | 件数（2026-09-26） | 実行                                         | CI  |
| --------------- | ------------------------------------------------- | ------------------ | -------------------------------------------- | --- |
| Web 単体        | `apps/web/**/*.test.ts`（vitest）                 | 43 ファイル / 432  | `pnpm --filter @everkano/web test`           | ✓   |
| API 単体 + 統合 | `apps/api/tests/`（統合は `tests/integration/`、エンジンは `tests/engine/<モジュール>/`、評価ハーネスは `tests/evals/`） | 1,539（うちエンジン 1,074・評価ハーネス 54・統合 65） | `(cd apps/api && uv run pytest -q)`           | ✓   |
| DB（pgTAP）     | `infra/supabase/tests/database/`                  | 14 ファイル / 286  | `pnpm db:test`                                | ✓   |
| Auth 設定       | `infra/supabase/tests/auth/signup_hardening.py`   | —                  | `python3 infra/supabase/tests/auth/signup_hardening.py` | ✓（`--static-only`） |
| `check-secrets.sh` / `check-scope.sh` の回帰 | `scripts/tests/check-secrets.test.sh`・`check-scope.test.sh` | —     | `bash scripts/tests/<名前>.test.sh`          | ✓   |
| E2E（Playwright）| `apps/web/e2e/`                                  | 16 spec / 86 × 2 端末 | 下記                                       | main への push・毎晩・手動 |
| 評価ハーネス    | `apps/api/evals/`                                 | 6 シナリオ          | [docs/eval/README.md](../eval/README.md)     | —（手動。結果を `docs/eval/` に記録） |

- API の統合テストはローカル Supabase の Postgres に自前のユーザー・データを作り、終了時に削除する。DB に接続できないとき、ローカルでは **skip**
  になるので、`pnpm db:start` 済みで実行し、`skipped` の件数を確認する（接続先は `TEST_DATABASE_URL`）。`REQUIRE_TEST_DB=1`（未設定なら
  環境変数 `CI=true` のとき有効）では skip せずに失敗する（所有者チェックなどのセキュリティのテストが黙って skip され、CI が成功扱いになるのを防ぐ）。
- pgTAP の各ファイルは `begin; ... rollback;` で完結し、DB に何も残さない。

### E2E の実行

テストはサーバーを起動しない。3 つのターミナルで:

```bash
# A: API（エンジンのスケジューラは止め、返答の後のジョブの待ちを 1 秒にする。CI と同じ）
cd apps/api
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres SUPABASE_URL=http://127.0.0.1:54321 \
LLM_MODE=mock EMBEDDING_MODE=hash CORS_ALLOW_ORIGINS=http://localhost:3000 APP_ENV=local \
ENGINE_SCHEDULER_ENABLED=false ENGINE_POST_TURN_DELAY_SECONDS=1 \
  uv run uvicorn app.main:app --port 8000

# B: Web（本番ビルド。apps/web/.env.local は pnpm setup:env で作成済み）
cd apps/web
NEXT_PUBLIC_SITE_URL=http://localhost:3000 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm build
NEXT_PUBLIC_SITE_URL=http://localhost:3000 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm start -p 3000

# C: E2E（初回は pnpm --filter @everkano/web exec playwright install chromium）
pnpm --filter @everkano/web e2e                     # 全部（iphone + android）
pnpm --filter @everkano/web e2e --project=iphone e2e/memory.spec.ts
pnpm --filter @everkano/web exec playwright show-report
```

詳細（環境変数・テストデータの扱い・スクリーンショットの更新）は [apps/web/e2e/README.md](../../apps/web/e2e/README.md)、最新の結果は
[docs/acceptance/e2e-results.md](../acceptance/e2e-results.md)。E2E はモック LLM の決定的な返答を前提にしている。スケジューラを動かしたままだと、テストの途中で
キャラの予定からの投稿・自発メッセージが増えてフィードの並びなどのテストが不安定になる。返答の後のジョブの既定の待ち（180 秒）のままだと「覚えました」の E2E が待ちきれない。

**CI**: `.github/workflows/ci.yml` の `e2e` ジョブ（main への push・毎晩 03:17 JST の定期実行・Actions → CI → Run workflow の手動実行。PR では動かない）。Realtime と Mailpit を含めて
`supabase start --workdir infra` → `pnpm install` / `uv sync` → `playwright install --with-deps chromium` → `setup-env.sh --force` → API（`ENGINE_SCHEDULER_ENABLED=false`・
`ENGINE_POST_TURN_DELAY_SECONDS=1`）と Web（本番ビルド）をバックグラウンド起動 → `pnpm --filter @everkano/web e2e` → `playwright-report` をアーティファクトに保存。
GitHub 上ではまだ一度も実行していないので、最初の実行結果を確認すること（172 件。`hydration.spec.ts` は CPU を遅くして繰り返し読み込むので数分かかる）。

## キャラクターエンジンの開発

構成は [01-architecture.md](01-architecture.md#キャラクターエンジンの構成)、判断の理由は [ADR-0035](../adr/0035-character-engine-architecture.md)〜[ADR-0049](../adr/0049-prompt-order-and-prefix-cache.md)。

### worker をローカルで動かす

既定（`ENGINE_WORKER_ENABLED=true`・`ENGINE_SCHEDULER_ENABLED=true`）では、`uv run uvicorn app.main:app` の API のプロセスの中でジョブと定期実行も動く（何もしなくてよい）。
本番と同じ分け方を試すときは:

```bash
cd apps/api
# ターミナル A: API（ジョブ・定期実行を動かさない）
ENGINE_WORKER_ENABLED=false ENGINE_SCHEDULER_ENABLED=false uv run uvicorn app.main:app --port 8000
# ターミナル B: worker（環境変数に関係なくワーカーとスケジューラを起動。片方だけなら --no-worker / --no-scheduler）
uv run python -m app.worker
```

- 返答の後のジョブは既定で 180 秒後に動く（`ENGINE_POST_TURN_DELAY_SECONDS`）。手元で「覚えました」をすぐ見たいときは `ENGINE_POST_TURN_DELAY_SECONDS=1`。
- `LLM_MOCK_STREAM_DELAY_MS=40` などにすると、モックでもストリーミングの表示を確かめられる。
- 定期実行の記録（`engine_schedules`）とリーダーのロックは共有の DB で 1 つ。テスト・評価ハーネスは名前空間（`ENGINE_SCHEDULE_NAMESPACE`）で分けている。
- ローカル DB のジョブ・予定の様子は [06-operations.md の SQL](06-operations.md#キャラクターエンジンの状態の調べ方) で見られる。

### エンジンのテスト

```bash
cd apps/api
uv run pytest tests/engine -q                   # すべて（統合テストはローカル Supabase を使う）
uv run pytest tests/engine/core -q              # ジョブ・スケジューラ・パイプライン・SSE・安全対応・OutputGuard・文脈
uv run pytest tests/engine/memory -q            # 記憶（分析・適用・日付・ランキング・検索・注入の防止・API）
uv run pytest tests/engine/calendar -q          # カレンダー（世界の時計・生成・状態・キャプション・DB・実際のペルソナ）
uv run pytest tests/engine/affinity -q          # 好感度（E1 の構造テスト・操作の検知・評価・段階・シミュレーション）
uv run pytest tests/engine/proactive -q         # 自発メッセージ（規則・文面・走査・設定 API）
```

- テストは `create_app(..., clock=ManualClock(...))` と `app/engine/pipeline_driver.py` の `EngineDriver` で時計を進める（ジョブは `worker.run_until_idle(now=...)`、定期実行は
  `scheduler.run_due(now=...)`）。共有の DB の他のデータに触れないよう、対象のキャラ・ユーザー（`EngineScope`）と名前空間を絞る。`jobs.cleanup` は動かさない（既定）。
- モジュールを差し替えるときは `create_app(engine_overrides=EngineOverrides(memory=...))`。

### 評価ハーネス

```bash
cd apps/api
uv run python -m evals.run --days 30                                   # 素の LLM とエンジンの両方・全シナリオ（mock）。結果を docs/eval/ に書く
uv run python -m evals.run --days 3 --scenario office_worker --scenario-characters-only \
  --min-fact-age-days 0 --no-write --skip-e1-tests                     # 速い確認（結果を書かない）
uv run --with tokenizers python -m evals.run --days 30 --tokenizer <tokenizer.json>   # 費用を本番のトークナイザで数える
uv run python -m evals.run --llm live --days 30 --max-cost-jpy 3000   # live（LLM_API_KEY が必要。費用の上限で止まる）
uv run python -m evals.cleanup --leftovers                             # 中断などで残った評価のデータを消す
```

詳細（シナリオ・指標・判定のプロンプト・仮定・結果の読み方）は [docs/eval/README.md](../eval/README.md)、設計は [ADR-0045](../adr/0045-evaluation-harness.md)。
プロンプト・調整値・ペルソナの `engine:` を変えたら再実行し、`docs/eval/results/` と `history.md` の記録をコミットする（エンジン仕様書 §9.3）。

### ペルソナの `engine:` セクションを追加・変更する

1. 書き方は [packages/personas/README.md](../../packages/personas/README.md) の「`engine:` セクション」と「キャラクターを追加する手順」（ルーティンは曜日ごとに 24 時間を重なり・すき間なく、
   単発の出来事 8 件以上、行事への反応、段階ごとの呼び方 `{name}`・口調、好感度の感度、自発メッセージの傾向）。
2. `pnpm personas:validate`（API の `EngineProfile` で読み込み、`packages/personas/scripts/engine_checks.py` の規則: ルーティンの重なり・すき間、画像タグの語彙、行事のキー、
   E2 / E3 / Gate #1 の語、責める言い方、期待投稿数の上限、ヤンデレの独占欲・人妻の `max_stage` など）。
3. `(cd apps/api && uv run pytest tests/engine/calendar/test_real_personas.py -q)`（1 年分の生成で一貫性の error が 0 件）。テンプレートだけを見るなら
   `app/engine/calendar/consistency.py` の `lint_life_spec()`。
4. 評価ハーネスで想起・状態の反映・好感度の進み方が崩れていないか確かめる。

### ジョブの種類・定期実行・監査ログのイベントを足す

- ジョブ: ハンドラ（`JobContext` を受け取り `JobResult` を返す。再試行で二重に処理しないよう冪等に）を `JobRegistry.register(kind, handler)` で登録する（`engine/jobs/handlers.py`）。
  登録は `PgJobQueue.enqueue(kind, payload, run_at=..., dedupe_key=...)`。入力の不備など再試行しても無駄な失敗は `PermanentJobError`。
- 定期実行: `app/container.py` の `periodic_tasks()` に `PeriodicTask(name, run, every(...) / daily_at_jst(h))` を足す。タスクは「今の時刻」で必要な処理をする（取りこぼした回を
  さかのぼって実行しない）。評価・テストのために対象を絞れる引数（`character_ids` / `user_ids`）を用意する。
- 監査ログのイベント: `app/services/audit.py` の `AuditEventType` に足し、[ADR-0035](../adr/0035-character-engine-architecture.md) の表も更新する（新しい ADR で追補）。

## コミットと PR

- **Conventional Commits**: `<type>(<scope>): <日本語の要約>`。type は `feat` / `fix` / `docs` / `test` / `chore` / `refactor` / `perf` / `ci`、
  scope は `web` / `api` / `db` / `content` など（例: `feat(web): DM一覧・DM会話・メモリパネルを実装`、`test(db): RLS/権限の pgTAP テストを追加`）。
- PR は [.github/pull_request_template.md](../../.github/pull_request_template.md) に沿って「何を・なぜ」「確認手順」を書き、UI 変更はスマホ幅の
  スクリーンショット（ライト / ダーク）を付ける。チェックリスト（RLS・所有者チェック・DB 型・シークレット・スコープ・ユーザー由来テキストは API 経由）を埋める。
- 設計判断を伴う変更は ADR を追加する（[docs/adr/README.md](../adr/README.md)）。運用が変わる変更は [06-operations.md](06-operations.md) も更新する。

### main ブランチの保護（GitHub での設定）

仕様書 §2 は「main ブランチ保護、PR 必須」。**リポジトリ側の設定なので、リポジトリの管理者が行う**（コードには含まれない）。
private リポジトリでブランチ保護 / ルールセットを使うには GitHub の有料プラン（Pro / Team / Enterprise）が必要。

CI（`.github/workflows/ci.yml`）のジョブ名 = 必須にするステータスチェック名:

- `Repo checks (secrets / scope / shellcheck / personas / compose)`
- `Web (lint / typecheck / test / build)`
- `API + DB (pgTAP RLS / ruff / mypy / pytest)`

**画面から**: Settings → Rules → Rulesets → New branch ruleset（Target: Default branch / `main`）で次を有効にする
（旧方式なら Settings → Branches → Add branch protection rule）。

- Restrict deletions / Block force pushes
- Require a pull request before merging（Required approvals: 1、Dismiss stale approvals、Require conversation resolution）
- Require status checks to pass（上の 3 つを追加、Require branches to be up to date）
- Bypass list は空（管理者にも適用）

一人で開発する期間は Required approvals を 0 にしてもよい（PR と CI の必須は残す）。

**gh CLI で**（旧方式のブランチ保護 API。このサンドボックスでは未実行）:

```bash
gh api -X PUT "repos/<owner>/<repo>/branches/main/protection" -H "Accept: application/vnd.github+json" --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Repo checks (secrets / scope / shellcheck / personas / compose)",
      "Web (lint / typecheck / test / build)",
      "API + DB (pgTAP RLS / ruff / mypy / pytest)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": { "dismiss_stale_reviews": true, "required_approving_review_count": 1 },
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
gh api "repos/<owner>/<repo>/branches/main/protection" --jq '.required_status_checks.contexts'   # 確認
```

## 開発の小技

- **API の Swagger UI**: http://localhost:8000/docs（`APP_ENV=production` では無効）。
- **UI 部品のカタログ**: http://localhost:3000/dev/ui（`next dev` のときだけ存在する。`app/(main)/dev/ui/page.dev.tsx`。本番ビルドにはルートもチャンクも
  含まれない。開発専用のページは `page.dev.tsx` と名付ける。[ADR-0031](../adr/0031-web-ui-accessibility-and-dev-only-pages.md)）。Python API への疎通確認もできる。
- **dev サーバーを複数並行で動かす**: `cd apps/web && NEXT_DIST_DIR=.next-browse pnpm exec next dev -p 3001`（`.next-*` は gitignore 済み）。
  **既定（`.next`）以外の `NEXT_DIST_DIR` を使うと、Next.js が `apps/web/tsconfig.json` の `include` に `.next-xxx/types/**/*.ts` を自動で追加する**
  （`next build` でも同じ）。この変更はコミットしないこと。戻すには `git checkout -- apps/web/tsconfig.json`。`tsconfig.json` に dist の名前を登録して
  おく方式はやめた（型検査が古い生成物を拾うため。登録し直さない）。
  `.env.example`（= `pnpm setup:env` で作る `apps/api/.env`）の `CORS_ALLOW_ORIGINS` には 3000〜3002 が入っている。
- **API をコンテナで**: `docker compose up --build api`（ビルドコンテキストはルート。片付けは `docker compose rm -sf api`）。
  Supabase のコンテナは Compose ではなく Supabase CLI が管理しているので、止めるときは `pnpm db:stop` を使う。
- **ログ**: API は JSON Lines。`uv run uvicorn ... | jq -c 'select(.level != "INFO")'` のように絞れる。
- **モック LLM の返答**を変えたいときは、DM の返答は `apps/api/app/services/llm.py` の `mock_*` 関数、エンジンの用途は各モジュールが `register_mock_handler` で登録する
  ハンドラ（`engine/memory/mock.py`・`engine/affinity/mock.py`・`engine/proactive/mock.py`・`engine/calendar/captions.py`）。E2E の A9 / A10 と評価ハーネスの mock の結果が
  この挙動に依存しているので、変えたら E2E と評価ハーネスも確認する。
