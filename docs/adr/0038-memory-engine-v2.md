# ADR-0038: 記憶エンジン v2（種類・返答の後の 1 回の分析・矛盾の置き換え・約束・ランキング・キャラ側の記憶）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 §4（M1〜M11）・§8・§9.2 / [ADR-0009](0009-memory-engine.md)（本 ADR で一部置き換え）・[ADR-0024](0024-memory-capacity-per-pair.md)・
  [ADR-0028](0028-llm-input-budgets-and-summary-retries.md)（本 ADR で追補）・[ADR-0005](0005-vector-index-and-exact-memory-search.md)・[ADR-0022](0022-embedding-failures-and-audit-additions.md)・
  [ADR-0035](0035-character-engine-architecture.md)・[ADR-0036](0036-engine-job-queue-and-scheduler.md)・[ADR-0039](0039-user-edited-memory-protection.md)・[ADR-0040](0040-character-calendar.md) /
  実装: `apps/api/app/engine/memory/`（`service.py`・`analysis.py`・`ranking.py`・`config.py`・`summary.py`・`promises.py`・`dates.py`・`capacity.py`・`store.py`・`panel.py`・
  `embedding.py`・`reembed.py`・`mock.py`）, `packages/prompts/templates/memory_analysis.ja.txt`・`memory_summary.ja.txt`, `apps/api/app/routers/memories.py`・`promises.py`,
  `infra/supabase/migrations/20260926000000_character_engine.sql`・`20260926100000_memory.sql`, `apps/api/tests/engine/memory/`

## コンテキスト

- エンジン仕様書 §4 は、会話ごとの自動抽出（M1）、種類の分類（M2）、重複の統合（M3）、矛盾したら新しい方を正にして古い方を履歴に残す（M4）、
  話題に関係する記憶の注入（M5）、約束の特別扱いと期日での話題化（M6）、要約（M7）、キャラ側の記憶（M8）、ユーザー × キャラの分離（M9）、
  自然な参照（M10）、メモリパネル（M11・E5）を求める。
- MVP（[ADR-0009](0009-memory-engine.md)）は返答の生成と並行して抽出し、候補の追加と類似度による更新だけを行っていた。種類・矛盾・約束・キャラ側の記憶は無く、
  抽出は返答の経路で毎ターン LLM を 1 回使っていた（E8・E7 に不利）。
- 調査・判断の対象（§4.3）: 先行事例、抽出のタイミングとコスト、重要度、検索のランキング、日本語の埋め込み、重複と矛盾、減衰、要約の粒度、自然な参照。

## 決定

### 種類と状態（M2・M4。`memories` の拡張）

- `kind`: `fact`（事実）/ `preference`（好み）/ `episode`（出来事）/ `promise`（約束・予定）/ `emotion`（気持ち）/ `relationship`（呼び方・距離感）/
  `summary`（中期要約。既存の `summary` タグの行は移行時に `kind = summary` にした）。
- `status`: `active` / `superseded`（矛盾で置き換えられた履歴）。`superseded_by`・`superseded_at`、参照の記録 `last_referenced_at`・`reference_count`、
  元の会話 `source_conversation_id`。

### 抽出のタイミング（M1）

- **返答の後のジョブ `post_turn` でまとめて分析する**（会話ごとにデバウンス。[ADR-0036](0036-engine-job-queue-and-scheduler.md)）。1 回に扱うのは
  `analyzed_until` より新しいターン（最大 `ENGINE_POST_TURN_MAX_TURNS`）。返答の経路では LLM を使わない。
- 相づち・あいさつだけのバッチ（「うん」「ただいま」など。`text.is_trivial_turn`）は LLM を呼ばない（E7）。その場合も、キャラの返答が約束に触れたこと
  （「おかえり！面接どうだった？」）だけは規則で記録する。
- Gate #1 で差し止めたターン・E6 の安全対応をしたターンは分析しない（本文を記憶に残さない）。
- MVP の並行抽出（`memory_extraction.ja.txt`・`services/memory.py` の抽出）は **廃止** した（ADR-0009 の該当部分を置き換え）。

### 1 回の分析で行うこと（`memory_analysis`。M1〜M4・M6・M8）

- 入力: 今回のターン、ユーザーの文ごとに似ている既存の記憶と、矛盾の検出用に常に添える重要な事実・呼び方・好み（`MemoryConfig.analysis_*`）、未達の約束、
  日付の早見表（今日・今週・来週・再来週の範囲。日本時間）。既存の記憶・約束は `m1` / `p1` … の参照で渡す。
- 出力（JSON。スキーマ検証 + 1 回の再生成。[ADR-0035](0035-character-engine-architecture.md)）:
  - `memories`: `add` / `update`（既存に統合, M3）/ `supersede`（矛盾 → 新しい記憶を作り、古い方を `superseded` の履歴に, M4）/ `noop`。
    Mem0 の ADD / UPDATE / DELETE / NOOP に相当し、DELETE は履歴を残す `supersede` にした。
  - `promises`: 約束の内容と **絶対日付に直した期日**（精度 `datetime` / `day` / `week` / `month` / `unknown`。日付だけなら日本時間のその日の 12:00）。
    過去・遠すぎる期日は日付の解決の誤りとして捨てる。
  - `promise_updates`: 既存の約束の `mentioned` / `done` / `cancelled`。
  - `character_statements`: キャラが自分について話したこと（M8 → `character_memories`、そのユーザー用）。
- 書き込みの前の安全装置（1 トランザクション・ペア単位のアドバイザリーロック。監査ログはコミットの後にまとめて書く）:
  1. 削除した記憶の墓標と同じ・よく似ていれば作らない（E5。[ADR-0039](0039-user-edited-memory-protection.md)）
  2. 有効な記憶と `MEMORY_DEDUP_SIMILARITY` 以上似ていれば統合する（M3。ユーザーが書いた記憶は上書きしない）
  3. ユーザーが書いた・直した記憶（`is_user_edited`）は `update` / `supersede` の対象にしない。新しい情報は別の記憶として追加する（E5）
  4. `add` は重要度が `MEMORY_IMPORTANCE_THRESHOLD` 以上のときだけ。`update` / `supersede` は重要度に関係なく適用する
  5. 件数の上限（[ADR-0024](0024-memory-capacity-per-pair.md)）。入れ替えは **置き換えられた履歴（`superseded`）を先に**、次に重要度が低く更新の古い自動記憶
     （ユーザーが書いた記憶と要約は入れ替えない）
  6. 存在しない参照・同じ記憶への重複した操作は、新しい情報として（重複判定つきで）追加する
- 設定・関係・評価を書き換えようとする発言は、分析の前にふるい落とす（記憶経由の注入の防止。[ADR-0039](0039-user-edited-memory-protection.md)）。

### 約束（M6・C8）

- `promises` テーブル（期日・精度・状態・元の記憶 `source_memory_id`・元の発言・カレンダーの予定 `event_id`）。約束の本文は `kind = promise` の記憶にもする。
- 状態: `pending`（未達）→ `mentioned`（キャラが話題にした。自発メッセージ、または返答の分析で検出）→ `done` / `cancelled`。自動の変更は前に進むだけ。
  ユーザーは API（`PATCH /promises/{id}`）で完了・取り消しができ、`done` ⇄ `cancelled` の付け替えも許す。すべて `promise.status_change` に残す。
- 取り消したら、紐づくカレンダーの予定を取り消し、元の記憶（「〜の予定がある」）を履歴（`superseded`）にする（ユーザーが書いた記憶は変えない）。
- 新しい約束は同じジョブの中でカレンダーに登録する（[ADR-0040](0040-character-calendar.md)）。期日が今日の前後数日（`MemoryConfig.promise_window`）の約束は
  返答の文脈に入り、当日は自発メッセージのきっかけになる（[ADR-0042](0042-proactive-messenger.md)）。
- **期日を過ぎた約束は、閉じられるまで `pending` / `mentioned` のまま**（自動で期限切れにする規則は未決。メモリパネルでは「期日を過ぎた約束」として出す）。

### 検索（M5）と減衰

- 候補はペアの中の厳密検索（`MATERIALIZED` CTE。[ADR-0005](0005-vector-index-and-exact-memory-search.md)）で取り出し、総合点で並べ替える:
  **類似度・重要度・新しさ・語の一致（固有名詞などの完全一致を拾うハイブリッド検索）の重み付き和 + 種類ごとの加点**（`engine/memory/ranking.py`）。
  重み・半減期・加点・件数は `engine/memory/config.py`（`RankingWeights` / `DEFAULT_HALF_LIFE_DAYS` / `DEFAULT_KIND_BOOST` / `MemoryConfig`）の調整値。
- 新しさ = 0.5 ^ (経過日数 / 種類ごとの半減期)。経過日数は「最後にプロンプトへ入れた日時」と作成日時の新しい方から数える（参照された記憶は忘れにくい）。
  **減衰はランキングだけに効き、時間の経過で記憶を消すことはない**。記憶が消えるのは、ユーザーの削除と件数の上限による入れ替えだけ。
- 常に入れる記憶: 呼び方・距離感（`relationship`）、重要度の高い事実（MemGPT の core memory に相当）、最新の要約。残りは関係の深いもの（類似度が下限以上）から、
  枠が余れば総合点の順に、件数と文字数の上限まで入れる。
- キャラ側の記憶（M8・C9）: そのペアのキャラの発言と、全ユーザー共通の予定由来の出来事（直近の数日分）から、質問に近いものを入れる。
- 使った記憶は返答の後に `mark_referenced`（`last_referenced_at` / `reference_count`。内容の更新ではないので `updated_at` は変えない）。

### 要約（M7）

- ジョブ `memory.summarize`（会話ごとにデバウンス）で、[ADR-0028](0028-llm-input-budgets-and-summary-retries.md) のチャンク化・失敗時のバックオフ・飛ばし方のまま要約する
  （MVP の BackgroundTask から移した）。`kind = summary`。プロンプトは話の流れ・気持ち・関係の変化を残すことに寄せ、会話ログの先頭に日付の範囲を付ける。
  Gate #1 で差し止めたターンと、書き換えを狙った発言は入れない。

### 自然な参照（M10）と分離（M9）

- プロンプトの「あなたが覚えていること」は 1 行 1 件で記録日を添え、「以前あなたは〜と言いました」のような機械的な言い方をしないよう指示する（`dm_system.ja.txt`）。
- すべてのクエリを (user_id, character_id) で絞る。キャラ側の記憶の全ユーザー共通の行（`user_id` null）は予定由来の出来事だけ。

### 埋め込み

- **`vector(1536)` と `text-embedding-3-small`（`EMBEDDING_MODEL`）のまま**。日本語の品質を上げる次の手は、`text-embedding-3-large` を `EMBEDDING_DIMENSIONS=1536`
  で使う（スキーマの変更なし）+ `scripts/reembed_memories.py`（`memories` と `character_memories` を再計算する。墓標は本文が無いのでハッシュでだけ照合される）。
- 日本語に強いオープンなモデル（multilingual-e5・ruri など）は、自前のホスティング（GPU）と次元数の変更（スキーマの変更・全件の再計算）が要るので今回は採らない。
  採否は live の評価ハーネスで想起率を比べて決める（公開のベンチマークの数値は、使う前に原典で確認すること）。

### 先行事例の比較（§4.3）

| 事例 | 取り入れた考え方 | 採らなかった考え方（理由） |
| --- | --- | --- |
| Generative Agents | 抽出時の重要度。検索 = 新しさ + 重要度 + 関係の深さ。新しさは最後に参照した時刻から | すべての観察を保存する・重要度だけの LLM 呼び出し（コスト）。「振り返り（reflection）」はチャンク単位の要約に簡略化 |
| MemGPT / Letta | 常に入れる核の記憶（呼び方・重要な事実）と、検索で入れる層の分離 | 返答の中でモデル自身に記憶を編集させる（返答の遅延 E8・コスト・会話からの注入で E5 を破られる危険） |
| Mem0 | 似ている既存の記憶と突き合わせて ADD / UPDATE / DELETE / NOOP を 1 回で決める | DELETE（履歴を残す supersede にした, M4）。グラフの記憶（追加の基盤） |
| Zep / Graphiti | 事実の有効期間（新旧の関係）を持つ。語の一致とベクトルのハイブリッド検索 | グラフ DB・エンティティの同定（追加の基盤と複雑さ）。有効期間は `status` / `superseded_*` でリレーショナルに表す |

### メモリパネルと API（M11）

- `GET /memories?include_superseded=`（既定は有効な記憶だけ）、`POST` / `PATCH /memories`（`kind` を指定可。`summary` は不可）、`DELETE /memories/{id}`
  （墓標を残して行を消し、その記憶から作られた未達の約束を取り消す。[ADR-0039](0039-user-edited-memory-protection.md)）、`GET /promises`・`PATCH /promises/{id}`。
  詳細は [04-api.md](../handover/04-api.md)。

## 結果・トレードオフ

- 返答の経路から記憶の LLM 呼び出しが無くなり（E8）、分析はデバウンスでまとめて行う（E7）。1 回の分析で統合・矛盾・約束・キャラの発言まで決める。
- 記憶ができるのは会話が止まってから（デバウンスの時間 + ジョブの実行）。直後の数ターンは短期の履歴でつながるが、「覚えました」の通知は遅れて届く。
- 矛盾した古い記憶は消さずに履歴に残る（M4。メモリパネルの「以前の記憶」で見える）。履歴は件数の上限では先に入れ替えられる。
- 期日を過ぎた約束が残り続ける（未決。期限切れの規則を決めたら定期実行で閉じる）。
- ここまでの検証は mock（ルールベースの分析・ハッシュの埋め込み）だけ。live の抽出・想起の品質は評価ハーネスの live 実行で確かめる（[docs/eval](../eval/README.md)）。

## 代替案

- **毎ターン、返答と並行して抽出する（MVP）**: 返答の経路で LLM を 2 回使い、1 ターンごとに費用がかかる。
- **セッションの終わりに抽出する**: DM には明確な終わりが無い。デバウンス（会話が止まったら）で近い効果を得た。
- **抽出・重複判定・矛盾判定を別々の LLM 呼び出しにする**: 精度は上がり得るが、呼び出しが 2〜3 倍になる。
- **矛盾した古い記憶を削除する**: M4（履歴として残す）に反する。
- **時間の経過で記憶を消す（忘却）**: ユーザーが話したことを勝手に消すことになる。ランキングの減衰だけにした。
- **グラフの記憶（Zep / Graphiti・Mem0 のグラフ版）**: 追加の DB と運用。Postgres の行と列で足りる。
