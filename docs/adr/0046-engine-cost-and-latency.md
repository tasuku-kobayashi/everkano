# ADR-0046: コストとレイテンシの方針（E7: 1 ユーザー月 ¥100 前後・E8: 最初の文字まで中央値 2.5 秒）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 E7・E8・§3・§9.2（レイテンシ・コスト）/ [ADR-0028](0028-llm-input-budgets-and-summary-retries.md)・[ADR-0035](0035-character-engine-architecture.md)・
  [ADR-0036](0036-engine-job-queue-and-scheduler.md)・[ADR-0037](0037-chat-streaming-sse.md)・[ADR-0038](0038-memory-engine-v2.md)・[ADR-0040](0040-character-calendar.md)・
  [ADR-0042](0042-proactive-messenger.md)・[ADR-0045](0045-evaluation-harness.md) /
  実装: `apps/api/app/engine/context_assembler.py`（`ContextBudget`）, `apps/api/app/services/prompt.py`（`chat_messages`）, `apps/api/app/engine/pipeline.py`（`_enqueue_post_turn`）,
  `apps/api/app/core/config.py`（`ENGINE_POST_TURN_DELAY_SECONDS` / `LLM_MODEL_*` / `ENGINE_PRICE_TABLE_JSON`）, `apps/api/app/engine/memory/config.py`（`skip_trivial_batches`）,
  `apps/api/evals/`（`meter.py`・`cost.py`）, `apps/web/components/dm/use-send-message.ts`

## コンテキスト

- E7: 3 つの仕組みの合計で、1 ユーザーあたりの月間 LLM コストは ¥100 を目安にする（超える場合は ADR で理由を示す）。粗利率 70% 以上の維持のため。
- E8: DM の送信から最初の文字の表示まで中央値 2.5 秒以内。重い処理は返答の後に非同期で行う。
- LLM は OpenRouter 経由の DeepSeek V3（[ADR-0008](0008-llm-embedding-providers-and-mock.md)）。DeepSeek は同じ先頭のプロンプトを自動でキャッシュし、キャッシュに当たった入力は安い。
- この環境では live の LLM を呼べないので、費用とレイテンシは評価ハーネス（[ADR-0045](0045-evaluation-harness.md)）の計測と推計で判断する。

## 決定

### 費用のかかる場所（用途）

| 用途 | いつ | 回数を決めるもの |
| --- | --- | --- |
| `chat` | 返答のたび（返答の経路の LLM は 1 回だけ） | ユーザーの発言数 |
| `memory_analysis` / `affinity_eval` | 返答の後の `post_turn`（会話ごと・デバウンスでまとめて） | 会話の区切りの数（発言数ではない） |
| `memory_summary` | 未要約のメッセージが溜まったとき（チャンクの上限あり） | 会話の長さ |
| `proactive_message` | 自発メッセージを送るとき | E4 の上限（1 日の通数） |
| `feed_caption` | 予定からフィードに投稿するとき | キャラの数と投稿の上限（全ユーザー共通の固定費。1 ユーザーの額には入れない） |
| 予定の生成 | — | LLM を使わない（ルールだけ。[ADR-0040](0040-character-calendar.md)） |

### 費用を抑える手段

- **返答の経路は LLM 1 回**。記憶の抽出を返答と並行に呼ぶのをやめた（[ADR-0038](0038-memory-engine-v2.md)）。
- **デバウンス**: `post_turn` は返答から `ENGINE_POST_TURN_DELAY_SECONDS` 後に、続けて話すあいだは後ろにずらして（上限あり）会話が止まってから 1 回だけ分析する。
  1 発言あたりの分析の回数が減る代わりに、「覚えました」の通知と約束の登録が遅れる。値は評価ハーネスの実験（`--set engine_post_turn_delay_seconds=...`）で
  費用と鮮度を比べて決める（`app/core/config.py` の既定値とその根拠のコメント、[docs/eval](../eval/README.md) の結果）。
- **分析を呼ばない**: 相づち・あいさつだけのバッチ（`skip_trivial_batches`）、すべてが操作の発言のバッチ（[ADR-0039](0039-user-edited-memory-protection.md)）、
  操作の発言は好感度の評価にも渡さない（[ADR-0041](0041-affinity-engine.md)）。
- **1 回の呼び出しでまとめる**: 記憶の追加・統合・矛盾・約束・キャラの発言を 1 回の分析で決める。好感度はバッチのターンを 1 回で採点する。
- **プレフィックスキャッシュ**: 変わらない system（ペルソナ・守ること）→ 直近の会話 → 毎回変わる〔今の状況〕+ 今回の発言、の順にする（[ADR-0035](0035-character-engine-architecture.md)）。
  履歴の先頭を目印の発言にそろえ、古い側を落としても先頭がずれないようにする。
- **予算**: 文脈のセクションごとの上限（`ContextBudget`）と、要約のチャンクの上限（[ADR-0028](0028-llm-input-budgets-and-summary-retries.md)）でプロンプトの大きさを抑える。
- **用途別のモデル**: live の評価で品質が保てると分かった用途から、`LLM_MODEL_ANALYSIS`・`LLM_MODEL_PROACTIVE`・`LLM_MODEL_CAPTION` で安いモデルに切り替えられる（既定は `LLM_MODEL`）。
- **自発メッセージ・投稿の上限**: 送るかどうか・投稿するかどうかはルールで先に決め、LLM は送る・投稿すると決めたものの文面だけに使う。

### レイテンシを守る手段（E8）

- 返答の経路で行うのは、ルールの判定（E6・Gate #1。ミリ秒単位）、文脈の並行の組み立て（締め切り `ENGINE_CONTEXT_TIMEOUT_SECONDS`。間に合わない要素は省く）、
  検索用の埋め込み（`EMBEDDING_TIMEOUT_SECONDS`。間に合わなければ検索を省く）、LLM のストリーミング（文単位で送る。[ADR-0037](0037-chat-streaming-sse.md)）だけ。
- 記憶の分析・約束の予定化・好感度の評価・参照の記録（`mark_referenced`）は返答の後。ジョブ・定期実行は本番では API とは別のプロセスグループで動かす
  （[ADR-0036](0036-engine-job-queue-and-scheduler.md)）。
- 忙しい状態でも返答を遅らせる演出はしない（[ADR-0040](0040-character-calendar.md)）。
- Web は「入力中…」を送信の 400 ms 後から出し、最初の文字の表示を送信から 1 秒後まで待つ（`MIN_REPLY_DELAY_MS`。即答のモックでも「入力中…」が一瞬で消えないように）。
  この下限が E8 に効くのは、サーバーが 1 秒より速く最初の文字を返した場合だけ。

### 測り方と判定

- 開発中: 評価ハーネスで、用途ごとの単価から 1 アクティブユーザーの月額（軽い・中央・多い利用）と、最初の文字までの時間を出す（mock では実測 + 本番の通信と
  モデルの最初のトークンまでの時間の仮定。[ADR-0045](0045-evaluation-harness.md)）。トークン数の仮定を変えた場合の月額も出す。
- 本番: 監査ログ `chat.response` の `ttft_ms`（E8）・`llm_first_chunk_ms`、各用途の `usage` と `model`（`chat.response`・`memory.analysis`・`memory.summary`・`affinity.update`・
  `proactive.send`・`calendar.post_create`・`llm.error`）から集計する（SQL は [06-operations.md](../handover/06-operations.md)）。
- **計測値は [docs/eval/README.md](../eval/README.md)・[history.md](../eval/history.md) と最終報告に記録し、この ADR には書かない**。live の実測で E7 の ¥100 を超える場合は、
  理由と対策をこの ADR の追補として記録する（エンジン仕様書 E7）。

## 結果・トレードオフ

- 費用の大半は `chat` と返答後の分析で、どちらも発言の数・会話の区切りの数に比例する。重い利用者（1 日の発言が多い）は目安を超え得る（評価ハーネスは利用の多さ別に出す）。
- デバウンスを長くするほど安いが、記憶・約束の反映が遅れる（会話が止まってからになる）。短期の履歴がその間をつなぐ。
- キャッシュの効き方はプロバイダの実装に依存する（DeepSeek 直結なら usage に `prompt_cache_hit_tokens` が出る。OpenRouter 経由では出ないことがある）。
- レイテンシの大部分はモデルの最初のトークンまでの時間で、アプリ側では縮められない。live で中央値が 2.5 秒を超えるなら、プロバイダ・モデル・リージョンを見直す。

## 代替案

- **毎ターン分析する（デバウンスしない）**: 鮮度は最良だが、1 発言あたりの分析の回数が増えて E7 に不利。
- **分析を数ターンごと・1 日 1 回にまとめる**: さらに安いが、約束の登録と「覚えました」が大きく遅れる（期日の近い約束を取りこぼす）。
- **最初から安いモデルを分析に使う**: 品質のデータが無い（live の評価の後に判断する）。
- **レイテンシを隠す演出（常に数秒待つ）**: 体験が悪くなり、E8 の趣旨に反する。
