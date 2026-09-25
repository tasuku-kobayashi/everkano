# 06. 運用手順（ランブック）

SQL はすべて Supabase ダッシュボードの SQL Editor（`postgres` ロール = RLS をバイパス）か `psql "$DATABASE_URL"` で実行する想定。
**本番データを変更する SQL は `begin;` で始め、件数を確認してから `commit;`** する。以下の SQL はローカル DB で構文を確認済み。

目次: [アカウント](#引き継ぐアカウント) / [デプロイとロールバック](#デプロイとロールバック) / [監視とログ](#監視とログ) /
[監査ログの調べ方](#監査ログaudit_logsの調べ方) / [障害対応](#障害対応) / [定常作業](#定常作業) / [バックアップ](#バックアップ) /
[レート制限とスケール](#レート制限とスケール) / [既知の制約と次フェーズ](#既知の制約と次フェーズ)

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
| API（Fly.io）  | リポジトリのルートで `fly deploy --config apps/api/fly.toml --dockerfile apps/api/Dockerfile`             | `fly releases --config apps/api/fly.toml --image` で以前のイメージを確認 → `fly deploy --config apps/api/fly.toml --image <イメージ>` |
| DB（Supabase） | `supabase db push --workdir infra`（新しいマイグレーションだけが適用される。`--dry-run` で事前確認）      | **前進のみ**。戻す内容の新しいマイグレーションを書く。データを壊す変更の前には[バックアップ](#バックアップ)      |

- 互換性の順番: DB の変更は「古い API / Web でも動く形」で先に入れ、次に API、最後に Web。列の削除・改名は 2 回のリリースに分ける。
- Fly.io / Vercel / ホスト版 Supabase のコマンドは、このリポジトリを作成したサンドボックスからは実行できていない（外部に接続できないため）。
  手順は各サービスの公式ドキュメントと突き合わせて確認すること。

## 監視とログ

| 対象                | 見る場所                                                                                                     | 内容                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| API の死活          | `GET /health`（認証不要）。Fly.io が 30 秒ごとに確認（`fly.toml` の `[[http_service.checks]]`）、`fly status` | `{"status":"ok","db":"ok","llm_mode":"live",...}`。DB に届かなければ `degraded` / `db: error`         |
| API のログ          | `fly logs --config apps/api/fly.toml`                                                                        | 1 行 1 JSON（`ts` / `level` / `logger` / `message` / `request_id` ほか）                               |
| API のエラー        | Sentry（`SENTRY_DSN` を設定した場合のみ。`send_default_pii=false`、トレースは無効）                           | 未処理例外                                                                                             |
| Web                 | Vercel の Runtime Logs（middleware・Route Handler）。Web には Sentry を入れていない                           | `[auth/confirm] verifyOtp failed` など                                                                 |
| DB / Auth / Realtime | Supabase ダッシュボードの Logs と Reports                                                                   | ログインメールのトラブルは Auth のログ                                                                  |
| 監査ログ            | `public.audit_logs`（下記）                                                                                  | すべての会話・生成・判定                                                                                |

API のロガー名: `everkano.access`（1 リクエスト 1 行: `method` / `path` / `status` / `duration_ms` / `client_ip` / `user_id`。`client_ip` は
`CLIENT_IP_HEADER`（Fly.io では `Fly-Client-IP`。エッジが上書きするので偽装できない）から取る。Fly.io 以外に移す場合は、そのプロキシが上書きする
ヘッダーに変えるか空にすること。`fly.toml` の `FORWARDED_ALLOW_IPS="*"` のままだと X-Forwarded-For の先頭がクライアントの自由な値になる）、
`everkano.audit`（監査ログの複製。DB への書き込みに失敗すると ERROR `failed to write audit log to database`）、`everkano.auth`
（401 の理由 `auth.failure`: `token_expired` / `invalid_issuer` / `hs256_not_configured` など）、`everkano.llm`（リトライ・失敗）。

**アラートの推奨**（未設定。引き継ぎ後に設定する）: `/health` の失敗、`llm.error` の急増（下の SQL）、ERROR ログ
（特に監査ログの書き込み失敗）、DB の接続数と容量。

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

## 障害対応

| 症状                                                        | 確認すること                                                                                  | 対処                                                                                                                                                    |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DM で返答が来ない / 503 `llm_unavailable`                    | `audit_logs` の `llm.error`（`status_code` / `error`）、`fly logs`                             | 提供元の障害・残高・キー。DeepSeek 直に切り替えるなら `fly.toml` の `LLM_BASE_URL=https://api.deepseek.com/v1`・`LLM_MODEL=deepseek-chat` とキーを変えてデプロイ。`error` が `deadline exceeded` なら LLM が遅い（[ADR-0019](../adr/0019-chat-deadline.md)） |
| API が起動しない / デプロイが失敗する                         | `fly logs` の設定検証エラー（`ValidationError`）                                              | `LLM_API_KEY` 未設定、`APP_ENV=production` で `LLM_MODE=mock`、`SUPABASE_URL` が https でない / ローカルのまま、`CORS_ALLOW_ORIGINS` がローカルのまま、ペルソナ YAML の不正（`age` < 20 など）、DB に接続できない |
| 全員が 401                                                  | `fly logs` の `auth.failure`（`reason`）                                                       | `SUPABASE_URL` のプロジェクト違い、旧 HS256 のプロジェクトで `SUPABASE_JWT_SECRET` 未設定、`iss` の不一致（`SUPABASE_JWT_ISSUER`）                        |
| ブラウザのコンソールに CORS エラー                          | Web のオリジン（`https://` + ドメイン、末尾 `/` なし）                                         | `fly secrets set --config apps/api/fly.toml CORS_ALLOW_ORIGINS=https://...`（カンマ区切り。設定すると再起動される）                                      |
| ログインメールが届かない                                     | Supabase の Auth ログ、SMTP 設定、Rate Limits（Emails sent）                                   | [supabase-auth.md](supabase-auth.md) の 4・5                                                                                                            |
| メールに 6 桁コードが無い / リンクが `/auth/confirm` でない  | Email Templates（Magic Link と Confirm signup の両方）                                         | [supabase-auth.md](supabase-auth.md) の 3                                                                                                               |
| ログインするとすぐ `/login?error=withdrawn`                  | `profiles.deleted_at`                                                                          | 退会済み。[退会ユーザーの復旧](#退会ユーザーの復旧)                                                                                                     |
| 429 `rate_limited`                                          | ユーザー・バケット                                                                             | [レート制限とスケール](#レート制限とスケール)                                                                                                            |
| DB の接続エラー / too many connections                      | Supabase の接続数、`DATABASE_POOL_MAX_SIZE` × マシン数                                          | Supavisor（session mode）経由にする。transaction mode（:6543）なら `DATABASE_STATEMENT_CACHE_SIZE=0`                                                    |
| 新しい DM / コメントがリアルタイムに出ない                   | `select * from pg_publication_tables where pubname = 'supabase_realtime';`                     | `messages` / `comments` が無ければマイグレーションの Realtime 部分を再実行。ネットワーク（wss）の遮断も確認                                               |
| キャラが自動返信しない                                      | `comment.create` の後に `comment.generate` / `llm.error` があるか                              | `COMMENT_AUTO_REPLY_PROBABILITY`、LLM の障害。生成中の再起動・デプロイで失われた返信は再生成されない（[ADR-0014](../adr/0014-comments-via-api-and-auto-reply.md)） |
| 画像が表示されない                                          | Vercel の `NEXT_PUBLIC_STORAGE_DRIVER` / `NEXT_PUBLIC_CDN_BASE_URL` / `BUNNY_TOKEN_AUTH_KEY`   | `NEXT_PUBLIC_*` はビルド時に埋め込まれるので変更後は再デプロイ。`/media/...` が 401 ならログイン切れ、404 なら `BUNNY_TOKEN_AUTH_KEY` 未設定             |
| 記憶を思い出さない・重複して作られる                         | `EMBEDDING_MODE` / `EMBEDDING_MODEL` を最近変えたか                                             | [埋め込み設定の切り替え](#埋め込み設定の切り替え)の再埋め込み                                                                                           |
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

`audit_logs` の行は残る（外部キー無し）。監査ログも消すかは、DD の要件と依頼内容で判断する（消す場合は `delete from public.audit_logs where user_id = ...`）。

### キャラクターを追加する

キャラの人格はペルソナ YAML が正（[ADR-0020](../adr/0020-content-seed-generation.md)）。管理画面は無い（仕様書 §12）。

1. `packages/personas/<key>.yaml` を作る（既存をコピー。`key` = ファイル名、`age` は 20 以上、スキーマは [packages/personas/README.md](../../packages/personas/README.md)）。
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

投稿は次の「投稿を追加する」の SQL で入れる。

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

シードの投稿は投入時刻基準なので、日が経つと「24 時間以内の投稿」が無くなる。フィードが「生きている」状態を保つには、
予約投稿を定期的に足す運用が必要（キャラの `schedule_pattern` に合う時刻で）。

### 画像を本番の CDN に移す

1. Backblaze B2 にバケットを作り（非公開推奨）、画像を置く。プレビューと有料の本体は別のキーにする。NSFW 画像を Vercel / Supabase Storage に置かない（H4）。
2. Bunny.net で Pull Zone を作り、オリジンを B2 にする（非公開バケットならオリジンの S3 互換認証に B2 のアプリケーションキーを設定）。
   変換（width / quality / blur）を使うなら Bunny Optimizer を有効にする。トークン認証を使うなら Token Authentication を有効にしてキーを控える。
3. `characters.avatar_url` / `posts.image_url` / `post_private_assets.image_url` をオブジェクトキー（例: `characters/misaki/avatar.jpg`）に更新する。
   絶対 URL の行はそのまま表示されるので、少しずつ移行してよい。
4. Vercel の環境変数を `NEXT_PUBLIC_STORAGE_DRIVER=bunny`・`NEXT_PUBLIC_CDN_BASE_URL=https://<zone>.b-cdn.net`（トークン認証なら
   `BUNNY_TOKEN_AUTH_KEY` と `BUNNY_TOKEN_TTL_SECONDS`）にして再デプロイする。

### 埋め込み設定の切り替え

`EMBEDDING_MODE`（hash ↔ live）や `EMBEDDING_MODEL` を変えると既存の記憶のベクトルが使えなくなる（次元数が同じなのでエラーにならない）。
切り替えた **直後に** 全件を再埋め込みする（冪等。途中で止まっても再実行してよい）。

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

ローテーション後は `GET /health` と、DM を 1 往復送って確認する。値はリポジトリに書かない（CI の `check-secrets.sh`）。

### ホスト版 Supabase の Auth 設定

`config.toml` の Auth 設定はホスト版に自動では反映されない。チェックリストは [supabase-auth.md](supabase-auth.md)。

## バックアップ

- Supabase のプランに応じた日次バックアップ / PITR を有効にする（ダッシュボードの Database → Backups）。
- 本番データを SQL で変更する前に、対象を退避する:
  `supabase db dump --workdir infra --linked --data-only -f <リポジトリの外のパス>/backup.sql`（`supabase link` 済みであること）。
  ダンプには個人データが含まれるので、リポジトリや共有フォルダに置かない。
- `audit_logs` は削除しない前提で増え続ける。保存期間を事業側と決め、期間を過ぎた分は外部（B2 など）へエクスポートしてから削除する運用にする。

## レート制限とスケール

| 項目                     | 現状                                                                                     | スケール時の注意                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| API のレート制限         | ユーザー単位・1 分あたり `/chat` 20、`/comments` + `/comments/generate` 10（プロセス内メモリ） | **マシンごとに数える**ので N 台で実質 N 倍。厳密にするなら共有ストア（Redis 等）へ（[ADR-0018](../adr/0018-in-process-rate-limit.md)） |
| API のプロセス           | 1 マシン 1 uvicorn プロセス、`min_machines_running = 1`（コールドスタートさせない）、512 MB | 台数は `fly scale count <n> --config apps/api/fly.toml`。同時リクエスト soft 40 / hard 80 |
| バックグラウンド処理     | 中期要約・コメントの自動返信は FastAPI の BackgroundTask（プロセス内）                     | デプロイ・再起動の瞬間に実行中のものは失われる（要約は次の送信で再判定、返信は失われる） |
| DB 接続                  | `DATABASE_POOL_MAX_SIZE`（10）× マシン数                                                  | Supabase のプランの上限内に。多いなら Supavisor 経由                              |
| 記憶の検索               | ペアごとの厳密検索                                                                       | 1 ペアの記憶が数万件になったら要約・統合・パーティショニングを検討（[ADR-0005](../adr/0005-vector-index-and-exact-memory-search.md)） |
| 容量                     | `audit_logs`（プロンプト全文を含む）と `memories` が最も増える                             | Supabase の Reports で DB サイズを定期的に確認。`AUDIT_LOG_PROMPTS=false` で削減可能 |
| LLM のコスト             | DM 1 往復で LLM 2 回（返答 + 記憶抽出）、コメント 1 件で最大 1 回、要約は約 40 メッセージに 1 回 | トークン使用量は上の SQL で日次集計                                               |

Supabase Auth 側のレート制限（メール送信数・コード検証数）は [supabase-auth.md](supabase-auth.md) の 5。

## 既知の制約と次フェーズ

| 項目                                  | 現状                                                                                                  | 対応の方向                                                                                |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 実機確認（A1 / A11）・第三者の環境構築（A15） | 未実施（エミュレーションのみ）                                                                       | [受け入れ検証レポート](../acceptance/report.md) の手順で実施                               |
| 実 LLM での品質確認（A8〜A10）        | ローカル・CI・E2E はモック LLM                                                                        | staging（`LLM_MODE=live`）で確認                                                          |
| Content-Security-Policy               | 未設定（[ADR-0015](../adr/0015-no-csp-in-mvp.md)）                                                   | 公開前に nonce 付き CSP を Report-Only から導入                                           |
| DB 接続のロール                       | API は `postgres` ロール                                                                              | 必要なテーブルだけ grant した専用ロールに切り替える                                        |
| レート制限                            | プロセス内（マシンごと）                                                                              | 複数台にするなら共有ストア                                                                |
| 再送の二重保存                        | ネットワーク断でレスポンスを受け取れずに再送すると、二重に保存され得る                                 | 冪等キー（[ADR-0019](../adr/0019-chat-deadline.md)）                                      |
| コメントの自動返信                    | プロセス内の BackgroundTask。再起動で失われる                                                         | ジョブキュー                                                                              |
| E2E                                   | `apps/web/e2e/`（Playwright）は CI の `e2e` ジョブで**手動実行のみ**（Actions → CI → Run workflow。所要時間が長いため PR ごとには回さない） | PR ごと、または夜間の定期実行にする |
| ハイドレーションエラー（React #418）  | 本番ビルドで動的ルート（`/dm/[characterId]`・`/c/[handle]`）を直接開いたとき、CPU 負荷が高い状況（E2E の並列実行など）で数十〜百回に 1 回程度。React がクライアントで描画し直すので表示と動作に影響は無い。静的ルート（`/`・`/dm`）では発生しない。検証済み: `loading.tsx` をすべて外しても、`htmlLimitedBots` を外しても再現するため、これらは原因ではない。ストリーミング SSR とハイドレーションのタイミングに依存する（負荷をかけないと再現しない）。原因は未特定 | Next.js / React の更新時に再確認。再現は E2E を `--workers=4 --repeat-each=2` で回すと得やすい |
| 監査ログの保存期間                    | 未決（増え続ける）                                                                                    | 事業側と合意して運用を決める                                                              |
| モデレーション辞書                    | コードの定数。実在人物名は代表例のみ                                                                  | `TermProvider` を DB 実装にして拡充                                                       |
| エラー監視                            | API のみ Sentry（任意）                                                                               | Web にも導入                                                                              |
| 依存の更新                            | 自動更新（Dependabot 等）は未設定                                                                     | GitHub で有効化                                                                           |
| **スコープ外（仕様書 §12・次フェーズ）** | 決済・トークン購入（有料投稿は UI のみ）、画像生成（Gate #2〜#5 はその後段の検証）、TTS・音声通話、動画、ユーザー投稿、フォロー、通知、管理画面、多言語 | 次フェーズで要件定義。`scripts/check-scope.sh` が混入を検出する。決済時は `post_private_assets` の署名 URL を返す API を追加する想定（[ADR-0006](../adr/0006-paid-post-private-assets.md)） |
