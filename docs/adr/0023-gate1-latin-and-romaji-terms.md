# ADR-0023: Gate #1 のラテン文字・ローマ字の照合と残存リスク

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §10・§1（成人のみ） / [ADR-0010](0010-gate1-moderation.md)（本 ADR で追補） /
  実装: `apps/api/app/services/moderation.py`（`MINOR_PATTERNS_ROMAJI` / `MINOR_WORD_PATTERNS` / `Term.word` / `_strip_noise` / `_collapse_noise`）,
  `apps/api/tests/test_moderation.py`, `packages/prompts/templates/dm_system.ja.txt`

## コンテキスト

- ADR-0010 の語彙は主に漢字・かなで、英字は `JK` / `JC` と `loli` / `underage` などの英語の語だけだった。
  「shougakusei」「joshikousei」「17sai」のようにローマ字で書くと、未成年を想起させる語が Gate #1 を素通りした。
- 照合前の正規化で空白・記号を取り除くため、英字の短い語は単語境界を取れない（「JK ga suki」は `jkgasuki`、「enji」は「genjitsu」の一部）。
  境界を取らずに部分一致させると一般語・人名に誤爆する（「shota」→ 翔太、「kokosei」→「koko seikatsu」、「youjo」→「youjou（養生）」）。
- 文字の間に結合文字（下線 U+0332 など）や見えない書式文字を挟むと、正規化後も語が分断されて照合を逃れられた。

## 決定

- **ローマ字の長い綴り**（`MINOR_PATTERNS_ROMAJI`）: 小学生 / 中学生 / 高校生 / 女子高生（学年表現 `shougaku5nen` を含む）、未成年、ランドセル、
  ロリコン、ショタコンを、長音の揺れ（ou / oo / o / ō / ô、uu / u / ū / û）込みの正規表現で、**空白・記号を除いた本文**に照合する。
  他の語の中に現れにくい長さの綴りだけを登録する。
- **英字・ローマ字の短い語**（`MINOR_WORD_PATTERNS`、`Term.word=True`）: `jk` / `jc`、`kokosei`、`youjo` 系、`enji`、年齢の `〜17 sai` を、
  前後が英字でないこと（単語境界）を条件に照合する。照合先は **空白・記号を除いた本文** と **空白・記号を 1 つの空白として残した本文**
  （`normalize(..., keep_word_breaks=True)`）の両方。後者で「JK ga suki」「I like jk girls」「15 sai desu」のように空白で区切った文を拾い、
  前者で記号・見えない文字を挟んだ表記を拾う。
  年齢は相対年齢・期間（「3sai ue」「5sai kara」「差」「違い」）を除外する。
- **結合文字・見えない書式文字**（Unicode の M* と Cf）は照合前に取り除く（NFKC で合成できなかった結合文字だけが残るため、除去しても
  通常の文字は変わらない）。
- 誤爆と見逃しの両方向を `tests/test_moderation.py` に固定する（「genjitsu」「koko seikatsu」「you join」「node.jsで書いた」「JSの勉強してる」
  「lollipop」などは通し、「s̲h̲o̲ugakusei」のように結合文字を挟んだ表記は止める）。
- **キーワード照合は第一層の対策** と位置付ける。別のローマ字表記・当て字・他の文字体系の似た字形（キリル文字の「о」「с」など）・
  言い換えは照合できない。残りは DM のシステムプロンプト（`packages/prompts/templates/dm_system.ja.txt`）の制約
  「未成年を想起させる表現を一切しない」と、全キャラ成人の設定（ペルソナの `age >= 20` の検証）で抑える。

## 結果・トレードオフ

- 代表的なローマ字・英字の言い換えは、入力・出力とも同じ語彙で止まる（`moderation.flag` に `matched_terms` が残る）。
- 残存リスク: 似た字形の文字への置き換え（ホモグリフ）と、辞書に無い言い換えは見逃す。監査ログの `chat.request` / `chat.response` を
  定期的に抜き取り確認し、見つかった表現は語彙とテストに追加する運用にする（[06-operations.md](../handover/06-operations.md)）。
- 語を足すほど誤爆の可能性が増える。追加するときは誤爆しやすい一般語・人名のテストも一緒に足す。

## 代替案

- **ラテン文字の部分一致（境界なし）**: 「shota」（翔太）・「koko」（ここ）・「enji」（現実）などへの誤爆が多く、通常の会話を差し止める。
- **ホモグリフの正規化（Unicode の confusables 表で置き換え）**: 表の保守が重く、日本語の通常の文字（全角記号・ギリシャ文字の単位など）を
  誤って置き換える恐れがある。MVP では採らず、LLM による分類などの後段の対策（次フェーズ）とあわせて検討する。
- **外部のモデレーション API**: ADR-0010 の代替案のとおり（遅延・コスト・会話内容の外部送信）。
