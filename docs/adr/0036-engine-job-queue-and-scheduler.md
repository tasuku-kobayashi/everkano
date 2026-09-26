# ADR-0036: 非同期ジョブと定期実行の基盤（Postgres のキュー `engine_jobs`・スケジューラ・worker プロセスグループ）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 §3（非同期ジョブ・定期実行の基盤の選定）・§13-1・E8 / [ADR-0035](0035-character-engine-architecture.md)・[ADR-0014](0014-comments-via-api-and-auto-reply.md)・
  [ADR-0018](0018-in-process-rate-limit.md) / 実装: `apps/api/app/engine/jobs/`（`queue.py`・`worker.py`・`handlers.py`）, `apps/api/app/engine/scheduler.py`,
  `apps/api/app/container.py`（`periodic_tasks`）, `apps/api/app/worker.py`, `apps/api/app/engine/pipeline.py`（`_enqueue_post_turn`）,
  `apps/api/fly.toml`（`[processes]`）, `apps/api/app/engine/pipeline_driver.py`, `apps/api/tests/engine/core/`（`test_jobs.py`・`test_scheduler.py`・`test_post_turn.py`・`test_worker_process.py`）

## コンテキスト

- E8 のため、記憶の分析・約束の予定化・好感度の評価は返答の後に非同期で行う。予定の生成・状態の更新・自発メッセージ・好感度の減衰は定期実行が要る。
- MVP の中期要約とコメントの自動返信は FastAPI の BackgroundTask（プロセス内）で、再起動・デプロイで失われていた。
- API は Fly.io（[ADR-0018](0018-in-process-rate-limit.md) により 1 マシン 1 プロセス）、DB は Supabase の Postgres。Redis は無い。
- 評価ハーネスは時計（`ManualClock`）を早送りして、ジョブと定期実行を **決定的に** 動かす必要がある。
- §13-1 の「ジョブのホスト」は未回答（[ADR-0035](0035-character-engine-architecture.md) の前提: API と同じイメージの別プロセスグループ）。

## 決定

### キュー（`public.engine_jobs`。`engine/jobs/queue.py`）

- 列: `kind`・`dedupe_key`・`payload`（jsonb）・`run_at`・`status`（queued / running / done / failed / dead）・`attempts` / `max_attempts`・`last_error`・
  `locked_at` / `locked_by`・`finished_at`。クライアントからは一切参照できない（RLS 有効・ポリシー無し・grant 無し）。
- **登録（enqueue）**: `dedupe_key` 付きなら、同じ `kind` + `dedupe_key` の未処理（queued / running）のジョブは 1 件だけ（部分一意索引 `engine_jobs_dedupe_idx`）。
  - 既に queued なら、`debounce_max_delay` を渡したときは `run_at` を新しい時刻まで後ろにずらす（**デバウンス**。ただし最初の登録から
    `debounce_max_delay` を超えては遅らせないので、話し続けるユーザーでも処理が止まらない）。
  - 実行中（running）なら、そのジョブの payload に「再実行の依頼」（`_rerun_at`）を付け、完了時に同じ内容で登録し直す（実行中に届いたターンを取りこぼさない）。
- **取得（claim）**: `update ... where id = (select id ... where status = 'queued' and run_at <= now order by run_at, id for update skip locked limit 1) returning *`。
  複数のワーカーが同時に動いても同じジョブを二重に取らない。**ワーカーはハンドラを登録済みの kind だけを取る**（ローリングデプロイ中に新旧のプロセスが
  並んでも、古いプロセスが新しい kind を取って失敗にしない）。
- **失敗**: 指数バックオフ（`ENGINE_JOB_BACKOFF_BASE_SECONDS` × 2^(試行回数 − 1)、上限 `ENGINE_JOB_BACKOFF_MAX_SECONDS`。**ジッターは入れない** = 評価で再現できる）で
  queued に戻し `engine.job_failed`。`ENGINE_JOB_MAX_ATTEMPTS` に達したら `dead` にして `engine.job_dead`。再試行しても無駄な失敗（`PermanentJobError`・
  未登録の kind）は `failed`。1 ジョブの実行時間の上限は `ENGINE_JOB_TIMEOUT_SECONDS`。
- **止まったジョブの回収**: running のまま `ENGINE_JOB_LOCK_TIMEOUT_SECONDS` を過ぎたジョブ（プロセスの強制終了など）は queued（上限に達していれば dead）に
  戻す（ワーカーのループが 1 分ごと + 日次の `jobs.cleanup`）。完了・失敗から `ENGINE_JOB_RETENTION_DAYS` たったジョブは `jobs.cleanup` で消す。
- 時刻はすべて時計（Clock）の値を書く。

### ジョブの種類（`engine/jobs/handlers.py`）

- **`post_turn`**（`dedupe_key` = 会話 ID）: 返答のたびに登録し、`run_at` = 返答の時刻 + `ENGINE_POST_TURN_DELAY_SECONDS`、デバウンスの上限はその定数倍
  （`pipeline.py` の `_enqueue_post_turn`）。続けて話すあいだは後ろにずれ、会話が止まってから 1 回だけ分析する（E7。値の決め方は
  [ADR-0046](0046-engine-cost-and-latency.md)）。処理:
  1. `conversations.analyzed_until` より新しいターン（ユーザー発言 + 直後のキャラの返答。自発メッセージ・挨拶は含めない）を古い順に最大
     `ENGINE_POST_TURN_MAX_TURNS` 件読む
  2. `memory.process_turns`（[ADR-0038](0038-memory-engine-v2.md)）→ 3. `calendar.sync_promise_events`（新しい約束の予定化。[ADR-0040](0040-character-calendar.md)）
     → 4. `affinity.evaluate_turns`（Gate #1 で差し止めたターン・E6 の安全対応をしたターンは渡さない。[ADR-0041](0041-affinity-engine.md)）
  5. `analyzed_until` を処理した最後の返答の時刻まで進める。残りがあれば続けて実行を依頼する
  - 終えた手順は payload に記録し（`_done_steps`）、再試行では残りの手順だけを行う（記憶の二重登録を防ぐ）。**最後の試行でも失敗した手順は飛ばして先に進む**
    （同じターンで以後のジョブが永久に止まらないように。ERROR ログ）。
  - 各手順は `ENGINE_*_ENABLED` で無効にできる。記憶・好感度がどちらも無効なら登録しない。
- **`memory.summarize`**（`dedupe_key` = 会話 ID）: 中期要約（[ADR-0038](0038-memory-engine-v2.md)。チャンク化と失敗時の扱いは [ADR-0028](0028-llm-input-budgets-and-summary-retries.md)）。
- 会話・ユーザーが待ちの間に消えた（退会・削除）場合は何もせずに完了にする。

### ワーカー（`engine/jobs/worker.py`）

- `ENGINE_WORKER_CONCURRENCY` 本のループが、それぞれ `FOR UPDATE SKIP LOCKED` でジョブを取り合う。空のときは `ENGINE_WORKER_POLL_INTERVAL_SECONDS` ごとに確認する
  （同じプロセスで登録したときは `notify()` で待ちを短縮する）。
- 停止（SIGTERM / SIGINT）: 新しいジョブを取らず、実行中のジョブの終了を待つ（最大 20 秒）。取り消したジョブは再試行できるよう queued に戻す。
- テスト・評価用の決定的な実行: `run_until_idle(now=...)`（`run_at <= now` のジョブが無くなるまで 1 件ずつ。対象を `kinds` / `dedupe_keys` で絞れる）。

### スケジューラ（`engine/scheduler.py`）

| タスク | 間隔 | 内容 |
| --- | --- | --- |
| `calendar.ensure_schedules` | `ENGINE_CALENDAR_ENSURE_INTERVAL_SECONDS`（既定 1 時間） | 有効な全キャラの予定を昨日〜`ENGINE_CALENDAR_DAYS_AHEAD`（既定 7）日先まで生成（冪等） |
| `calendar.tick` | `ENGINE_CALENDAR_TICK_INTERVAL_SECONDS`（既定 5 分） | キャラの状態の更新・過ぎた予定の完了とキャラ側の記憶・フィード投稿 |
| `proactive.scan` | `ENGINE_PROACTIVE_SCAN_INTERVAL_SECONDS`（既定 10 分） | 自発メッセージの判定・送信 |
| `affinity.daily` | 毎日 `ENGINE_AFFINITY_DAILY_HOUR_JST` 時（既定 4 時 JST） | 気まずさ・不満・独占欲の減衰、昇格・降格の判定の日次の更新 |
| `jobs.cleanup` | 毎日 3 時 JST（`container.JOBS_CLEANUP_HOUR_JST`） | 古いジョブの削除・止まったジョブの回収 |

- 前回・次回の実行時刻と最後のエラーは `public.engine_schedules` に記録する（再起動しても間隔が保たれる）。日次のタスクは、初回は予定時刻だけを記録する
  （起動直後に走らせない）。
- **リーダー選出**: `ENGINE_SCHEDULER_POLL_INTERVAL_SECONDS` ごとの確認のたびに `pg_try_advisory_lock` を取れたプロセスだけが、期限の来たタスクを実行する。
  ロックは接続単位なので実行後に必ず解放する（プールに戻した接続にロックを残さない）。複数のプロセス・マシンで有効にしても二重に実行されない。
- 失敗したタスクは遅くとも 10 分後に再実行し、`engine.schedule_failed` を残す。**取りこぼした回をさかのぼって実行はしない**（各タスクは「今の時刻」で必要な処理をする）。
- 名前空間 `ENGINE_SCHEDULE_NAMESPACE`: `engine_schedules` の名前の接頭辞とロックのキーを分ける（評価ハーネス・テストが共有の DB で時計を早送りしても、
  本番・開発サーバーの記録と混ざらない）。対象のキャラ・ユーザーは `EngineScope` で絞る（本番は空 = 全員）。
- テスト・評価用の決定的な実行: `run_due(now=...)`。`EngineDriver`（`engine/pipeline_driver.py`）は時計を刻みごとに進め、各時刻で `run_due` → `run_until_idle` を行う。
  `jobs.cleanup` は対象を絞れないので、時計を早送りする実行では動かさない（共有の DB の他のジョブを消し・止まったとみなしてしまうため）。

### プロセスの配置

- ローカル・開発: API のプロセスの中でワーカーとスケジューラを動かす（`ENGINE_WORKER_ENABLED` / `ENGINE_SCHEDULER_ENABLED` の既定は true）。
- 本番（Fly.io）: 同じイメージで 2 つのプロセスグループにする（`fly.toml` の `[processes]`）。
  - `app`: `uvicorn`。`[env]` で `ENGINE_WORKER_ENABLED=false`・`ENGINE_SCHEDULER_ENABLED=false`（返答のレイテンシにジョブを影響させない）。
  - `worker`: `python -m app.worker`。**環境変数に関係なく** ワーカーとスケジューラを起動する（`--no-worker` / `--no-scheduler` で片方だけにできる）。
    HTTP を受けないので `http_service` の対象外（`processes = ["app"]`）。台数は `fly scale count app=1 worker=1 --config apps/api/fly.toml`。
  - worker を複数台にしても、定期実行はリーダーの 1 台だけが行い、ジョブは `SKIP LOCKED` で分け合う。

## 結果・トレードオフ

- 追加の基盤（Redis・キューのサービス）無しで、再起動に強いジョブと定期実行ができた。ジョブ・定期実行の状態は SQL で見える（[06-operations.md](../handover/06-operations.md)）。
- 時計を差し替えれば、ジョブと定期実行をテスト・評価で決定的に再現できる。
- 取り出しは **少なくとも 1 回**（at-least-once）。ハンドラは冪等に書く（`post_turn` は `analyzed_until` と手順の記録、自発メッセージは一意キー、
  予定の生成は `generated_for`）。
- ポーリングの分だけ DB に問い合わせが増える（ループの本数 × 秒ごと。`engine_jobs_ready_idx` の部分索引で軽い）。優先度の概念は無い（`run_at` 順）。
  大量のジョブ・高いスループットが必要になったら専用のキューを検討する。
- 最後の試行で手順を飛ばすと、そのターンの記憶・好感度は反映されない（ERROR ログと `engine.job_failed` で追える）。
- コメントの自動返信は引き続き BackgroundTask（[ADR-0014](0014-comments-via-api-and-auto-reply.md)）。このキューに移すのが次の改善候補。
- `worker` プロセスグループのマシンが動いていないと、記憶・好感度・予定・自発メッセージが止まる（返答は届き続ける）。監視の対象にする。

## 代替案

- **Celery / RQ / arq + Redis**: 実績はあるが、Redis（Fly.io の Upstash など別の事業者）という追加の基盤・費用・運用が要る。時計の差し替えで早送りするには
  結局ワーカーを直接呼ぶ仕組みが要る。MVP の規模では Postgres で足りる。
- **Supabase の pg_cron + キュー（pgmq など）**: 定期実行は DB で完結するが、エンジンの Python のコードを実行できない（結局ワーカーが要る）。Supabase への依存が深まり、
  評価ハーネスで時計を早送りしにくい。
- **FastAPI の BackgroundTask / プロセス内のスケジューラ（APScheduler など）**: 再起動で失われる。複数のマシンで二重に動く。
- **ジョブ専用の別アプリ・別ホスト**: デプロイ・シークレット・監視の対象が増える。同じイメージのプロセスグループなら 1 回の `fly deploy` で揃う。
