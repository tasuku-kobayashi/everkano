# ADR-0018: レート制限はプロセス内のスライディングウィンドウ（1 マシン 1 プロセス）

- ステータス: 採用（スケールアウト時に再検討する）
- 日付: 2026-09-25
- 関連: 仕様書 §14 D-3「レート制限」 / 実装: `apps/api/app/services/rate_limit.py`, `apps/api/app/container.py`（`RateLimit`）, `apps/api/Dockerfile`（`CMD`）, `apps/api/fly.toml`

## コンテキスト

- `/chat` とコメント生成は 1 リクエストごとに LLM を呼ぶ（コストがかかる）。連打・スクリプトによる乱用を抑えたい。
- MVP の API は Fly.io の 1 台（`min_machines_running = 1`）で動かす想定で、Redis などの共有ストアは構成に無い。

## 決定

- ユーザー × バケット単位の **スライディングウィンドウ（60 秒）** をプロセス内メモリで数える（`SlidingWindowRateLimiter`）。
  - `chat` バケット: `POST /chat`、上限 `RATE_LIMIT_CHAT_PER_MINUTE`（既定 20）。
  - `comments` バケット: `POST /comments` と `POST /comments/generate` の合計、上限 `RATE_LIMIT_COMMENTS_PER_MINUTE`（既定 10）。
  - `/conversations` と `/memories*` は制限しない（LLM を呼ばない。記憶の追加・更新は埋め込みを呼ぶ）。
- 超過時は 429 `rate_limited` と `Retry-After`（秒）を返し、WARNING ログを出す。Web は「少し時間をおいてから…」を表示する。
- 認証（JWT 検証 + 退会確認）の後に数える（依存関係 `RateLimit` が認証も兼ねる）ので、キーは検証済みの `user_id`。
- **1 マシン 1 プロセス**で動かす（`Dockerfile` の `uvicorn` は `--workers` を付けない）。スケールはマシン数で行う。

## 結果・トレードオフ

- 依存が増えず、テスト（`tests/test_rate_limit.py`、統合テストの `test_rate_limit` / `test_comment_rate_limit`）も単純。
- **マシンごとに独立して数える**ため、`fly scale count N` で N 台にすると実質の上限は最大 N 倍になる。再起動・デプロイでカウントは消える。
- 同じ理由で、中期要約の二重起動防止（会話単位のフラグ）と JWKS のキャッシュもプロセスごと（[ADR-0009](0009-memory-engine.md)、
  [ADR-0007](0007-jwt-verification-jwks-and-hs256.md)）。複数台でも正しさは DB 側（`for update`）で担保している。
- IP 単位の制限や未認証リクエストの制限は無い（未認証は JWT 検証で 401 になり、DB にも LLM にも到達しない）。
- Supabase 直結の操作（いいね・コメント削除など）のレート制限は Supabase 側の設定に依存する。

## 代替案

- **Redis / Upstash などの共有ストア**: 複数台でも厳密に数えられる。台数を増やす段階で導入する（`SlidingWindowRateLimiter` を同じ
  インターフェースの別実装に差し替える）。
- **Postgres で数える**: 追加の書き込みが毎リクエスト発生する。
- **Fly.io のプロキシや CDN で制限**: ユーザー単位（JWT の `sub`）で数えられない。
