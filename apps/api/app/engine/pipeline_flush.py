"""ストリーミング返答の文単位のフラッシュと出力検査（ENGINE_BRIEF §2.4）。

LLM の断片をそのまま流すと、Gate #1・キャラの NG ワード・OutputGuard（E2 / E3）に引っかかる語が
画面に出てしまう。そこで:
  - 断片はバッファにため、**文の区切り**（。！？!?\\n…）まで来たら、その手前までを1つの delta として出す。
    区切りが来ないまま約 40 文字たまったら、読点・空白で区切って出す（その場合も末尾 12 文字は手元に残す）。
  - 出す前に、**それまでに受け取った全文**（出した分 + バッファ）を検査する。引っかかったら以後は何も出さず
    （held）、LLM の返答を最後まで受け取ってから、完成した全文でもう一度判定する（途中までの本文だけで判定すると
    語の境界の条件で誤検知することがあるため。最終判定は Gate #1 と同じく全文に対して行う）。
    最終判定でも引っかかれば、呼び出し側が `replace`（キャラの定型の断り文）を送る。
  - 返答の先頭の「名前:」と、全体を囲むかぎかっこ・引用符は取り除く（clean_reply と同じ整形。出した delta を
    後から書き換えないよう、先頭は判定できるまで待ち、末尾の閉じかっこは最後まで手元に残す）。
  保存する本文 = 送った delta の連結（置き換えた場合は断り文）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.engine.types import OutputGuard
from app.services.moderation import Moderator

SENTENCE_BOUNDARY: Final[str] = "。！？!?\n…"
SOFT_BOUNDARY: Final[str] = "、，,　 ♪〜～"
SOFT_FLUSH_CHARS: Final[int] = 40
HOLD_CHARS: Final[int] = 12
REPLY_MAX_CHARS: Final[int] = 2000
_OPENING_QUOTES: Final[dict[str, str]] = {"「": "」", '"': '"', "'": "'", "『": "』"}


@dataclass(frozen=True, slots=True)
class OutputFlag:
    categories: tuple[str, ...]
    matched: tuple[str, ...]


class OutputChecker:
    """返答の検査: Gate #1（キャラの NG ワードを含む）+ OutputGuard（E2 commerce_coupling / E3 human_claim）。"""

    def __init__(self, moderator: Moderator, guard: OutputGuard | None, *, ng_words: Sequence[str] = ()) -> None:
        self._moderator = moderator
        self._guard = guard
        self._ng_words = tuple(ng_words)

    def check(self, text: str) -> OutputFlag | None:
        categories: list[str] = []
        matched: list[str] = []
        moderation = self._moderator.check(text, extra_ng_words=self._ng_words)
        if moderation.flagged:
            categories.extend(moderation.categories)
            matched.extend(moderation.matched_terms)
        if self._guard is not None:
            guard = self._guard.check(text)
            if guard.flagged:
                categories.extend(c for c in guard.categories if c not in categories)
                matched.extend(m for m in guard.matched if m not in matched)
        if not categories:
            return None
        return OutputFlag(categories=tuple(categories), matched=tuple(matched))


@dataclass(slots=True)
class FlushResult:
    """finish() の結果。flag があれば返答全体を差し替える。deltas は最後に送る残り。"""

    text: str  # 保存する本文（= 送った delta の連結）。flag があれば LLM の全文（監査用）
    deltas: list[str] = field(default_factory=list)
    flag: OutputFlag | None = None


class StreamFlusher:
    def __init__(
        self,
        checker: OutputChecker,
        *,
        persona_name: str,
        soft_flush_chars: int = SOFT_FLUSH_CHARS,
        hold_chars: int = HOLD_CHARS,
        max_chars: int = REPLY_MAX_CHARS,
    ) -> None:
        self._checker = checker
        self._name_prefix = re.compile(rf"^{re.escape(persona_name)}\s*[:：]\s*")
        self._name = persona_name
        self._soft = soft_flush_chars
        self._hold = hold_chars
        self._max = max_chars
        self._raw = ""  # 受け取った全文（整形前）
        self._started = False  # 先頭の整形（名前・かぎかっこ）を終えた
        self._closing_quote: str | None = None
        self._emitted = ""  # 送った本文
        self._buffer = ""  # まだ送っていない本文（整形後）
        self.held = False  # 検査に引っかかったので以後は送らない（最終判定は finish）
        self.flag_during_stream: OutputFlag | None = None

    @property
    def emitted(self) -> str:
        return self._emitted

    @property
    def received(self) -> str:
        return self._raw

    def feed(self, chunk: str) -> list[str]:
        """断片を受け取り、今送ってよい delta（0〜1件）を返す。"""
        self._raw += chunk
        if not self._started:
            if not self._start():
                return []
        else:
            self._buffer += chunk
        if self.held:
            return []
        cut = self._flush_point()
        if cut <= 0:
            return []
        flag = self._checker.check(self._emitted + self._buffer)
        if flag is not None:
            self.held = True
            self.flag_during_stream = flag
            return []
        piece = self._buffer[:cut]
        room = self._max - len(self._emitted)
        if room <= 0:
            return []
        piece = piece[:room]
        self._emitted += piece
        self._buffer = self._buffer[cut:]
        return [piece]

    def finish(self) -> FlushResult:
        """LLM の返答が終わった。全文で最終判定し、残りの delta を返す。"""
        if not self._started:
            # 名前の接頭辞かどうか判定できないほど短かった → そのまま本文として扱う
            self._started = True
            self._buffer = self._raw.lstrip()
        tail = self._buffer.rstrip()
        if self._closing_quote is not None and tail.endswith(self._closing_quote):
            tail = tail[: -len(self._closing_quote)].rstrip()
        full = self._emitted + tail
        if len(full) > self._max:
            tail = tail[: max(self._max - len(self._emitted) - 1, 0)] + "…"
            full = self._emitted + tail
        flag = self._checker.check(full) if full.strip() else None
        if flag is not None:
            return FlushResult(text=full, flag=flag)
        self._emitted = full
        self._buffer = ""
        return FlushResult(text=full, deltas=[tail] if tail else [])

    # ------------------------------------------------------------------
    def _start(self) -> bool:
        """先頭の「名前:」「かぎかっこ」を判定できたら取り除いてバッファに入れる。まだ判定できなければ False。"""
        text = self._raw.lstrip()
        if not text:
            return False
        prefix = self._name_prefix.match(text)
        if prefix is not None:
            text = text[prefix.end() :]
        elif self._could_be_name_prefix(text):
            return False
        text = text.lstrip()
        if not text:
            return False
        closing = _OPENING_QUOTES.get(text[0])
        if closing is not None:
            self._closing_quote = closing
            text = text[1:]
        self._started = True
        self._buffer = text
        return True

    def _could_be_name_prefix(self, text: str) -> bool:
        """text が「名前:」の途中かもしれない（名前 + 空白までしか来ていない）。"""
        head = self._name
        if len(text) <= len(head):
            return head.startswith(text)
        return text.startswith(head) and text[len(head) :].strip() == ""

    def _flush_point(self) -> int:
        """バッファの先頭から何文字を送れるか（0 = まだ送らない）。"""
        buffer = self._buffer
        # 文の区切り（最後に現れた区切りまで。閉じかっこ・連続する記号は区切りに含める）
        last = max(buffer.rfind(ch) for ch in SENTENCE_BOUNDARY)
        if last >= 0:
            end = last + 1
            while end < len(buffer) and buffer[end] in SENTENCE_BOUNDARY + "」』)）":
                end += 1
            # 閉じかっこが全体の終わりかもしれない場合（先頭のかぎかっこを外した）は残しておく
            if self._closing_quote is not None and buffer[:end].endswith(self._closing_quote):
                end -= len(self._closing_quote)
            return end
        if len(buffer) < self._soft:
            return 0
        limit = len(buffer) - self._hold
        if limit <= 0:
            return 0
        soft = max(buffer.rfind(ch, 0, limit) for ch in SOFT_BOUNDARY)
        if soft >= 0:
            return soft + 1
        if len(buffer) >= self._soft * 2:
            return limit
        return 0
