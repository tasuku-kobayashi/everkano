# ADR-0019: `/chat` の締め切り（CHAT_DEADLINE_SECONDS）と「何も保存しない」失敗

- ステータス: 採用（記憶の抽出を締め切りの中で待つことは [ADR-0038](0038-memory-engine-v2.md)、応答をストリーミングしないことは [ADR-0037](0037-chat-streaming-sse.md) により置き換え。埋め込みの障害では 503 にしないことを [ADR-0022](0022-embedding-failures-and-audit-additions.md)、Web 側の通信失敗の扱いを [ADR-0029](0029-web-network-failure-policy.md)、ストリーミングでの締め切りと失敗の扱いを [ADR-0037](0037-chat-streaming-sse.md) で追補）
- 日付: 2026-09-25
- 関連: 仕様書 §7・§15「エラーは握りつぶさない」 / [ADR-0009](0009-memory-engine.md) / 実装: `apps/api/app/services/chat.py`, `apps/api/app/core/config.py`（`chat_deadline_seconds`）, `apps/web/lib/api/client.ts`（`CHAT_TIMEOUT_MS`）

## コンテキスト

- `/chat` は埋め込み → 検索 → LLM 生成（1 回あたり `LLM_TIMEOUT_SECONDS` = 30 秒 × 最大 `LLM_MAX_RETRIES` 回のリトライ）→ 保存、と外部呼び出しが続く。
- Web はタイムアウト（`CHAT_TIMEOUT_MS` = 45 秒）で送信を失敗として表示し、ユーザーは再送できる。API がその後に遅れて保存を終えると、
  再送と合わせて **同じ発言が二重に保存される**。
- LLM 障害時に、ユーザー発言だけが保存されてキャラの返答が無い、という中途半端な状態も避けたい。

## 決定

- `/chat` 全体に締め切り `CHAT_DEADLINE_SECONDS`（既定 38 秒。`.env.example` に記載）を設ける。**Web の `CHAT_TIMEOUT_MS`（45 秒）より必ず短くする。**
- 5〜8（記憶の取得・プロンプト組立・返答生成）が締め切りまでに終わらなければ、`llm.error`（`error = deadline exceeded`）を記録して
  **503 `llm_unavailable` を返し、メッセージは何も保存しない**。LLM がリトライ後に失敗した場合も同じ。
- ユーザー発言とキャラ返答は **1 トランザクション** で保存する（片方だけが残ることはない）。
- 記憶の抽出は、返答ができた時点で **残り時間だけ** 待つ。間に合わなければ候補なしで進め、`llm.error`（`purpose=memory_extraction`）を記録する。
- 記憶の保存は残り時間（最低 3 秒）で打ち切る。失敗してもチャットは成功として返す（ERROR ログを出す）。
- Gate #1（入力）でヒットした場合は LLM を呼ばないので締め切りと無関係に即応答する。

## 結果・トレードオフ

- クライアントが諦めた後に保存されることが無いので、再送で二重にならない（統合テスト `test_chat_deadline_returns_503_and_saves_nothing`、
  `test_slow_extraction_is_abandoned_at_deadline`）。
- 遅い LLM では 503 が増える。モデルやプロバイダを変えるときは、`LLM_TIMEOUT_SECONDS`・`LLM_MAX_RETRIES`・`CHAT_DEADLINE_SECONDS`・
  `CHAT_TIMEOUT_MS` の大小関係（API の締め切り < Web のタイムアウト）を保つ。
- ネットワーク断などで Web がレスポンスを受け取れなかった場合（API は保存済み）は、再送すると二重になり得る。Web は Realtime と再取得で
  保存済みの発言を表示するので、ユーザーには見える。厳密にするなら冪等キー（下記）が必要。
- 応答はストリーミングしない（返答全体ができてから表示。その間は「入力中…」を表示する）。

## 代替案

- **冪等キー（クライアントの送信 ID）で重複を防ぐ**: 再送を安全にできるが、`messages` に列と一意制約が必要。次の改善候補。
- **ストリーミング（SSE）で返す**: 体感速度は上がるが、Gate #1（出力）を全文に対して行う設計と両立させる工夫が要る（出力の途中で差し止められない）。
- **締め切りを設けずクライアントのタイムアウトだけに頼る**: 上記の二重保存が起きる。
