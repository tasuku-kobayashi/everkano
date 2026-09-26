# ADR-0028: LLM 呼び出しの上限（DM 履歴の文字数・中期要約のチャンク化と失敗時のバックオフ・外部 HTTP の接続数）

- ステータス: 採用（DM の履歴の上限の値は [ADR-0035](0035-character-engine-architecture.md) により置き換え（Context Assembler の予算）。中期要約をジョブで実行することを [ADR-0038](0038-memory-engine-v2.md) で追補）
- 日付: 2026-09-26
- 関連: 仕様書 §7・§9.1 / [ADR-0009](0009-memory-engine.md)（本 ADR で追補）・[ADR-0019](0019-chat-deadline.md)・[ADR-0022](0022-embedding-failures-and-audit-additions.md) /
  実装: `apps/api/app/services/prompt.py`（`HISTORY_MAX_CHARS` / `fit_chat_history` / `TRANSCRIPT_MAX_CHARS` / `fit_transcript_prefix`）,
  `apps/api/app/services/memory.py`（`maybe_summarize` / `_on_summary_failure` / `_skip_chunk`）, `apps/api/app/core/http.py`,
  `apps/api/fly.toml`（`[http_service.concurrency]`）, `apps/api/tests/test_http_limits.py`, `apps/api/tests/integration/test_memory_limits_and_summary.py`

## コンテキスト

納品前の検査で、LLM に渡す入力量と呼び出しの失敗の扱いに上限が無いことによる問題が見つかった。

- **DM の履歴**: 短期メモリ（直近 30 ターン = 60 件、1 件最大 2000 文字）を全文で渡していた（観測した最大は約 6.8 万文字）。長文を貼り付ける
  ユーザーほどプロンプトが膨らみ、待ち時間と費用が増える。コンテキストの小さいモデルでは 400（再試行しない）になり、その会話が以後ずっと送れなくなる。
- **中期要約**: 未要約分の **新しい方の 12,000 文字** だけを要約に渡しながら、`summary_cursor` は未要約分の最後まで進めていたため、古い部分は
  一度も要約されずに失われていた。逆に要約が失敗するとカーソルが進まず、以後の `/chat` のたびに、増え続ける会話ログで要約を再試行していた
  （5 回連続で失敗し、プロンプトが 2976 → 3472 文字に伸び続けることを再現）。
- **外部 HTTP の接続数**: LLM・埋め込み用の httpx クライアントの同時接続が 50 だった。`/chat` 1 件は LLM を 2 本（返答と記憶抽出）並行で使うので、
  Fly.io の `hard_limit`（80 同時リクエスト）に対して足りず、接続待ちのまま締め切り（38 秒）で 503 になり得た。

## 決定

- **DM の履歴の上限**（`prompt.fit_chat_history`）: 新しい側から `HISTORY_FULL_MESSAGES`（6）件は全文、それより古い発言は
  `HISTORY_OLDER_MESSAGE_MAX_CHARS`（500）文字 + 「…」に切り詰め、合計が `HISTORY_MAX_CHARS`（16,000 文字）を超える古い分は渡さない。
  今回の発言は別枠で必ず全文を渡す（最大 2000 文字）。応答生成に渡したプロンプトの文字数を `chat.response` の `prompt_chars` に記録する。
  値はコードの定数（環境変数にしない。モデルを変えるときに見直す）。
- **中期要約のチャンク化**: 未要約メッセージを古い順に最大 `SUMMARY_FETCH_LIMIT`（200）件読み、会話ログの上限 `TRANSCRIPT_MAX_CHARS`（12,000 文字）
  に収まる分だけを 1 チャンクとして要約する。`summary_cursor` は **実際に要約に含めたメッセージまで** しか進めない。1 回のバックグラウンド処理で
  要約するのは最大 `MAX_SUMMARY_CHUNKS_PER_RUN`（3）チャンク（溜まった分は以後の送信で少しずつ消化する）。
- **要約の失敗**: 会話ごとに指数バックオフ（60 秒から倍々、最大 1 時間）し、その間は要約を試みない。同じチャンクで
  `SUMMARY_MAX_ATTEMPTS_PER_CHUNK`（3）回失敗した場合と、LLM が内容を理由に拒否した場合（HTTP 400 / 413 / 422。同じ内容では何度でも失敗する）は、
  **そのチャンクを飛ばしてカーソルを進める**（永久に再試行しない）。どちらも `llm.error`（`purpose=memory_summary`。飛ばしたときは `skipped=true` と
  `skipped_messages`）に残す。バックオフの状態はプロセス内メモリ（再起動で消え、次の送信で再判定される）。
- **外部 HTTP**: LLM・埋め込み用のクライアントは同時接続 200（keep-alive 100）、接続の確立と接続待ち（pool）はそれぞれ 5 秒で打ち切る
  （`upstream_timeout`。リクエスト単位の `timeout=` に数値を渡すと pool の待ち時間まで上書きされるため、`httpx.Timeout` で渡す）。
  `fly.toml` の `hard_limit` × 2 ≦ 200 を `tests/test_http_limits.py` で検査する（`hard_limit` を上げたら接続数も上げる）。
  JWKS は別のクライアント（4 接続・5 秒）にし、LLM の混雑に巻き込まれないようにする（[ADR-0025](0025-db-tls-and-api-entry-failures.md)）。

## 結果・トレードオフ

- 1 往復のプロンプトの大きさに上限ができ、長文の貼り付けで会話が送れなくなる状態を防げる。代わりに、古い長文の発言は LLM から見ると途中までになる
  （詳しい内容は中期要約と長期記憶で補う）。
- 要約は古い順に漏れなく作られる。失敗が続く・拒否される区間は要約されずに飛ばされる（`llm.error` の `skipped=true` で追える）。
- 要約のコストは 1 回の処理で最大 3 回の LLM 呼び出しに抑えられる。溜まった未要約分が多い会話では、要約が追いつくまで何回かの送信が必要になる。
- `hard_limit` を上げるときは `HTTP_MAX_CONNECTIONS` も見直す（テストが失敗して気付ける）。

## 代替案

- **トークン数（tokenizer）で厳密に数える**: モデルごとに tokenizer が違い、依存も増える。日本語の文字数で上限を掛ければ十分に抑えられる。
- **履歴を LLM で要約してから渡す**: 1 往復ごとに LLM 呼び出しが増え、遅延と費用が増える。中期要約（ADR-0009）が既にその役割を持つ。
- **要約の失敗を無視してカーソルを進める**: 一時的な障害（LLM の 5xx・タイムアウト）でも会話の区間が失われる。バックオフしてから諦める方を選んだ。
- **uvicorn の `--limit-concurrency` で同時実行を絞る**: Fly.io のプロキシが `hard_limit` で既に絞っている。接続数の方をそれに合わせた。
