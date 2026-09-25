# 05. メモリエンジンと Gate #1 モデレーション

「この子は自分のことを覚えている」（仕様書 §18）を支えるメモリエンジンと、入出力を守る Gate #1 の仕組み・調整方法・確認方法。
設計判断の理由は [ADR-0009](../adr/0009-memory-engine.md)（メモリ）、[ADR-0005](../adr/0005-vector-index-and-exact-memory-search.md)（検索）、
[ADR-0010](../adr/0010-gate1-moderation.md)（Gate #1）、[ADR-0008](../adr/0008-llm-embedding-providers-and-mock.md)（LLM / 埋め込み / モック）。

## メモリの 3 層

| 層   | 実体                                                        | 作られるタイミング                                          | LLM への渡し方                                                  |
| ---- | ----------------------------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------- |
| 短期 | `messages` の直近 60 件（30 ターン）                         | 毎回の `/chat` で保存                                        | `user` / `assistant` の会話履歴メッセージ（古い順）              |
| 中期 | `memories`（`tags = {summary}`、重要度 0.7）                 | 未要約が 100 件を超えたら、応答後のバックグラウンドで作成    | 最新 2 件を常に「あなたが覚えていること」へ（`（これまでの会話の要約）`） |
| 長期 | `memories`（抽出 or ユーザーが追加）                         | `/chat` の返答生成と並行して抽出 → 重要度 0.6 以上を保存      | 発言に近い上位 5 件を重要度順に「あなたが覚えていること」へ       |

## パラメーター

すべて API の環境変数（`app/core/config.py` で起動時に範囲を検証）。値を変えたら API を再起動する。

| 変数                            | 既定  | 意味と調整の目安                                                                                             |
| ------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------ |
| `MEMORY_SHORT_TERM_TURNS`       | 30    | 短期に入れるターン数（×2 件）。増やすと文脈は伸びるがプロンプトのトークンが増える                              |
| `MEMORY_SUMMARY_TRIGGER_TURNS`  | 50    | 未要約メッセージが「この値 × 2」件を超えたら中期要約                                                          |
| `MEMORY_IMPORTANCE_THRESHOLD`   | 0.6   | 抽出候補を保存する最低重要度。抽出プロンプトにも「これ未満は出力しない」と伝えている。上げると記憶が減る         |
| `MEMORY_RETRIEVAL_TOP_K`        | 5     | 類似度で取る件数（この後に重要度で並べ替え）                                                                   |
| `MEMORY_DEDUP_SIMILARITY`       | 0.92  | この類似度以上の既存の記憶は「同じ記憶」とみなして更新（ユーザー編集済みはスキップ）。下げると統合が増える      |
| `EMBEDDING_MODE` / `EMBEDDING_MODEL` | hash / text-embedding-3-small | 変えたら **全件の再埋め込みが必須**（[06-operations.md](06-operations.md#埋め込み設定の切り替え)） |
| `AUDIT_LOG_PROMPTS`             | true  | 監査ログにプロンプト全文と抽出の生出力を入れる                                                                 |

コード上の定数（`services/memory.py`）: 1 発言あたりの候補は最大 5 件、記憶の本文は 500 文字、抽出に渡す文脈は直近 6 件、要約は 900 文字まで。

## プロンプトの構成

`packages/prompts/templates/dm_system.ja.txt`（仕様書 §8.2 の構成 + 今の状況・制約の追加）を、ペルソナ YAML の値で埋める。

```
[system]  人物設定 / 話し方 / 関係性 / 今の状況（日本時間と schedule_pattern）/
          あなたが覚えていること（1 行 1 件。先頭に（二人だけの秘密）/（これまでの会話の要約）、末尾に（YYYY年M月D日（曜）に記録））/
          直近の会話（注記のみ）/ 制約（1〜3 文、AI であることを示唆しない、未成年・実在人物・差別の禁止、記憶の扱い方 など）
[assistant / user ...]  短期メモリ（古い順。Gate #1 で差し止めた発言は「（不適切な発言のため省略）」）
[user]    今回の発言
```

- 記憶が無ければ「（まだ特にない）」。記憶に呼び方の希望があれば、既定の二人称より優先するよう制約に書いている。
- テンプレートは起動時に必須プレースホルダの有無を検証する（typo で API が起動しない）。文面の調整はコード変更なしでできる
  （[packages/prompts/README.md](../../packages/prompts/README.md)、`uv run pytest tests/test_prompt.py`）。

## 記憶の一生

```mermaid
flowchart TD
  M["ユーザーの発言（/chat）"] --> X{"Gate #1（入力）"}
  X -- ヒット --> S1["保存するが以後の履歴・抽出・要約では本文を渡さない"]
  X -- 通過 --> E["抽出（LLM 1 回、JSON）"]
  E --> T{"重要度 >= 0.6"}
  T -- いいえ --> D1["捨てる（chat.response の extraction に候補として残る）"]
  T -- はい --> N{"同じペアに cos >= 0.92 の記憶がある"}
  N -- ない --> C["新規作成（memory.create）"]
  N -- ある --> U{"その記憶は is_user_edited"}
  U -- はい --> K["何もしない（ユーザーの記憶を守る）"]
  U -- いいえ --> UP["本文・埋め込みを更新、重要度は大きい方（memory.update）"]
  P["メモリパネルで追加・編集"] --> UE["is_user_edited = true（memory.create / update）"]
  C --> R["次の /chat で検索対象"]
  UP --> R
  UE --> R
  DEL["メモリパネルで削除（memory.delete）"] --> GONE["以後は検索されない"]
```

## Gate #1

| 観点           | 内容                                                                                                                             |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 語彙           | `apps/api/app/services/moderation.py` の定数: `NG_WORDS` / `NG_PATTERNS` / `NG_PATTERNS_UNFOLDED`（`ng_word`）、`MINOR_WORDS` / `MINOR_PATTERNS` / `MINOR_PATTERNS_UNFOLDED`（`minor`）、`REAL_PERSON_NAMES`（`real_person`）。出力時はキャラの `speech.ng_words`（`persona_ng_word`） |
| 正規化         | NFKC → 小文字 → カタカナをひらがな → 空白・記号・制御文字を除去（誤爆する語はかなを統一しない / 前後の条件付き正規表現）           |
| 入力でヒット   | DM: 定型文（ペルソナの `moderation_reply`、無ければ「ごめん、その話はちょっとできないな」）を返し両方保存 / コメント・記憶: 422 `moderation_blocked` |
| 出力でヒット   | DM: 定型文に差し替え / コメント返信: 保存しない                                                                                    |
| 記録           | すべて `audit_logs` の `moderation.flag`（`stage`・`context`・`categories`・`matched_terms`・`text`）                            |

### 語を追加・調整する

1. `moderation.py` の該当する定数に追加する。**一般語に誤爆しないか**を考える（ひらがなの短い語は記号除去後の文に紛れやすい。
   カタカナ語はひらがな化すると別の語に一致することがある → `*_UNFOLDED` か前後条件付きの正規表現にする）。
2. `apps/api/tests/test_moderation.py` に「ヒットすべき文」と「ヒットしてはいけない文」の両方を足す。
3. `cd apps/api && uv run pytest tests/test_moderation.py -q`。
4. キャラ固有の禁止語は、そのキャラの YAML の `speech.ng_words` に足す（出力チェックだけに使われる。口調例 `examples` に含めると検証で落ちる）。

実在人物名のリストは代表例のみ。運用で増やす場合は `TermProvider` を DB 実装に差し替えることを推奨（管理画面は無いので SQL で管理する）。

### 成人のみの担保

- ペルソナ YAML の `age` が 20 未満（または整数でない）なら API は起動しない。
- `pnpm personas:validate`（CI）が、YAML・`seed/feed.yaml`・`seed.sql` に未成年を想起させる語や 20 歳未満の年齢表記が無いことを検査する。
- キャラを書くときのルールは [packages/personas/README.md](../../packages/personas/README.md#コンテンツの方針キャラを書くときのルール)。

## 動作の確認

| 目的                                       | 方法                                                                                                                             |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| 記憶が作られ、後で使われるか（A9）         | DM で「来週、大阪に出張するんだ」→ 別の話を数往復 →「大阪でおすすめの場所ある？」。レスポンスの `memories_used` と返答、メモリパネル |
| 削除した記憶が返答に出ないか（A10）         | メモリパネルで削除 → 同じ質問。`memories_used` に含まれないこと                                                                  |
| 何が検索・注入されたか                     | `audit_logs` の `chat.response`: `memories_used` / `memories_created` / `extraction.candidates` / `prompt_messages`（[SQL 例](06-operations.md#監査ログaudit_logsの調べ方)） |
| 自動テスト                                 | `apps/api/tests/integration/test_memory_api.py`（10 往復後の想起・削除後の非参照・重複排除とユーザー編集の保護・要約の発火と除外）、`apps/web/e2e/memory.spec.ts` |

**注意**: ローカル・CI・E2E は `LLM_MODE=mock` / `EMBEDDING_MODE=hash`。モックはルールベースの抽出と語彙の重なりによる想起なので、
**本物の LLM・埋め込みでの品質（文脈理解・自然な想起・重要度の較正）は staging で確認する**必要がある。
