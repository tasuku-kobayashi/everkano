# 06. 運用手順（ランブック）

SQL はすべて Supabase ダッシュボードの SQL Editor（`postgres` ロール = RLS をバイパス）か `psql "$DATABASE_URL"` で実行する想定。
**本番データを変更する SQL は `begin;` で始め、件数を確認してから `commit;`** する。以下の SQL はローカル DB で構文を確認済み。

目次: [アカウント](#引き継ぐアカウント) / [デプロイとロールバック](#デプロイとロールバック) / [監視とログ](#監視とログ) /
[監査ログの調べ方](#監査ログaudit_logsの調べ方) / [キャラクターエンジンの状態の調べ方](#キャラクターエンジンの状態の調べ方) / [障害対応](#障害対応) /
[定常作業](#定常作業) / [バックアップ](#バックアップ) / [レート制限とスケール](#レート制限とスケール) / [既知の制約と次フェーズ](#既知の制約と次フェーズ)

## 引き継ぐアカウント

| サービス              | 用途                                   | 持っている秘密情報                                                  |
| --------------------- | -------------------------------------- | ------------------------------------------------------------------- |
| GitHub                | リポジトリ・CI（Actions）              | —（CI はシークレットを使わない）                                    |
| Supabase              | DB / Auth / Realtime（staging・本番）  | DB パスワード、JWT 鍵、service_role key、SMTP 設定                  |
| Vercel                | Web                                    | 環境変数（`NEXT_PUBLIC_*`、`BUNNY_TOKEN_AUTH_KEY`）                  |
| Fly.io                | Python API                             | secrets（`DATABASE_URL`・`LLM_API_KEY` など）                        |
| OpenRouter / DeepSeek | LLM                                    | API キー・残高                                                      |
| 埋め込みの提供元      | Embedding（OpenAI 互換）               | API キー                                                            |
| SMTP 事業者           | ログインメール                         | SMTP パスワード、送信ドメインの SPF / DKIM                          |
| Backblaze B2          | 画像の保存                             | アプリケーションキー                                                |
| Bunny.net             | CDN                                    | Pull Zone の Token Authentication キー                              |
| Sentry（任意）        | API のエラー監視                       | DSN                                                                 |

引き継ぎ時は権限を移し、旧担当者の権限を外し、[シークレットをすべてローテーション](#シークレットのローテーション)する。

## デプロイとロールバック

初回の構築手順はルートの [README.md](../../README.md#デプロイ)（Supabase → Fly.io → Vercel → URL の相互設定）。

| 対象           | 通常のデプロイ                                                                                          | ロールバック                                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Web（Vercel）  | main へのマージで Production、PR で Preview（Git 連携）                                                  | Vercel ダッシュボードの Deployments → 以前のデプロイを「Promote / Instant Rollback」                            |
| API（Fly.io）  | リポジトリのルートで `fly deploy --config apps/api/fly.toml --dockerfile apps/api/Dockerfile`（`app` と `worker` の 2 つのプロセスグループが同じイメージで更新される） | `fly releases --config apps/api/fly.toml --image` で以前のイメージを確認 → `fly deploy --config apps/api/fly.toml --image <イメージ>`（両方のグループが戻る） |
| DB（Supabase） | `supabase db push --workdir infra`（新しいマイグレーションだけが適用される。`--dry-run` で事前確認）      | **前進のみ**。戻す内容の新しいマイグレーションを書く。データを壊す変更の前には[バックアップ](#バックアップ)      |

- 互換性の順番: DB の変更は「古い API / Web でも動く形」で先に入れ、次に API、最後に Web。列の削除・改名は 2 回のリリースに分ける。
- **worker プロセスグループ**（キャラクターエンジンの返答後のジョブと定期実行。[ADR-0036](../adr/0036-engine-job-queue-and-scheduler.md)）: デプロイの後に `fly status --config apps/api/fly.toml` で
  `app` と `worker` の両方のマシンが動いていることを確かめる。無ければ `fly scale count app=1 worker=1 --config apps/api/fly.toml`。`app` グループはジョブ・定期実行を
  動かさない（`fly.toml` の `[env]` の `ENGINE_WORKER_ENABLED=false`・`ENGINE_SCHEDULER_ENABLED=false`）ので、**worker が止まると記憶・約束・好感度・予定・自発メッセージが
  止まる**（返答は届き続ける）。worker を複数台にしても定期実行はリーダーの 1 台だけが行い、ジョブは分け合う。
- ワーカーはハンドラを登録済みのジョブの種類だけを取るので、ローリングデプロイの途中で新旧のプロセスが並んでもジョブは失敗にならない。デプロイ中に止めた実行中のジョブは
  queued に戻って再実行される。
- ホスト版 Supabase には `seed_engine.sql`（投稿用の画像プール `post_image_pool`）を `supabase db push --include-seed` の初回に一緒に入れる（`config.toml` の `sql_paths`）。
  URL は開発用のプレースホルダなので、本番の画像に差し替える（[画像を本番の CDN に移す](#画像を本番の-cdn-に移す)）。
- Web の Service Worker はデプロイごとに更新される（登録 URL `/sw.js?v=<ビルド ID>`。Vercel ではデプロイ ID が自動で使われるので作業は無い）。
  `NEXT_PUBLIC_BUILD_ID` を設定する場合は、デプロイごとに違う値にする（同じ値のままだとオフラインページが古いまま残る）。キャッシュの構成を変えたときだけ
  `apps/web/public/sw.js` の `CACHE_POLICY` を上げる（[ADR-0032](../adr/0032-service-worker-versioning.md)）。
- Fly.io / Vercel / ホスト版 Supabase のコマンドは、このリポジトリを作成したサンドボックスからは実行できていない（外部に接続できないため）。
  手順は各サービスの公式ドキュメントと突き合わせて確認すること。

## 監視とログ

| 対象                | 見る場所                                                                                                     | 内容                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| API の死活          | `GET /health`（認証不要）。Fly.io が 30 秒ごとに確認（`fly.toml` の `[[http_service.checks]]`。`app` グループだけ）、`fly status` | `{"status":"ok","db":"ok","llm_mode":"live",...}`。DB に届かなければ `degraded` / `db: error`         |
| worker の死活       | HTTP のヘルスチェックは無い。`fly status` のマシンの状態と、`engine_schedules` の `last_run_at` が間隔どおりに進んでいるか・`engine_jobs` の待ちが溜まっていないか（[下の SQL](#キャラクターエンジンの状態の調べ方)） | `calendar.tick` は 5 分・`proactive.scan` は 10 分ごと。止まっていれば worker の停止かリーダーのロックの問題 |
| API / worker のログ | `fly logs --config apps/api/fly.toml`（両方のグループのマシンのログが出る）                                  | 1 行 1 JSON（`ts` / `level` / `logger` / `message` / `request_id` ほか）。worker は `engine.worker` / `engine.scheduler` / `engine.jobs`（`engine job done`・`periodic task done` / `failed`）、起動時の `engine worker process started` |
| API のエラー        | Sentry（`SENTRY_DSN` を設定した場合のみ。トレースは無効）。送るのは例外の型・メッセージ・スタックトレース（変数なし）・メソッドと URL だけ（下記） | 未処理例外と ERROR ログ（本文系の項目は伏せ字）。詳細は `request_id` で API のログを引く                   |
| Web                 | Vercel の Runtime Logs（middleware・Route Handler）。Web には Sentry を入れていない                           | `[auth/confirm] verifyOtp failed`（期限切れ・使用済みのリンク）、`[auth/confirm] rejected cross-site POST`（他サイトからのログインの送信を拒否。続くならログイン CSRF の試み）、`[middleware] session verification failed` など |
| DB / Auth / Realtime | Supabase ダッシュボードの Logs と Reports                                                                   | ログインメールのトラブルは Auth のログ                                                                  |
| 監査ログ            | `public.audit_logs`（下記）                                                                                  | すべての会話・生成・判定                                                                                |

Sentry の送信内容（`apps/api/app/core/observability.py`。[ADR-0034](../adr/0034-supply-chain-and-telemetry-minimization.md)）: `send_default_pii=False` に加えて、
`include_local_variables=False`（ローカル変数を送らない。ASGI の scope にはアクセストークンがそのまま入っている）、`max_request_body_size="never"`
（リクエスト本文 = DM・記憶の内容を送らない）、ログをパンくずにしない、監査ロガー `everkano.audit` は Sentry から除外。`before_send` で
リクエストの本文・Cookie・環境変数を捨て、ヘッダーは許可リスト（User-Agent・Content-Type・Content-Length・X-Request-ID・Origin・Accept）だけ残し、
フレームの変数・ユーザー情報を消し、`extra` / `contexts` の本文系のキー（payload / text / body / message / reply / content / prompt_messages / token / error など）を
`[redacted]` にする。新しいログの項目名に本文を入れる場合は、`_SENSITIVE_KEYS` に足すこと。

API のロガー名: `everkano.access`（1 リクエスト 1 行: `method` / `path` / `status` / `duration_ms` / `client_ip` / `user_id`。`client_ip` は
`CLIENT_IP_HEADER`（Fly.io では `Fly-Client-IP`。エッジが上書きするので偽装できない）から取る。Fly.io 以外に移す場合は、そのプロキシが上書きする
ヘッダーに変えるか空にすること。`fly.toml` の `FORWARDED_ALLOW_IPS="*"` のままだと X-Forwarded-For の先頭がクライアントの自由な値になる）、
`everkano.audit`（監査ログの複製。DB への書き込みに失敗すると ERROR `failed to write audit log to database`）、`everkano.auth`
（401 の理由 `auth.failure`: `token_expired` / `invalid_issuer` / `hs256_not_configured` など）、`everkano.llm`（リトライ・失敗）。

**アラートの推奨**（未設定。引き継ぎ後に設定する）: `/health` の失敗、`llm.error` の急増（下の SQL）、ERROR ログ
（特に監査ログの書き込み失敗）、DB の接続数と容量。キャラクターエンジンでは: `engine.job_dead` が出た・`engine_jobs` の queued の最大の待ちが 30 分を超えた
（worker の停止）・`engine_schedules` の `last_run_at` が 1 時間以上進まない・`engine.schedule_failed` が続く・`engine.context_degraded` の急増（文脈の一部を省いた返答が増えた）・
`chat.response` の `ttft_ms` の中央値が 2.5 秒を超えた（E8）。`safety.trigger` は件数を日次で見る（急増したら内容を確認する）。`llm.error` は LLM だけでなく **埋め込み（Embedding）の障害** でも記録される
（[ADR-0022](../adr/0022-embedding-failures-and-audit-additions.md)）。埋め込みの障害ではユーザーへのエラー表示が無い（DM は返答し続ける）ので、
`purpose` ごとに監視し、特に `embedding_query`（長期記憶の検索を省略した）と `memory_save`（抽出した記憶を保存できなかった）が
続けて出ていたらアラートにする（下の「`llm.error` の用途別の件数」の SQL）。

| `llm.error` の `purpose`      | 意味                                                                   | ユーザーへの影響                                      |
| ----------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------- |
| `chat`                        | DM の返答生成の失敗・締め切り超過                                        | 503 / ストリームの `error`（送信失敗・再送）。何も保存されない |
| `memory_analysis`             | 返答の後の記憶の分析の失敗（一時的な障害はジョブが再試行。`invalid_output` はスキーマに合わない出力が 2 回続いた） | 返答は届く。そのバッチの記憶・約束が作られない（再試行で回復することがある） |
| `memory_analysis_context` / `memory_save` | 分析の前の埋め込み / 分析した記憶の埋め込みの失敗（一時的なものはジョブが再試行） | 同上                                                  |
| `memory_summary`              | 中期要約の失敗（`skipped=true` は同じ範囲を諦めた）                      | なし（次のジョブで再判定）                             |
| `memory_summary_embedding`    | 要約を埋め込み無しで保存した                                            | なし（最新 2 件の要約は常に使われる）                  |
| `affinity_eval`               | 好感度の評価の失敗（一時的な障害はジョブの再実行で採点し直す。拒否・不正な出力・最後の試行の失敗はそのターンを変化なし）                  | なし（好感度はユーザーに見えない）                     |
| `proactive_message`           | 自発メッセージの文面の生成の失敗                                        | その回は届かない                                      |
| `feed_caption`                | 予定からの投稿のキャプションの生成の失敗                                | その投稿は作られない                                  |
| `character_memory_embedding`  | 終わった予定のキャラ側の記憶を埋め込み無しで保存した                    | その出来事が検索で思い出されにくい                    |
| `comment_reply`               | キャラのコメント返信の生成の失敗                                        | 返信が付かない                                        |
| `embedding_query`             | 検索用の埋め込みの失敗・時間切れ                                        | 返答は届くが、以前の話を思い出さない                   |
| `user_memory`                 | メモリパネルの追加・編集の埋め込みの失敗                                | 503（「記憶を保存できませんでした。…」）              |

MVP の `memory_extraction`（返答と並行した抽出）は廃止した。古い行に残っているだけ。

記憶が自動で消えるのは、ペアの記憶が上限（`MEMORY_MAX_PER_CHARACTER`）に達して、自動の分析・要約が置き換えられた履歴 → 重要度の最も低い自動記憶と入れ替えたときだけ
（`memory.delete` の `source=capacity_eviction`。ユーザーの削除は `source=user`）。中期要約を諦めた区間は `llm.error`（`purpose=memory_summary`・
`skipped=true`・`skipped_messages`）に残る（[ADR-0028](../adr/0028-llm-input-budgets-and-summary-retries.md)）。どちらも日次で件数を見る（下の SQL）。

## 監査ログ（audit_logs）の調べ方

イベント種別と payload は [ADR-0013](../adr/0013-audit-log.md)。`created_at` は UTC なので、日次集計は `at time zone 'Asia/Tokyo'` で日本時間にする。

```sql
-- メールアドレスからユーザー ID
select id, email, created_at, last_sign_in_at from auth.users where email = 'user@example.com';

-- あるユーザーの全イベント（DD: 会話・生成・判定のすべて）
select id, created_at, event_type, character_id, payload->>'request_id' as request_id, payload
  from public.audit_logs
 where user_id = '<user uuid>'
 order by id;

-- チャットのリクエスト / レスポンス（A12）。request_id で対になる
select created_at, event_type, payload->>'request_id' as request_id,
       payload->>'message' as message, payload->>'reply' as reply,
       payload->>'model' as model, (payload->>'latency_ms')::int as latency_ms,
       payload->'memories_used' as memories_used
  from public.audit_logs
 where event_type in ('chat.request', 'chat.response') and user_id = '<user uuid>'
 order by id desc
 limit 20;

-- 1 リクエストの全イベント（問い合わせのあった X-Request-ID で）
select created_at, event_type, user_id, payload
  from public.audit_logs
 where payload->>'request_id' = '<request id>'
 order by id;

-- Gate #1 のヒット数（日別・入力 / 出力・画面別）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst,
       payload->>'stage' as stage, payload->>'context' as context,
       count(*) as flags, count(distinct user_id) as users
  from public.audit_logs
 where event_type = 'moderation.flag' and created_at >= now() - interval '30 days'
 group by 1, 2, 3
 order by 1 desc, 2, 3;

-- Gate #1 のカテゴリ別件数
select c.category, count(*)
  from public.audit_logs a
 cross join lateral jsonb_array_elements_text(a.payload->'categories') as c(category)
 where a.event_type = 'moderation.flag' and a.created_at >= now() - interval '30 days'
 group by 1
 order by 2 desc;

-- チャットのレイテンシ（日別。latency_ms = API 全体、llm_latency_ms = 返答生成）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst,
       count(*) as responses,
       percentile_cont(0.5)  within group (order by (payload->>'latency_ms')::int) as p50_ms,
       percentile_cont(0.95) within group (order by (payload->>'latency_ms')::int) as p95_ms,
       max((payload->>'latency_ms')::int) as max_ms,
       percentile_cont(0.5)  within group (order by (payload->>'llm_latency_ms')::int) as llm_p50_ms,
       count(*) filter (where (payload->>'moderated')::boolean) as moderated
  from public.audit_logs
 where event_type = 'chat.response' and created_at >= now() - interval '30 days'
 group by 1
 order by 1 desc;

-- llm.error の用途別の件数（直近 1 時間。アラート用: embedding_query / memory_save が続けて出ていたら埋め込み API の障害）
select payload->>'purpose' as purpose, count(*), max(created_at) as last_at,
       max(payload->>'embedding_model') as embedding_model, max(payload->>'error') as sample_error
  from public.audit_logs
 where event_type = 'llm.error' and created_at >= now() - interval '1 hour'
 group by 1
 order by 2 desc;

-- 長期記憶の検索を省略した返答の割合（日別。埋め込みの障害の影響範囲）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst, count(*) as responses,
       count(*) filter (where (payload->>'retrieval_skipped')::boolean) as retrieval_skipped,
       count(*) filter (where payload->>'memory_save_error' is not null) as memory_save_failed
  from public.audit_logs
 where event_type = 'chat.response' and created_at >= now() - interval '30 days'
 group by 1
 order by 1 desc;

-- 上限による記憶の入れ替えと、諦めた中期要約の区間（日別）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst,
       count(*) filter (where event_type = 'memory.delete' and payload->>'source' = 'capacity_eviction') as evicted_memories,
       count(*) filter (where event_type = 'llm.error' and payload->>'purpose' = 'memory_summary'
                          and coalesce((payload->>'skipped')::boolean, false)) as skipped_summary_chunks,
       coalesce(sum((payload->>'skipped_messages')::int)
                  filter (where event_type = 'llm.error' and payload->>'purpose' = 'memory_summary'), 0) as skipped_messages
  from public.audit_logs
 where event_type in ('memory.delete', 'llm.error') and created_at >= now() - interval '30 days'
 group by 1
 order by 1 desc;

-- LLM のエラー（日別・用途別・HTTP ステータス別）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst,
       payload->>'purpose' as purpose, payload->>'status_code' as status_code, count(*)
  from public.audit_logs
 where event_type = 'llm.error' and created_at >= now() - interval '30 days'
 group by 1, 2, 3
 order by 1 desc, 4 desc;

-- トークン使用量（日別・モデル別。プロバイダが usage を返す場合のみ）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst, payload->>'model' as model,
       count(*) as responses, sum((payload->'usage'->>'total_tokens')::bigint) as total_tokens
  from public.audit_logs
 where event_type = 'chat.response' and payload->'usage' is not null
   and created_at >= now() - interval '30 days'
 group by 1, 2
 order by 1 desc;

-- DM を送ったユーザー数（日別）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst,
       count(distinct user_id) as chat_users, count(*) as chats
  from public.audit_logs
 where event_type = 'chat.request' and created_at >= now() - interval '30 days'
 group by 1
 order by 1 desc;

-- あるユーザーの記憶の変化（作成・更新・削除・要約）
select created_at, event_type, payload->>'source' as source, payload->>'content' as content, payload->'changes' as changes
  from public.audit_logs
 where event_type like 'memory.%' and user_id = '<user uuid>'
 order by id;

-- イベント種別ごとの件数と期間 / 容量
select event_type, count(*), min(created_at), max(created_at) from public.audit_logs group by 1 order by 2 desc;
select pg_size_pretty(pg_total_relation_size('public.audit_logs')) as audit_logs,
       pg_size_pretty(pg_database_size(current_database())) as db;
```

CSV で渡す場合（DD 資料など）: `psql "$DATABASE_URL" -c "\copy (select ...) to 'audit.csv' csv header"`。
**会話本文・プロンプト全文（個人情報）を含む**ので、出力先と共有範囲に注意する。

## キャラクターエンジンの状態の調べ方

ジョブ・定期実行は [ADR-0036](../adr/0036-engine-job-queue-and-scheduler.md)、イベント種別は [ADR-0035](../adr/0035-character-engine-architecture.md) の「監査ログ（E9）」。
以下はローカル DB で構文を確認済み（読み取りだけ。変更する SQL は `begin;` 〜 `commit;`）。

```sql
-- ジョブの状態の件数と、実行時刻を過ぎて待っているジョブの最大の待ち（worker が止まっていないか）
select status, kind, count(*) as jobs,
       max(now() - run_at) filter (where status = 'queued' and run_at <= now()) as max_wait
  from public.engine_jobs
 group by 1, 2
 order by 1, 2;

-- 諦めた（dead）・再試行しない（failed）ジョブ（直近 7 日。完了・失敗のジョブは ENGINE_JOB_RETENTION_DAYS 日で消える）
select id, kind, dedupe_key, attempts, max_attempts, finished_at, left(last_error, 200) as last_error, payload
  from public.engine_jobs
 where status in ('dead', 'failed') and finished_at >= now() - interval '7 days'
 order by finished_at desc
 limit 50;

-- running のまま止まっているジョブ（ENGINE_JOB_LOCK_TIMEOUT_SECONDS = 10 分を過ぎたものは worker が queued に戻す）
select id, kind, dedupe_key, locked_by, locked_at, attempts
  from public.engine_jobs
 where status = 'running' and locked_at < now() - interval '10 minutes'
 order by locked_at;

-- 定期実行の前回・次回（名前に ":" が入っているのは評価ハーネス・テストの名前空間）
select name, last_run_at at time zone 'Asia/Tokyo' as last_run_jst, next_run_at at time zone 'Asia/Tokyo' as next_run_jst, last_error
  from public.engine_schedules
 where name not like '%:%'
 order by name;

-- エンジンの注意が要るイベント（日別）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst, event_type, count(*)
  from public.audit_logs
 where (event_type like 'engine.%'
        or event_type in ('safety.trigger', 'affinity.manipulation_detected', 'memory.injection_skipped',
                          'memory.tombstone_suppressed', 'proactive.dropped', 'calendar.conflict'))
   and created_at >= now() - interval '7 days'
 group by 1, 2
 order by 1 desc, 3 desc;

-- 最初の文字までの時間（E8。ttft_ms = 認証の後から最初の delta / replace まで）と、文脈の一部を省いた返答の数
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst, count(*) as responses,
       percentile_cont(0.5)  within group (order by (payload->>'ttft_ms')::int) as ttft_p50_ms,
       percentile_cont(0.95) within group (order by (payload->>'ttft_ms')::int) as ttft_p95_ms,
       percentile_cont(0.5)  within group (order by (payload->>'llm_first_chunk_ms')::int) as llm_first_chunk_p50_ms,
       count(*) filter (where jsonb_array_length(coalesce(payload->'context_degraded', '[]'::jsonb)) > 0) as context_degraded
  from public.audit_logs
 where event_type = 'chat.response' and created_at >= now() - interval '30 days'
 group by 1
 order by 1 desc;

-- LLM のトークン使用量（日別・用途別。E7 の月額の見積もり。価格表を掛け、下の月間の DM 利用者数で割る）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst, event_type,
       payload->>'model' as model, count(*) as calls,
       sum((payload->'usage'->>'prompt_tokens')::bigint) as prompt_tokens,
       sum((payload->'usage'->>'completion_tokens')::bigint) as completion_tokens,
       sum((payload->'usage'->>'prompt_cache_hit_tokens')::bigint) as cache_hit_tokens
  from public.audit_logs
 where event_type in ('chat.response', 'memory.analysis', 'memory.summary', 'affinity.update',
                      'proactive.send', 'proactive.dropped', 'calendar.post_create', 'llm.error')
   and jsonb_typeof(payload->'usage') = 'object'
   and created_at >= now() - interval '30 days'
 group by 1, 2, 3
 order by 1 desc, 2;

select date_trunc('month', created_at at time zone 'Asia/Tokyo') as month_jst, count(distinct user_id) as chat_users
  from public.audit_logs
 where event_type = 'chat.request'
 group by 1
 order by 1 desc;

-- 自発メッセージ（日別・きっかけ別。message_id が null = 検査で差し止めた記録）
select date_trunc('day', sent_at at time zone 'Asia/Tokyo') as day_jst, trigger,
       count(*) filter (where message_id is not null) as sent,
       count(*) filter (where message_id is null) as dropped,
       count(*) filter (where replied_at is not null) as replied
  from public.proactive_messages
 where sent_at >= now() - interval '30 days'
 group by 1, 2
 order by 1 desc, 2;

-- キャラの今の状態（calendar.tick が 5 分ごとに更新する。updated_at が古ければスケジューラが止まっている）
select c.handle, s.status_label, s.busyness, s.updated_at at time zone 'Asia/Tokyo' as updated_jst
  from public.characters c
  left join public.character_states s on s.character_id = c.id
 where c.is_active
 order by s.updated_at nulls first;

-- E6 の安全対応（日別）
select date_trunc('day', created_at at time zone 'Asia/Tokyo') as day_jst, count(*) as triggers, count(distinct user_id) as users
  from public.audit_logs
 where event_type = 'safety.trigger' and created_at >= now() - interval '30 days'
 group by 1
 order by 1 desc;

-- あるユーザーの記憶・約束の変化（分析・置き換え・墓標・ユーザーの操作）
select created_at, event_type, payload->>'op' as op, payload->>'kind' as kind, payload->>'content' as content, payload->>'source' as source
  from public.audit_logs
 where (event_type like 'memory.%' or event_type like 'promise.%') and user_id = '<user uuid>'
 order by id;

-- あるユーザーの好感度の変化（調査用。ユーザーには見せない）
select created_at, payload->'before' as before, payload->'after' as after, payload->>'stage_before' as stage_before,
       payload->>'stage_after' as stage_after, payload->>'turns_manipulation' as manipulation_turns
  from public.audit_logs
 where event_type in ('affinity.update', 'affinity.stage_change') and user_id = '<user uuid>'
 order by id;
```

### dead のジョブを再実行する / 定期実行をすぐに動かす

`last_error` で原因（LLM・埋め込みの障害、データの不整合）を直してから行う。同じ会話の未処理の `post_turn` が既にあると一意索引で失敗する（その場合は再実行しなくてよい。
新しいジョブが同じターンから処理する）。

```sql
begin;
update public.engine_jobs
   set status = 'queued', attempts = 0, run_at = now(), last_error = null, finished_at = null, locked_at = null, locked_by = null
 where id = <job id> and status in ('dead', 'failed');
-- 1 行更新されたことを確認してから
commit;

-- 定期実行を次の確認（ENGINE_SCHEDULER_POLL_INTERVAL_SECONDS = 30 秒）で動かす（例: キャラを足した直後に予定を作る）
begin;
update public.engine_schedules set next_run_at = now() where name = 'calendar.ensure_schedules';
commit;
```

## 障害対応

| 症状                                                        | 確認すること                                                                                  | 対処                                                                                                                                                    |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 全員が 503 `internal_error`（「ログイン状態を確認できません」）| `fly logs` の `failed to fetch JWKS`、Supabase のステータス                                    | Supabase Auth（JWKS）に接続できない。401 にしないので Web はログアウトしない。Supabase の復旧を待つ（[ADR-0025](../adr/0025-db-tls-and-api-entry-failures.md)） |
| キャラが以前の話を思い出さない（返答は届く）                  | `llm.error` の `embedding_query` / `memory_save`、`chat.response` の `retrieval_skipped`       | 埋め込み API の障害・キー・残高。`EMBEDDING_TIMEOUT_SECONDS` が短すぎないか（[ADR-0022](../adr/0022-embedding-failures-and-audit-additions.md)） |
| DM で返答が来ない / 503 `llm_unavailable`                    | `audit_logs` の `llm.error`（`status_code` / `error`）、`fly logs`                             | 提供元の障害・残高・キー。DeepSeek 直に切り替えるなら `fly.toml` の `LLM_BASE_URL=https://api.deepseek.com/v1`・`LLM_MODEL=deepseek-chat` とキーを変えてデプロイ。`error` が `deadline exceeded` なら LLM が遅い（[ADR-0019](../adr/0019-chat-deadline.md)） |
| API・worker が起動しない / デプロイが失敗する                  | `fly logs` の設定検証エラー（`ValidationError`）                                              | `LLM_API_KEY` 未設定、`APP_ENV=production` で `LLM_MODE=mock`、`SUPABASE_URL` が https でない / ローカルのまま、`CORS_ALLOW_ORIGINS` がローカルのまま、`DATABASE_URL` に `sslmode=verify-full`（または `require`）が無い、ペルソナ YAML の不正（`age` < 20・`engine:` の未知のキーなど）、相談窓口の YAML（`SAFETY_RESOURCES_PATH`）の形式の誤り、`ENGINE_PRICE_TABLE_JSON` の形式の誤り、`ENGINE_JOB_BACKOFF_BASE_SECONDS` > `…_MAX_SECONDS`、テンプレートのプレースホルダの誤り、DB に接続できない |
| 「覚えました」が出ない・記憶や約束が作られない（返答は届く）    | 会話が止まってから **約 3 分**（`ENGINE_POST_TURN_DELAY_SECONDS` = 180 秒。話し続けると最大 18 分）待ったか。`fly status` の worker、[ジョブの SQL](#キャラクターエンジンの状態の調べ方) の待ち・dead、`llm.error`（`memory_analysis`） | 仕様どおりの遅れなら対処不要。worker が止まっていれば `fly scale count app=1 worker=1 --config apps/api/fly.toml`。LLM・埋め込みの障害ならジョブは再試行される。`memory.tombstone_suppressed`（削除した記憶と同じ）・`memory.user_edited_skipped`・`memory.injection_skipped` なら意図どおり |
| DM ヘッダーの状況が「アクティブ」のまま・予定からの投稿が無い   | `engine_schedules` の `calendar.tick` / `calendar.ensure_schedules` の `last_run_at`・`last_error`、`engine.schedule_failed`、`character_states.updated_at` | worker（スケジューラ）の停止、`ENGINE_CALENDAR_ENABLED=false`、ペルソナの `engine:` の不備（フォールバックの予定になる）。キャラを足した直後なら [定期実行をすぐに動かす](#dead-のジョブを再実行する--定期実行をすぐに動かす) |
| 自発メッセージが届かない                                    | `proactive_messages`・`proactive.dropped`・`proactive_settings`                                  | 多くは仕様どおり: 送らない時間帯（既定 0〜7 時 JST）・1 日 3 通・知り合いの段階（約束の期日以外は送らない）・未返信の自発メッセージが最後にある（P4）・キャラが忙しい / 寝ている・30 日以上話していない・停止の設定。worker の停止、`ENGINE_PROACTIVE_ENABLED=false` も確認 |
| 返答の最初の文字が遅い                                      | `chat.response` の `ttft_ms` / `llm_first_chunk_ms` / `context_timings_ms` / `context_degraded`（[SQL](#キャラクターエンジンの状態の調べ方)） | `llm_first_chunk_ms` が大半ならモデル・プロバイダの遅さ（[ADR-0046](../adr/0046-engine-cost-and-latency.md)）。`app` グループで `ENGINE_WORKER_ENABLED` / `ENGINE_SCHEDULER_ENABLED` が true になっていないか（ジョブが返答と CPU を取り合う） |
| `engine.job_dead` が出る                                     | [dead のジョブの SQL](#キャラクターエンジンの状態の調べ方) の `last_error`                     | 原因を直して [再実行](#dead-のジョブを再実行する--定期実行をすぐに動かす)。`post_turn` は最後の試行で失敗した手順を飛ばして進むので、dead は手順の外（DB など）の失敗 |
| 普通の会話に相談窓口の案内が返る（E6 の誤検知）               | `safety.trigger` の `matched` / `text`                                                          | 再現率を優先した設計（冗談の「死にたい」も拾う）。拾うべきでない形なら `detector.py` とテストの「検出してはいけない文」に足す（[05](05-memory-and-moderation.md#安全対応e6)） |
| 全員が 401                                                  | `fly logs` の `auth.failure`（`reason`）                                                       | `SUPABASE_URL` のプロジェクト違い、旧 HS256 のプロジェクトで `SUPABASE_JWT_SECRET` 未設定、`iss` の不一致（`SUPABASE_JWT_ISSUER`）                        |
| ブラウザのコンソールに CORS エラー                          | Web のオリジン（`https://` + ドメイン、末尾 `/` なし）                                         | `fly secrets set --config apps/api/fly.toml CORS_ALLOW_ORIGINS=https://...`（カンマ区切り。設定すると再起動される）                                      |
| ログインメールが届かない                                     | Supabase の Auth ログ、SMTP 設定、Rate Limits（Emails sent）                                   | [supabase-auth.md](supabase-auth.md) の 4・5                                                                                                            |
| 全員のログインメールが届かない（Auth のログに `/auth/v1/otp` の 429 `over_email_send_rate_limit` が多数） | 要求元の IP・宛先の偏り                                                           | プロジェクト全体の送信枠（Emails sent）が使い切られている。CAPTCHA が未導入のため、ボットによる大量要求で起こり得る（[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md) の残存リスク）。枠を一時的に上げる・Network Restrictions / 送信元の遮断を検討し、恒久対策として hCaptcha を Web と合わせて導入する |
| ログインすると `/login?error=banned`（「このアカウントは利用停止中です。」） | `auth.users.banned_until`                                                                       | 運営が利用停止にしたアカウント。解除は [ユーザーの利用停止](#ユーザーの利用停止ban) の `ban_duration: "none"`                                        |
| メールに 6 桁コードが無い / リンクが `/auth/confirm` でない  | Email Templates（Magic Link と Confirm signup の両方）                                         | [supabase-auth.md](supabase-auth.md) の 3                                                                                                               |
| ログインするとすぐ `/login?error=withdrawn`                  | `profiles.deleted_at`                                                                          | 退会済み。[退会ユーザーの復旧](#退会ユーザーの復旧)                                                                                                     |
| 429 `rate_limited`                                          | ユーザー・バケット                                                                             | [レート制限とスケール](#レート制限とスケール)                                                                                                            |
| 413（「送信内容が大きすぎます。」）                          | リクエストの `Content-Length`                                                                   | 本文が `MAX_REQUEST_BODY_BYTES`（64 KiB）を超えている。正規の画面操作では起きない（`/chat` の最大は約 8 KB）。多発するなら送信元を確認する                   |
| メモリパネルで追加すると 422（「覚えておける記憶は…件まで」） | そのペアの `memories` の件数                                                                   | 上限 `MEMORY_MAX_PER_CHARACTER`（既定 500）。不要な記憶を消してもらうか、上限を上げる（[ADR-0024](../adr/0024-memory-capacity-per-pair.md)） |
| DB の接続エラー / too many connections                      | Supabase の接続数、`DATABASE_POOL_MAX_SIZE` × マシン数（`app` + `worker`）                       | Supavisor（session mode）経由にする。transaction mode（:6543）なら `DATABASE_STATEMENT_CACHE_SIZE=0`（worker は transaction mode にしない。上の「レート制限とスケール」の DB 接続） |
| 新しい DM / コメントがリアルタイムに出ない                   | `select * from pg_publication_tables where pubname = 'supabase_realtime';`                     | `messages` / `comments` が無ければマイグレーションの Realtime 部分を再実行。ネットワーク（wss）の遮断も確認                                               |
| キャラが自動返信しない                                      | `comment.create` の後に `comment.generate` / `llm.error` があるか                              | `COMMENT_AUTO_REPLY_PROBABILITY`、LLM の障害。生成中の再起動・デプロイで失われた返信は再生成されない（[ADR-0014](../adr/0014-comments-via-api-and-auto-reply.md)） |
| 画像が表示されない                                          | Vercel の `NEXT_PUBLIC_STORAGE_DRIVER` / `NEXT_PUBLIC_CDN_BASE_URL` / `BUNNY_TOKEN_AUTH_KEY`   | `NEXT_PUBLIC_*` はビルド時に埋め込まれるので変更後は再デプロイ。`/media/...` が 401 ならログイン切れ、404 なら `BUNNY_TOKEN_AUTH_KEY` 未設定             |
| 記憶を思い出さない・重複して作られる                         | `EMBEDDING_MODE` / `EMBEDDING_MODEL` を最近変えたか、`engine.context_degraded`（`memory`）       | [埋め込み設定の切り替え](#埋め込み設定の切り替え)の再埋め込み。文脈の組み立てが締め切り（1.5 秒）に間に合っていなければ DB・埋め込み API の遅さ     |
| 起動ログに `persona YAML missing for active characters`     | `characters.persona_key` と YAML のファイル名                                                   | YAML をイメージに入れて再デプロイ（それまでは `system_prompt` で代替動作。監査ログの `persona_fallback=true`）                                           |

## 定常作業

### 退会ユーザーの復旧

退会（`/me` の「退会する」）は `profiles.deleted_at` を設定する論理削除で、本人は取り消せない（トリガーで禁止）。本人確認のうえで戻す。
会話・記憶は削除されていないのでそのまま戻る。

```sql
select u.id, u.email, p.deleted_at
  from auth.users u join public.profiles p on p.id = u.id
 where u.email = 'user@example.com';

begin;
update public.profiles set deleted_at = null where id = '<user uuid>';
-- 1 行更新されたことを確認してから
commit;
```

この操作は `audit_logs` に自動では残らない。依頼内容・日時・担当者を運用の記録（チケット等）に残す。

### ユーザーの利用停止（ban）

規約違反などで利用を止める。Supabase Auth の ban はログイン・トークンの更新を止め、Web は次のアカウント確認（画面を開いたとき）で
この端末のセッションを消して `/login?error=banned` を表示する（[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md)）。
管理 API で行う（service_role key を使うので運用者の端末から。キーはリポジトリ・チャットに貼らない）:

```bash
# 利用停止（ban_duration は "876000h" = 約 100 年。期間を決めるなら "720h" など）
curl -X PUT "https://<project-ref>.supabase.co/auth/v1/admin/users/<user uuid>" \
  -H "apikey: <service_role key>" -H "Authorization: Bearer <service_role key>" \
  -H "Content-Type: application/json" -d '{"ban_duration":"876000h"}'
# 解除
curl -X PUT "https://<project-ref>.supabase.co/auth/v1/admin/users/<user uuid>" \
  -H "apikey: <service_role key>" -H "Authorization: Bearer <service_role key>" \
  -H "Content-Type: application/json" -d '{"ban_duration":"none"}'
```

確認は `select banned_until from auth.users where id = '<user uuid>';`（解除後は NULL）。**API は発行済みのアクセストークンを有効期限（最長 1 時間）まで
受け付ける**（JWT の検証と `profiles.deleted_at` だけを見るため）。直ちに API も止める必要があれば、[退会ユーザーの復旧](#退会ユーザーの復旧) の逆で
`profiles.deleted_at` も設定する（API は 403 `account_deleted`。本人の画面には「退会済み」と表示される）。この操作も `audit_logs` には残らないので、運用の記録に残す。

### ユーザーの物理削除

本人からのデータ削除依頼など。**先にコメントを消さないと `auth.users` の削除が check 制約違反で失敗する**（[ADR-0004](../adr/0004-schema-changes-from-spec.md)）。

```sql
begin;
-- 1. コメント（キャラの返信も cascade で消える。削除は comment.delete として audit_logs に残る）
delete from public.comments where author_user_id = '<user uuid>';
-- 2. ユーザー本体（profiles → likes / conversations / messages / memories は cascade で消える）
delete from auth.users where id = '<user uuid>';
commit;
```

キャラクターエンジンの行（約束・墓標・ユーザーとの予定・キャラ側の記憶のそのユーザーの分・好感度・自発メッセージとその設定）も `profiles` からの cascade で消える。
`engine_jobs` は外部キーが無いが、会話が無ければ何もせずに完了し、`ENGINE_JOB_RETENTION_DAYS` 日で消える。
`audit_logs` の行は残る（外部キー無し）。監査ログも消すかは、DD の要件と依頼内容で判断する（消す場合は `delete from public.audit_logs where user_id = ...`）。
`memories.source_message_id` と `comments.author_user_id` には索引があるので、会話の多いユーザーでも削除は速い（索引が無いとメッセージ 1 件ごとに
`memories` 全体を走査していた）。

### キャラクターを追加する

キャラの人格はペルソナ YAML が正（[ADR-0020](../adr/0020-content-seed-generation.md)）。管理画面は無い（仕様書 §12）。

1. `packages/personas/<key>.yaml` を作る（既存をコピー。`key` = ファイル名、`age` は 20 以上、スキーマは [packages/personas/README.md](../../packages/personas/README.md)）。
   キャラクターエンジン用の `engine:` セクション（好感度の感度・段階ごとの振る舞い・生活のルーティンと出来事・行事への反応・自発メッセージの傾向）も書く
   （書き方の順番は README の「キャラクターを追加する手順」。`pnpm personas:validate` が `engine_checks.py` の規則で検査する）。
2. `packages/personas/seed/feed.yaml` の `characters` の **末尾** に追加し、`posts.<key>` に 4〜6 件（有料 1〜2 件）を書く。
3. `pnpm --filter @everkano/personas seed:generate` → `pnpm personas:validate` → ローカルで `pnpm db:reset`（**ローカルの DB を作り直す**）して画面で確認。
4. PR → マージ → **API を再デプロイ**（YAML はイメージに入る。API は起動時に YAML を読む）。
5. 本番 DB に行を入れる（ホスト版には `seed.sql` を再投入できない。固定 UUID の INSERT が重複する）。`system_prompt` は生成された
   `seed.sql` の該当キャラの文字列をコピーする（YAML が読める間は使われないフォールバック）:

```sql
begin;
insert into public.characters (handle, name, persona_key, follower_count, avatar_url, bio, system_prompt)
values ('<handle>', '<name>', '<key>', 0, 'characters/<handle>/avatar.jpg', '<bio>', '<seed.sql からコピーした system_prompt>')
returning id;
commit;
```

投稿は次の「投稿を追加する」の SQL で入れる。キャラクターエンジンは次の `calendar.ensure_schedules`（1 時間以内。[すぐに動かす](#dead-のジョブを再実行する--定期実行をすぐに動かす)）で
予定を作り、`calendar.tick` が状態（DM ヘッダーの表示）と予定からの投稿を作る。キャラ専用の画像を使うなら `post_image_pool` にそのキャラの行を足す（無ければ共通の画像を使う）。

### 投稿を追加する

`image_url` は本番（`bunny` ドライバ）ならオブジェクトキー、`passthrough` なら絶対 URL。`published_at` を未来にすると予約投稿になる。

```sql
-- 無料の投稿
begin;
insert into public.posts (character_id, image_url, caption, published_at)
values ((select id from public.characters where handle = 'misaki_ol'),
        'posts/misaki/2026-10-01-cafe.jpg',
        '出社前の、いつものカフェ☕',
        timestamptz '2026-10-01 08:20:00+09')
returning id;
commit;

-- 有料の投稿（プレビューと本体は互いに推測できない別のキーにする。ADR-0006）
begin;
with p as (
  insert into public.posts (character_id, image_url, caption, is_paid, price_tokens, published_at)
  values ((select id from public.characters where handle = 'misaki_ol'),
          'previews/<uuid A>.jpg', '旅先のオフショット', true, 120, now())
  returning id
)
insert into public.post_private_assets (post_id, image_url)
select id, 'private/<uuid B>.jpg' from p;
commit;

-- 別のキャラからのコメント（SQL で入れたコメントは Gate #1 を通らないので内容は人が確認する）
insert into public.comments (post_id, author_type, author_character_id, body, created_at)
values ('<post id>', 'character', (select id from public.characters where handle = 'hinata_umi'),
        'いいなー！', timestamptz '2026-10-01 08:45:00+09');
```

### 予約投稿を確認する / キャラを非表示にする

```sql
-- これから公開される投稿（RLS により公開時刻まで誰にも見えない）
select p.id, c.handle, p.published_at at time zone 'Asia/Tokyo' as published_at_jst, p.is_paid, left(p.caption, 30) as caption
  from public.posts p join public.characters c on c.id = p.character_id
 where p.published_at > now()
 order by p.published_at;

-- キャラを非表示にする（削除はしない。他の投稿に書いたコメントがあると削除は失敗する。ADR-0004）
update public.characters set is_active = false where handle = '<handle>';
```

シードの投稿は投入時刻基準なので、日が経つと古くなる。キャラクターエンジンは予定が終わったときに投稿を自動で作る（1 キャラ 1 日 2 件まで。画像は `post_image_pool`
から選ぶ。[ADR-0040](../adr/0040-character-calendar.md)）ので、worker が動いていればフィードは更新され続ける。特定の投稿（有料投稿・告知など）は引き続き予約投稿で足す。

### 画像を本番の CDN に移す

1. Backblaze B2 にバケットを作り（非公開推奨）、画像を置く。プレビューと有料の本体は別のキーにする。NSFW 画像を Vercel / Supabase Storage に置かない（H4）。
2. Bunny.net で Pull Zone を作り、オリジンを B2 にする（非公開バケットならオリジンの S3 互換認証に B2 のアプリケーションキーを設定）。
   変換（width / quality / blur）を使うなら Bunny Optimizer を有効にする。トークン認証を使うなら Token Authentication を有効にしてキーを控える。
3. `characters.avatar_url` / `posts.image_url` / `post_private_assets.image_url` をオブジェクトキー（例: `characters/misaki/avatar.jpg`）に更新する。
   絶対 URL の行はそのまま表示されるので、少しずつ移行してよい。予定からの投稿の画像プール `post_image_pool.image_url` も同じ（シードは開発用のプレースホルダで、
   写真の内容はタグと一致しない）。タグ（`TAG_VOCABULARY` の語彙）ごとに共通の画像を 3 枚以上置く。
4. Vercel の環境変数を `NEXT_PUBLIC_STORAGE_DRIVER=bunny`・`NEXT_PUBLIC_CDN_BASE_URL=https://<zone>.b-cdn.net`（トークン認証なら
   `BUNNY_TOKEN_AUTH_KEY` と `BUNNY_TOKEN_TTL_SECONDS`）にして再デプロイする。

### 埋め込み設定の切り替え

`EMBEDDING_MODE`（hash ↔ live）や `EMBEDDING_MODEL` を変えると既存の記憶のベクトルが使えなくなる（次元数が同じなのでエラーにならない）。
切り替えた **直後に** 全件を再埋め込みする（冪等。途中で止まっても再実行してよい）。対象は `memories` と `character_memories`。削除した記憶の墓標は本文が無いので
計算し直せず、切り替えの後は本文のハッシュの一致でだけ復活を止める。

```bash
fly secrets set --config apps/api/fly.toml EMBEDDING_API_KEY=...        # live にする場合
# apps/api/fly.toml の EMBEDDING_MODE / EMBEDDING_MODEL を変更してデプロイ
fly ssh console --config apps/api/fly.toml -C "/opt/venv/bin/python /app/scripts/reembed_memories.py --dry-run"   # 件数確認
fly ssh console --config apps/api/fly.toml -C "/opt/venv/bin/python /app/scripts/reembed_memories.py"
# ローカル: cd apps/api && uv run python scripts/reembed_memories.py [--batch-size 100] [--user-id <uuid>]
```

### シークレットのローテーション

| シークレット                          | 保存場所                         | 手順                                                                                                                  |
| ------------------------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `LLM_API_KEY` / `EMBEDDING_API_KEY`   | Fly.io                           | 提供元で新しいキーを発行 → `fly secrets set --config apps/api/fly.toml LLM_API_KEY=...`（再起動される）→ 古いキーを失効 |
| `DATABASE_URL`（DB パスワード）        | Fly.io                           | Supabase の Database 設定でパスワードを再設定 → すぐに Fly の secret を更新（その間 API は DB に接続できない）          |
| JWT の署名鍵                          | Supabase                         | Supabase の JWT Keys で新しい鍵を作ってローテーション。非対称鍵なら API は JWKS から自動で取り直す（未知の `kid` は即時）。旧 HS256 の JWT Secret を変えた場合は `SUPABASE_JWT_SECRET` も更新 |
| anon / publishable key                | Vercel                           | `NEXT_PUBLIC_SUPABASE_ANON_KEY` を更新して再デプロイ（ビルド時に埋め込まれる）                                         |
| service_role / secret key             | Supabase（アプリでは未使用）      | ダッシュボードで再発行。E2E などで使っている場所があれば更新                                                           |
| `BUNNY_TOKEN_AUTH_KEY`                | Vercel（サーバー専用）            | Bunny.net の Pull Zone で再発行 → Vercel を更新して再デプロイ（発行済みの署名 URL は旧キーでは失効する）               |
| SMTP のパスワード                     | Supabase ダッシュボード           | SMTP 事業者で再発行 → Authentication → Emails → SMTP Settings                                                         |
| B2 のアプリケーションキー              | Bunny.net の Pull Zone           | B2 で新しいキー → Bunny のオリジン設定を更新 → 古いキーを削除                                                          |
| `SENTRY_DSN`                          | Fly.io                           | Sentry でキーを再発行 → `fly secrets set`                                                                              |

ローテーション後は `GET /health` と、DM を 1 往復送って確認する（`fly secrets set` は `app` と `worker` の両方のマシンを再起動する）。値はリポジトリに書かない（CI の `check-secrets.sh`）。

### ホスト版 Supabase の Auth 設定

`config.toml` の Auth 設定はホスト版に自動では反映されない。チェックリストは [supabase-auth.md](supabase-auth.md)。

### API のベースイメージと uv の更新（毎月）

`apps/api/Dockerfile` のベースイメージ（`PYTHON_IMAGE`）と uv（`UV_IMAGE`）は digest（sha256）まで固定している。Dependabot はこの形式を
更新できないため、**毎月 1 回** と、Python / Debian のセキュリティ修正の公開時に手で更新する（手順は `Dockerfile` の先頭）。
`Dockerfile` の `ARG`、`apps/api/fly.toml` の `[build.args]`、`docker-compose.yml` の既定値の 3 か所を同じ値にし、uv の版を変えたら
`.github/workflows/ci.yml` の `setup-uv` の `version` も揃える。PR の CI（checks / api-db）が通ることを確認してからデプロイする。

### 監査ログの抜き取り確認（毎週）

Gate #1 はキーワード照合なので、辞書に無い言い換え・別の文字への置き換えは通る（[ADR-0023](../adr/0023-gate1-latin-and-romaji-terms.md)）。
`chat.request` / `chat.response` / `comment.create` を無作為に抜き取って確認し、見つかった不適切な表現は `apps/api/app/services/moderation.py` の
語彙と `tests/test_moderation.py`（誤爆しやすい一般語のテストも）に追加する。

### 相談窓口の番号の確認（公開前・定期）

E6 の安全対応で案内する相談窓口（`packages/prompts/safety/resources.ja.yaml`）の電話番号・受付時間・URL は **2026-09 時点の情報を元にした未検証の値**。
**公開前に必ず** 各窓口の公式サイトで確かめ、以後も四半期に 1 回程度確かめる。Web の相談窓口のカードの「119 番」の行（`apps/web/components/dm/safety-resource-card.tsx`）も
一緒に確かめる。YAML を直したら API と worker を再デプロイする（起動時に形式を検証する。[ADR-0043](../adr/0043-safety-e6-and-output-guard.md)）。

### live の LLM に切り替える前・モデルや価格が変わったとき

- `LLM_MODEL`（既定 `deepseek/deepseek-chat`）と、評価ハーネスの費用の推計に使う価格表（`ENGINE_PRICE_TABLE_JSON`。未設定なら DeepSeek V3 の既定）を **要確認**。
  2026 年の二次情報では `deepseek-chat` の名前が 2026-07-24 に廃止され、価格も変わったとされる（この環境からは未確認）。提供元の公式の資料で確かめ、`fly.toml` の `LLM_MODEL` と
  `ENGINE_PRICE_TABLE_JSON` を合わせる。
- その上で評価ハーネスを live で実行し（`--llm live --max-cost-jpy 3000`）、E7（1 ユーザー月 ¥100 前後）・E8（最初の文字まで中央値 2.5 秒）と言語の質を確かめる
  （[docs/eval/README.md](../eval/README.md)・[ADR-0046](../adr/0046-engine-cost-and-latency.md)）。

### 評価ハーネスの再実行（プロンプト・パラメータを変えたとき）

プロンプト（`packages/prompts/templates/`）・エンジンの調整値・ペルソナの `engine:` を変えたら、評価ハーネスを再実行して `docs/eval/results/` と `docs/eval/history.md` に
記録する（エンジン仕様書 §9.3）。手順は [docs/eval/README.md](../eval/README.md)、[08-dev-guide.md](08-dev-guide.md#評価ハーネス)。

## バックアップ

- Supabase のプランに応じた日次バックアップ / PITR を有効にする（ダッシュボードの Database → Backups）。
- 本番データを SQL で変更する前に、対象を退避する:
  `supabase db dump --workdir infra --linked --data-only -f <リポジトリの外のパス>/backup.sql`（`supabase link` 済みであること）。
  ダンプには個人データが含まれるので、リポジトリや共有フォルダに置かない。
- `audit_logs` は削除しない前提で増え続ける。保存期間を事業側と決め、期間を過ぎた分は外部（B2 など）へエクスポートしてから削除する運用にする。

## レート制限とスケール

| 項目                     | 現状                                                                                     | スケール時の注意                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| API のレート制限         | ユーザー単位・1 分あたり `/chat` 20、`/comments` + `/comments/generate` 10、`POST` / `PATCH /memories` 30（プロセス内メモリ） | **マシンごとに数える**ので N 台で実質 N 倍。厳密にするなら共有ストア（Redis 等）へ（[ADR-0018](../adr/0018-in-process-rate-limit.md)） |
| API のプロセス           | 1 マシン 1 uvicorn プロセス、`min_machines_running = 1`（コールドスタートさせない）、512 MB。リクエスト本文は 64 KiB まで（巨大な本文でメモリを使わせない） | 台数は `fly scale count <n> --config apps/api/fly.toml`。同時リクエスト soft 40 / hard 80。`hard_limit` を上げるなら `app/core/http.py` の同時接続 200（= `hard_limit` × 2 以上）も上げる（`tests/test_http_limits.py` が検査） |
| バックグラウンド処理     | キャラクターエンジンのジョブ（記憶・約束・好感度・要約）は Postgres のキュー（`engine_jobs`）で worker が処理し、再起動しても失われない。コメントの自動返信だけは FastAPI の BackgroundTask（プロセス内） | 自動返信はデプロイ・再起動の瞬間に失われる（キューへの移行が次の改善候補） |
| worker                   | `worker` プロセスグループ 1 台（512 MB）。`ENGINE_WORKER_CONCURRENCY`（2）本のループでジョブを並行に処理。定期実行はリーダーの 1 台だけ | ジョブが溜まるなら `fly scale count worker=2 --config apps/api/fly.toml` か `ENGINE_WORKER_CONCURRENCY` を上げる（DB の接続数 = プールの最大 × マシン数に注意）。自発メッセージの走査・予定の tick はユーザー・キャラの数に比例する |
| DB 接続                  | `DATABASE_POOL_MAX_SIZE`（10）× マシン数（`app` と `worker` の両方。worker の定期実行はリーダーのロックに接続を 1 本使う） | Supabase のプランの上限内に。多いなら Supavisor 経由。**worker は direct か session mode で接続する**（スケジューラのリーダー選出はセッション単位の `pg_try_advisory_lock` なので、transaction mode（:6543）では正しく動かない） |
| 記憶の検索               | ペアごとの厳密検索。1 ペアの記憶は `MEMORY_MAX_PER_CHARACTER`（500）件まで（[ADR-0024](../adr/0024-memory-capacity-per-pair.md)） | 上限を大きく上げる場合は検索の遅延を測ってから。要約・統合・パーティショニングを検討（[ADR-0005](../adr/0005-vector-index-and-exact-memory-search.md)） |
| 容量                     | `audit_logs`（プロンプト全文を含む）と `memories` が最も増える                             | Supabase の Reports で DB サイズを定期的に確認。`AUDIT_LOG_PROMPTS=false` で削減可能 |
| LLM のコスト             | DM 1 往復で返答の LLM 1 回。返答の後の分析（記憶・好感度）は会話が止まってからまとめて（評価ハーネスで 1 発言あたり約 0.57 回）、要約はチャンクごと、自発メッセージは 1 ユーザー 1 日 3 通まで、予定からの投稿のキャプションは 1 キャラ 1 日 2 件まで。コメント 1 件で最大 1 回 | 1 アクティブユーザーの月額の推計は [ADR-0046](../adr/0046-engine-cost-and-latency.md)（中央の利用で ¥72〜77・mock）。実際の使用量は [上の SQL](#キャラクターエンジンの状態の調べ方) で日次集計 |

Supabase Auth 側のレート制限（メール送信数・コード検証数）は [supabase-auth.md](supabase-auth.md) の 5。

## 既知の制約と次フェーズ

| 項目                                  | 現状                                                                                                  | 対応の方向                                                                                |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 実機確認（A1 / A11）・第三者の環境構築（A15） | 未実施（エミュレーションのみ）                                                                       | [受け入れ検証レポート](../acceptance/report.md) の手順で実施                               |
| 実 LLM での品質確認（A8〜A10）        | ローカル・CI・E2E・評価ハーネスの記録はモック LLM                                                     | staging（`LLM_MODE=live`）と評価ハーネスの live 実行で確認                                |
| Content-Security-Policy               | 未設定（[ADR-0015](../adr/0015-no-csp-in-mvp.md)）                                                   | 公開前に nonce 付き CSP を Report-Only から導入                                           |
| DB 接続のロール                       | API は `postgres` ロール                                                                              | 必要なテーブルだけ grant した専用ロールに切り替える                                        |
| レート制限                            | プロセス内（マシンごと）                                                                              | 複数台にするなら共有ストア                                                                |
| 再送の二重保存                        | ネットワーク断でレスポンスを受け取れずに再送すると、二重に保存され得る。同じタブ内の二重送信は画面を離れて戻っても防ぐが、別のタブ・端末からの同時送信（同じ会話で 2 つの `/chat` が並行）は防いでいない | 冪等キー（[ADR-0019](../adr/0019-chat-deadline.md)）、会話単位の直列化（API） |
| ボット対策（CAPTCHA）                  | 未導入。ログインメールの大量要求でプロジェクト全体の送信枠を使い切れる（全員のログインを一時的に妨げられる）。コードの有効期限は 15 分に短縮済み | Web のログイン画面に hCaptcha を入れ（`captchaToken`）、その後に Supabase の Attack Protection で有効化（順番を逆にすると誰もログインできない。[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md)） |
| API → DB の証明書の検証               | TLS は必須（`sslmode=require` 以上でないと起動しない）。証明書の検証（`verify-full`）には Supabase のルート証明書をマシンに置く作業が必要 | 本番構築時に [07-security.md](07-security.md#ホスト版-supabase-の-db-の設定本番構築時に必須) の手順で `verify-full` にする |
| キャラの返信の一意性                  | コメント 1 件につきキャラの返信 1 件は API（ロックと事前確認）で保証。DB の制約は無い | 部分一意索引 `comments (parent_comment_id) where author_type = 'character'` を追加（[ADR-0027](../adr/0027-comment-reply-generation-limits.md)） |
| コメントの遡り表示                    | 1 投稿で新しい方から 500 件だけを表示（それより前は表示しない旨を出す）                               | `(created_at, id)` のカーソルで「以前のコメントを表示」                                  |
| オフラインの表示                      | 操作したときにエラー（トースト・再送ボタン）で分かる。常設のオフラインバナーは無い                     | タブバー・固定の入力欄と重ならない配置で追加を検討                                        |
| 利用停止の即時性                      | Auth の ban 後も、API は発行済みのアクセストークンを有効期限（最長 1 時間）まで受け付ける              | 即時に止めるなら `profiles.deleted_at` も設定する（[ユーザーの利用停止](#ユーザーの利用停止ban)）。API で ban を確認するなら Auth への問い合わせが増える |
| 権利表示・SBOM                        | `LICENSE` の権利者は「開発委託契約に定める者」の仮の表記。機械可読な SBOM（CycloneDX 等）は生成していない | 依頼者が権利者の名義を確認して `LICENSE` を更新。DD で求められたら SBOM の生成を CI に追加 |
| コメントの自動返信                    | プロセス内の BackgroundTask。再起動で失われる                                                         | キャラクターエンジンのジョブキュー（`engine_jobs`）に移す                                |
| E2E                                   | `apps/web/e2e/`（Playwright）は CI の `e2e` ジョブで main への push・毎晩（03:17 JST）・手動実行（Actions → CI → Run workflow）のときに動く。所要時間が長いため PR ごとには回さない | 失敗したら Actions の `playwright-report` アーティファクトを確認。PR ごとに回す場合はワーカー数と所要時間を見て判断 |
| ハイドレーションエラー（React #418）  | **解決済み**（2026-09-26）。原因は、Next.js 15.5.26 に同梱の React 19.2 canary が、ハイドレーション中に中断した素のホスト要素（`MainShell` の `<main>` の直下の RSC の `children`）を再開するときにハイドレーションの位置を戻さないこと。静的なルート（`/dm` が最も多い）でも起きていた。`components/ui/main-shell.tsx` の `RouteContent` で `children` を包んで解消した（[ADR-0048](../adr/0048-web-confirm-history-hydration-fixes.md)。回帰テスト `e2e/hydration.spec.ts`） | 同じ形（ホスト要素の直下の RSC の `children`）を書かない（`apps/web/README.md`）。上流で直ったら `RouteContent` を外してよい |
| キャラクターエンジンの live の品質     | 評価ハーネスの結果はすべて mock（仕組みの計測）。live の LLM での想起・自己矛盾・状態の反映・費用・最初の文字までの時間は未計測 | API キーを用意して評価ハーネスを live で実行（[live の LLM に切り替える前](#live-の-llm-に切り替える前モデルや価格が変わったとき)） |
| 相談窓口の情報                        | `resources.ja.yaml` の番号・受付時間は未検証                                                        | 公開前に確認（[相談窓口の番号の確認](#相談窓口の番号の確認公開前定期)） |
| 期日を過ぎた約束                      | 閉じられるまで `pending` / `mentioned` のまま（自動の期限切れは未実装）                             | 期限切れの規則を決めて定期実行で閉じる |
| キャラ同士の予定（C10）               | 構造（`participants`）だけ。相手のキャラのカレンダー・フィードに反映しない                          | 相手の予定の生成・投稿を実装 |
| 約束の予定化                          | 期日の精度が週・月・不明の約束はカレンダーに入らない                                                | 必要なら期間の予定として入れる |
| 自発メッセージの A/B テスト            | 設計だけ（[ADR-0042](../adr/0042-proactive-messenger.md)）。群の割り当て・記録は未実装              | 公開後に実装して継続率への効果を測る |
| 有料投稿の告知（P5）                  | 実装はあるが既定で無効（決済が無いため）                                                            | 決済の導入時に、E2 の検査を通して有効化を判断 |
| 画像プール                            | `post_image_pool` のシードは開発用のプレースホルダ                                                  | 本番の画像に差し替える（[画像を本番の CDN に移す](#画像を本番の-cdn-に移す)） |
| 多い利用者の LLM コスト                | 1 日 40 発言の利用で月 ¥192〜205（mock の推計）。目安 ¥100 を超える                                 | [ADR-0046](../adr/0046-engine-cost-and-latency.md) の手段（分析のモデル・記憶の予算・デバウンス） |
| 監査ログの保存期間                    | 未決（増え続ける）                                                                                    | 事業側と合意して運用を決める                                                              |
| モデレーション辞書                    | コードの定数。実在人物名は代表例のみ                                                                  | `TermProvider` を DB 実装にして拡充                                                       |
| エラー監視                            | API のみ Sentry（任意）                                                                               | Web にも導入                                                                              |
| 依存の更新                            | Dependabot（`.github/dependabot.yml`: GitHub Actions / npm / uv を毎週。公開から 7 日経った版のみ）。npm は CI の `pnpm audit --audit-level high`（PR・push・毎晩）。脆弱性のアラート・セキュリティ更新 PR は GitHub の Settings → Code security で有効化する（未設定）。API のベースイメージは[毎月手で更新](#api-のベースイメージと-uv-の更新毎月) | GitHub の設定を有効化。`pip-audit` 等の Python 依存の脆弱性検査を、`apps/api` のロック済みの開発依存として CI に追加（`uvx` で入れるとロックされていない取得になる） |
| **スコープ外（仕様書 §12・次フェーズ）** | 決済・トークン購入（有料投稿は UI のみ）、画像生成（Gate #2〜#5 はその後段の検証）、TTS・音声通話、動画、ユーザー投稿、フォロー、通知、管理画面、多言語 | 次フェーズで要件定義。`scripts/check-scope.sh` が混入を検出する。決済時は `post_private_assets` の署名 URL を返す API を追加する想定（[ADR-0006](../adr/0006-paid-post-private-assets.md)） |
