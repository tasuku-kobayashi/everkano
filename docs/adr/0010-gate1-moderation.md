# ADR-0010: Gate #1 モデレーション設計

- ステータス: 採用（ラテン文字・ローマ字の照合と残存リスクを [ADR-0023](0023-gate1-latin-and-romaji-terms.md)、公開されるキャラの返信での URL・ドメイン名の差し止め（`link`）を [ADR-0027](0027-comment-reply-generation-limits.md)、E6 の安全対応を Gate #1 より前に行うことと出力の追加の検査（OutputGuard: `commerce_coupling` / `human_claim`）を [ADR-0043](0043-safety-e6-and-output-guard.md) で追補）
- 日付: 2026-09-25
- 関連: 仕様書 §10・§8.2・§1（成人のみ） / [ADR-0013](0013-audit-log.md) / 実装: `apps/api/app/services/moderation.py`, `apps/api/tests/test_moderation.py`, `packages/personas/scripts/validate_personas.py`

## コンテキスト

- 仕様書 §10 は本 MVP で **Gate #1（入力・出力のテキスト検証）だけ** を実装し、NG ワード辞書・未成年を示唆する語・実在人物名で照合すること、
  ヒット時は生成を中断して定型文を返し `moderation.flag` を記録すること、辞書は `moderation.py` の定数として定義し後から DB 化・
  外部ファイル化できる構造にすることを求めている。画像生成が無いので Gate #2〜#5 は次フェーズ。
- 日本語はひらがな / カタカナ / 全角半角 / 記号挟み（「死 ね」「ｼﾈ」）の揺れが大きい一方、単純な部分一致は一般語に誤爆しやすい
  （「ころり」「カロリー」に「ロリ」、「いきちがい」に「きちがい」）。
- キャラクターは全員成人であることが前提（未成年を想起させる表現を一切出さない）。

## 決定

- **語彙リスト**をカテゴリ別のモジュール定数にする: `ng_word`（暴言・脅迫・差別・性暴力など）、`minor`（小学生 / 中学生 / 高校生 / JK / JC /
  ロリ / 幼女 / 児童 / 未成年 / ランドセル …、`〜17歳` の年齢表現を算用数字・漢数字で）、`real_person`（代表的な実在人物名。ここ以外のコード・
  コンテンツに実在人物名を書かない）。出力チェックでは加えてキャラの `speech.ng_words`（`persona_ng_word`）を照合する。
- `TermProvider` プロトコル（`terms() -> Sequence[Term]`）経由で参照する。既定は `StaticTermProvider`（定数）。DB やファイルに移すときは
  実装を差し替えるだけでよい。
- **正規化してから照合する**: NFKC → 小文字化 → カタカナをひらがなに → 空白・記号・制御文字を除去（長音「ー」は残す）。
  誤爆しやすい語は「かなを統一しない」照合や前後の文字の条件付き正規表現にする（`fold_kana=False`、`pattern=True`）。
  英字略語（JK / JC）は英字の境界付き。キャラ別 NG ワードは NFKC + 小文字化のみで、かな語は語の途中でないことを条件にする。
- 結果は `ModerationResult(flagged, categories, matched_terms)`。**ヒットはすべて `moderation.flag`** を `audit_logs` に記録する
  （`stage`・`context`・`categories`・`matched_terms`・`text` ほか）。
- 適用箇所と挙動:

  | 箇所                                   | stage / context            | ヒット時                                                                                           |
  | -------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------- |
  | `POST /chat` のユーザー発言            | `input` / `chat`           | LLM と記憶抽出を行わず、ペルソナの `moderation_reply`（無ければ「ごめん、その話はちょっとできないな」）を返す。**両方のメッセージを保存**し `moderated=true` |
  | `POST /chat` の LLM 出力               | `output` / `chat`          | 返答を同じ定型文に差し替えて保存、`moderated=true`                                                 |
  | `POST /comments` の本文                | `input` / `comment`        | 422 `moderation_blocked`（保存しない）。画面はトースト                                              |
  | キャラのコメント返信（自動・手動）     | `output` / `comment_reply` | 保存しない（`/comments/generate` は `comment: null`）                                              |
  | `POST /memories`・`PATCH /memories/{id}` の本文 | `input` / `memory` | 422 `moderation_blocked`（記憶はプロンプトに入るため）                                            |

- 差し止めた DM の発言は保存されるが、以後の LLM への履歴・抽出・要約には本文を渡さない（[ADR-0009](0009-memory-engine.md)）。
- コンテンツ側の防御として、ペルソナ YAML の `age` は 20 以上の整数でなければ **API が起動しない**（`services/persona.py`）。
  YAML / `seed/feed.yaml` / `seed.sql` に未成年を想起させる語や 20 歳未満の年齢表記が無いことも CI（`pnpm personas:validate`）で検査する。

## 結果・トレードオフ

- 決定的で速い（LLM を呼ばない）ので、入力チェックで遅延やコストが増えない。判定はテスト（`tests/test_moderation.py`）で誤検知・見逃しの
  両方向を固定している。語を追加したらテストも足す。
- 辞書方式なので、言い換え・文脈による表現は見逃す。実在人物名は代表例のみ。運用で `TermProvider` を DB 実装にして拡充する想定。
- 入力で差し止めた DM も保存するため、会話の記録（監査）とユーザーの画面表示は一致する（差し止めた発言も本人の DM 画面には残る）。
- 画像・音声の検証（Gate #2〜#5）は未実装（次フェーズ。[06-operations.md](../handover/06-operations.md#既知の制約と次フェーズ)）。

## 代替案

- **外部のモデレーション API / LLM による分類**: 文脈を理解できるが、遅延・コスト・会話内容の外部送信が増える。仕様書は辞書照合を指定している。
  次フェーズで Gate #1 の後段に追加する候補。
- **入力で差し止めた発言を保存しない**: 監査上「何を言われ、何を返したか」が会話の中で追えなくなる。保存したうえで LLM の文脈から除外した。
- **辞書を最初から DB に置く**: 管理画面がスコープ外（§12）のため編集手段が SQL だけになる。定数 + `TermProvider` で差し替え可能にした。
