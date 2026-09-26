# ADR-0037: DM の返答のストリーミング（`POST /chat/stream`・SSE・文単位のフラッシュと出力の検査）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 E8・§3・§9.2（レイテンシ）/ [ADR-0019](0019-chat-deadline.md)（本 ADR で一部置き換え・追補）・[ADR-0010](0010-gate1-moderation.md)・[ADR-0035](0035-character-engine-architecture.md)・
  [ADR-0043](0043-safety-e6-and-output-guard.md)・[ADR-0047](0047-web-engine-ui.md) /
  実装: `apps/api/app/engine/pipeline.py`・`pipeline_flush.py`・`pipeline_sse.py`, `apps/api/app/services/chat.py`（`PipelineChannel`）, `apps/api/app/routers/chat.py`,
  `apps/api/app/services/llm.py`（`LLMClient.stream` / `stream_completion`）, `packages/shared/src/api.ts`（`ChatStreamEvent`）,
  `apps/web/lib/api/sse.ts`・`client.ts`（`streamChat`）, `apps/api/tests/engine/core/`（`test_chat_stream_api.py`・`test_flush.py`・`test_sse.py`・`test_llm_stream.py`）

## コンテキスト

- E8: 送信から最初の文字の表示まで中央値 2.5 秒以内。MVP の `/chat` は返答の全文ができてから返していた（[ADR-0019](0019-chat-deadline.md) の「応答はストリーミングしない」）。
  長い返答ほど最初の文字が遅い。
- 一方で、LLM の出力は Gate #1・キャラの NG ワード・E2（購入と関係の結びつけ）・E3（実在の人間だという主張）で検査してから見せたい。
  トークンをそのまま流すと、検査で引っかかる語が画面に出てから消すことになる（表示したものは取り消せない）。
- クライアントが途中で接続を切っても、返答の保存は二重・欠落にしたくない（[ADR-0019](0019-chat-deadline.md)）。

## 決定

- **`POST /chat/stream`**（Server-Sent Events）を追加し、`POST /chat` と **同じパイプライン**（`ChatPipeline`）で処理する。`/chat` はイベントを最後まで読んで
  `done` の `ChatResponse` を返す。入力（`ChatRequest`）とレート制限のバケット（`RATE_LIMIT_CHAT_PER_MINUTE`）は共通。
- 認証・所有者・レート制限・入力のエラーは **ストリームを始める前に** 通常の JSON エラー（HTTP ステータス）で返す。
- イベント（`packages/shared/src/api.ts` の `ChatStreamEvent`。`event: <type>` + `data: <1 行の JSON>`）:

  | type | data | 意味 |
  | --- | --- | --- |
  | `delta` | `{text}` | 検査を通過した返答の続き。表示中の吹き出しに追記する |
  | `replace` | `{text, reason: "moderated" \| "safety"}` | 表示中の本文を置き換える（出力が差し止められた・入力が Gate #1 に当たった / E6 の安全対応） |
  | `done` | `ChatResponse` | 保存済みの結果。以後イベントは無い |
  | `error` | `{code, message, request_id}` | 失敗。**何も保存していない**。直後に接続を閉じる |

  開始直後にコメント行 `: ok`、イベントが無い間は `CHAT_STREAM_HEARTBEAT_SECONDS`（既定 10 秒）ごとに `: keep-alive` を送る（中継のアイドルタイムアウト対策）。
  ヘッダーは `Cache-Control: no-cache, no-transform`・`X-Accel-Buffering: no`。
- **文単位のフラッシュ**（`pipeline_flush.StreamFlusher`）: LLM の断片はバッファにため、文の区切り（。！？!?改行…）まで来たらその手前までを 1 つの `delta` にする。
  区切りが来ないまま一定の文字数（`SOFT_FLUSH_CHARS`）がたまったら読点・空白で区切って出す（末尾の数文字は手元に残す）。
  - 出す前に、**それまでに受け取った全文**（出した分 + バッファ）を検査する（Gate #1 + キャラの `speech.ng_words` + OutputGuard）。引っかかったら以後は何も出さず、
    LLM の返答を最後まで受け取ってから **完成した全文でもう一度判定** する（途中までの本文だけでは語の境界の条件で誤検知することがあるため）。
    最終判定でも引っかかれば `replace`（キャラの定型の断り文 `persona.refusal_reply`、`reason: moderated`）を送り、`moderation.flag`（`stage: output`）を残す。
  - 返答の先頭の「名前:」と全体を囲むかぎかっこは取り除く（出した `delta` を後から書き換えないよう、先頭は判定できるまで待つ）。
  - 保存する本文 = 送った `delta` の連結（置き換えた場合は断り文）。
- E6 の安全対応（[ADR-0043](0043-safety-e6-and-output-guard.md)）と Gate #1（入力）のヒットは LLM を呼ばず、`replace`（`reason: safety` / `moderated`）→ `done`。
  安全対応の `done` の `ChatResponse.safety` に相談窓口の一覧が入る。
- **生成は別のタスクで最後まで行う**（`ChatService` が `PipelineChannel` 経由でイベントを受け取る）。クライアントが接続を切っても、返答の生成・保存・
  返答後のジョブの登録は完了する（保存したものは次の画面表示・Realtime で届く）。
- 締め切り `CHAT_DEADLINE_SECONDS` は文脈の組み立て + 返答の生成にかかる（[ADR-0019](0019-chat-deadline.md) の考え方のまま）。LLM の失敗（リトライの後）・締め切りの超過は、
  `delta` を送った後でも `error`（503 `llm_unavailable`）にして **何も保存しない**。`llm.error` に送った文字数（`streamed_chars`）を残す。
- LLM クライアント: `LLMClient.stream()` は OpenAI 互換の `stream: true` の SSE を読み、`stream_options: {include_usage: true}` で使用量を受け取る
  （プロバイダが 400 で拒否したら付けずに再試行。**最初の断片を受け取った後は再試行しない**）。`stream` を持たないクライアントは `stream_completion()` が
  `complete()` にフォールバックする。モックは断片に分けて返す（`LLM_MOCK_STREAM_DELAY_MS` で断片ごとの待ちを入れられる）。
- 記憶の分析は返答の後なので、`done` の `memories_created` は常に空（新しい記憶は Realtime の `memories` の INSERT で届く）。
- 監査ログ `chat.response` に `ttft_ms`（認証の後から最初の `delta` / `replace` を送るまで。E8 の指標）・`llm_first_chunk_ms`・`llm_latency_ms`・`output_held`・
  `streamed` を残す。
- Web は `fetch` + `ReadableStream` で読む（EventSource は POST と `Authorization` ヘッダーを送れないため）。ストリーミングを使えない環境・`/chat/stream` が
  404 / 405 の API では `/chat` にフォールバックし、そのセッションでは以後 `/chat` を使う。タイムアウトは接続から `done` まで 45 秒
  （`CHAT_STREAM_TIMEOUT_MS`。API の締め切り 38 秒より長い）。詳細は [ADR-0047](0047-web-engine-ui.md)。

## 結果・トレードオフ

- 最初の文字は「文脈の組み立て + LLM の最初の 1 文」の時間で届く（返答の全文を待たない）。モックでの計測と live の推計は [docs/eval](../eval/README.md) と
  [ADR-0046](0046-engine-cost-and-latency.md)。
- 検査で引っかかる語は画面に出ない。ただし、それまでに出した文（その時点の全文で検査を通過したもの）が表示された後で、全体が断り文に置き換わることはある。
- 最初の `delta` は文の区切りか一定の文字数まで待つので、トークンをそのまま流すより少し遅い。
- 途中で `error` になった場合、Web は受信中の吹き出しを消して再送できるようにする（何も保存されていない）。
- Fly.io の同時リクエスト数（`hard_limit`）は SSE の接続も 1 件と数える（生成が終われば閉じる）。
- ネットワーク断で `done` を受け取れず再送すると、二重に保存され得る（[ADR-0019](0019-chat-deadline.md) の既知の制約のまま。冪等キーが次の改善候補）。

## 代替案

- **WebSocket**: 双方向は不要。接続ごとの認証・再接続・プロキシの設定が増える。1 往復の返答には SSE で足りる。
- **トークンをそのまま流す**: 最速だが、NG ワード・E2 / E3 の違反が検査の前に画面に出る。出したものは取り消せない。
- **全文をためてから返す（MVP のまま）**: 長い返答で E8 を満たせない。
- **EventSource（GET）**: `Authorization` ヘッダーを付けられず、トークンを URL に載せることになる（ログ・履歴に残る）。
