# @everkano/api — Python API（FastAPI）

DM のキャラ返答生成（メモリエンジン・Gate #1 モデレーション・監査ログ）、コメント投稿とキャラの返信生成、
メモリパネル用の記憶 CRUD を提供する。H5 に従い Vercel ではなく Fly.io 等の別ホストで動かす。

- 契約: 入出力の型は `packages/shared/src/api.ts`（Pydantic モデル `app/models/*.py` と一致。`tests/test_openapi_contract.py` で検証）
- OpenAPI: `docs/api/openapi.json`（`pnpm --filter @everkano/api openapi` で再生成、`openapi:check` で差分検出）
- 環境変数: リポジトリ直下の `.env.example`（API セクション）。`app/core/config.py` が起動時に検証する

## ローカル開発

```bash
pnpm db:start                         # Supabase ローカルスタック（Postgres :54322 / Auth :54321）
cp .env.example apps/api/.env          # もしくは pnpm setup:env
cd apps/api
uv sync                                # Python 3.12 の仮想環境を .venv に作成
uv run uvicorn app.main:app --reload --port 8000   # = pnpm --filter @everkano/api dev
curl http://localhost:8000/health
```

`LLM_MODE=mock` / `EMBEDDING_MODE=hash`（既定）なら外部 API キー無しで動く。モック LLM はペルソナの口調例・
一人称/呼び方・`schedule_pattern`（日本時間）と、検索された記憶を使って決定的に返答する
（例: 「来週、大阪に出張するんだ」→ 後日「大阪のお土産…」に「そういえば、来週、大阪に出張するって言ってたよね。」）。

Docker で動かす場合（ビルドコンテキストはリポジトリのルート）:

```bash
docker compose up --build api          # ルートの docker-compose.yml
```

## 品質ゲート

```bash
uv run ruff check . && uv run ruff format --check .   # = pnpm --filter @everkano/api lint
uv run mypy app                                        # strict
uv run pytest -q                                       # 統合テストはローカル Supabase が無ければ skip
uv run python scripts/validate_personas.py             # packages/personas/*.yaml の検証
```

統合テスト（`tests/integration/`）はローカル Supabase の Postgres（`TEST_DATABASE_URL` で変更可）に、
自前のユーザー（`auth.users`）・キャラ・投稿を作って実行し、終了時にすべて削除する。JWT は HS256
（テスト用シークレット）で署名する。ES256（JWKS）経路は `tests/test_security.py` の偽 JWKS で検証している。

## エンドポイント

| Method | Path | 認証 | 概要 |
|---|---|---|---|
| GET | `/health` | 不要 | 死活監視（DB 疎通を含む） |
| POST | `/conversations` | 要 | 会話の取得または作成（新規時はペルソナの `greeting` を保存） |
| POST | `/chat` | 要 | DM 返答生成（§7 の処理フロー。レート制限 `RATE_LIMIT_CHAT_PER_MINUTE`） |
| GET | `/memories?character_id=` | 要 | 自分の記憶一覧（重要度降順。上限 `MEMORY_MAX_PER_CHARACTER` 件まで全件） |
| POST | `/memories` | 要 | 記憶を追加（`is_user_edited=true`）。上限到達で 422、`summary` タグは指定不可。レート制限 `RATE_LIMIT_MEMORIES_PER_MINUTE` |
| PATCH | `/memories/{id}` | 要 | 内容・重要度・タグを更新（`is_user_edited=true`、内容変更時は再 embedding）。レート制限は POST と共通 |
| DELETE | `/memories/{id}` | 要 | 削除（204） |
| POST | `/comments` | 要 | コメント投稿（Gate #1 で拒否なら 422 `moderation_blocked`）。確率で投稿者キャラが自動返信 |
| POST | `/comments/generate` | 要 | 投稿者キャラが**自分の**コメントに返信（他人のコメントは 404。返信はコメント1件につき1件まで・既存があればそれを返す。出力が拒否されたら `comment: null`） |

エラーはすべて `{"error": {"code", "message", "request_id"}}`（message は日本語）。全レスポンスに `X-Request-ID`。
本文が `MAX_REQUEST_BODY_BYTES`（既定 64KiB）を超えるリクエストは、認証より前に本文を読まずに 413 `validation_error`。
認証サーバー（JWKS）に接続できないときは 401 ではなく 503 `internal_error`（`Retry-After: 5`。Web はログアウトしない）。

## 構成

```
app/
  main.py              アプリファクトリ create_app()、lifespan（DB プール・ペルソナ/テンプレート読込・HTTP クライアント）
  container.py         サービスの組み立て（Services）とレート制限の依存関係
  core/                config（環境変数検証）/ security（Supabase JWT: ES256・RS256=JWKS, HS256=共有鍵）/
                       logging（JSON Lines）/ db（asyncpg）/ errors / middleware（X-Request-ID・アクセスログ・本文サイズ上限）/
                       http（LLM・埋め込み用と JWKS 用の httpx クライアント）/ observability（Sentry の初期化とスクラブ）
  routers/             薄いルーター（health / conversations / chat / memories / comments）
  services/
    chat.py            POST /chat のオーケストレーション
    memory.py          メモリエンジン（短期・長期検索・抽出/保存/重複排除・中期要約）
    memory_capacity.py ユーザー × キャラあたりの記憶の上限（入れ替え・ペア単位ロック）
    llm.py             OpenAI 互換クライアント（リトライ付き）/ MockLLM
    embedding.py       OpenAI 互換 /embeddings / 文字 n-gram ハッシュ埋め込み
    persona.py         ペルソナ YAML の読込・検証（age >= 20）
    prompt.py          packages/prompts テンプレートの描画
    moderation.py      Gate #1（語彙リスト + 正規化。TermProvider で差し替え可能）
    audit.py           監査ログ（audit_logs + stdout）
    rate_limit.py      プロセス内スライディングウィンドウ
    comments.py / conversations.py / user_memories.py
  models/              Pydantic スキーマ（api.ts と一致）
scripts/               export_openapi.py / validate_personas.py / reembed_memories.py（埋め込み設定の切り替え後に実行）
tests/                 単体テスト + integration/（ローカル Supabase）
```

## 設計上の注意

- API は `postgres` ロールで接続し RLS をバイパスするため、**全クエリを検証済み user_id でスコープ**している
  （他人の会話・記憶は 404）。新しいクエリを追加するときも必ず守ること。
- 長期メモリの検索は (user, character) に絞った**厳密検索**（`MATERIALIZED` CTE）。HNSW は使わない（ADR-0005）。
- ユーザーが編集した記憶（`is_user_edited`）は自動抽出の重複排除で上書きしない。
- レート制限はプロセス内メモリ。Fly.io で複数マシンにスケールする場合はマシンごとのカウントになる。
- 監査ログの DB 書き込みはチャットのトランザクションとは別接続。失敗しても応答は返すが ERROR ログを出す。
  stdout への複製（INFO）は `LOG_LEVEL=WARNING` 以上でも出る（`everkano.audit` ロガーは常に INFO 以下）。
- `/chat` 全体の上限は `CHAT_DEADLINE_SECONDS`（既定 38 秒）。応答生成が間に合わなければ 503 `llm_unavailable`
  で何も保存しない。記憶抽出は残り時間だけ待つ（間に合わなければ候補なし + audit `llm.error`）。
  **Web の `CHAT_TIMEOUT_MS`（45 秒）より必ず短くする**（クライアントが諦めた後に保存され、再送で二重になるのを防ぐ）。
- Gate #1（入力）で差し止めた発言も `messages` には保存するが、以後の LLM 履歴・記憶抽出の文脈では本文を
  「（不適切な発言のため省略）」に置き換え、中期要約からはそのターンごと除く（`memory.sanitize_history`）。
- 検索用の埋め込みは `EMBEDDING_TIMEOUT_SECONDS`（既定 5 秒・リトライ `EMBEDDING_MAX_RETRIES` = 1）で打ち切り、
  失敗しても長期記憶の検索を省略して返答する（埋め込み障害で `/chat` 全体を 503 にしない）。
  `/memories` の埋め込みは 10 秒で打ち切って 503（Web の 15 秒より前に返し、再送による二重保存を防ぐ）。
  埋め込みの失敗はどれも audit `llm.error` に残す（`purpose`: `embedding_query` = 検索を省略 / `memory_save` =
  抽出した記憶を保存できなかった / `user_memory` = メモリパネルの追加・編集が 503 / `memory_summary_embedding` =
  要約を埋め込み無しで保存）。`chat.response` にも `retrieval_skipped` / `memory_save_error` を記録する。
- 応答生成に渡す履歴（短期メモリ・直近 30 ターン）は合計 16,000 字まで（`prompt.HISTORY_MAX_CHARS`）。直近 6 件は全文、
  それより古い発言は 500 字に切り詰め、上限を超える古い分は渡さない（長文の貼り付けでプロンプト・待ち時間・費用が
  膨らまないように。`chat.response` の `prompt_chars` で監視できる）。
- 中期要約は古い順に、会話ログの文字数上限（`TRANSCRIPT_MAX_CHARS` = 12000）に収まる分ずつ要約し、カーソルは要約に
  含めた分までしか進めない（1 回のバックグラウンド処理で最大 3 チャンク）。失敗は会話ごとに指数バックオフ（60 秒〜1 時間）し、
  同じチャンクで 3 回失敗するか内容で拒否（HTTP 400/413/422）されたらそのチャンクを飛ばす（audit `llm.error` の `skipped=true`）。
  要約の埋め込みに失敗した場合は埋め込み無しで保存する（最新 2 件は常に注入されるため使われる）。
- ユーザー × キャラの記憶は `MEMORY_MAX_PER_CHARACTER`（既定 500）件まで。ユーザーの追加は上限で 422、自動抽出・要約は
  重要度の最も低い自動記憶（ユーザー編集済み・要約を除く）を入れ替える（audit `memory.delete`、`source=capacity_eviction`）。
- 記憶の本文・コメント本文はプロンプトに入れる前に改行を空白にして 1 行にする（見出しの偽造対策）。`summary` タグは
  利用者が新たに付けられない。キャラのコメント返信（公開）は Gate #1 に加えて URL・ドメイン名も差し止める。
- 外部 HTTP: LLM・埋め込み用のクライアントは同時接続 200（`/chat` 1 件で 2 本使うため、`fly.toml` の `hard_limit` × 2 以上。
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
```

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
