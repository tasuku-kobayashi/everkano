# ADR-0013: 監査ログ設計（イベント種別と payload）

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 H6・§2「監視・ログ」・§7-10・A12 / 実装: `apps/api/app/services/audit.py`, `apps/api/app/core/logging.py`, マイグレーションの `audit_logs` と `audit_comment_delete()`

## コンテキスト

- H6: すべての会話・生成・判定を構造化ログで保存する（DD 対応）。仕様書 §2 は「`audit_logs` テーブル + stdout」への JSON 出力を指定している。
- 監査ログの書き込み失敗でユーザーの DM を失敗させたくない。一方で失敗を握りつぶしてもいけない（§15）。
- DM が失敗した（LLM 障害など）ケースでも「何を送られたか」は残したい。

## 決定

- テーブル `audit_logs(id bigserial, event_type, user_id, character_id, payload jsonb, created_at)`。RLS 有効・ポリシー無し・grant 無し
  （クライアントからは一切アクセスできない）。`user_id` に外部キーは付けない（ユーザー削除後も残す）。索引は `(event_type, created_at desc)` と
  `(user_id, created_at desc)`。
- API の `AuditLogger` が書く。**チャットのトランザクションとは別の接続** で INSERT し、同じ内容を JSON 1 行で stdout にも出す
  （ロガー `everkano.audit`。`LOG_LEVEL` を上げても INFO で出る）。DB への書き込みに失敗したらリクエストは続行し、payload 付きの ERROR ログを出す。
- **payload には必ず `request_id`**（レスポンスヘッダー `X-Request-ID` と同じ値。クライアントが送れば引き継ぐ）を入れる。
- `AUDIT_LOG_PROMPTS=true`（既定）のとき、LLM に渡したメッセージ列全文（`prompt_messages`）と抽出の生出力も入れる。
- イベント種別と主な payload:

  | event_type            | 書き手         | いつ                                                   | 主な payload（`request_id` は共通）                                                                                                                             |
  | --------------------- | -------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `chat.request`        | API            | `/chat` の所有者確認の直後                              | `conversation_id`, `message`                                                                                                                                    |
  | `chat.response`       | API            | `/chat` の保存後                                        | `conversation_id`, `user_message_id`, `message_id`, `reply`, `moderated`, `moderation_stage`, `model`, `llm_latency_ms`, `latency_ms`, `usage`, `memories_used`, `memories_created`, `memories_updated`, `memories_skipped_user_edited`, `memory_candidates`, `extraction{failed, error, model, latency_ms, usage, threshold, candidates[], prompt_messages?, raw_output?}`, `history_messages`, `persona_key`, `persona_fallback`, `prompt_messages?` |
  | `moderation.flag`     | API            | Gate #1 のヒットごと                                    | `stage`（input / output）, `context`（chat / comment / comment_reply / memory）, `categories`, `matched_terms`, `text`, `conversation_id` / `post_id` / `parent_comment_id` |
  | `llm.error`           | API            | LLM・埋め込みの失敗（リトライ後）、`/chat` の締め切り超過 | `purpose`（chat / memory_extraction / memory_summary / comment_reply）, `error`, `status_code`, `attempts`, `conversation_id` / `post_id`, `elapsed_ms`（chat） |
  | `conversation.create` | API            | 会話の新規作成                                          | `conversation_id`, `greeting_message_id`, `greeting`, `persona_key`, `persona_fallback`                                                                         |
  | `memory.create`       | API            | 記憶の新規作成                                          | `memory_id`, `source`（extraction / user）, `content`, `importance`, `category` / `source_message_id`（抽出）, `tags`（ユーザー）                               |
  | `memory.update`       | API            | 記憶の更新                                              | `memory_id`, `source`（extraction_dedupe / user）, 抽出: `content`, `importance`, `source_message_id` / ユーザー: `changes{content, importance, tags: {before, after}}` |
  | `memory.delete`       | API            | メモリパネルでの削除                                    | `memory_id`, `content`, `importance`, `tags`, `was_user_edited`                                                                                                 |
  | `memory.summary`      | API            | 中期要約の作成                                          | `memory_id`, `conversation_id`, `summarized_messages`, `excluded_moderated_messages`, `summary_cursor`, `content`, `model`, `latency_ms`                         |
  | `comment.create`      | API            | ユーザーのコメント保存                                  | `comment_id`, `post_id`, `parent_comment_id`, `body`                                                                                                            |
  | `comment.generate`    | API            | キャラの返信生成（出力が差し止められた場合も記録）      | `comment_id`, `post_id`, `parent_comment_id`, `body`, `moderated`, `trigger`（auto / manual）, `model`, `latency_ms`, `usage`, `prompt_messages?`               |
  | `auth.failure`        | API            | トークンは正しいがプロフィール無し / 退会済み（403）    | `reason`（profile_missing / account_deleted）, `path`                                                                                                           |
  | `comment.delete`      | DB トリガー    | `comments` の DELETE（本人の削除・cascade・運用者の削除） | `comment_id`, `post_id`, `author_type`, `author_user_id`, `body`, `deleted_by_role`。`user_id` = `auth.uid()`（運用者・cascade なら NULL）。`request_id` は無い |

- トークン不正（401）の `auth.failure` は **stdout のみ**（WARNING。`reason` / `detail` / `path` / `client_ip`）。不正なトークンの連打で表を汚さないため。
- `chat.request` は Gate #1・LLM 呼び出しの前に書く。LLM が失敗して何も保存されなかった場合も、`chat.request` と `llm.error` が残る。

## 結果・トレードオフ

- A12 の確認と DD 用の分析が SQL だけでできる（例は [06-operations.md](../handover/06-operations.md#監査ログaudit_logsの調べ方)）。
  E2E（`apps/web/e2e/audit.spec.ts`）で `chat.request` / `chat.response` の対応を検証している。
- `audit_logs` には **会話本文・プロンプト全文（個人情報を含み得る）** が入る。アクセスは `postgres` / `service_role` に限られるが、
  保存期間・エクスポート・削除の方針は事業側と決める必要がある（未決。[07-security.md](../handover/07-security.md)）。
- `prompt_messages` は 1 件あたり数十 KB になり得る。容量が問題になれば `AUDIT_LOG_PROMPTS=false` にするか、期間を決めて外部へ退避する。
- 別接続で書くので、`chat.request` が記録された後に DM の保存が失敗するケースがあり得る（その場合も `chat.response` は無い = 追跡できる）。
- いいね・既読・退会はテキストを含まないため監査ログの対象外（退会の復旧は運用者が記録を残す）。

## 代替案

- **stdout（ログ基盤）だけに出す**: DD で SQL による集計・突合ができない。仕様書もテーブルを指定している。
- **チャットと同じトランザクションで書く**: 保存が失敗すると `chat.request` まで消え、「何を送られたか」が残らない。
- **外部の監査ログサービス**: 構成要素と契約が増える。stdout の JSON を Fly.io のログ転送で外部に送ることは追加で可能。
