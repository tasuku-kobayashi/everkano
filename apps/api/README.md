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
| GET | `/memories?character_id=` | 要 | 自分の記憶一覧（重要度降順） |
| POST | `/memories` | 要 | 記憶を追加（`is_user_edited=true`） |
| PATCH | `/memories/{id}` | 要 | 内容・重要度・タグを更新（`is_user_edited=true`、内容変更時は再 embedding） |
| DELETE | `/memories/{id}` | 要 | 削除（204） |
| POST | `/comments` | 要 | コメント投稿（Gate #1 で拒否なら 422 `moderation_blocked`）。確率で投稿者キャラが自動返信 |
| POST | `/comments/generate` | 要 | 投稿者キャラが指定コメントに返信（出力が拒否されたら `comment: null`） |

エラーはすべて `{"error": {"code", "message", "request_id"}}`（message は日本語）。全レスポンスに `X-Request-ID`。

## 構成

```
app/
  main.py              アプリファクトリ create_app()、lifespan（DB プール・ペルソナ/テンプレート読込・HTTP クライアント）
  container.py         サービスの組み立て（Services）とレート制限の依存関係
  core/                config（環境変数検証）/ security（Supabase JWT: ES256・RS256=JWKS, HS256=共有鍵）/
                       logging（JSON Lines）/ db（asyncpg）/ errors / middleware（X-Request-ID・アクセスログ）
  routers/             薄いルーター（health / conversations / chat / memories / comments）
  services/
    chat.py            POST /chat のオーケストレーション
    memory.py          メモリエンジン（短期・長期検索・抽出/保存/重複排除・中期要約）
    llm.py             OpenAI 互換クライアント（リトライ付き）/ MockLLM
    embedding.py       OpenAI 互換 /embeddings / 文字 n-gram ハッシュ埋め込み
    persona.py         ペルソナ YAML の読込・検証（age >= 20）
    prompt.py          packages/prompts テンプレートの描画
    moderation.py      Gate #1（語彙リスト + 正規化。TermProvider で差し替え可能）
    audit.py           監査ログ（audit_logs + stdout）
    rate_limit.py      プロセス内スライディングウィンドウ
    comments.py / conversations.py / user_memories.py
  models/              Pydantic スキーマ（api.ts と一致）
scripts/               export_openapi.py / validate_personas.py
tests/                 単体テスト + integration/（ローカル Supabase）
```

## 設計上の注意

- API は `postgres` ロールで接続し RLS をバイパスするため、**全クエリを検証済み user_id でスコープ**している
  （他人の会話・記憶は 404）。新しいクエリを追加するときも必ず守ること。
- 長期メモリの検索は (user, character) に絞った**厳密検索**（`MATERIALIZED` CTE）。HNSW は使わない（ADR-0005）。
- ユーザーが編集した記憶（`is_user_edited`）は自動抽出の重複排除で上書きしない。
- レート制限はプロセス内メモリ。Fly.io で複数マシンにスケールする場合はマシンごとのカウントになる。
- 監査ログの DB 書き込みはチャットのトランザクションとは別接続。失敗しても応答は返すが ERROR ログを出す。
- JWT の `iss` は `SUPABASE_JWT_ISSUER`（任意）→ 無ければ `{SUPABASE_URL}/auth/v1` と照合する。
  Docker から `host.docker.internal` 経由で Supabase を参照する場合は `SUPABASE_JWT_ISSUER` を設定する。

## デプロイ（Fly.io）

```bash
# リポジトリのルートで
fly deploy --config apps/api/fly.toml --dockerfile apps/api/Dockerfile
fly secrets set --config apps/api/fly.toml DATABASE_URL=... SUPABASE_URL=... LLM_API_KEY=... CORS_ALLOW_ORIGINS=...
```

TLS を中継するプロキシ配下でイメージをビルドする場合は、CA 証明書を BuildKit secret で渡せる:
`docker build --secret id=extra_ca,src=/path/to/ca.crt -f apps/api/Dockerfile .`
