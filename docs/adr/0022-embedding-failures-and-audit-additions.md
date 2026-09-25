# ADR-0022: 埋め込み（Embedding）の障害時の扱いと監査ログの追加項目

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §7・§9・H6 / [ADR-0008](0008-llm-embedding-providers-and-mock.md)・[ADR-0009](0009-memory-engine.md)・[ADR-0013](0013-audit-log.md)（本 ADR で追補）・[ADR-0019](0019-chat-deadline.md) /
  実装: `apps/api/app/services/embedding.py`（`embedding_failure_payload`）, `apps/api/app/services/memory.py`（`embed_query` / `log_embedding_error` / 要約）,
  `apps/api/app/services/chat.py`, `apps/api/app/services/user_memories.py`, `apps/api/app/core/config.py`

## コンテキスト

- 当初の実装では、埋め込み API も LLM と同じ `LLM_TIMEOUT_SECONDS`（30 秒）・`LLM_MAX_RETRIES`（2 回）で呼んでいた。`/chat` は最初に
  ユーザー発言を埋め込んで長期記憶を検索するため、埋め込み API が遅い・落ちているだけで `/chat` 全体が締め切り（`CHAT_DEADLINE_SECONDS`
  = 38 秒）に達して 503 になり、DM がまったく使えなくなっていた。長期記憶の検索は返答の質を上げる任意の機能で、DM の成否を左右すべきではない。
- 一方で、埋め込みの失敗を黙って握りつぶすと「キャラが何も思い出さない」「抽出した記憶が保存されない」状態が続いても誰も気付かない（§15）。
  ADR-0013 の `llm.error` は LLM の失敗だけを想定していた。

## 決定

- 埋め込み専用の設定を設ける: `EMBEDDING_TIMEOUT_SECONDS`（既定 5 秒）・`EMBEDDING_MAX_RETRIES`（既定 1 回）。埋め込みは
  `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` を使わない。`EMBEDDING_TIMEOUT_SECONDS` は `CHAT_DEADLINE_SECONDS` の半分未満にする
  （`tests/test_config.py` で既定値の関係を検査）。
- 失敗時の挙動（どれも `llm.error` を 1 行残す）:

  | 場面                                      | 挙動                                                                                   | `llm.error` の `purpose`   | payload の追加項目                                     |
  | ----------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------- | ------------------------------------------------------ |
  | `/chat` の検索用の埋め込み                 | 長期記憶の検索を省略して返答する（200）。最新の要約（最大 2 件）は検索なしで注入される | `embedding_query`          | `conversation_id`                                      |
  | `/chat` の抽出した記憶の保存               | 返答は 200 のまま。抽出した記憶は保存されない                                           | `memory_save`              | `conversation_id`, `user_message_id`, `lost_candidates` |
  | `POST` / `PATCH /memories`（メモリパネル） | 10 秒で打ち切って 503 `llm_unavailable`（Web の 15 秒より前に返し、再送の二重保存を防ぐ） | `user_memory`              | `action`（create / update）, `memory_id`               |
  | 中期要約の埋め込み                         | 要約は埋め込み無しで保存する（最新 2 件の要約は常に注入されるので使われる）            | `memory_summary_embedding` | `conversation_id`, `memory_id`                         |

  埋め込みの `llm.error` には共通して `error` / `status_code` / `attempts`（`embedding_failure_payload`）と `embedding_model` が入る。
- `chat.response` に次の項目を追加する: `retrieval_skipped`（検索を省略したか）、`memory_save_error`（記憶の保存の失敗理由。成功なら null）、
  `prompt_chars`（応答生成に渡したプロンプトの文字数。履歴の上限 `prompt.HISTORY_MAX_CHARS` の監視用）。
- `memory.summary` の payload は `memory_id`, `conversation_id`, `summarized_messages`, `excluded_moderated_messages`, `summary_cursor`,
  `content`, `model`, `latency_ms`, `usage`, `embedded`（埋め込み付きで保存できたか）と、`AUDIT_LOG_PROMPTS=true` のとき
  `prompt_messages` / `raw_output`（他の生成と同じく入力と生出力。H6）。
- ADR-0013 の表のうち `llm.error` の `purpose` は、上の 4 つを加えて chat / memory_extraction / memory_summary / comment_reply /
  embedding_query / memory_save / user_memory / memory_summary_embedding になる。

## 結果・トレードオフ

- 埋め込み API の障害中も DM は使い続けられる（返答は短期メモリと最新の要約だけで作られ、「以前話した細かいこと」を思い出さない）。
  ユーザーには何も表示しない。気付く手段は監査ログとアラートだけなので、`llm.error` を `purpose` ごとに監視する
  （[06-operations.md](../handover/06-operations.md#監視とログ) の集計 SQL。特に `embedding_query` と `memory_save`）。
- `memory_save` の失敗で失われた記憶の候補は再抽出しない（`lost_candidates` で件数だけ分かる）。重要な内容は会話の中で再び話されれば抽出される。
- 埋め込み無しで保存した要約は類似検索に出てこないが、最新 2 件は検索と無関係に注入されるため、直近の要約は使われる。
  古い要約を検索対象に戻すには `scripts/reembed_memories.py` を実行する。

## 代替案

- **埋め込みの失敗で `/chat` を 503 にする（従来）**: 任意の機能の障害で中核機能（DM）が止まる。
- **LLM と同じタイムアウトのまま検索だけ非同期にする**: 返答生成は検索結果（プロンプト）を待つので、結局は待ち時間が伸びる。
- **失敗をログ（stdout）だけに出す**: 監査ログ（DD）で「なぜこの返答は記憶に触れなかったか」を追えない。アラートも SQL で組みにくい。
