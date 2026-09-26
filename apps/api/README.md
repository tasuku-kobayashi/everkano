# @everkano/api — Python API（FastAPI）

DM のキャラ返答生成とストリーミング（SSE）、コメント投稿とキャラの返信生成、メモリパネル用の記憶・約束の API、
自発メッセージの設定、相談窓口の一覧を提供する。返答はキャラクターエンジン v1.0（記憶・キャラのカレンダーと状態・好感度・
自発メッセージ・安全対応（E6）・出力の検査（OutputGuard）。`app/engine/`）が組み立て、Gate #1 モデレーションと監査ログを通す。
返答の後の分析（記憶・約束・好感度）と定期実行（予定・状態・自発メッセージ）は、同じイメージのワーカー（`python -m app.worker`）が
Postgres のジョブキューで行う。H5 に従い Vercel ではなく Fly.io 等の別ホストで動かす。

- 契約: 入出力の型は `packages/shared/src/api.ts`（Pydantic モデル `app/models/*.py` と一致。`tests/test_openapi_contract.py` で検証）
- OpenAPI: `docs/api/openapi.json`（`pnpm --filter @everkano/api openapi` で再生成、`openapi:check` で差分検出）
- 環境変数: リポジトリ直下の `.env.example`（API セクション）。`app/core/config.py` が起動時に検証する（一覧はルートの
  [README.md](../../README.md#環境変数)、エンジンの分は下の「キャラクターエンジン」）
- 設計: キャラクターエンジンは [ADR-0035](../../docs/adr/0035-character-engine-architecture.md)〜[ADR-0049](../../docs/adr/0049-prompt-order-and-prefix-cache.md)、
  構成と運用は [docs/handover/](../../docs/handover/README.md)（01・02・05・06）、評価ハーネスは [docs/eval/](../../docs/eval/README.md)

## ローカル開発

```bash
pnpm db:start                         # Supabase ローカルスタック（Postgres :54322 / Auth :54321）
cp .env.example apps/api/.env          # もしくは pnpm setup:env
cd apps/api
uv sync                                # Python 3.12 の仮想環境を .venv に作成
uv run uvicorn app.main:app --reload --port 8000   # = pnpm --filter @everkano/api dev
curl http://localhost:8000/health
```

既定（`ENGINE_WORKER_ENABLED=true`・`ENGINE_SCHEDULER_ENABLED=true`）では、API のプロセスの中でジョブと定期実行も動く。
本番（Fly.io）と同じように分けるときは、API を `ENGINE_WORKER_ENABLED=false ENGINE_SCHEDULER_ENABLED=false` で起動し、
別のターミナルで `uv run python -m app.worker` を動かす（下の「キャラクターエンジン」）。

`LLM_MODE=mock` / `EMBEDDING_MODE=hash`（既定）なら外部 API キー無しで動く。モック LLM はペルソナの口調例・
一人称/呼び方・キャラの今の状態（カレンダー。無ければ `schedule_pattern`）・関係の段階・期日の近い約束と、検索された記憶を使って決定的に返答する
（例: 「来週、大阪に出張するんだ」→ 後日「大阪のお土産…」に「そういえば、来週、大阪に出張するって言ってたよね。」）。
記憶になるのは返答の後のジョブ（`post_turn`）が動いてからで、既定では**会話が止まって約 3 分後**（`ENGINE_POST_TURN_DELAY_SECONDS` = 180 秒。
続けて話すと後ろへずれ、最大 18 分）。手元ですぐ確かめるなら `apps/api/.env` で `ENGINE_POST_TURN_DELAY_SECONDS=1` にする。
`LLM_MOCK_STREAM_DELAY_MS=40` などにすると、モックでもストリーミングの表示を確かめられる。

Docker で動かす場合（ビルドコンテキストはリポジトリのルート）:

```bash
docker compose up --build api          # ルートの docker-compose.yml
```

## 品質ゲート

```bash
uv run ruff check . && uv run ruff format --check .   # = pnpm --filter @everkano/api lint
uv run mypy app evals                                  # strict（CI・pnpm typecheck と同じ）
uv run pytest -q                                       # 統合テストはローカル Supabase が無ければ skip
uv run pytest tests/engine -q                          # キャラクターエンジンだけ（core / memory / calendar / affinity / proactive）
uv run python scripts/validate_personas.py             # packages/personas/*.yaml の検証（engine: セクションを含む）
```

統合テスト（`tests/integration/`）はローカル Supabase の Postgres（`TEST_DATABASE_URL` で変更可）に、
自前のユーザー（`auth.users`）・キャラ・投稿を作って実行し、終了時にすべて削除する。JWT は HS256
（テスト用シークレット）で署名する。ES256（JWKS）経路は `tests/test_security.py` の偽 JWKS で検証している。

## エンドポイント

| Method | Path | 認証 | 概要 |
|---|---|---|---|
| GET | `/health` | 不要 | 死活監視（DB 疎通を含む） |
| POST | `/conversations` | 要 | 会話の取得または作成（新規時はペルソナの `greeting` を保存） |
| POST | `/chat/stream` | 要 | DM 返答を Server-Sent Events（`delta` / `replace` / `done` / `error`）で順に返す。Web の DM はこちら。レート制限は `/chat` と共有 |
| POST | `/chat` | 要 | DM 返答生成（`/chat/stream` と同じ処理で、最後の結果だけを返す。レート制限 `RATE_LIMIT_CHAT_PER_MINUTE`） |
| GET | `/memories?character_id=&include_superseded=` | 要 | 自分の記憶一覧（有効なもの → 重要度降順。`include_superseded=true` で置き換えられた履歴も） |
| POST | `/memories` | 要 | 記憶を追加（`is_user_edited=true`・種類 `kind`）。上限到達で 422、`summary` のタグ・種類は指定不可。レート制限 `RATE_LIMIT_MEMORIES_PER_MINUTE` |
| PATCH | `/memories/{id}` | 要 | 内容・重要度・タグ・種類を更新（`is_user_edited=true`、内容変更時は再 embedding。以後、自動処理は上書きしない（E5））。レート制限は POST と共通 |
| DELETE | `/memories/{id}` | 要 | 削除（204）。本文を持たない墓標を残し、自動抽出で作り直さない。この記憶の未達の約束は取り消す |
| GET | `/promises?character_id=&include_closed=` | 要 | 自分とそのキャラの約束（既定は未達だけ・期日の近い順） |
| PATCH | `/promises/{id}` | 要 | `{status: "done" \| "cancelled"}`。取り消すとカレンダーの予定も取り消す |
| GET | `/proactive/settings` | 要 | 自発メッセージの設定（全体の有効・送らない時間帯 + キャラ別の有効） |
| PUT | `/proactive/settings` | 要 | 全体の有効・送らない時間帯（JST の時 0〜23）を変更 |
| PUT | `/proactive/settings/{character_id}` | 要 | キャラ別の有効 / 無効 |
| GET | `/safety/resources` | 要 | E6 の相談窓口の一覧（`packages/prompts/safety/resources.ja.yaml`） |
| POST | `/comments` | 要 | コメント投稿（Gate #1 で拒否なら 422 `moderation_blocked`）。確率で投稿者キャラが自動返信 |
| POST | `/comments/generate` | 要 | 投稿者キャラが**自分の**コメントに返信（他人のコメントは 404。返信はコメント1件につき1件まで・既存があればそれを返す。出力が拒否されたら `comment: null`） |

入出力の詳細・SSE のイベントの例・エラーコードは [docs/handover/04-api.md](../../docs/handover/04-api.md)。
エラーはすべて `{"error": {"code", "message", "request_id"}}`（message は日本語）。全レスポンスに `X-Request-ID`
（`/chat/stream` はストリームを始める前のエラー（認証・所有者・レート制限・入力）だけが通常の JSON。始めた後は `error` イベント）。
本文が `MAX_REQUEST_BODY_BYTES`（既定 64KiB）を超えるリクエストは、認証より前に本文を読まずに 413 `validation_error`。
認証サーバー（JWKS）に接続できないときは 401 ではなく 503 `internal_error`（`Retry-After: 5`。Web はログアウトしない）。

## 構成

```
app/
  main.py              アプリファクトリ create_app()、lifespan（DB プール・ペルソナ/テンプレート読込・HTTP クライアント・
                       ENGINE_WORKER_ENABLED / ENGINE_SCHEDULER_ENABLED ならワーカー・スケジューラも起動）
  worker.py            ジョブのワーカーとスケジューラの常駐プロセス（python -m app.worker。--no-worker / --no-scheduler）
  container.py         サービスの組み立て（Services・エンジン）とレート制限の依存関係
  core/                config（環境変数検証）/ security（Supabase JWT: ES256・RS256=JWKS, HS256=共有鍵）/
                       logging（JSON Lines）/ db（asyncpg）/ errors / middleware（X-Request-ID・アクセスログ・本文サイズ上限）/
                       http（LLM・埋め込み用と JWKS 用の httpx クライアント）/ observability（Sentry の初期化とスクラブ）
  routers/             薄いルーター（health / conversations / chat / memories / promises / proactive / safety / comments）
  engine/              キャラクターエンジン v1.0（ADR-0035）
    types.py           モジュール間の契約（Protocol と値オブジェクト）
    context_assembler.py  文脈の組み立て（ContextBudget・締め切り ENGINE_CONTEXT_TIMEOUT_SECONDS）
    pipeline.py        DM の 1 ターン（E6 → Gate #1 → 文脈 → 生成 → 出力検査 → 保存 → post_turn の登録）
    pipeline_flush.py / pipeline_sse.py  文単位のフラッシュと出力検査 / SSE への変換
    pipeline_driver.py 評価ハーネス・テスト用（ManualClock で時計を進めてジョブ・定期実行を動かす EngineDriver）
    jobs/              Postgres のジョブキュー（engine_jobs）・ワーカー・返答の後のジョブ（post_turn など）
    scheduler.py       定期実行（engine_schedules。リーダーは pg_try_advisory_lock）
    memory/            記憶エンジン v2（分析・保存・検索のランキング・約束・要約・墓標・注入の防止・メモリパネル・再埋め込み）
    calendar/          キャラの予定・状態・世界の時計・予定からの投稿（ルールだけ）
    affinity/          好感度（評価・段階・操作の検知・口調の指示）
    proactive/         自発メッセージ（規則・文面・設定）
    safety/            E6 の検出と応答・相談窓口の読込・OutputGuard（E2 / E3）
  services/
    chat.py            POST /chat・/chat/stream の入口（パイプラインを別タスクで動かし、切断されても最後まで保存）
    memory.py / memory_capacity.py / user_memories.py / reembed.py  互換モジュール（実装は engine/memory/）
    llm.py             OpenAI 互換クライアント（リトライ付き・ストリーミング・用途別のモデル）/ MockLLM
    embedding.py       OpenAI 互換 /embeddings / 文字 n-gram ハッシュ埋め込み
    persona.py         ペルソナ YAML の読込・検証（age >= 20）
    prompt.py          packages/prompts テンプレートの描画
    moderation.py      Gate #1（語彙リスト + 正規化。TermProvider で差し替え可能）
    audit.py           監査ログ（audit_logs + stdout）
    rate_limit.py      プロセス内スライディングウィンドウ
    comments.py / conversations.py / characters.py
  models/              Pydantic スキーマ（api.ts と一致）
evals/                 評価ハーネス（python -m evals.run。本番のイメージには入らない。docs/eval/）
scripts/               export_openapi.py / validate_personas.py / reembed_memories.py（埋め込み設定の切り替え後に実行）
tests/                 単体テスト + integration/（ローカル Supabase）+ engine/（core・memory・calendar・affinity・proactive）+ evals/
```

## キャラクターエンジン

### ジョブと定期実行（worker）

```bash
uv run python -m app.worker                  # ワーカーとスケジューラ（環境変数 ENGINE_WORKER_ENABLED / ENGINE_SCHEDULER_ENABLED に関係なく起動）
uv run python -m app.worker --no-scheduler   # ジョブのワーカーだけ（--no-worker でスケジューラだけ）
```

- ジョブは Postgres のキュー（`engine_jobs`。`FOR UPDATE SKIP LOCKED` で複数台が分け合う）。返答のたびに `post_turn`（会話単位で 1 件にまとめ、
  `ENGINE_POST_TURN_DELAY_SECONDS` 後に記憶の分析・約束・キャラの発言の記憶・好感度の評価。続けて話すと後ろへずれ、最大 6 倍）を登録する。
  失敗は指数バックオフで再試行し、`ENGINE_JOB_MAX_ATTEMPTS` 回で `dead`（audit `engine.job_dead`）。SIGTERM では新しいジョブを取るのをやめ、実行中の完了を待つ。
- 定期実行（`engine_schedules` に前回・次回を記録。リーダーは `pg_try_advisory_lock` で 1 台だけ）: `calendar.ensure_schedules`（1 時間ごと。7 日先までの予定）・
  `calendar.tick`（5 分。状態・予定の完了・予定からの投稿）・`proactive.scan`（10 分。自発メッセージ）・`affinity.daily`（毎日 4 時 JST）・`jobs.cleanup`（毎日 3 時 JST）。
- セッション単位の advisory lock を使うので、worker は DB に direct か Supavisor の session mode で接続する（transaction mode（:6543）では正しく動かない）。
- ジョブ・定期実行の様子を SQL で見る方法・dead のジョブの再実行は [06-operations.md](../../docs/handover/06-operations.md#キャラクターエンジンの状態の調べ方)。

### 環境変数（エンジン）

既定値は `app/core/config.py`。すべての変数と説明はルートの [README.md](../../README.md#環境変数)。

| 変数 | 既定 | 概要 |
|---|---|---|
| `LLM_MODEL_ANALYSIS` / `LLM_MODEL_PROACTIVE` / `LLM_MODEL_CAPTION` | 空（= `LLM_MODEL`） | 用途別のモデル（記憶の分析・要約・好感度の評価 / 自発メッセージ / 予定からの投稿のキャプション） |
| `LLM_MOCK_STREAM_DELAY_MS` | `0` | モックのストリーミングで数文字ごとに入れる待ち（表示の確認用） |
| `CHAT_STREAM_HEARTBEAT_SECONDS` | `10` | `/chat/stream` のキープアライブの間隔 |
| `ENGINE_MEMORY_ENABLED` / `ENGINE_CALENDAR_ENABLED` / `ENGINE_AFFINITY_ENABLED` / `ENGINE_PROACTIVE_ENABLED` | `true` | 各仕組みの有効 / 無効（すべて false = 評価ハーネスの「素の LLM」） |
| `ENGINE_CONTEXT_TIMEOUT_SECONDS` | `1.5` | 文脈の組み立ての締め切り（間に合わない要素は省いて返答。audit `engine.context_degraded`） |
| `ENGINE_WORKER_ENABLED` / `ENGINE_SCHEDULER_ENABLED` | `true`（`fly.toml` は `false`） | API のプロセスの中でジョブ / 定期実行を動かすか |
| `ENGINE_WORKER_CONCURRENCY` / `ENGINE_WORKER_POLL_INTERVAL_SECONDS` | `2` / `1` | 同時に処理するジョブの数 / 空のときの確認間隔（秒） |
| `ENGINE_JOB_MAX_ATTEMPTS` / `ENGINE_JOB_BACKOFF_BASE_SECONDS` / `ENGINE_JOB_BACKOFF_MAX_SECONDS` | `5` / `30` / `3600` | 再試行の回数と指数バックオフ |
| `ENGINE_JOB_TIMEOUT_SECONDS` / `ENGINE_JOB_LOCK_TIMEOUT_SECONDS` / `ENGINE_JOB_RETENTION_DAYS` | `300` / `600` / `7` | 1 ジョブの上限 / 止まった running を戻すまで / 完了したジョブを消すまでの日数 |
| `ENGINE_POST_TURN_DELAY_SECONDS` / `ENGINE_POST_TURN_MAX_TURNS` | `180` / `10` | 返答の後の分析の待ち（デバウンス。[ADR-0046](../../docs/adr/0046-engine-cost-and-latency.md)）/ 1 回の最大ターン数 |
| `ENGINE_SCHEDULER_POLL_INTERVAL_SECONDS` | `30` | 定期実行の期限を確認する間隔 |
| `ENGINE_CALENDAR_ENSURE_INTERVAL_SECONDS` / `ENGINE_CALENDAR_DAYS_AHEAD` / `ENGINE_CALENDAR_TICK_INTERVAL_SECONDS` | `3600` / `7` / `300` | 予定の生成の間隔と日数 / tick の間隔 |
| `ENGINE_PROACTIVE_SCAN_INTERVAL_SECONDS` | `600` | 自発メッセージの判定の間隔 |
| `ENGINE_AFFINITY_DAILY_HOUR_JST` | `4` | 好感度の日次処理の時刻（JST） |
| `ENGINE_SCHEDULE_NAMESPACE` | 空 | 定期実行の記録とロックの名前空間（テスト・評価ハーネス用） |
| `ENGINE_PROACTIVE_DAILY_LIMIT` / `ENGINE_PROACTIVE_QUIET_START` / `ENGINE_PROACTIVE_QUIET_END` | `3` / `0` / `7` | 自発メッセージの 1 ユーザー 1 日の上限と既定の送らない時間帯（JST。E4） |
| `ENGINE_PRICE_TABLE_JSON` | 空（= DeepSeek V3 の価格） | 評価ハーネスの費用の推計の価格表（円 / 100 万トークン） |
| `SAFETY_RESOURCES_PATH` | `packages/prompts/safety/resources.ja.yaml`（Docker は `/srv/everkano/safety/resources.ja.yaml`） | E6 の相談窓口と返答文面（起動時に検証。**番号・受付時間は公開前に要確認**） |

エンジンの調整値の多く（記憶のランキング・好感度のしきい値・自発メッセージの規則・`ContextBudget` など）は環境変数ではなくコードの定数
（`app/engine/*/config.py`・`context_assembler.py`）。変えたら評価ハーネスを再実行して記録する。

### 評価ハーネス

```bash
uv run python -m evals.run --days 30                                   # 素の LLM とエンジンの両方・全シナリオ（mock）。結果を docs/eval/ に書く
uv run python -m evals.run --days 90 --engine on --scenario office_worker   # シナリオ・モードを絞る
uv run python -m evals.run --days 30 --set engine_post_turn_delay_seconds=120 --label exp-120s --no-history   # 調整値の実験
uv run --with tokenizers python -m evals.run --days 30 --tokenizer <tokenizer.json>   # 費用を本番のトークナイザで数える
LLM_API_KEY=... uv run python -m evals.run --llm live --days 30 --max-cost-jpy 3000   # live（費用の上限で止まる）
uv run python -m evals.cleanup --leftovers                             # --keep や中断で残った評価のデータを消す
```

ローカル Supabase の DB を使う（評価用のユーザー・キャラを作り、終わったら消す。定期実行は名前空間で分ける）。指標・仮定・結果の読み方は
[docs/eval/README.md](../../docs/eval/README.md)、設計は [ADR-0045](../../docs/adr/0045-evaluation-harness.md)。**mock の結果はエンジンの仕組みの計測で、
モデルの言語の質ではない**。live で実行する前に `LLM_MODEL` と価格表が現行のものか確かめる（[06-operations.md](../../docs/handover/06-operations.md#live-の-llm-に切り替える前モデルや価格が変わったとき)）。

## 設計上の注意

- API は `postgres` ロールで接続し RLS をバイパスするため、**全クエリを検証済み user_id でスコープ**している
  （他人の会話・記憶は 404）。新しいクエリを追加するときも必ず守ること。
- 長期メモリの検索は (user, character) に絞った**厳密検索**（`MATERIALIZED` CTE）。HNSW は使わない（ADR-0005）。
- ユーザーが編集した記憶（`is_user_edited`）は自動抽出の重複排除で上書きしない。
- レート制限はプロセス内メモリ。Fly.io で複数マシンにスケールする場合はマシンごとのカウントになる。
- 監査ログの DB 書き込みはチャットのトランザクションとは別接続。失敗しても応答は返すが ERROR ログを出す。
  stdout への複製（INFO）は `LOG_LEVEL=WARNING` 以上でも出る（`everkano.audit` ロガーは常に INFO 以下）。
- `/chat`・`/chat/stream` の上限は `CHAT_DEADLINE_SECONDS`（既定 38 秒。文脈の組み立て + 応答生成）。間に合わなければ 503 `llm_unavailable`
  （ストリームでは `error` イベント）で何も保存しない。記憶の分析は返答を待たせず、返答の後のジョブ `post_turn` で行う（E8）。
  **Web の `CHAT_TIMEOUT_MS` / `CHAT_STREAM_TIMEOUT_MS`（45 秒）より必ず短くする**（クライアントが諦めた後に保存され、再送で二重になるのを防ぐ）。
- 生成はリクエストとは別のタスクで動かす（`services/chat.py`）。クライアントが接続を切っても返答は最後まで生成・保存され、次の画面表示で届く。
- Gate #1（入力）で差し止めた発言も `messages` には保存するが、以後の LLM 履歴・記憶の分析の文脈では本文を
  「（不適切な発言のため省略）」に置き換え、中期要約からはそのターンごと除く（`engine/memory/text.py` の `sanitize_history`）。
  E6 で安全対応を返したターンも、記憶の分析・好感度の評価には使わない。
- 検索用の埋め込みは `EMBEDDING_TIMEOUT_SECONDS`（既定 5 秒・リトライ `EMBEDDING_MAX_RETRIES` = 1）で打ち切り、
  失敗しても長期記憶の検索を省略して返答する（埋め込み障害で `/chat` 全体を 503 にしない）。
  `/memories` の埋め込みは 10 秒で打ち切って 503（Web の 15 秒より前に返し、再送による二重保存を防ぐ）。
  埋め込みの失敗はどれも audit `llm.error` に残す（`purpose`: `embedding_query` = 検索を省略 / `memory_save` =
  抽出した記憶を保存できなかった / `user_memory` = メモリパネルの追加・編集が 503 / `memory_summary_embedding` =
  要約を埋め込み無しで保存）。`chat.response` にも `retrieval_skipped` / `memory_save_error` を記録する。
- 応答生成に渡す履歴（短期メモリ・直近 30 ターン）は合計 4,000 字まで（`engine/context_assembler.py` の `ContextBudget.history_chars`。
  `prompt.HISTORY_MAX_CHARS` の 16,000 字は MVP の値で、エンジンの返答では使わない）。直近 6 件は全文、それより古い発言は 500 字に切り詰め、
  上限を超える古い分は渡さない。窓の先頭は目印の発言（8 件に 1 件）にそろえ、プレフィックスキャッシュを効かせる（ADR-0049）。
  記憶・状態・関係性などの各部分にも上限がある（`ContextBudget`。`chat.response` の予算の内訳・`prompt_chars` で監視できる）。
- 中期要約（ジョブ `memory.summarize`）は古い順に、会話ログの文字数上限（`TRANSCRIPT_MAX_CHARS` = 12000）に収まる分ずつ要約し、カーソルは要約に
  含めた分までしか進めない（1 回のジョブで最大 3 チャンク）。失敗は会話ごとに指数バックオフ（60 秒〜1 時間）し、
  同じチャンクで 3 回失敗するか内容で拒否（HTTP 400/413/422）されたらそのチャンクを飛ばす（audit `llm.error` の `skipped=true`）。
  要約の埋め込みに失敗した場合は埋め込み無しで保存する（最新 2 件は常に注入されるため使われる）。
- ユーザー × キャラの記憶は `MEMORY_MAX_PER_CHARACTER`（既定 500）件まで。ユーザーの追加は上限で 422、自動の分析・要約は
  置き換えられた履歴 → 重要度の最も低い自動記憶の順に入れ替える（ユーザー編集済み・要約は入れ替えない。audit `memory.delete`、`source=capacity_eviction`）。
- 記憶の本文・コメント本文はプロンプトに入れる前に改行を空白にして 1 行にする（見出しの偽造対策）。`summary` タグは
  利用者が新たに付けられない。キャラのコメント返信（公開）は Gate #1 に加えて URL・ドメイン名も差し止める。
- 外部 HTTP: LLM・埋め込み用のクライアントは同時接続 200（`/chat`・`/chat/stream` 1 件で 2 本使うため、`fly.toml` の `hard_limit` × 2 以上。
  `tests/test_http_limits.py` で検査）、接続待ちは 5 秒で打ち切る。JWKS は別クライアント（LLM の混雑の影響を受けない）。
- Sentry（`SENTRY_DSN` 設定時のみ）は `send_default_pii=False` に加えて、ローカル変数・リクエスト本文・ログのパンくずを送らず、
  監査ロガーを除外し、`before_send` でヘッダー（Authorization / Cookie）・本文系のキーを伏せる（`app/core/observability.py`）。
- 統合テストは DB に接続できないとローカルでは skip するが、`REQUIRE_TEST_DB=1`（未設定なら `CI=true`）では失敗にする。
- `APP_ENV=staging` / `production` では、`SUPABASE_URL`（https 必須）と `CORS_ALLOW_ORIGINS` がローカルの既定値のままだと
  起動しない（`fly secrets set` の登録漏れ検出）。リモートの DB に接続する `DATABASE_URL` に `sslmode=require` 以上
  （`require` / `verify-ca` / `verify-full`。環境変数 `PGSSLMODE` でも可）が無い場合も起動しない（下の「DB への接続（TLS）」）。
- 設定の検証エラーには入力値を含めない（`hide_input_in_errors`。起動失敗のログに API キー・DB のパスワードを出さない）。
- JWT の `iss` は `SUPABASE_JWT_ISSUER`（任意）→ 無ければ `{SUPABASE_URL}/auth/v1` と照合する。
  Docker から `host.docker.internal` 経由で Supabase を参照する場合は `SUPABASE_JWT_ISSUER` を設定する。

## デプロイ（Fly.io）

```bash
# リポジトリのルートで
fly deploy --config apps/api/fly.toml --dockerfile apps/api/Dockerfile
fly secrets set --config apps/api/fly.toml DATABASE_URL=... SUPABASE_URL=... LLM_API_KEY=... CORS_ALLOW_ORIGINS=...
fly scale count app=1 worker=1 --config apps/api/fly.toml   # worker のマシンが無ければ（fly status で確認）
```

`fly.toml` の `[processes]` で同じイメージを `app`（uvicorn。HTTP を受ける）と `worker`（`python -m app.worker`）の 2 つのプロセスグループで動かす。
`[env]` の `ENGINE_WORKER_ENABLED=false`・`ENGINE_SCHEDULER_ENABLED=false` で `app` はジョブ・定期実行を動かさない（返答のレイテンシ（E8）を守る）。
**worker が止まると記憶・約束・好感度・予定・自発メッセージが止まる**（返答は届く）。`fly secrets set` は両方のグループのマシンを再起動する。
Fly.io のヘルスチェックは `fly.toml` の `[[http_service.checks]]`（`app` だけ）。`Dockerfile` の `HEALTHCHECK`（:8000 の `/health`）は API 用なので、
Docker で worker を動かすときは `docker run --no-healthcheck ... python -m app.worker` のように外す（外さないと unhealthy と表示される）。

イメージのベース（`python:3.12.14-slim-trixie`）と uv は digest で固定している（`Dockerfile` の `PYTHON_IMAGE` / `UV_IMAGE` と
`fly.toml` の `[build.args]`。更新手順は `Dockerfile` の先頭）。実行イメージには pip を入れず、非 root（uid 10001）で動かす。

### DB への接続（TLS）

API は RLS をバイパスする `postgres` ロールで、DM・記憶・監査ログを Fly.io（nrt）から Supabase へ公衆網越しに運ぶ。
asyncpg の既定（`sslmode=prefer`）は証明書を検証せず、TLS を張れなければ平文に落ちるため、staging / production では
`DATABASE_URL` に `sslmode` の指定を必須にしている。

- 推奨: `verify-full`（Supabase のルート証明書で証明書と接続先ホスト名を検証する）
  1. Supabase ダッシュボードの Database Settings → SSL Configuration → Download certificate で `prod-ca-2021.crt` を取得
  2. `fly secrets set --config apps/api/fly.toml SUPABASE_DB_CA_CERT="$(base64 -w0 prod-ca-2021.crt)"`
  3. `fly.toml` の `[[files]]`（`/app/certs/supabase-ca.crt`）のコメントを外す
  4. `DATABASE_URL='postgresql://...?sslmode=verify-full&sslrootcert=/app/certs/supabase-ca.crt'`
- 最低限: `sslmode=require`（暗号化のみ。証明書は検証しないため、経路上のなりすましは防げない）
- 実際に使われている値は起動ログ（`startup complete`）の `database_sslmode` で確認できる
- Supabase 側でも Database Settings の「Enforce SSL on incoming connections」を有効にし、Network Restrictions で
  接続元を API の送信元 IP に絞ることを推奨する（Fly.io で固定の送信元 IP（static egress IP）を割り当てた上で設定する）。

### 埋め込み設定の切り替え（EMBEDDING_MODE / EMBEDDING_MODEL）

`hash`（開発用の文字 n-gram ハッシュ）と `live`（OpenAI 互換 API）はベクトル空間がまったく別で、次元数（1536）が
同じためエラーにならないまま、既存の記憶の検索（長期メモリの再注入）と重複排除（cos ≥ 0.92）が壊れる。
`memories` はどの設定で埋め込んだかを保存していないので、**切り替えたら直後に全件を再埋め込みする**。
`EMBEDDING_MODEL` を変える場合も同じ。

1. `fly secrets set --config apps/api/fly.toml EMBEDDING_API_KEY=...`（live にする場合）
2. `apps/api/fly.toml` の `EMBEDDING_MODE`（または `EMBEDDING_MODEL`）を変更してデプロイ
3. すぐに再埋め込みを実行する（冪等。途中で止まっても再実行すればよい）:
   ```bash
   fly ssh console --config apps/api/fly.toml -C "/opt/venv/bin/python /app/scripts/reembed_memories.py --dry-run"  # 件数確認
   fly ssh console --config apps/api/fly.toml -C "/opt/venv/bin/python /app/scripts/reembed_memories.py"
   # ローカル: cd apps/api && uv run python scripts/reembed_memories.py
   ```
4. 完了するまでの間、既存の記憶は会話で思い出されにくく、近い内容の記憶が重複して作られることがある

再埋め込みは本文が変わっていない行だけを更新する（実行中にユーザーが編集した記憶は、編集時の埋め込みを残す）。
`memories.updated_at` はトリガーにより実行時刻になる。

TLS を中継するプロキシ配下でイメージをビルドする場合は、CA 証明書を BuildKit secret で渡せる:
`docker build --secret id=extra_ca,src=/path/to/ca.crt -f apps/api/Dockerfile .`
