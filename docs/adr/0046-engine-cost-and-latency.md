# ADR-0046: コストとレイテンシの方針（E7: 1 ユーザー月 ¥100 前後・E8: 最初の文字まで中央値 2.5 秒）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 E7・E8・§3・§9.2（レイテンシ・コスト）/ [ADR-0028](0028-llm-input-budgets-and-summary-retries.md)・[ADR-0035](0035-character-engine-architecture.md)・
  [ADR-0036](0036-engine-job-queue-and-scheduler.md)・[ADR-0037](0037-chat-streaming-sse.md)・[ADR-0038](0038-memory-engine-v2.md)・[ADR-0040](0040-character-calendar.md)・
  [ADR-0042](0042-proactive-messenger.md)・[ADR-0045](0045-evaluation-harness.md)・[ADR-0049](0049-prompt-order-and-prefix-cache.md) /
  実装: `apps/api/app/core/config.py`（`engine_post_turn_delay_seconds` / `llm_model_for` / `price_table`）, `apps/api/app/engine/pipeline.py`（`_enqueue_post_turn`）,
  `apps/api/app/engine/context_assembler.py`（`ContextBudget`）, `apps/api/app/engine/memory/config.py`（`skip_trivial_batches`）, `apps/api/evals/`（`meter.py`・`cost.py`・`tokenizer.py`）,
  `apps/web/components/dm/use-send-message.ts` / 計測: [docs/eval/results/2026-09-26-mock-30d-tuned.md](../eval/results/2026-09-26-mock-30d-tuned.md)・
  [2026-09-26-mock-90d-tuned.md](../eval/results/2026-09-26-mock-90d-tuned.md)・[experiments/](../eval/results/experiments/)

## コンテキスト

- E7: 3 つの仕組みの合計で、1 ユーザーあたりの月間 LLM コストは ¥100 を目安にする（超える場合は ADR で理由を示す）。粗利率 70% 以上の維持のため。
- E8: DM の送信から最初の文字の表示まで中央値 2.5 秒以内。重い処理は返答の後に非同期で行う。
- LLM は OpenRouter 経由の DeepSeek V3（[ADR-0008](0008-llm-embedding-providers-and-mock.md)）。DeepSeek は同じ先頭のプロンプトを自動でキャッシュし、キャッシュに当たった入力は安い。
  既定の価格表（`ENGINE_PRICE_TABLE_JSON` 未設定時）は入力 ¥40 / キャッシュに当たった入力 ¥11 / 出力 ¥165（100 万トークンあたり。$0.27 / $0.07 / $1.10 @ ¥150）。
- この環境では live の LLM を呼べないので、費用とレイテンシは評価ハーネス（[ADR-0045](0045-evaluation-harness.md)）の計測と推計で判断した。

## 決定

### 費用のかかる場所（用途）

| 用途 | いつ | 回数を決めるもの |
| --- | --- | --- |
| `chat` | 返答のたび（返答の経路の LLM は 1 回だけ） | ユーザーの発言数 |
| `memory_analysis` / `affinity_eval` | 返答の後の `post_turn`（会話ごと・デバウンスでまとめて） | 会話の区切りの数（発言数ではない） |
| `memory_summary` | 未要約のメッセージが溜まったとき（チャンクの上限あり） | 会話の長さ |
| `proactive_message` | 自発メッセージを送るとき | E4 の上限（1 ユーザー 1 日 3 通まで） |
| `feed_caption` | 予定からフィードに投稿するとき | キャラの数と投稿の上限（1 キャラ 1 日 2 件。全ユーザー共通の固定費で、1 ユーザーの額には入れない） |
| 予定の生成 | — | LLM を使わない（ルールだけ。[ADR-0040](0040-character-calendar.md)） |

### 費用を抑える手段

- **返答の経路は LLM 1 回**。記憶の抽出を返答と並行に呼ぶのをやめた（[ADR-0038](0038-memory-engine-v2.md)）。
- **デバウンス 180 秒**: `post_turn` は返答から `ENGINE_POST_TURN_DELAY_SECONDS`（既定 **180 秒**）後に実行し、続けて話すあいだは後ろにずらす（最大 6 倍 = 18 分）。
  会話が止まってから 1 回だけ分析する。評価ハーネス（30 日・全シナリオ・mock・トークナイザで計数）の比較:

  | 待ち | 分析の回数 / 発言 | 月額（中央の利用） |
  | --- | --- | --- |
  | 20 秒 | 1.87 | ¥88.8 |
  | 120 秒 | 1.04 | ¥78.3 |
  | **180 秒** | **0.57** | **¥72.2** |
  | 300 秒 | 0.55 | ¥72.8 |

  どの値でも §9.2 の全指標は合格。180 秒で下げ止まるので、鮮度（「覚えました」の通知と約束の登録が、会話が止まってから約 3 分・最大 18 分）との釣り合いで 180 秒にした
  （[experiments/](../eval/results/experiments/) と [2026-09-26-mock-30d-tuned.md](../eval/results/2026-09-26-mock-30d-tuned.md)）。
- **分析を呼ばない**: 相づち・あいさつだけのバッチ（`skip_trivial_batches`）、すべてが操作の発言のバッチ（[ADR-0039](0039-user-edited-memory-protection.md)）。操作の発言は好感度の評価にも渡さない
  （[ADR-0041](0041-affinity-engine.md)）。
- **1 回の呼び出しでまとめる**: 記憶の追加・統合・矛盾・約束・キャラの発言を 1 回の分析で決める。好感度はバッチのターン（最大 10）を 1 回で採点する。
  `memory_analysis` のプロンプトは約 3,300 字 → 約 2,700 字に削り（分単位の時刻は user 側に移した）、`affinity_eval` の出力は 0 の軸を省かせる。
- **プレフィックスキャッシュ**: 変わらない system → 直近の会話 → 毎回変わる〔今の状況〕+ 今回の発言、の順。履歴の窓の先頭を目印にそろえる（[ADR-0049](0049-prompt-order-and-prefix-cache.md)）。
- **予算**: 文脈のセクションごとの上限（`ContextBudget`。[ADR-0035](0035-character-engine-architecture.md)）と要約のチャンクの上限（[ADR-0028](0028-llm-input-budgets-and-summary-retries.md)）。
- **用途別のモデル**: live の評価で品質が保てると分かった用途から、`LLM_MODEL_ANALYSIS`・`LLM_MODEL_PROACTIVE`・`LLM_MODEL_CAPTION` で安いモデルに切り替えられる（既定は `LLM_MODEL`）。
- **自発メッセージ・投稿の上限**: 送るかどうか・投稿するかどうかはルールで先に決め、LLM は送る・投稿すると決めたものの文面だけに使う。

### 計測した費用（mock・DeepSeek-V3 のトークナイザで計数）

| 実行 | 月額（中央 15 発言/日） | 同（1.0 トークン/文字の保守的な仮定） | 軽い（5 発言/日） | 多い（40 発言/日） |
| --- | --- | --- | --- | --- |
| 30 日・6 シナリオ | **¥72.2** | ¥97.0 | ¥24.3 | ¥192.2 |
| 90 日・5 シナリオ | **¥77.0** | ¥103.1 | ¥25.9 | ¥204.7 |

- 内訳（30 日）: `chat` 1 発言あたり ¥0.13（定常時の入力 約 5,050 トークン、キャッシュに当たる見込み 54%）+ 返答後の分析 1 発言あたり ¥0.030（0.57 回）+ 自発メッセージ 1 日あたり
  ¥0.0094。フィードのキャプションは 1 キャラ 1 日あたり ¥0.0125（固定費）。
- トークナイザ（PyPI の `deepseek-tokenizer` 0.1.3 に同梱の DeepSeek-V3 の `tokenizer.json`）で数えた文字あたりのトークン数は、入力の平均 0.73・出力の平均 0.57。
  **根拠として使うのはトークナイザの計数**で、1.0 は保守的な上限。調整の前（同じ 1.0 の仮定）は ¥154.7 だった（[2026-09-26-mock-30d.md](../eval/results/2026-09-26-mock-30d.md)）。
- **E7 の判断**: 中央の利用ではトークナイザの計数で ¥72〜77 と ¥100 以内。保守的な 1.0 の仮定でも 30 日 ¥97.0・90 日 ¥103.1 で「¥100 前後」。
  **多い利用（1 日 40 発言）は ¥192〜205 で目安を超える**。費用は発言の数に比例する（1 発言ごとに返答の LLM を 1 回呼ぶ）ので、利用が多い人ほど高くなる。粗利は平均の利用で見る
  前提とし、多い利用者が問題になったら次を順に使う: `LLM_MODEL_ANALYSIS` を安いモデルにする / 記憶の予算（`ContextBudget`）を減らす / デバウンスを延ばす。
  なお mock の返答は短い（live では出力の分だけ増え、中央の利用で月 ¥4 程度の見込み）。

### レイテンシを守る手段（E8）

- 返答の経路で行うのは、ルールの判定（E6・Gate #1。ミリ秒単位）、文脈の並行の組み立て（締め切り `ENGINE_CONTEXT_TIMEOUT_SECONDS`、既定 1.5 秒。間に合わない要素は省く）、
  検索用の埋め込み（`EMBEDDING_TIMEOUT_SECONDS`、既定 5 秒。間に合わなければ検索を省く）、LLM のストリーミング（文単位で送る。[ADR-0037](0037-chat-streaming-sse.md)）だけ。
- 記憶の分析・約束の予定化・好感度の評価・参照の記録（`mark_referenced`）は返答の後。ジョブ・定期実行は本番では API とは別のプロセスグループで動かす（[ADR-0036](0036-engine-job-queue-and-scheduler.md)）。
- 忙しい状態でも返答を遅らせる演出はしない（[ADR-0040](0040-character-calendar.md)）。
- Web は「入力中…」を送信の 400 ms 後から出し、最初の文字の表示を送信から 1 秒後まで待つ（`MIN_REPLY_DELAY_MS`。即答のモックでも「入力中…」が一瞬で消えないように）。
  この下限が E8 に効くのは、サーバーが 1 秒より速く最初の文字を返した場合だけ。

### 計測したレイテンシ（推計）

- mock では、パイプラインの実測（最初の文字まで p50 27 ms。文脈の部品はそれぞれ数 ms）に、本番の通信の仮定（埋め込み API 200 ms + DB 20 ms）と、モデルの最初のトークンまでの
  時間（TTFT）の仮定を足して推計する。**中央値 1,747 ms（30 日）/ 1,748 ms（90 日）**（TTFT 1.5 秒の仮定）。TTFT 1.0 秒なら約 1,250 ms、**3.0 秒なら約 3,250 ms で E8 を満たさない**。
  素の LLM（文脈の組み立て・検索なし）は 1,573 ms。
- つまり E8 の合否はモデルの TTFT で決まる。live で測り、中央値が 2.5 秒を超えるならプロバイダ・モデル・リージョンを見直す。

### 測り方と、live の前に確かめること

- 開発中: 評価ハーネスで用途ごとの単価から 1 アクティブユーザーの月額（軽い・中央・多い）と最初の文字までの時間を出す（[ADR-0045](0045-evaluation-harness.md)）。
- 本番: 監査ログ `chat.response` の `ttft_ms`（E8）・`llm_first_chunk_ms`、各用途の `usage` と `model`（`chat.response`・`memory.analysis`・`memory.summary`・`affinity.update`・
  `proactive.send`・`calendar.post_create`・`llm.error`）から集計する（SQL は [06-operations.md](../handover/06-operations.md)）。
- **要確認（live で実行する前に）**: `LLM_MODEL`（既定 `deepseek/deepseek-chat`）と価格表（`ENGINE_PRICE_TABLE_JSON` の既定）。2026 年の二次情報では、`deepseek-chat` の名前は
  2026-07-24 に廃止され、価格も変わったとされる（この環境からは未確認）。提供元の公式の資料でモデル名と価格を確かめ、必要なら `LLM_MODEL` と `ENGINE_PRICE_TABLE_JSON` を設定してから
  評価ハーネスを live で再実行し、E7・E8 をこの ADR の追補として記録する。
- 計測値の推移は [docs/eval/README.md](../eval/README.md)・[history.md](../eval/history.md) と最終報告に残す。

## 結果・トレードオフ

- 費用の大半は `chat` と返答後の分析で、どちらも発言の数・会話の区切りの数に比例する。多い利用者は目安を超える（上の理由と対策）。
- デバウンスを長くするほど安いが、記憶・約束の反映が遅れる（会話が止まってから約 3 分、話し続ければ最大 18 分）。短期の履歴がその間をつなぐ。
- キャッシュの効き方はプロバイダの実装に依存する（DeepSeek 直結なら usage に `prompt_cache_hit_tokens` が出る。OpenRouter 経由では出ないことがある）。
- レイテンシの大部分はモデルの TTFT で、アプリ側では縮められない。
- ここまでの数値はすべて mock（仕組みの計測）。live の費用・レイテンシは未計測。

## 代替案

- **毎ターン分析する（デバウンスしない・短い待ち）**: 鮮度は最良だが、1 発言あたりの分析が約 1.9 回になり E7 に不利（上の表の 20 秒）。
- **分析を数ターンごと・1 日 1 回にまとめる**: さらに安いが、約束の登録と「覚えました」が大きく遅れる（期日の近い約束を取りこぼす）。300 秒以上では費用もほぼ下がらない。
- **最初から安いモデルを分析に使う**: 品質のデータが無い（live の評価の後に判断する）。
- **レイテンシを隠す演出（常に数秒待つ）**: 体験が悪くなり、E8 の趣旨に反する。
