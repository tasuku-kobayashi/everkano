# ADR-0035: キャラクターエンジンの全体構成（モジュールと契約・Context Assembler・用途別のモデル・構造化出力・時計・機能フラグ）と着手前の前提

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 §2（E1〜E9）・§3・§8・§10・§13 / [ADR-0008](0008-llm-embedding-providers-and-mock.md)・[ADR-0013](0013-audit-log.md)・[ADR-0020](0020-content-seed-generation.md)（本 ADR で追補）・
  [ADR-0028](0028-llm-input-budgets-and-summary-retries.md)（DM 履歴の上限の値を本 ADR により置き換え）・[ADR-0036](0036-engine-job-queue-and-scheduler.md)〜[ADR-0049](0049-prompt-order-and-prefix-cache.md) /
  実装: `apps/api/app/engine/`（`types.py`・`context_assembler.py`・`pipeline.py`）, `apps/api/app/container.py`, `apps/api/app/core/config.py`（`llm_model_for` / `ENGINE_*`）,
  `apps/api/app/services/llm.py`（`LLMRequest.json_mode` / `register_mock_handler`）, `apps/api/app/services/persona.py`（`EngineProfile`）, `packages/personas/*.yaml`（`engine:`）,
  `packages/personas/scripts/engine_checks.py`, `infra/supabase/migrations/20260926000000_character_engine.sql`〜`20260926140100_proactive_quiet_pair.sql`

## コンテキスト

MVP の納品後に、追加仕様「実装指示書 Project P キャラクターエンジン v1.0（記憶 × カレンダー × 好感度）」（以下、エンジン仕様書。
マイグレーションのコメントの `project-p-character-engine-spec.md`）を受けた。要点は次のとおり。

- 3 つの仕組み（自動記憶・キャラクターカレンダー・好感度）と自発メッセージを独立したモジュールにし、Context Assembler が 1 つのプロンプトに
  まとめる（§3）。すべて Python API（`apps/api`）側に置く。
- ハードルール E1〜E9（§2）。特に E1（課金を好感度に影響させない。構造で守る）、E8（送信から最初の文字まで中央値 2.5 秒。重い処理は返答の後に
  非同期）、E9（3 つの仕組みの状態の変化をすべて `audit_logs` に残す）。
- 評価ハーネスで 30 日・90 日の利用を早送りで再現し、素の LLM（3 つの仕組みなし）と比べる（§9・§11）。そのために時計を差し替えられること。
- 判断に迷ったときの優先順位は「法務 > キャラの一貫性 > ユーザー体験 > コスト > 実装の簡単さ」。
- §13 の確認事項（非同期ジョブのホスト・評価の LLM 費用の上限・好感度を見せるか）にはクライアントから回答が無かった（仮定を置いて ADR に記録する）。

MVP のメモリエンジンは `/chat` の返答生成と並行して記憶を抽出していた（[ADR-0009](0009-memory-engine.md)）。1 往復で LLM を 2 回呼び、返答の経路に
抽出が乗っていた。

## 決定

### 配置と契約

- すべて `apps/api/app/engine/` に置く: `memory/`・`calendar/`・`affinity/`・`proactive/`・`safety/`（各モジュール）、`jobs/`・`scheduler.py`（非同期ジョブと定期実行。
  [ADR-0036](0036-engine-job-queue-and-scheduler.md)）、`context_assembler.py`、`pipeline.py`（返答のパイプライン。[ADR-0037](0037-chat-streaming-sse.md)）。
- モジュール間のやり取りは **`engine/types.py` の値オブジェクトと Protocol だけ**（`MemoryService` / `CalendarService` / `AffinityService` / `ProactiveService` /
  `SafetyService` / `OutputGuard` / `JobQueue`）。他のモジュールの内部実装を import しない。各モジュールのファサードは `app/container.py` で組み立て、
  テストと評価ハーネスは `EngineOverrides` で差し替える。
- ペルソナに `engine:` セクションが無い（フォールバックの）キャラでも、各モジュールは既定値で動く。

### 返答の経路と、返答の後の処理

- 返答の経路（リクエストの中）でするのは、E6 の判定 → Gate #1 → Context Assembler → LLM のストリーミング → 出力の検査 → 保存 → ジョブの登録だけ
  （[ADR-0037](0037-chat-streaming-sse.md)）。**記憶の分析・約束の予定化・好感度の評価は返答の経路では行わない**（E8）。返答の後のジョブ `post_turn` で
  まとめて行う（[ADR-0036](0036-engine-job-queue-and-scheduler.md)・[ADR-0038](0038-memory-engine-v2.md)）。
- 予定の生成・状態の更新・自発メッセージの判定・好感度の日次処理は、スケジューラの定期実行で行う（[ADR-0036](0036-engine-job-queue-and-scheduler.md)）。

### Context Assembler（`engine/context_assembler.py`）

- 返答の前に、短期の履歴・記憶（質問文の埋め込み → 関連する記憶・キャラ側の記憶・期日の近い約束）・世界の時間とキャラの状態・ふたりの関係の指針を
  **並行して**集める（各モジュールが自分の DB 接続を取る）。呼び方の `{name}` は、記憶からユーザーの名前・呼び名が分かれば置き換える。
- セクションごとの上限（トークンの代わりに文字数）を `ContextBudget` で持つ。既定: ペルソナ（静的）1,800 / 世界の時間と状態 300 / 関係 400 /
  記憶 1,200 字・10 件 / キャラ側の記憶 500 / 約束 300 / 短期の履歴 4,000（新しい側を優先）。評価ハーネスで測った値で調整する（使った文字数は
  `ContextBundle.budget_report` → 監査ログ `chat.response` の `context_budget`）。10 体のペルソナの静的な部分は 1,235〜1,406 字で上限の内に収まる。
- **プロンプトの順序は、変わらない部分を先に、毎回変わる部分を最後に** する: system（静的なペルソナと守ること）→ 直近の会話 → 最新の user メッセージの先頭に
  〔今の状況〕（世界の時間・今の状況・ふたりの関係・覚えていること・自分について話したこと・約束）+ 今回の発言。履歴の窓の先頭は「目印の発言」にそろえる。
  DeepSeek の自動のプレフィックスキャッシュを効かせるための判断で、詳細と退けた案は [ADR-0049](0049-prompt-order-and-prefix-cache.md)。
- 全体の締め切りは `ENGINE_CONTEXT_TIMEOUT_SECONDS`（既定 1.5 秒）。**例外を出した・締め切りに間に合わなかったモジュールのセクションは省いて返答を続け**、
  ログと監査ログ `engine.context_degraded` に残す（チャット自体は失敗させない）。
- DM の履歴の詰め方（新しい側の数件は全文・古い発言は切り詰め・上限を超える古い分は渡さない）は [ADR-0028](0028-llm-input-budgets-and-summary-retries.md) の
  `fit_chat_history` のまま。**上限の値は `HISTORY_MAX_CHARS` ではなく `ContextBudget.history_chars` を使う**（ADR-0028 の DM 履歴の上限の値を置き換え）。

### 用途別のモデル

- LLM の用途（purpose）: `chat` / `memory_analysis` / `memory_summary` / `affinity_eval` / `proactive_message` / `feed_caption` / `comment_reply`
  （評価ハーネスだけが使う `sim_user` / `eval_judge`）。
- モデルは LLM クライアントが用途から決める（`Settings.llm_model_for(purpose)`）: `LLM_MODEL_ANALYSIS` → `memory_analysis`・`memory_summary`・`affinity_eval`、
  `LLM_MODEL_PROACTIVE` → `proactive_message`、`LLM_MODEL_CAPTION` → `feed_caption`。未設定なら `LLM_MODEL`。各モジュールはモデル名を知らない。
- `LLM_MODE=mock` では、各モジュールが自分の用途のモック応答を `register_mock_handler(purpose, handler)` で登録する（決定的。評価ハーネス・E2E が使う）。

### 構造化出力（JSON）

- JSON を返す用途（`memory_analysis`・`memory_summary`・`affinity_eval`）は JSON モード（`response_format: {"type": "json_object"}`。プロバイダが 400 で
  拒否したら付けずに再試行）+ **Pydantic のスキーマ検証**。スキーマに合わなければ **検証エラーを添えて 1 回だけ再生成** を頼み、それでも合わなければ
  何も変えずに `llm.error`（`invalid_output`）を残す。
- 一時的な障害（429・5xx・タイムアウト）の扱いは用途ごと: 記憶の分析はジョブを失敗にしてキューの再試行に任せる（何も書き込んでいない）。
  好感度の評価はそのターンを変化なしにする（[ADR-0041](0041-affinity-engine.md)）。
- 既存の記憶・約束は UUID ではなく短い参照（`m1`・`p1` …）で LLM に渡す（トークンの節約と、ID の捏造を防ぐため）。

### 時計

- 現在時刻は `Clock`（`SystemClock` / `ManualClock`）から取る。コンテナが 1 つを持ち（`create_app(clock=...)` で差し替え可能）、パイプライン・ジョブ・
  スケジューラが `now` を取って各モジュールのメソッドに渡す。**エンジンのコードは `datetime.now()` を呼ばない**。
- エンジンが書く日時（メッセージ・記憶・予定・約束・好感度・自発メッセージ・監査ログの `created_at`（`AuditLogger.log(..., at=now)`））はすべて時計の値を
  明示的に書く。`memories` / `promises` の `updated_at` のトリガーはアプリが指定した値を尊重する（`20260926100000_memory.sql`）。
- 同じ会話のメッセージの時刻は単調に増やす（ユーザー発言 = max(now, 直前 + 1ms)、キャラの返答 = +1ms）。時計を止めた評価でも順序が崩れない。
- キャラの世界は日本時間で動く（`to_jst` / `jst_date`）。DB には UTC で保存する。

### 機能フラグ

- `ENGINE_MEMORY_ENABLED` / `ENGINE_CALENDAR_ENABLED` / `ENGINE_AFFINITY_ENABLED` / `ENGINE_PROACTIVE_ENABLED`（既定はすべて true）。false のモジュールは
  Context Assembler・パイプライン・ジョブのハンドラ・スケジューラに渡さない（生成はする）。評価ハーネスの「素の LLM」はすべて false で動かす（[ADR-0045](0045-evaluation-harness.md)）。
- フラグはプロセス全体に効く（ユーザー単位の切り替えではない）。A/B テストはフラグではなくユーザーの割り当てで行う設計（[ADR-0042](0042-proactive-messenger.md)）。

### ペルソナ YAML の `engine:` セクション（エンジン仕様書 §10。ADR-0020 の追補）

- キャラの個性は **モデルではなくデータ** に持たせる: 好感度の感度（軸ごとの感度・`stage_pace`・`expression_delay`・`max_stage`）、段階ごとの振る舞い
  （呼び方 `{name}`・口調・甘え方・話題・例文）、生活（曜日ごとのルーティン・単発の出来事のテンプレート・誕生日・予定の無い時間の過ごし方）、
  季節・行事への反応、自発メッセージの傾向（頻度・きっかけ・しばらく話していないと感じる日数）。10 体すべてに記入した。
- API の Pydantic モデル（`EngineProfile`。未知のキーは拒否）で起動時に検証し、`pnpm personas:validate` が `packages/personas/scripts/engine_checks.py` の
  追加の規則（ルーティンが 24 時間を重なり・すき間なく埋める、画像タグの語彙、行事のキー、E2 / E3 / Gate #1 の語、責める自発メッセージの語、
  期待投稿数の上限など）で検査する（CI）。

### データと権限

- 新しいテーブル・列はマイグレーション `20260926000000_character_engine.sql` と各モジュールの追加（`2026092610*`〜`2026092614*`）。すべて RLS 有効。
  クライアントが読めるのは `character_states`（`character_id`・`status_label`・`busyness`・`updated_at`）、本人の `promises` と `proactive_settings`、
  `memories` の新しい列だけ。書き込みは API とワーカーだけ（[ADR-0002](0002-data-access-split.md) の方針のまま）。
  `memories` を Realtime の publication に加え、anon には主キー列だけを grant する（[ADR-0016](0016-realtime-anon-primary-key-grant.md)）。
- E1: `affinity_*` のテーブルは課金・有料投稿に関わるテーブルへの外部キー・結合を持たない（[ADR-0041](0041-affinity-engine.md) のテストで検査）。
  一覧は [03-data-model.md](../handover/03-data-model.md)。

### 監査ログ（E9。ADR-0013 の追補）

状態の変化はすべて `audit_logs` に残す。payload には原因の ID（`conversation_id`・`message_id`・`memory_id`・`event_id`・`promise_id` など）を入れ、
リクエストの中なら `request_id` も入る（ジョブ・定期実行の中では無い）。`created_at` は時計の時刻。

| event_type | モジュール | いつ |
| --- | --- | --- |
| `memory.create` / `memory.update` / `memory.delete` | 記憶 | 記憶の作成・更新（統合を含む）・削除（ユーザー / 上限による入れ替え）。`source` に `analysis` / `user` など |
| `memory.supersede` | 記憶 | 矛盾する新しい情報で古い記憶を履歴にした（M4）。約束の取り消しで元の記憶を履歴にした場合も |
| `memory.tombstone_suppressed` / `memory.user_edited_skipped` | 記憶 | 削除した記憶の復活を止めた / ユーザーが書いた記憶の上書きを止めた（E5。本文は残さずハッシュだけ） |
| `memory.injection_skipped` | 記憶 | 設定・関係・評価を書き換えようとする発言を記憶にしなかった（[ADR-0039](0039-user-edited-memory-protection.md)） |
| `memory.analysis` / `memory.summary` | 記憶 | 1 回の分析の結果（件数・使用量）/ 中期要約の作成 |
| `promise.create` / `promise.status_change` | 記憶 | 約束の作成 / 状態の変化（`source`: analysis / proactive / user / memory_deleted） |
| `character_memory.create` | 記憶・カレンダー | キャラ側の記憶の作成（キャラの発言 / 終わった予定） |
| `calendar.generate` / `calendar.conflict` | カレンダー | 予定の生成 / 既存の予定と重なって入れなかった予定 |
| `calendar.state_change` / `calendar.event_done` | カレンダー | キャラの状態（今の予定）が変わった / 予定を完了にした |
| `calendar.post_create` / `calendar.promise_event` | カレンダー | 予定からフィードに投稿した / 約束をカレンダーに登録・移動・取り消した |
| `affinity.update` / `affinity.stage_change` / `affinity.decay` | 好感度 | 評価による変化 / 段階の変化 / 日次の減衰 |
| `affinity.manipulation_detected` / `affinity.skipped` | 好感度 | 操作の試みを検知して変化を 0 にした / 安全対応・Gate #1 のターンを評価しなかった |
| `proactive.send` / `proactive.dropped` / `proactive.settings_update` | 自発メッセージ | 送った / 検査で差し止めた / 利用者が設定を変えた |
| `safety.trigger` | 安全対応 | E6 の安全対応をした |
| `engine.job_failed` / `engine.job_dead` / `engine.schedule_failed` / `engine.context_degraded` | 基盤 | ジョブの失敗・再試行の上限 / 定期実行の失敗 / 文脈の一部を省いた |

`llm.error` の `purpose` には上の用途が入る（MVP の `memory_extraction` は廃止）。`moderation.flag` には自発メッセージ（`stage: proactive`）と
フィードのキャプション（`context: feed_caption`）の検査が、`categories` に `commerce_coupling`（E2）・`human_claim`（E3）が加わる
（[ADR-0043](0043-safety-e6-and-output-guard.md)。自発メッセージの責める言い方 `guilt_trip` は `proactive.dropped` の理由に残る）。好感度の変化は `affinity_history` にも行で残る。
`AuditEventType` にある `promise.update` と `proactive.skipped` は予約（現在は書いていない）。

### 着手前の前提（クライアント未回答。エンジン仕様書 §13）

回答が無かったので、次の仮定で進めた。回答があれば該当する ADR を見直す。

1. **非同期ジョブを動かすホスト**: Python API と **同じイメージ**・同じ Fly.io アプリの **別のプロセスグループ `worker`**（`python -m app.worker`）。
   API のプロセスグループではジョブ・定期実行を動かさない。Redis などの追加の基盤は使わない（[ADR-0036](0036-engine-job-queue-and-scheduler.md)）。
2. **評価ハーネスの LLM 費用の上限（開発期間中）**: この開発環境には LLM の API キーも外部への通信も無いため、評価はすべて mock（`LLM_MODE=mock`・
   `EMBEDDING_MODE=hash`）で実行した。live で実行するときは `--max-cost-jpy`（既定 ¥3,000）で打ち切る（[ADR-0045](0045-evaluation-harness.md)）。
3. **好感度をユーザーに見せるか**: 事業側の希望が無いので、**何も見せない**（数値も段階の名前も）（[ADR-0041](0041-affinity-engine.md) の A11 の判断）。

## 結果・トレードオフ

- モジュールごとに差し替え・単体テストができ、素の LLM との比較と時間の早送りができる（評価の結果は [docs/eval/README.md](../eval/README.md)）。
- 返答の経路の LLM 呼び出しは 1 回になった（MVP は返答 + 抽出の 2 回）。代わりに、返答の後の処理のための常駐プロセス（worker）が増えた。
- 文字数はトークン数の近似（日本語はおおむね 1 文字 ≒ 1 トークン弱）。モデルを変えたら `ContextBudget` を測り直す。
- 文脈の一部を省いて返答を続けるため、モジュールの障害は「キャラが覚えていない・状態を知らない」形で静かに現れる。`engine.context_degraded` の件数を
  監視する（[06-operations.md](../handover/06-operations.md#監視とログ)）。
- 機能フラグはプロセス単位なので、本番で一部のユーザーだけ仕組みを止めることはできない。
- `memory_extraction`（MVP の並行抽出）を廃止したため、`ChatResponse.memories_created` は常に空になった。「覚えました」は Realtime（`memories` の INSERT）で出す。

## 代替案

- **1 回の LLM 呼び出しで返答・記憶・好感度をまとめて出させる**: 呼び出しは減るが、返答の遅延が増え（E8）、好感度の評価が返答生成から隔離されない（A10）。
- **モジュールごとにモデルの設定を持つ**: 設定が散らばる。用途からモデルを決める 1 か所（`llm_model_for`）にした。
- **分析系を最初から安いモデルにする**: live での品質のデータが無い。DeepSeek V3 は既に安いので、live の評価で差を測ってから `LLM_MODEL_ANALYSIS` を設定する。
- **トークナイザで予算を数える**: モデルごとに違い、依存が増える（[ADR-0028](0028-llm-input-budgets-and-summary-retries.md) と同じ判断）。
  評価ハーネスだけは任意でトークナイザを使える（[ADR-0045](0045-evaluation-harness.md)）。
- **関数呼び出し（tool use）で構造化出力を得る**: OpenRouter 経由のプロバイダごとに対応が違う。JSON モード + スキーマ検証 + 1 回の再生成は、
  OpenAI 互換の API ならどこでも動く。
- **エンジンを別のサービス（マイクロサービス）にする**: 配置・認証・監視の対象が増える。同じイメージのプロセスグループで足りる。
