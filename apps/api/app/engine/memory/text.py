"""メモリエンジンの文字列処理（正規化・ハッシュ・文分割・会話ログの描画）。

LLM クライアント（app/services/llm.py）の内部ヘルパーには依存しない（モジュール間の結合を増やさないため）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Final

from app.engine.types import JST
from app.services.moderation import Moderator
from app.services.types import HistoryItem

# Gate #1（入力）で差し止めた発言を LLM に渡すときの置き換え文
MODERATED_PLACEHOLDER: Final[str] = "（不適切な発言のため省略）"

# 会話ログ（要約・分析）の描画の上限（トークン節約, ADR-0028）
TRANSCRIPT_MESSAGE_MAX_CHARS: Final[int] = 300
TRANSCRIPT_MAX_CHARS: Final[int] = 12000

WEEKDAYS_JA: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")

_LINE_BREAKS_RE: Final = re.compile(r"[\r\n\v\f\x85  ]+")
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[。！？!?\n])")
QUESTION_END: Final = re.compile(r"[？?]\s*$")
# 文末から落とすもの（strip_trailing で繰り返し適用する）
_TRAILING_RULES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"[\s。、．，,.！？!?…‥〜~～♪☆★・]+$"),
    re.compile(r"[（(]笑[）)]$"),
    re.compile(r"(?<![一-鿿々])笑+$"),
    re.compile(r"(?<![A-Za-z])[wｗ]+$"),
    re.compile(r"(?<![ァ-ヺー])ー+$"),
    re.compile(r"(?<=[ァ-ヺ]ー)ー+$"),
)
# 文末表現（「面接なんだよね」→「面接」）。語の一部を削らないよう、長いものから順に1回ずつ落とす
_ENDINGS: Final[tuple[str, ...]] = (
    "なんだけどね",
    "なんだけど",
    "なんだよね",
    "なんだよ",
    "なんだ",
    "なのよね",
    "なのよ",
    "なのね",
    "なの",
    "んだけどね",
    "んだけど",
    "んだよね",
    "んだよ",
    "んだね",
    "んだ",
    "だよね",
    "だよ",
    "だね",
    "だわ",
    "のよ",
    "よね",
    "かな",
    "かも",
    "よ",
    "ね",
    "の",
)
_LEADING_JUNK: Final = re.compile(r"^[\s、。,.!！?？・…〜~]+|^(?:は|が|に|で|を|も|と|って)[、,\s]*")
_SPACES: Final = re.compile(r"\s+")


def one_line(text: str) -> str:
    """改行の類を空白にして1行にまとめる（利用者由来の文章をプロンプトの1行に入れる）。"""
    return _SPACES.sub(" ", _LINE_BREAKS_RE.sub(" ", text)).strip()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def strip_trailing(text: str) -> str:
    """文末の句読点・記号・笑い（w / 笑）・伸ばし棒を落とす。カタカナ語の長音（「サッカー」）は残す。"""
    previous: str | None = None
    value = text.strip()
    while value != previous:
        previous = value
        for rule in _TRAILING_RULES:
            value = rule.sub("", value)
    return value


def strip_endings(text: str) -> str:
    """文末表現（〜なんだよね / 〜の / 〜よ）を落とす。落とした結果が空になる場合は元のまま。"""
    value = strip_trailing(text)
    for _ in range(3):
        for ending in _ENDINGS:
            if value.endswith(ending) and len(value) > len(ending) + 1:
                value = strip_trailing(value[: -len(ending)])
                break
        else:
            break
    return value


def strip_leading(text: str) -> str:
    """文頭の句読点・助詞（「、面接」「は面接」→「面接」）を落とす。"""
    previous: str | None = None
    value = text.strip()
    while value != previous:
        previous = value
        value = _LEADING_JUNK.sub("", value).strip()
    return value


def normalize_for_hash(content: str) -> str:
    """墓標・重複判定用の正規化（NFKC・小文字・空白/句読点/記号/制御文字の除去）。"""
    value = unicodedata.normalize("NFKC", content).lower()
    return "".join(ch for ch in value if unicodedata.category(ch)[0] not in {"Z", "P", "S", "C"})


def content_hash(content: str) -> str:
    """正規化した本文の SHA-256（memory_tombstones.content_hash）。"""
    return hashlib.sha256(normalize_for_hash(content).encode("utf-8")).hexdigest()


def _bigrams(text: str) -> set[str]:
    value = normalize_for_hash(text)
    if len(value) < 2:
        return {value} if value else set()
    return {value[i : i + 2] for i in range(len(value) - 1)}


def bigram_jaccard(a: str, b: str) -> float:
    """文字 bigram の Jaccard 係数（約束の重複判定など、埋め込みを使わない軽い比較）。"""
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def format_date_ja(value: date, *, today: date | None = None) -> str:
    """「10月1日（木）」。年が今日と違えば「2027年1月3日（日）」。"""
    head = f"{value.month}月{value.day}日（{WEEKDAYS_JA[value.weekday()]}）"
    if today is not None and value.year != today.year:
        return f"{value.year}年{head}"
    return head


def format_datetime_ja(value: datetime) -> str:
    local = value.astimezone(JST)
    return f"{local.year}年{local.month}月{local.day}日（{WEEKDAYS_JA[local.weekday()]}）{local:%H:%M}"


# 分析しなくてよいターン（相づち・あいさつだけ）。キャラの返答に過去の出来事の話（キャラ側の記憶, M8）があれば分析する
_PHATIC_RE: Final = re.compile(
    r"^(?:うん+|うむ|ううん|はい|いいえ|そう(?:だね|なんだ|か|かも)?|そっか|へえ+|へー+|ほう|なるほど|たしかに|確かに|"
    r"それな|わかる|分かる|了解|りょ|おけ|ok|okay|いいね|すごい|すご|まじ|マジ|ほんと|本当|だよね|ね|えー+|あー+|"
    r"おはよう?(?:ございます)?|おやすみ(?:なさい)?|こんにちは|こんばんは|ただいま|おかえり|ありがと(?:う)?|"
    r"おつかれ(?:さま)?|お疲れ(?:様)?|またね|じゃあね|ばいばい|笑|w+|草)+$"
)
_TRIVIAL_MAX_CHARS: Final[int] = 12
_REPLY_PAST_CUE_RE: Final = re.compile(r"(昨日|きのう|一昨日|おととい|先週|この前|こないだ|さっき|今朝|週末|先月)")


def is_trivial_turn(user_text: str, reply_text: str) -> bool:
    """相づち・あいさつだけのターンか（記憶の分析を省く。キャラが過去の出来事を話した返答は省かない）。"""
    core = normalize_for_hash(user_text)
    if len(core) > _TRIVIAL_MAX_CHARS:
        return False
    if core and _PHATIC_RE.match(core) is None:
        return False
    return _REPLY_PAST_CUE_RE.search(reply_text) is None


_PROMISE_TERM_RUN: Final = re.compile(r"[\u4e00-\u9fff\u3005]{2,}|[\u30a1-\u30fa\u30fc]{2,}|[A-Za-z0-9]{2,}")
_PROMISE_TERM_STOP: Final[frozenset[str]] = frozenset({"一緒", "約束", "予定", "今度", "来週", "明日", "週末"})


def promise_terms(content: str) -> list[str]:
    """約束の本文の内容語（「一緒に映画見に行こう」→「映画見」「映画」「画見」）。一般的な語は除く。

    返答や発言が約束に触れたかどうかの判定（mentioned / done / cancelled）に使う。
    """
    terms: list[str] = []
    for run in _PROMISE_TERM_RUN.findall(content):
        parts = [run] + ([run[i : i + 2] for i in range(len(run) - 1)] if len(run) >= 3 else [])
        terms.extend(p for p in parts if p not in _PROMISE_TERM_STOP and p not in terms)
    if not terms and len(content) >= 2:
        terms = [content]
    return terms


def mentions_promise(text: str, content: str) -> bool:
    terms = promise_terms(content)
    return any(term in text for term in terms)


# 呼び方の記憶（「ユーザーは「たっくん」と呼ばれたい」）から呼び名を取り出す
_CALL_NAME_RE: Final = re.compile(r"「([^「」]{1,20})」と呼(?:ばれたい|んでほしい|んで欲しい)")


def extract_call_name(contents: Iterable[str]) -> str | None:
    """関係性の記憶の本文から、ユーザーの呼ばれたい名前を取り出す（新しいものを先に渡す）。

    Context Assembler が好感度の指針の `{name}` を埋めるのに使える。見つからなければ None。
    """
    for content in contents:
        match = _CALL_NAME_RE.search(content)
        if match:
            return match.group(1).strip()
    return None


def sanitize_history(items: Sequence[HistoryItem], moderator: Moderator, *, drop: bool = False) -> list[HistoryItem]:
    """Gate #1（入力）で差し止めたユーザー発言の本文を、LLM に渡す履歴から取り除く。

    差し止めた発言も messages には保存されるが、保存時の判定結果は列として持たない。
    Gate #1 は正規化テキストへの決定的な照合なので、保存済みの本文にもう一度かければ同じ判定になる。
    - drop=False: 本文をプレースホルダに置き換える（直後の定型返答は残し、会話の流れは保つ）
    - drop=True: その発言と直後のキャラ発言（定型返答）を取り除く（要約など、記憶として残る用途）
    """
    sanitized: list[HistoryItem] = []
    skip_reply = False
    for item in items:
        if skip_reply:
            skip_reply = False
            if item.sender_type == "character":
                continue
        if item.sender_type == "user" and moderator.check(item.body).flagged:
            if drop:
                skip_reply = True
                continue
            item = replace(item, body=MODERATED_PLACEHOLDER)  # noqa: PLW2901
        sanitized.append(item)
    return sanitized


def transcript_line(item: HistoryItem, character_name: str, per_message_max: int = TRANSCRIPT_MESSAGE_MAX_CHARS) -> str:
    speaker = "ユーザー" if item.sender_type == "user" else character_name
    body = one_line(item.body)
    if len(body) > per_message_max:
        body = body[:per_message_max] + "…"
    return f"{speaker}: {body}"


def fit_transcript_prefix(
    history: Sequence[HistoryItem],
    character_name: str,
    *,
    per_message_max: int = TRANSCRIPT_MESSAGE_MAX_CHARS,
    total_max: int = TRANSCRIPT_MAX_CHARS,
) -> int:
    """古い順の history を先頭から描画したとき、total_max に収まる件数（中期要約のチャンク分け用）。"""
    total = 0
    for index, item in enumerate(history):
        total += len(transcript_line(item, character_name, per_message_max)) + 1
        if total > total_max:
            return index
    return len(history)


def render_transcript(
    history: Sequence[HistoryItem],
    character_name: str,
    *,
    per_message_max: int = TRANSCRIPT_MESSAGE_MAX_CHARS,
    total_max: int = TRANSCRIPT_MAX_CHARS,
) -> str:
    """会話ログ（古い順）。上限を超える場合は新しい側を優先して残す。"""
    lines = [transcript_line(item, character_name, per_message_max) for item in history]
    total = 0
    kept: list[str] = []
    for line in reversed(lines):
        total += len(line) + 1
        if total > total_max:
            break
        kept.append(line)
    kept.reverse()
    return "\n".join(kept) if kept else "（なし）"
