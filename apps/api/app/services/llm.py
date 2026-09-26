"""LLM クライアント（OpenAI 互換 Chat Completions / モック）。

- `LLM_MODE=live` : `OpenAICompatibleLLM`。OpenRouter 経由の DeepSeek-V3 を想定。`LLM_BASE_URL` を
  `https://api.deepseek.com/v1` にすれば DeepSeek 直契約でも動く。429 / 5xx / タイムアウトは
  指数バックオフでリトライ（`LLM_MAX_RETRIES`）。
- `LLM_MODE=mock` : `MockLLM`。ネットワークを使わず、ペルソナ（口調例・一人称/二人称・予定）と
  検索された記憶から決定的な応答を作る。記憶抽出はキーワードによるルールベース。
  出力形式（抽出/要約の JSON）は live と同じなので、パース処理は共通で検証される。

呼び出し側は描画済みのプロンプト（messages）と、モック用の構造化ヒント（hints）を渡す。
live は hints を無視する。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Protocol

import httpx

from app.core.config import Settings
from app.core.http import upstream_timeout
from app.core.logging import get_logger
from app.services.persona import Persona
from app.services.prompt import JST, ChatMessage
from app.services.types import HistoryItem, RetrievedMemory

logger = get_logger("llm")

# 用途（監査ログ・コスト集計・モデルの使い分けのキー）。キャラクターエンジンの各モジュールが用途を追加する:
#   chat / memory_extraction / memory_summary / comment_reply（MVP）
#   memory_analysis / affinity_eval / proactive_message / feed_caption / sim_user / eval_judge（エンジン v1.0）
Purpose = str


@dataclass(frozen=True, slots=True)
class MockHints:
    """MockLLM 用の構造化コンテキスト（live では使わない）。"""

    persona: Persona
    now: datetime
    user_message: str = ""
    memories: tuple[RetrievedMemory, ...] = ()
    history: tuple[HistoryItem, ...] = ()
    post_caption: str | None = None
    comment_body: str | None = None


@dataclass(frozen=True, slots=True)
class LLMRequest:
    purpose: Purpose
    messages: list[ChatMessage]
    temperature: float
    max_tokens: int
    json_mode: bool = False
    hints: MockHints | None = None
    # 用途ごとのモデルの上書き（None = LLM_MODEL）。例: 分析系は安いモデルに分ける
    model: str | None = None
    # MockLLM の用途別ハンドラに渡す構造化データ（live では使わない）
    mock_context: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    latency_ms: int
    usage: dict[str, int] | None = None


class LLMError(Exception):
    """LLM 呼び出しの失敗（リトライ後）。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = attempts
        super().__init__(message)


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    async def complete(self, request: LLMRequest) -> LLMResult: ...


# ===========================================================================
# live: OpenAI 互換 Chat Completions
# ===========================================================================

MAX_RETRY_AFTER_SECONDS: Final[float] = 10.0


class OpenAICompatibleLLM:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        backoff_base_seconds: float = 0.5,
    ) -> None:
        if settings.llm_api_key is None:
            raise ValueError("LLM_API_KEY is required for live LLM")
        self._url = f"{settings.llm_base_url}/chat/completions"
        self._api_key = settings.llm_api_key.get_secret_value()
        self._model = settings.llm_model
        # 接続待ち（pool）は短く、応答の読み取りは LLM_TIMEOUT_SECONDS（app/core/http.py）
        self._timeout = upstream_timeout(settings.llm_timeout_seconds)
        self._max_retries = settings.llm_max_retries
        self._backoff = backoff_base_seconds
        self._http = http
        self._headers: dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        # OpenRouter のアプリ識別ヘッダ（任意）
        if "openrouter.ai" in settings.llm_base_url:
            if settings.llm_http_referer:
                self._headers["HTTP-Referer"] = settings.llm_http_referer
            if settings.llm_app_title:
                self._headers["X-Title"] = settings.llm_app_title

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(self, request: LLMRequest) -> LLMResult:
        body: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        attempt = 0
        while True:
            retry_after: float | None = None
            try:
                response = await self._http.post(self._url, json=body, headers=self._headers, timeout=self._timeout)
            except httpx.TimeoutException as exc:
                error = LLMError(f"timeout: {exc!r}", retryable=True)
            except httpx.TransportError as exc:
                error = LLMError(f"transport error: {exc!r}", retryable=True)
            else:
                if response.status_code == 200:
                    return self._parse(response, started)
                if response.status_code == 400 and "response_format" in body:
                    # JSON モード非対応のモデル/プロバイダ → response_format なしで再試行
                    logger.warning(
                        "LLM rejected response_format; retrying without it",
                        extra={"fields": {"purpose": request.purpose, "body": response.text[:300]}},
                    )
                    body.pop("response_format")
                    continue
                retryable = response.status_code == 429 or response.status_code >= 500
                error = LLMError(
                    f"HTTP {response.status_code}: {response.text[:300]}",
                    status_code=response.status_code,
                    retryable=retryable,
                )
                retry_after = _parse_retry_after(response.headers.get("retry-after"))
            error.attempts = attempt + 1
            if not error.retryable or attempt >= self._max_retries:
                logger.error(
                    "LLM request failed",
                    extra={
                        "fields": {
                            "purpose": request.purpose,
                            "error": str(error),
                            "status_code": error.status_code,
                            "attempts": error.attempts,
                            "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        }
                    },
                )
                raise error
            delay = self._backoff * (2**attempt) + random.uniform(0, self._backoff)
            if retry_after is not None:
                delay = min(max(delay, retry_after), MAX_RETRY_AFTER_SECONDS)
            logger.warning(
                "LLM request failed; retrying",
                extra={"fields": {"purpose": request.purpose, "error": str(error), "delay_s": round(delay, 2)}},
            )
            attempt += 1
            await asyncio.sleep(delay)

    def _parse(self, response: httpx.Response, started: float) -> LLMResult:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"invalid response: {exc!r}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("empty completion")
        usage_raw = data.get("usage")
        usage: dict[str, int] | None = None
        if isinstance(usage_raw, dict):
            usage = {k: int(v) for k, v in usage_raw.items() if isinstance(v, int | float)}
        model = data.get("model")
        return LLMResult(
            text=content.strip(),
            model=model if isinstance(model, str) else self._model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
        )


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


# ===========================================================================
# mock: 決定的なペルソナ応答
# ===========================================================================

MOCK_MODEL_NAME: Final[str] = "mock-persona-v1"

# --- 記憶抽出のキーワード（§9.2 の観点） -------------------------------------------
EMOTION_WORDS: Final[tuple[str, ...]] = (
    "悲し",
    "かなし",
    "辛い",
    "つらい",
    "嬉し",
    "うれし",
    "寂し",
    "さみし",
    "さびし",
    "不安",
    "疲れ",
    "つかれ",
    "しんどい",
    "ありがとう",
    "感謝",
    "怒",
    "泣",
    "落ち込",
    "悩",
    "緊張",
    "楽しみ",
    "心配",
    "怖",
    "幸せ",
    "最悪",
    "喧嘩",
    "けんか",
    "イライラ",
)
PERSONAL_WORDS: Final[tuple[str, ...]] = (
    "仕事",
    "会社",
    "職場",
    "上司",
    "同僚",
    "出張",
    "残業",
    "転職",
    "バイト",
    "家族",
    "母",
    "父",
    "姉",
    "兄",
    "妹",
    "弟",
    "実家",
    "健康",
    "病院",
    "入院",
    "風邪",
    "体調",
    "眠れ",
    "寝不足",
    "睡眠",
    "頭痛",
    "日課",
    "毎日",
    "毎朝",
    "毎晩",
    "好き",
    "趣味",
    "名前",
    "住んで",
    "住み",
    "出身",
    "誕生日",
    "ペット",
    "飼って",
    "引っ越",
)
PROMISE_WORDS: Final[tuple[str, ...]] = (
    "来週",
    "明日",
    "あした",
    "今度",
    "週末",
    "来月",
    "来年",
    "絶対",
    "一緒に",
    "約束",
    "予定",
)
RELATIONSHIP_WORDS: Final[tuple[str, ...]] = (
    "呼んで",
    "好き",
    "大好き",
    "愛して",
    "付き合",
    "会いたい",
    "特別",
)

_CATEGORY_WEIGHTS: Final[dict[str, tuple[tuple[str, ...], float]]] = {
    "personal": (PERSONAL_WORDS, 0.35),
    "promise": (PROMISE_WORDS, 0.35),
    "relationship": (RELATIONSHIP_WORDS, 0.35),
    "emotion": (EMOTION_WORDS, 0.25),
}
_BASE_IMPORTANCE: Final[float] = 0.3
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[。！？!?\n])")
_QUESTION_END: Final = re.compile(r"[？?]\s*$")
# 文末から落とすもの（strip_trailing で繰り返し適用する）。語の一部になりうる文字は条件付きで落とす
_TRAILING_RULES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"[\s。、．，,.！？!?…‥〜~～♪☆★・]+$"),
    re.compile(r"[（(]笑[）)]$"),
    re.compile(r"(?<![\u4e00-\u9fff\u3005])笑+$"),  # 「楽しかった笑」の笑（「爆笑」「苦笑」は残す）
    re.compile(r"(?<![A-Za-z])[wｗ]+$"),  # 「楽しかったw」の w（英単語の末尾は残す）
    re.compile(r"(?<![\u30a1-\u30fa\u30fc])ー+$"),  # かな・漢字の後の伸ばし（「ねー」「楽しかったー」）
    re.compile(r"(?<=[\u30a1-\u30fa]ー)ー+$"),  # カタカナ語の後の余分な伸ばし（「サッカーーー」→「サッカー」）
)
# 記憶の引用から落とす文末表現（語の一部を削らないよう、単独の「な/の/わ/さ」は落とさない）
_SENTENCE_ENDINGS: Final = re.compile(
    r"(?:なんだよね|なんだよ|なんだ|なのよね|なのよ|なのね|なの|んだよね|んだよ|んだ|だよね|だよ|だわ|のよ|よね|(?<!さ)かな|よ|ね)$"
)
_QUOTED: Final = re.compile(r"「(.+)」")
# 内容語の連続（漢字 / カタカナ / 英数字をそれぞれ別の語として扱う: 「ラーメン食べた」→「ラーメン」「食」）
_CONTENT_RUN: Final = re.compile(r"[\u4e00-\u9fff\u3005]+|[\u30a1-\u30fa\u30fc]+|[A-Za-z0-9]+")
_KANJI: Final = re.compile(r"[\u4e00-\u9fff]")
# 話題として拾わない語（時間表現など）
_TOPIC_STOP: Final[frozenset[str]] = frozenset(
    {"今日", "明日", "昨日", "来週", "今週", "先週", "今度", "週末", "最近", "今年", "来年", "毎日", "ユーザー"}
)
_TOPIC_STOP_RE: Final = re.compile("|".join(re.escape(w) for w in sorted(_TOPIC_STOP, key=len, reverse=True)))
# 記録日を添えて思い出すべき相対的な時間表現（「来週」と言われたのが何日も前なら日付を付けて話す）
_RELATIVE_TIME_WORDS: Final[tuple[str, ...]] = (
    "来週",
    "今週",
    "明日",
    "あした",
    "明後日",
    "今度",
    "週末",
    "来月",
    "今日",
    "今夜",
    "今朝",
    "昨日",
    "さっき",
)
_SINGLE_KANJI_STOP: Final[frozenset[str]] = frozenset(
    "今何日時人事方気前後中上下私僕俺君手目年月分回度本思言行来見話出入会食寝家円的大小多少良悪"
)
_NEGATIVE_WORDS: Final[tuple[str, ...]] = (
    "疲れ",
    "つかれ",
    "つらい",
    "辛い",
    "しんどい",
    "悲し",
    "寂し",
    "さみし",
    "不安",
    "落ち込",
    "最悪",
    "眠れ",
)
_POSITIVE_WORDS: Final[tuple[str, ...]] = ("嬉し", "うれし", "楽し", "よかった", "最高", "やった", "幸せ")


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _pick[T](options: Sequence[T], seed: int, salt: int = 0) -> T:
    return options[(seed + salt * 7919) % len(options)]


def strip_trailing(text: str) -> str:
    """文末の句読点・記号・笑い（w / 笑）・伸ばし棒を落とす。カタカナ語の長音（「サッカー」）は残す。"""
    previous: str | None = None
    value = text
    while value != previous:
        previous = value
        for rule in _TRAILING_RULES:
            value = rule.sub("", value)
    return value


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def score_sentence(sentence: str) -> tuple[float, str | None]:
    """キーワードの観点ごとに加点した重要度と、最初に該当した観点を返す。"""
    score = _BASE_IMPORTANCE
    first: str | None = None
    for category, (words, weight) in _CATEGORY_WEIGHTS.items():
        if any(w in sentence for w in words):
            score += weight
            first = first or category
    return min(round(score, 2), 0.95), first


def mock_extract(user_message: str) -> list[dict[str, Any]]:
    """ルールベースの記憶抽出（質問文は除外）。"""
    results: list[dict[str, Any]] = []
    for sentence in split_sentences(user_message):
        if _QUESTION_END.search(sentence):
            continue
        core = strip_trailing(sentence).strip()
        if len(core) < 4:
            continue
        importance, category = score_sentence(core)
        if category is None:
            continue
        core = core[:100]
        results.append({"content": f"ユーザーは「{core}」と話していた", "importance": importance, "category": category})
    return results


def memory_core(content: str) -> str:
    """記憶の本文から「言ってたこと」部分を取り出し、文末表現を落とす。"""
    quoted = _QUOTED.search(content)
    core = quoted.group(1) if quoted else content
    core = re.sub(r"^ユーザー(?:は|が|の)", "", core)
    core = strip_trailing(core)
    core = _SENTENCE_ENDINGS.sub("", core)
    return strip_trailing(core).strip()


def content_tokens(text: str) -> set[str]:
    """内容語（漢字・カタカナ・英数字の連続）とその2文字断片。時間表現（今日・来週…）は含めない。

    「今日は雨だったね」と「今日も仕事で疲れたよ」が「今日」だけで結び付かないようにする。
    """
    tokens: set[str] = set()
    for run in _CONTENT_RUN.findall(text):
        for part in _TOPIC_STOP_RE.split(run):
            if not part:
                continue
            if len(part) == 1:
                if part not in _SINGLE_KANJI_STOP and _KANJI.match(part):
                    tokens.add(part)
                continue
            tokens.add(part)
            tokens.update(part[i : i + 2] for i in range(len(part) - 1))
    return tokens


def pick_relevant_memory(user_message: str, memories: Sequence[RetrievedMemory]) -> RetrievedMemory | None:
    """ユーザー発言と内容語が重なる記憶のうち、最も関連の強いものを選ぶ（要約は除く）。"""
    tokens = content_tokens(user_message)
    if not tokens:
        return None
    best: tuple[int, float, float] | None = None
    chosen: RetrievedMemory | None = None
    for memory in memories:
        if memory.is_summary:
            continue
        core = memory_core(memory.content)
        overlap = sum(len(t) for t in tokens if t in core)
        if overlap == 0:
            continue
        key = (overlap, memory.similarity or 0.0, memory.importance)
        if best is None or key > best:
            best, chosen = key, memory
    return chosen


def _time_bucket(hour: int) -> Literal["morning", "day", "evening", "night"]:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "day"
    if 17 <= hour < 22:
        return "evening"
    return "night"


_SEGMENT_TIME: Final = re.compile(
    r"^\s*(\d{1,2})(?:[:：](\d{2}))?\s*(?:時)?(?:\s*[-〜~ー–]\s*\d{1,2}(?:[:：]\d{2})?\s*(?:時)?)?"
    r"\s*(?:以降|から|頃|ごろ|まで)?\s*(?:は|に)?\s*"
)


_WEEKDAY_CHARS: Final[str] = "月火水木金土日"  # datetime.weekday() の順
_SCHEDULE_HEAD: Final = re.compile(r"^[^:：\d]*[:：]")
_HEAD_PARENS: Final = re.compile(r"[（(]([^）)]*)[）)]")
_DAY_RANGE: Final = re.compile(r"([月火水木金土日])(?:曜日?)?\s*[〜~～\-–]\s*([月火水木金土日])(?:曜日?)?")
_DAY_SINGLE: Final = re.compile(r"([月火水木金土日])曜")
_WEEKDAY_PREFIXES: Final[tuple[str, ...]] = ("平日",)
_HOLIDAY_PREFIXES: Final[tuple[str, ...]] = ("休日", "土日", "週末", "休み")


def parse_schedule_days(head: str) -> frozenset[int] | None:
    """行頭の見出しのかっこ内の曜日指定を解釈する（月=0〜日=6）。指定が無ければ None。

    例: 「平日（木〜月）」「平日（金〜水）」（週をまたぐ範囲）/「休日（火曜・水曜）」/「休日（主に土日）」/
    「平日（サロン出勤日。火曜以外）」。「七日に一度」のように曜日が読み取れないものは None。
    """
    days: set[int] = set()
    for spec in _HEAD_PARENS.findall(head):
        found: set[int] = set()
        for start, end in _DAY_RANGE.findall(spec):
            day, last = _WEEKDAY_CHARS.index(start), _WEEKDAY_CHARS.index(end)
            found.add(day)
            while day != last:
                day = (day + 1) % 7
                found.add(day)
        rest = _DAY_RANGE.sub("", spec)
        found.update(_WEEKDAY_CHARS.index(d) for d in _DAY_SINGLE.findall(rest))
        if "土日" in rest:
            found.update({5, 6})
        if found and "以外" in rest:
            found = set(range(7)) - found
        days |= found
    return frozenset(days) if days else None


def _schedule_line(schedule_pattern: str, weekday: int) -> str | None:
    """今日の曜日に当てはまる「平日」/「休日」の行を選ぶ。

    かっこ内の曜日指定（「平日（木〜月）」など）を優先し、指定の無い行は月〜金=平日・土日=休日とみなす。
    """
    candidates: list[tuple[str, frozenset[int] | None]] = []
    for raw in schedule_pattern.splitlines():
        line = raw.strip()
        if not line.startswith(_WEEKDAY_PREFIXES + _HOLIDAY_PREFIXES):
            continue
        head = _SCHEDULE_HEAD.match(line)
        candidates.append((line, parse_schedule_days(head.group(0)) if head else None))
    explicit = next((line for line, days in candidates if days is not None and weekday in days), None)
    if explicit is not None:
        return explicit
    unspecified = [line for line, days in candidates if days is None]
    target = _HOLIDAY_PREFIXES if weekday >= 5 else _WEEKDAY_PREFIXES
    return next((line for line in unspecified if line.startswith(target)), unspecified[0] if unspecified else None)


def current_activity(schedule_pattern: str, now: datetime) -> str | None:
    """schedule_pattern（「平日: 9:00出社 / 19:00退社 ...」）から現在時刻の過ごし方を推定する。"""
    local = now.astimezone(JST)
    line = _schedule_line(schedule_pattern, local.weekday())
    if line is None:
        return None
    body = re.split(r"[:：]", line, maxsplit=1)[1] if _SCHEDULE_HEAD.search(line) else line
    segments = [s.strip() for s in re.split(r"[/／]", body) if s.strip()]
    if not segments:
        return None
    timed: list[tuple[int, str]] = []
    untimed: list[str] = []
    for seg in segments:
        m = _SEGMENT_TIME.match(seg)
        if m and m.group(1) is not None and re.match(r"^\s*\d", seg):
            minutes = int(m.group(1)) * 60 + int(m.group(2) or 0)
            text = seg[m.end() :].strip()
            if text:
                timed.append((minutes, text))
        else:
            untimed.append(seg)
    now_minutes = local.hour * 60 + local.minute
    if timed:
        past = [t for t in timed if t[0] <= now_minutes]
        # 最初の予定より前（深夜・早朝）は前日の最後の予定の続きとみなす
        return max(past)[1] if past else max(timed)[1]
    if untimed:
        bucket = _time_bucket(local.hour)
        if bucket == "morning":
            return untimed[0]
        if bucket == "day":
            return untimed[min(1, len(untimed) - 1)]
        return untimed[-1]
    return None


def _greeting(message: str, hour: int, sp: str) -> str | None:
    bucket = _time_bucket(hour)
    if "おやすみ" in message:
        return f"おやすみ、{sp}。また明日ね。"
    if "おはよ" in message:
        return f"おはよう、{sp}。" if bucket == "morning" else f"おはよう…って、{sp}いま起きたの？"
    if "こんばんは" in message:
        return "こんばんは。"
    if "こんにちは" in message:
        return "こんにちは。"
    if "ただいま" in message:
        return f"おかえり、{sp}。"
    if "おつかれ" in message or "お疲れ" in message:
        return "おつかれさま。"
    return None


def _memory_reference(memory: RetrievedMemory, seed: int, now: datetime) -> str:
    core = memory_core(memory.content)
    # 「来週」「明日」などを含む記憶が前日以前のものなら、言われた日を添える（今も「来週」扱いしない）
    created = memory.created_at.astimezone(JST)
    when = ""
    if (now.astimezone(JST).date() - created.date()).days >= 1 and any(w in core for w in _RELATIVE_TIME_WORDS):
        when = f"{created.month}月{created.day}日に"
    options = (
        f"そういえば、{when}{core}って言ってたよね。",
        f"{when}{core}って話してくれたの、ちゃんと覚えてるよ。",
        f"{when or '前に'}{core}って言ってたけど、あれからどう？",
    )
    text = _pick(options, seed, 1)
    if memory.is_secret:
        text = "二人だけの秘密の話だけど、" + text
    return text


# 送り仮名が続く述語の語幹（「お酒好き」→「酒好」、「食べ物」→「食」にならないよう区切る）
_PREDICATE_STEM: Final = re.compile(r"[好嫌](?=[きい])|[食飲](?=[べみんま])")


def _topic_word(message: str, exclude: Sequence[str] = ()) -> str | None:
    """ユーザー発言から話題語を1つ選ぶ（長い語を優先。時間表現・一般的な1文字漢字・キャラ名は除く）。"""
    candidates: list[str] = [
        run
        for run in _CONTENT_RUN.findall(_PREDICATE_STEM.sub(" ", message))
        if run not in _TOPIC_STOP
        and run not in exclude
        and (len(run) >= 2 or (_KANJI.match(run) is not None and run not in _SINGLE_KANJI_STOP))
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (-len(r), message.find(r)))[0]


_GREETING_WORDS: Final = re.compile(
    r"おはよ[うー]*|こんばんは|こんにちは|おやすみ(?:なさい)?|ただいま|おつかれ(?:さま)?|お疲れ(?:様)?"
)


# 「いま何してる？」系の質問（キャラの現在の過ごし方を答える）
_ACTIVITY_QUESTION: Final = re.compile(r"(?:何|なに|なん)(?:して|やって)|(?:今|いま)(?:何|なに|なん|どこ)")


def _has_substance(message: str, exclude: Sequence[str] = ()) -> bool:
    """あいさつ語を除いた本文に、反応すべき内容（話題語・質問・感情語）があるか。"""
    stripped = _GREETING_WORDS.sub("", message)
    if _QUESTION_END.search(stripped):
        return True
    if any(w in stripped for w in (*_NEGATIVE_WORDS, *_POSITIVE_WORDS)):
        return True
    return _topic_word(stripped, exclude) is not None


def _asks_current_activity(message: str) -> bool:
    """「いま何してるの？」のように、キャラが今なにをしているかを尋ねる発言か。"""
    if _ACTIVITY_QUESTION.search(message) is None:
        return False
    return _QUESTION_END.search(message) is not None or message.rstrip().endswith(("の", "か"))


def _reaction(message: str, seed: int, persona: Persona, now: datetime) -> str:
    fp, sp = persona.speech.first_person, persona.speech.second_person
    local = now.astimezone(JST)
    if any(w in message for w in _NEGATIVE_WORDS):
        return _pick(
            (
                f"{sp}、ほんとにおつかれさま。無理しないでね。",
                f"そっか…話してくれてありがとう。{fp}はちゃんと味方だよ。",
                f"しんどいときは、{fp}に全部言っていいからね。",
            ),
            seed,
            2,
        )
    if any(w in message for w in _POSITIVE_WORDS):
        return _pick(
            (
                f"よかったね！聞いてる{fp}までうれしくなっちゃう。",
                f"ふふ、{sp}がうれしそうだと{fp}もうれしいな。",
                "それ、すごくいいね！",
            ),
            seed,
            3,
        )
    topic = _topic_word(message, (persona.name,))
    if _asks_current_activity(message):
        activity = current_activity(persona.schedule_pattern, now) if persona.schedule_pattern else None
        if activity:
            label = f"「{activity}」" if activity.endswith("時間") else f"「{activity}」の時間"
            return _pick(
                (
                    f"{fp}？いまは{label}って感じかな。",
                    f"えっとね、{fp}はいま{label}だよ。",
                ),
                seed,
                4,
            )
    if _QUESTION_END.search(message) and topic:
        # 過ごし方以外の質問は、話題語で受けて聞き返す（「美咲はお酒好き？」→「酒のこと？…」）
        return _pick(
            (
                f"{topic}のこと？うーん、{sp}はどう思う？",
                f"{topic}かあ。{sp}はどうなの？",
                f"えっとね…{topic}の話なら、{fp}もいろいろ話したいな。",
            ),
            seed,
            9,
        )
    if _QUESTION_END.search(message) or _asks_current_activity(message):
        return _pick(
            {
                "morning": (f"{fp}はさっき起きたところ。まだちょっと眠いかも。",),
                "day": (f"{fp}はいま、ちょっとひと息ついてたところ。",),
                "evening": (f"{fp}は今日の予定がひと段落したとこだよ。",),
                "night": (f"{fp}はもうのんびりモード。{sp}はまだ起きてるの？",),
            }[_time_bucket(local.hour)],
            seed,
            5,
        )
    if topic:
        kw = topic
        return _pick(
            (
                f"{kw}の話、もっと聞かせて？",
                f"へえ、{kw}かあ。{sp}のそういう話、好きだな。",
                f"{kw}のこと、{fp}も気になってた。",
            ),
            seed,
            6,
        )
    return _pick(
        ("うんうん、それで？", f"そうなんだ。{sp}の話、もっと聞きたいな。", f"ふふ、{sp}らしいね。"),
        seed,
        7,
    )


def mock_chat_reply(hints: MockHints) -> str:
    persona = hints.persona
    message = hints.user_message.strip()
    seed = _seed(persona.key, message, str(len(hints.history)))
    local = hints.now.astimezone(JST)
    parts: list[str] = []
    greeting = _greeting(message, local.hour, persona.speech.second_person)
    if greeting:
        parts.append(greeting)
    memory = pick_relevant_memory(message, hints.memories)
    if memory is not None:
        parts.append(_memory_reference(memory, seed, hints.now))
    elif greeting is None or _has_substance(message, (persona.name,)):
        parts.append(_reaction(message, seed, persona, hints.now))
    if len(parts) < 3 and persona.speech.examples:
        # 直前の文と書き出しが同じ口調例（「わたし？…」が2回続く等）は避ける
        openings = {part[:3] for part in parts}
        examples = [ex for ex in persona.speech.examples if ex[:3] not in openings] or persona.speech.examples
        parts.append(_pick(examples, seed, 8))
    return "".join(parts[:3])


def mock_summary(hints: MockHints) -> str:
    user_lines = [h.body for h in hints.history if h.sender_type == "user"]
    picked: list[str] = []
    for body in user_lines:
        for sentence in split_sentences(body):
            core = strip_trailing(sentence).strip()
            if core and score_sentence(core)[1] is not None and core not in picked:
                picked.append(core[:40])
    if not picked:
        picked = [strip_trailing(b).strip()[:40] for b in user_lines[:3] if b.strip()]
    quoted = "".join(f"「{p}」" for p in picked[:6])
    summary = f"これまでの{len(hints.history)}件のやりとりで、ユーザーは{quoted}と話していた。"
    return summary[:400]


def mock_comment_reply(hints: MockHints) -> str:
    persona = hints.persona
    sp = persona.speech.second_person
    body = (hints.comment_body or "").strip()
    seed = _seed(persona.key, body, hints.post_caption or "")
    if _QUESTION_END.search(body):
        return _pick(("それはね、ひみつ。DMで聞いてくれたら教えるかも", "ふふ、気になる？またDMでね"), seed)
    return _pick(
        (
            f"コメントありがとう！{sp}にそう言ってもらえるとうれしいな",
            f"見てくれてありがとう。{sp}も今日おつかれさま",
            "ありがとう♪ また見にきてね",
            f"ふふ、{sp}のコメントいつも楽しみにしてるよ",
        ),
        seed,
    )


MockHandler = Callable[[LLMRequest], str]
_MOCK_HANDLERS: dict[str, MockHandler] = {}


def register_mock_handler(purpose: str, handler: MockHandler) -> None:
    """MockLLM に用途別の応答生成関数を登録する（キャラクターエンジンの各モジュールが import 時に登録）。

    登録済みの用途では、hints より先にこのハンドラが使われる。ハンドラは live と同じ形式の文字列
    （JSON モードなら JSON 文字列）を返す。request.mock_context に構造化データが入る。
    """
    _MOCK_HANDLERS[purpose] = handler


def registered_mock_purposes() -> frozenset[str]:
    return frozenset(_MOCK_HANDLERS)


class MockLLM:
    """外部 API を呼ばない決定的な LLM。"""

    @property
    def model_name(self) -> str:
        return MOCK_MODEL_NAME

    async def complete(self, request: LLMRequest) -> LLMResult:
        started = time.perf_counter()
        hints = request.hints
        handler = _MOCK_HANDLERS.get(request.purpose)
        if handler is not None:
            text = handler(request)
        elif hints is None:
            text = self._without_hints(request)
        elif request.purpose == "chat":
            text = mock_chat_reply(hints)
        elif request.purpose == "memory_extraction":
            text = json.dumps({"memories": mock_extract(hints.user_message)}, ensure_ascii=False)
        elif request.purpose == "memory_summary":
            text = json.dumps({"summary": mock_summary(hints)}, ensure_ascii=False)
        else:
            text = mock_comment_reply(hints)
        prompt_chars = sum(len(m["content"]) for m in request.messages)
        usage = {
            "prompt_tokens": prompt_chars // 2,
            "completion_tokens": len(text) // 2,
            "total_tokens": (prompt_chars + len(text)) // 2,
        }
        return LLMResult(
            text=text,
            model=MOCK_MODEL_NAME,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
        )

    @staticmethod
    def _without_hints(request: LLMRequest) -> str:
        if request.purpose == "memory_extraction":
            return json.dumps({"memories": []})
        if request.purpose == "memory_summary":
            return json.dumps({"summary": "これまでの会話の要約"}, ensure_ascii=False)
        return "うんうん、それで？"


def create_llm_client(settings: Settings, http: httpx.AsyncClient) -> LLMClient:
    if settings.llm_mode == "live":
        return OpenAICompatibleLLM(settings, http)
    return MockLLM()


# ===========================================================================
# 出力の後処理（live / mock 共通）
# ===========================================================================


def parse_json_object(text: str) -> Any:
    """LLM の JSON 出力を寛容にパースする（コードブロック・前後の説明文を許容）。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object found in LLM output")


def clean_reply(text: str, persona_name: str, *, max_chars: int) -> str:
    """名前の接頭辞や外側のかぎかっこ・引用符を取り除き、長さを制限する。"""
    value = text.strip()
    value = re.sub(rf"^(?:{re.escape(persona_name)})\s*[:：]\s*", "", value)
    if len(value) >= 2 and value[0] in "「\"'" and value[-1] in "」\"'":
        value = value[1:-1].strip()
    if len(value) > max_chars:
        value = value[: max_chars - 1] + "…"
    return value
