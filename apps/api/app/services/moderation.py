"""Gate #1 モデレーション（入力・出力テキスト検証。仕様 §10 / BRIEF §2.8）。

- 語彙リストはカテゴリ別のモジュール定数として定義し、`TermProvider` プロトコル経由で参照する。
  将来 DB や外部ファイルに移す場合は `TermProvider` の実装を差し替えるだけでよい。
- 照合前に正規化する: NFKC → 小文字化 → カタカナをひらがなに変換 → 空白・記号を除去。
  （「死 ね」「ｼﾈ」「シネ」などの揺れを同一視する）
- 一部の語（「ロリ」「レイプ」など）はひらがな化すると一般語（「ころり」「きれい、プロ…」）に誤爆するため、
  カタカナのまま（fold_kana=False）照合する。年齢表現も同様（「1サイズ」の「サイ」を「さい」にしない）。
- 英字の略語（JK/JC など）・ローマ字の短い語は単語境界付きの正規表現で、記号を除いた本文と、空白・記号を
  区切りとして残した本文の両方に照合する（`Term.word`）。ローマ字の長い綴り（shougakusei など）は
  記号を除いた本文に照合する。
- キーワード照合は第一層の対策で、言い換え・当て字・別の文字体系への置き換えをすべて防ぐものではない
  （残りはシステムプロンプトの「未成年を想起させる表現を出さない」制約で抑える。ADR-0010）。
- 出力チェックではキャラの `speech.ng_words` も追加で照合する。こちらは短いかな語（「ブス」「きもい」）が
  多く、上記の正規化（かな統一・記号除去）をすると「ライブすごく」「ときもいい」「楽しく、そして」に誤爆する。
  そのため NFKC + 小文字化のみで照合し、かな語は前後が同じ文字種のかなでないこと（語の途中でないこと）を
  条件にする（`_persona_word_pattern`）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final, Literal, Protocol

Category = Literal["ng_word", "minor", "real_person", "persona_ng_word", "link"]

# URL・ドメイン名（公開されるキャラのコメント返信に、コメント経由で誘導されたリンクを載せないため）
_LINK_RE: Final = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|jp|io|co|me|ly|app|dev|info|biz|xyz|link|site|online|tv|gg|to|cc|us|uk|page|club|shop)\b"
    r"(?:/\S*)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Term:
    """照合語。`pattern=True` の場合 text は（正規化後テキストに対する）正規表現。

    `word=True`（英字の短い語）は、空白・記号を除いた本文に加えて、空白・記号を1つの空白として残した本文にも
    照合する（単語境界を正規表現で取れるようにするため）。
    """

    text: str
    category: Category
    fold_kana: bool = True
    pattern: bool = False
    word: bool = False


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
    "強姦",
    "輪姦",
    "痴漢して",
    "盗撮",
    "リベンジポルノ",
    "児ポ",
    "児童ポルノ",
    "穢多",
    "覚醒剤",
)
# 正規化後（ひらがな化済み）テキストに対する正規表現。部分一致だと一般語に誤爆する語
NG_PATTERNS: Final[tuple[str, ...]] = (
    r"(?<![いゆ])きちがい",  # 「いきちがい（行き違い）」を除外
    r"土人(?!形)",  # 「粘土人形」「土人形」を除外
)
# ひらがな化・部分一致で一般語に誤爆するため、かなを統一せずに照合する語
# （「ガイジン」「カロリー」、「きれい、プロポーズ」→「れいぷ」等を除外）
NG_PATTERNS_UNFOLDED: Final[tuple[str, ...]] = (
    r"ガイジ(?!ン)",
    r"シナ人",
    r"(?<!グ)レイプ",
    r"(?<![きぐ])れいぷ",
)

# 未成年を想起させる語（入力・出力共通）
# ※「少女」単体は「少女漫画」「魔法少女」など一般的な作品名に誤爆するため登録しない。
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
    "幼児",
    "女児",
    "園児",
    "幼稚園児",
    "小学校低学年",
    "児童",
    "未成年",
    "ランドセル",
    "スク水",
    "ペド",
    "中坊",
)
# 年齢表現の直後に来ると「相対年齢・期間」を表す語（「3歳差」「2歳年上」「5歳から習ってた」）
_AGE_RELATIVE: Final[str] = r"(?!差|違|離れ|年上|年下|上|下|から|くらい違|ぐらい違)"
# かなを統一せずに照合する語（ひらがな化すると一般語に誤爆するもの）:
# - 「ロリ」は「ころり」「とろり」「カロリー」「ロリポップ」、「ショタ」は「でしょ、たぶん」に誤爆する
# - 年齢表現は「1サイズ」「2サイクル」のカタカナ「サイ」を「さい」と同一視しないようにする
MINOR_PATTERNS_UNFOLDED: Final[tuple[str, ...]] = (
    r"(?<![ァ-ヺー])ロリ(?:ータ|コン)?(?!ー|ポップ)",
    r"(?<![ぁ-ゖ])ろりこん",
    r"(?<![ァ-ヺ])ショタ",
    r"(?<![ぁ-ゖ])しょた(?!い)",  # 「しょたいめん（初対面）」「しょたい（所帯）」を除外
    rf"(?<![0-9])(?:1[0-7]|[1-9])(?:歳|さい|才){_AGE_RELATIVE}",
    rf"(?<![0-9])(?:1[0-7]|[1-9])サイ(?![ァ-ヺー]){_AGE_RELATIVE}",
    rf"(?<![〇零一二三四五六七八九十百千万])(?:十[一二三四五六七]?|[一二三四五六七八九])(?:歳|さい|才){_AGE_RELATIVE}",
)
# 正規化後（ひらがな化済み）テキストに対する正規表現（英語の語・学年表現など）
# ※ 空白は除去済みのため英語の単語境界は取れない。一般語に含まれにくい綴りだけを登録する
#   （「shota」単体は人名「翔太」に誤爆するため shotacon のみ。短い語は MINOR_WORD_PATTERNS）。
MINOR_PATTERNS: Final[tuple[str, ...]] = (
    r"loli(?!p)",  # lolicon / lolita / loli画像（lolipop は除外）
    r"shotacon|underage|preteen",
    r"pa?edo(?:phil|fil)",  # pedophile / paedophile / pedofilia（「torpedo」などは除外）
    r"(?<![0-9])(?:1[0-7]|[1-9])(?:yo|yrsold|yearsold|yrold|yearold)(?![a-z])",
    r"ようじょ(?!う)",  # 「ようじょう（養生）」を除外
    r"(?<!日本)男児",  # 「日本男児」を除外
    r"(?<!最)(?:小|中|高)[1-6一二三四五六](?:年生|の|だ|です|で|って|$)",
)
# ローマ字表記（「shougakusei」「joshikousei」など、かな・漢字の語彙を素通りする言い換え）。
# 空白・記号を除いた本文に対して照合するため、他の語の中に現れにくい長い綴りだけを登録する
# （短い綴りは下の MINOR_WORD_PATTERNS で単語として照合する）。長音の揺れ（ou / oo / o / ō）も拾う。
_ROMAJI_O: Final[str] = "(?:ou|oo|o|ō|ô)"
_ROMAJI_LONG_O: Final[str] = "(?:ou|oo|ō|ô)"
_ROMAJI_U: Final[str] = "(?:uu|u|ū|û)"
MINOR_PATTERNS_ROMAJI: Final[tuple[str, ...]] = (
    rf"(?:sh|sy){_ROMAJI_O}gaku(?:sei|[1-6]nen)",  # 小学生 / 小学5年
    rf"(?:ch|ty|cy){_ROMAJI_U}gaku(?:sei|[1-6]nen)",  # 中学生 / 中学2年
    # 高校生（「kokosei」は「koko seikatsu（ここ生活）」に誤爆するため単語として照合する）
    rf"k{_ROMAJI_LONG_O}k{_ROMAJI_O}sei|kok{_ROMAJI_LONG_O}sei",
    rf"(?:jo|jyo|zyo)(?:shi|si)k{_ROMAJI_O}sei",  # 女子高生（joshikosei 等。女子中学生・女子小学生は上で拾う）
    r"miseinen",  # 未成年
    r"randoseru",  # ランドセル
    r"ro(?:ri|li)(?:kon|con)",  # ロリコン（「loricon」は人名 Lori Con… に誤爆するため r のみ）
    r"(?:sh|sy)otakon",  # ショタコン
)
# 英字の短い語（単語境界付き）。空白・記号を除いた本文に加え、空白・記号を区切りとして残した本文にも照合する
# （「JK ga suki」「I like JK」は空白を除くと jkgasuki / ilikejk になり、単語境界が取れないため）。
MINOR_WORD_PATTERNS: Final[tuple[str, ...]] = (
    r"(?<![a-z])(?:jk|jc)(?![a-z])",  # 「js」は JavaScript に誤爆するため登録しない
    r"(?<![a-z])kokosei(?![a-z])",  # 高校生
    r"(?<![a-z])(?:you|yoo|yo|yō|yô)(?:jo|jyo|zyo)(?![a-z])",  # 幼女（「you join」「youjou（養生）」は除外）
    r"(?<![a-z])enji(?![a-z])",  # 園児（「genjitsu」「genji」は除外）
    # 年齢（「17sai」「15 sai desu」）。相対年齢・期間（「3sai sa」「5sai kara」）は除外する
    r"(?<![0-9a-z])(?:1[0-7]|[1-9]) ?sai(?![a-z])"
    r"(?! ?(?:(?:kara|sa|ue|shita)(?![a-z])|chiga|toshiue|toshishita|hanare|[gk]urai ?chiga))"
    r"(?! ?(?:差|違|離れ|年上|年下|上|下|から|くらい違|ぐらい違))",
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
        terms += [Term(p, "ng_word", pattern=True) for p in NG_PATTERNS]
        terms += [Term(p, "ng_word", fold_kana=False, pattern=True) for p in NG_PATTERNS_UNFOLDED]
        terms += [Term(w, "minor") for w in MINOR_WORDS]
        terms += [Term(p, "minor", fold_kana=False, pattern=True) for p in MINOR_PATTERNS_UNFOLDED]
        terms += [Term(p, "minor", pattern=True) for p in MINOR_PATTERNS]
        terms += [Term(p, "minor", pattern=True) for p in MINOR_PATTERNS_ROMAJI]
        terms += [Term(p, "minor", pattern=True, word=True) for p in MINOR_WORD_PATTERNS]
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
_HIRAGANA_START: Final = 0x3041  # ぁ
_HIRAGANA_END: Final = 0x3096  # ゖ


def katakana_to_hiragana(text: str) -> str:
    return "".join(chr(ord(ch) - _KANA_OFFSET) if _KATAKANA_START <= ord(ch) <= _KATAKANA_END else ch for ch in text)


def hiragana_to_katakana(text: str) -> str:
    return "".join(chr(ord(ch) + _KANA_OFFSET) if _HIRAGANA_START <= ord(ch) <= _HIRAGANA_END else ch for ch in text)


# 照合前に除去する Unicode カテゴリ: 空白（Z*）・記号（P*, S*）・制御/書式文字（C*）・結合文字（M*）。
# 結合文字は NFKC で合成できなかったもの（「s̲h̲o̲u̲…」のように文字の間に挟んだ下線など）だけが残る。
# 長音「ー」・踊り字「ゝ」(Lm) は残す。
_NOISE_CATEGORIES: Final[frozenset[str]] = frozenset({"Z", "P", "S", "C", "M"})
# 単語境界用の本文で「区切り」にせず、単に取り除くカテゴリ（見えない書式文字・結合文字）
_INVISIBLE_CATEGORIES: Final[frozenset[str]] = frozenset({"Cf", "Mn", "Me", "Mc"})


def _strip_noise(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in _NOISE_CATEGORIES)


def _collapse_noise(text: str) -> str:
    """空白・記号・制御文字の連続を1つの空白にする（見えない書式文字・結合文字は取り除く）。"""
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category in _INVISIBLE_CATEGORIES:
            continue
        if category[0] in _NOISE_CATEGORIES:
            if out and out[-1] != " ":
                out.append(" ")
            continue
        out.append(ch)
    return "".join(out).strip()


def normalize(text: str, *, fold_kana: bool = True, keep_word_breaks: bool = False) -> str:
    """NFKC → 小文字化 → （fold_kana なら）カタカナをひらがなに → 空白・記号を除去。

    `keep_word_breaks=True` なら空白・記号を除去せずに1つの空白にまとめる（英字の単語境界を取る照合用）。
    """
    value = unicodedata.normalize("NFKC", text).lower()
    if fold_kana:
        value = katakana_to_hiragana(value)
    return _collapse_noise(value) if keep_word_breaks else _strip_noise(value)


# ---------------------------------------------------------------------------
# キャラ別 NG ワード（出力チェック）
# ---------------------------------------------------------------------------

_HIRA_CLASS: Final[str] = "ぁ-ゖゝゞ"
_KATA_CLASS: Final[str] = "ァ-ヺヽヾー"
_HIRAGANA_WORD: Final = re.compile(r"^[ぁ-ゖゝゞー]+$")
_KATAKANA_WORD: Final = re.compile(r"^[ァ-ヺヽヾー]+$")
_ASCII_WORD: Final = re.compile(r"^[a-z0-9]+$")
# ひらがな語の直前に来てもよい助詞 / 直後に来てもよい文末表現。
# 「そんなのうざいよ」「きもいね」は拾い、「ひとりのときもいいよね」「スーパーでぶどう」は拾わない。
_PERSONA_HIRA_BEFORE: Final[str] = "はがもをの"
_PERSONA_HIRA_AFTER: Final[str] = "よねなしわだ"


def _persona_word_pattern(word: str) -> re.Pattern[str] | None:
    """キャラ別 NG ワード1語の照合パターン（NFKC + 小文字化した本文に対して使う）。

    - かな語はひらがな・カタカナの両方の表記を照合する（「うざい」で「ウザイ」も拾う）。
      ただし前後が同じ文字種のかな（= 語の途中）の場合は一致としない
      （「ライブすごく」の「ブす」、「ラブストーリー」の「ブス」、「ときもいい」の「きもい」）。
    - 英数字の語は英数字の単語境界付き。
    - 漢字を含む語（「死ね」「刺す」など）はそのまま部分一致。
    """
    value = unicodedata.normalize("NFKC", word).lower().strip()
    if not value:
        return None
    if _HIRAGANA_WORD.match(value) or _KATAKANA_WORD.match(value):
        hira = re.escape(katakana_to_hiragana(value))
        kata = re.escape(hiragana_to_katakana(value))
        hira_pattern = (
            rf"(?:(?<![{_HIRA_CLASS}])|(?<=[{_PERSONA_HIRA_BEFORE}])){hira}"
            rf"(?:(?![{_HIRA_CLASS}])|(?=[{_PERSONA_HIRA_AFTER}]))"
        )
        kata_pattern = rf"(?<![{_KATA_CLASS}]){kata}(?![{_KATA_CLASS}])"
        return re.compile(f"{hira_pattern}|{kata_pattern}")
    if _ASCII_WORD.match(value):
        return re.compile(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])")
    return re.compile(re.escape(value))


@lru_cache(maxsize=256)
def _persona_patterns(words: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for word in words:
        pattern = _persona_word_pattern(word)
        if pattern is not None:
            compiled.append((word, pattern))
    return tuple(compiled)


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
    word: bool = False


class Moderator:
    def __init__(self, provider: TermProvider | None = None) -> None:
        self._provider = provider or StaticTermProvider()
        self._compiled = self._compile(self._provider.terms())

    @staticmethod
    def _compile(terms: Iterable[Term]) -> tuple[_CompiledTerm, ...]:
        compiled: list[_CompiledTerm] = []
        for term in terms:
            if term.pattern:
                compiled.append(
                    _CompiledTerm(term.text, term.category, term.fold_kana, None, re.compile(term.text), word=term.word)
                )
            else:
                literal = normalize(term.text, fold_kana=term.fold_kana)
                if literal:
                    compiled.append(_CompiledTerm(term.text, term.category, term.fold_kana, literal, None))
        return tuple(compiled)

    def check(self, text: str, *, extra_ng_words: Sequence[str] = (), block_links: bool = False) -> ModerationResult:
        """Gate #1。`block_links=True` なら URL・ドメイン名もヒット扱いにする（公開されるキャラの出力用）。"""
        folded = normalize(text, fold_kana=True)
        unfolded = normalize(text, fold_kana=False)
        categories: list[str] = []
        matched: list[str] = []

        def hit(category: str, term: str) -> None:
            if category not in categories:
                categories.append(category)
            if term not in matched:
                matched.append(term)

        # 英字の短い語（Term.word）用に、空白・記号を区切りとして残した本文（必要になったときだけ作る）
        spaced: dict[bool, str] = {}

        def word_target(fold_kana: bool) -> str:
            if fold_kana not in spaced:
                spaced[fold_kana] = normalize(text, fold_kana=fold_kana, keep_word_breaks=True)
            return spaced[fold_kana]

        for term in self._compiled:
            target = folded if term.fold_kana else unfolded
            if term.literal is not None and term.literal in target:
                hit(term.category, term.original)
            elif term.regex is not None:
                m = term.regex.search(target)
                if m is None and term.word:
                    m = term.regex.search(word_target(term.fold_kana))
                if m is not None:
                    hit(term.category, m.group(0))
        if extra_ng_words:
            # 語境界を保つため、記号除去・かな統一はしない（NFKC + 小文字化のみ）
            plain = unicodedata.normalize("NFKC", text).lower()
            for word, pattern in _persona_patterns(tuple(extra_ng_words)):
                if pattern.search(plain):
                    hit("persona_ng_word", word)
        if block_links and (link := _LINK_RE.search(unicodedata.normalize("NFKC", text))) is not None:
            hit("link", link.group(0))
        return ModerationResult(flagged=bool(matched), categories=categories, matched_terms=matched)
