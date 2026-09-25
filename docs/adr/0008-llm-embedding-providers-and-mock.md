# ADR-0008: LLM / Embedding プロバイダ抽象化とモックモード

- ステータス: 採用（埋め込みの専用のタイムアウト・リトライを [ADR-0022](0022-embedding-failures-and-audit-additions.md) で追補）
- 日付: 2026-09-25
- 関連: 仕様書 §2「LLM / 生成」・§17-3 / 実装: `apps/api/app/services/llm.py`, `apps/api/app/services/embedding.py`, `apps/api/app/core/config.py`

## コンテキスト

- 仕様書は「OpenRouter 経由の DeepSeek-V3」を指定し、「DeepSeek API 直契約でも動くよう基底 URL を環境変数化」することを求めている。
- 開発・CI・受け入れ確認は API キー無し（外部ネットワークに出られない環境を含む）でも動かせる必要がある。受け入れ基準 A8〜A10
  （文脈に合った返答・10 往復後の想起・削除した記憶が返答に出ない）もオフラインで再現したい。
- 長期メモリには埋め込み（1536 次元。DB の `vector(1536)`）が必要。

## 決定

- **LLM**: `LLMClient` プロトコル（`model_name` / `complete(LLMRequest) -> LLMResult`）を定義し、`LLM_MODE` で実装を選ぶ。
  - `live` = `OpenAICompatibleLLM`: httpx で `{LLM_BASE_URL}/chat/completions` を呼ぶ（OpenAI 互換）。OpenRouter
    （`https://openrouter.ai/api/v1` + `deepseek/deepseek-chat`）と DeepSeek 直（`https://api.deepseek.com/v1` + `deepseek-chat`）の両方に対応。
    - 429 / 5xx / タイムアウト / 通信エラーは指数バックオフ + ジッターで最大 `LLM_MAX_RETRIES` 回リトライ（`Retry-After` は最大 10 秒まで尊重）。
      1 回あたりのタイムアウトは `LLM_TIMEOUT_SECONDS`。`/chat` 全体は別途締め切りがある（[ADR-0019](0019-chat-deadline.md)）。
    - 記憶の抽出・要約は `temperature 0` + `response_format: {type: json_object}`。プロバイダが 400 で拒否したら付けずに再試行し、
      出力は寛容にパースする（コードブロック・前後の文を許容）。
    - OpenRouter のときだけ任意ヘッダー `HTTP-Referer`（`LLM_HTTP_REFERER`）/ `X-Title`（`LLM_APP_TITLE`）を付ける。
  - `mock` = `MockLLM`: ネットワークを使わず決定的に応答する（モデル名 `mock-persona-v1`）。
    - DM: ペルソナの口調例・一人称 / 呼び方・`schedule_pattern`（日本時間の現在時刻）から組み立て、発言と内容語が重なる記憶があれば
      「そういえば、〜って言ってたよね」のように触れる。
    - 記憶抽出: 感情・個人情報（仕事 / 家族 / 健康 / 日課 / 好き / 名前 / 住んでる …）・約束（来週 / 明日 / 絶対 / 一緒に / 約束 …）・
      関係性（呼んで / 好き …）のキーワードで重要度を採点し、`ユーザーは「…」と話していた` を返す。質問文は除外。
    - 出力形式（抽出 / 要約の JSON）は live と同じなので、パース処理は共通で検証される。
  - 呼び出し側は描画済みのプロンプト（messages）に加えて、モック用の構造化ヒント（`MockHints`）を渡す。live はヒントを無視する。
- **Embedding**: `EmbeddingClient` プロトコル、`EMBEDDING_MODE` で選ぶ。
  - `live` = OpenAI 互換 `{EMBEDDING_BASE_URL}/embeddings`（既定 `text-embedding-3-small`。`text-embedding-3*` には `dimensions` を指定）。
  - `hash` = 文字 1〜3-gram と内容語（漢字 / カタカナ / 英数字の連続）を blake2b で 1536 次元に符号付きハッシュし L2 正規化する。
    Python の `hash()` はプロセスごとに変わるため使わない。語彙の重なりを捉える程度の精度。
- **起動時の検証**（`app/core/config.py`。違反なら起動しない）: `APP_ENV=production` で `LLM_MODE=mock` を禁止、`LLM_MODE=live` は
  `LLM_API_KEY` 必須、`EMBEDDING_MODE=live` は `EMBEDDING_API_KEY` 必須、`EMBEDDING_DIMENSIONS` は 1536 固定。
- 現在のモードは `GET /health`（`llm_mode` / `embedding_mode`）と起動ログに出す。`audit_logs` の `chat.response.model` にも実際のモデル名が残る。

## 結果・トレードオフ

- 外部キー無しで、ローカル開発・CI・E2E（A8〜A10 を含む）がすべて動く。ただし **モックの返答は品質の検証にならない**。
  実際の LLM の文脈理解・口調は staging（`LLM_MODE=live`）で確認する必要がある（[acceptance report](../acceptance/report.md)）。
- `apps/api/fly.toml` は staging を `LLM_MODE=live` / `EMBEDDING_MODE=hash` にしている。埋め込みの API キーを用意したら `live` に切り替え、
  **直後に既存の記憶を再埋め込みする**（`scripts/reembed_memories.py`。hash と live はベクトル空間が別で、次元数が同じためエラーにならない）。
- プロバイダの切り替えは環境変数だけで済む（コード変更なし）。OpenAI 互換でないプロバイダを使う場合は `LLMClient` の実装を 1 つ足す。
- トークン使用量（`usage`）はプロバイダが返す場合だけ監査ログに残る。

## 代替案

- **公式 SDK（openai / LangChain など）**: 依存が増え、リトライ・タイムアウト・監査用のメタデータを細かく制御しにくい。OpenAI 互換の
  HTTP 呼び出しは数十行で済むため採用しなかった。
- **録画したレスポンスの再生（VCR 方式）でテスト**: 新しい入力に応答できず、E2E や手動確認に使えない。ルールベースのモックにした。
- **埋め込みもモックせず必須にする**: キー無しでは記憶が一切動かず、A9 / A10 をオフラインで確認できない。
