"""Gate #1 モデレーション（入力・出力テキスト検証。仕様 §10 / BRIEF §2.8）。

- 語彙リストはカテゴリ別のモジュール定数として定義し、`TermProvider` プロトコル経由で参照する。
  将来 DB や外部ファイルに移す場合は `TermProvider` の実装を差し替えるだけでよい。
- 照合前に正規化する: NFKC → 小文字化 → カタカナをひらがなに変換 → 空白・記号を除去。
  （「死 ね」「ｼﾈ」「シネ」などの揺れを同一視する）
- 一部の語（「ロリ」など）はひらがな化すると一般語（「ころり」「とろり」）に誤爆するため、
  カタカナのまま（fold_kana=False）照合する。
- 英字の略語（JK/JC など）は単語境界付きの正規表現で照合する。
- 出力チェックではキャラの `speech.ng_words` も追加で照合する。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

Category = Literal["ng_word", "minor", "real_person", "persona_ng_word"]


@dataclass(frozen=True, slots=True)
class Term:
    """照合語。`pattern=True` の場合 text は（正規化後テキストに対する）正規表現。"""

    text: str
    category: Category
    fold_kana: bool = True
    pattern: bool = False


@dataclass(frozen=True, slots=True)
class ModerationResult:
    flagged: bool
    categories: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)


class TermProvider(Protocol):
    """照合語の供給元（定数 / DB / ファイル等に差し替え可能）。"""

    def terms(self) -> Sequence[Term]: ...


# ---------------------------------------------------------------------------
# 語彙リスト（定数）
# ---------------------------------------------------------------------------

# 暴言・脅迫・差別・性暴力など（入力・出力共通）
# ※ ひらがなのみの短い語（「しね」「ころす」等）は「すこしねむい」「ところすごく」のように
#   記号除去後の一般文に誤爆するため登録しない。
NG_WORDS: Final[tuple[str, ...]] = (
    "死ね",
    "氏ね",
    "殺す",
    "殺してやる",
    "ぶっ殺",
    "消えろ",
    "自殺しろ",
    "レイプ",
    "強姦",
    "輪姦",
    "痴漢して",
    "盗撮",
    "リベンジポルノ",
    "児ポ",
    "児童ポルノ",
    "キチガイ",
    "土人",
    "穢多",
    "覚醒剤",
)
# ひらがな化・部分一致で一般語に誤爆するため、カタカナのまま正規表現で照合する語
# （「ガイジン」「カロリー」等を除外）
NG_PATTERNS_KATAKANA: Final[tuple[str, ...]] = (r"ガイジ(?!ン)", r"シナ人")

# 未成年を想起させる語（入力・出力共通）
MINOR_WORDS: Final[tuple[str, ...]] = (
    "小学生",
    "中学生",
    "高校生",
    "女子高生",
    "女子中学生",
    "女子小学生",
    "男子高校生",
    "男子中学生",
    "しょうがくせい",
    "ちゅうがくせい",
    "こうこうせい",
    "幼女",
    "児童",
    "未成年",
    "ランドセル",
    "スク水",
    "ペド",
    "中坊",
)
# ひらがな化すると「ころり」「とろり」、部分一致だと「カロリー」に誤爆するため、
# カタカナのまま前後のカタカナを除外して照合する
MINOR_PATTERNS_KATAKANA: Final[tuple[str, ...]] = (r"(?<![ァ-ヺー])ロリ(?:ータ|コン)?(?!ー)",)
# 正規化後テキストに対する正規表現（英字略語は単語境界付き、年齢表現、学年表現）
MINOR_PATTERNS: Final[tuple[str, ...]] = (
    r"(?<![a-z])(?:jk|jc|js)(?![a-z])",
    r"(?<![0-9])(?:1[0-7]|[1-9])(?:歳|さい|才)",
    r"(?<![〇零一二三四五六七八九十百千万])(?:十[一二三四五六七]?|[一二三四五六七八九])(?:歳|さい|才)",
    r"(?<!最)(?:小|中|高)[1-6一二三四五六](?:年生|の|だ|です|で|って|$)",
)

# 実在人物（政治家・著名人など）。ここ以外のコード・コンテンツに実在人物名を書かないこと。
# 本MVPでは代表例のみ。運用時は TermProvider を DB 実装に差し替えて拡充する。
# （「トランプ」単体はカードゲームに誤爆するため登録しない）
REAL_PERSON_NAMES: Final[tuple[str, ...]] = (
    "石破茂",
    "岸田文雄",
    "菅義偉",
    "安倍晋三",
    "高市早苗",
    "小泉進次郎",
    "河野太郎",
    "麻生太郎",
    "トランプ大統領",
    "ドナルドトランプ",
    "バイデン",
    "プーチン",
    "習近平",
    "ゼレンスキー",
    "金正恩",
    "天皇陛下",
    "大谷翔平",
    "木村拓哉",
    "イーロンマスク",
)


class StaticTermProvider:
    """モジュール定数の語彙を返す既定の TermProvider。"""

    def __init__(self) -> None:
        terms: list[Term] = []
        terms += [Term(w, "ng_word") for w in NG_WORDS]
        terms += [Term(p, "ng_word", fold_kana=False, pattern=True) for p in NG_PATTERNS_KATAKANA]
        terms += [Term(w, "minor") for w in MINOR_WORDS]
        terms += [Term(p, "minor", fold_kana=False, pattern=True) for p in MINOR_PATTERNS_KATAKANA]
        terms += [Term(p, "minor", pattern=True) for p in MINOR_PATTERNS]
        terms += [Term(w, "real_person") for w in REAL_PERSON_NAMES]
        self._terms = tuple(terms)

    def terms(self) -> Sequence[Term]:
        return self._terms


# ---------------------------------------------------------------------------
# 正規化
# ---------------------------------------------------------------------------

_KATAKANA_START: Final = 0x30A1  # ァ
_KATAKANA_END: Final = 0x30F6  # ヶ
_KANA_OFFSET: Final = 0x60


def katakana_to_hiragana(text: str) -> str:
    return "".join(chr(ord(ch) - _KANA_OFFSET) if _KATAKANA_START <= ord(ch) <= _KATAKANA_END else ch for ch in text)


def _strip_noise(text: str) -> str:
    # 空白（Z*）・記号（P*, S*）・制御文字（C*）を除去。長音「ー」(Lm) は残す。
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in {"Z", "P", "S", "C"})


def normalize(text: str, *, fold_kana: bool = True) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    if fold_kana:
        value = katakana_to_hiragana(value)
    return _strip_noise(value)


# ---------------------------------------------------------------------------
# Moderator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CompiledTerm:
    original: str
    category: Category
    fold_kana: bool
    literal: str | None
    regex: re.Pattern[str] | None


class Moderator:
    def __init__(self, provider: TermProvider | None = None) -> None:
        self._provider = provider or StaticTermProvider()
        self._compiled = self._compile(self._provider.terms())

    @staticmethod
    def _compile(terms: Iterable[Term]) -> tuple[_CompiledTerm, ...]:
        compiled: list[_CompiledTerm] = []
        for term in terms:
            if term.pattern:
                compiled.append(_CompiledTerm(term.text, term.category, term.fold_kana, None, re.compile(term.text)))
            else:
                literal = normalize(term.text, fold_kana=term.fold_kana)
                if literal:
                    compiled.append(_CompiledTerm(term.text, term.category, term.fold_kana, literal, None))
        return tuple(compiled)

    def check(self, text: str, *, extra_ng_words: Sequence[str] = ()) -> ModerationResult:
        folded = normalize(text, fold_kana=True)
        unfolded = normalize(text, fold_kana=False)
        categories: list[str] = []
        matched: list[str] = []

        def hit(category: str, term: str) -> None:
            if category not in categories:
                categories.append(category)
            if term not in matched:
                matched.append(term)

        for term in self._compiled:
            target = folded if term.fold_kana else unfolded
            if term.literal is not None and term.literal in target:
                hit(term.category, term.original)
            elif term.regex is not None and (m := term.regex.search(target)) is not None:
                hit(term.category, m.group(0))
        for word in extra_ng_words:
            literal = normalize(word)
            if literal and literal in folded:
                hit("persona_ng_word", word)
        return ModerationResult(flagged=bool(matched), categories=categories, matched_terms=matched)
