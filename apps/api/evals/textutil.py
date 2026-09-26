"""判定（mock のキーワード照合）のための日本語テキストの正規化と内容語の抽出。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

_KATAKANA_START: Final[int] = ord("ァ")
_KATAKANA_END: Final[int] = ord("ヶ")
_KANA_OFFSET: Final[int] = ord("ァ") - ord("ぁ")
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[。！？!?\n])")
# 内容語: 漢字の連続・カタカナの連続（長音を含む）・英数字の連続
_CONTENT_RUN: Final = re.compile(r"[一-鿿々]+|[ァ-ヺー]{2,}|[A-Za-z0-9]{2,}")
# 活動の語としては意味の薄い語（時間表現・一般語）
STOP_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "今日",
        "明日",
        "昨日",
        "今週",
        "来週",
        "先週",
        "今度",
        "最近",
        "時間",
        "自分",
        "一日",
        "毎日",
        "感じ",
        "本当",
        "大丈夫",
        "ゆっくり",
        "ちょっと",
        "中",
        "今",
        "日",
        "時",
        "私",
        "何",
    }
)
# 否定・不確かさ（「聞いてない」「わからない」「教えて」）
UNCERTAIN_RE: Final = re.compile(
    r"(知らな|しらな|聞いてな|きいてな|聞いたことな|聞いた事な|教えて|おしえて|わからな|分からな|わかんな|覚えてな|"
    r"おぼえてな|まだ聞|初耳|はつみみ|ないよ|ないかも|なかった|いないよ|いなかった|いないでしょ|じゃないかな|"
    r"ごめん|思い出せな|出てこな|知りたい|なんだっけ|なんだろう|どうだっけ)"
)
AFFIRM_RE: Final = re.compile(r"(楽しかった|よかった|良かった|行ってきた|行った|最高だった|満喫|すごかった|いい思い出)")
NEGATE_RE: Final = re.compile(
    r"(行ってない|行ってないよ|してない|なかった|ないよ|行けなかった|違う|ちがう|行く予定|まだ)"
)
# 覚えている・知っているという断定（「〜って言ってたよね」「〜だったよね」「〜でしょ」）
RECALL_CLAIM_RE: Final = re.compile(
    r"(言ってた|いってた|話してくれ|覚えてるよ|覚えてる。|ちゃんと覚えて|だったよね|だよね|でしょ|だったね|って名前|"
    r"っていう名前|って子|ちゃん|くん|歳だった|歳だよ|生まれ|\d+月\d+日|\d+歳)"
)
BUSY_RE: Final = re.compile(r"(バタバタ|忙し|手短|短め|あとで|後で|落ち着いたら|今は手が|ちょっとだけ)")


def fold_kana(text: str) -> str:
    return "".join(chr(ord(ch) - _KANA_OFFSET) if _KATAKANA_START <= ord(ch) <= _KATAKANA_END else ch for ch in text)


def normalize(text: str) -> str:
    """NFKC・小文字化・空白の除去（キーワードの照合用。カタカナは残す）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def contains_any(text: str, terms: Iterable[str]) -> list[str]:
    """正規化した text に含まれる terms（正規化して照合）。カタカナ・ひらがなの違いは同一視する。"""
    base = fold_kana(normalize(text))
    return [t for t in terms if t and fold_kana(normalize(t)) in base]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def is_question(sentence: str) -> bool:
    return bool(re.search(r"[？?]\s*$", sentence)) or sentence.rstrip().endswith(("かな", "っけ"))


def content_tokens(text: str) -> set[str]:
    """内容語とその 2 文字の断片（「居酒屋」→「居酒屋」「居酒」「酒屋」）。1 文字・時間表現は除く。"""
    tokens: set[str] = set()
    for run in _CONTENT_RUN.findall(unicodedata.normalize("NFKC", text)):
        if len(run) < 2 or run in STOP_TOKENS:
            continue
        tokens.add(run)
        if len(run) > 2:
            tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    return {t for t in tokens if t not in STOP_TOKENS}


def overlap(a: Iterable[str], b: Iterable[str]) -> set[str]:
    return set(a) & set(b)
