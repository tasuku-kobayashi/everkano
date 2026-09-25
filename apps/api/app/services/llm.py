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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.persona import Persona
from app.services.prompt import JST, ChatMessage
from app.services.types import HistoryItem, RetrievedMemory

logger = get_logger("llm")

Purpose = Literal["chat", "memory_extraction", "memory_summary", "comment_reply"]


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
        self._timeout = settings.llm_timeout_seconds
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
            "model": self._model,
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
_TRAILING_PUNCT: Final = re.compile(r"[\s。、．，！？!?…〜~ー♪wｗ笑]+$")
_SENTENCE_ENDINGS: Final = re.compile(
    r"(?:なんだよね|なんだよ|なんだ|んだよね|んだよ|んだ|だよね|だよ|よね|かな|よ|ね|な|の|わ|さ)$"
)
_QUOTED: Final = re.compile(r"「(.+)」")
# 内容語の連続（漢字 / カタカナ / 英数字をそれぞれ別の語として扱う: 「ラーメン食べた」→「ラーメン」「食」）
_CONTENT_RUN: Final = re.compile(r"[\u4e00-\u9fff\u3005]+|[\u30a1-\u30fa\u30fc]+|[A-Za-z0-9]+")
_KANJI: Final = re.compile(r"[\u4e00-\u9fff]")
# 話題として拾わない語（時間表現など）
_TOPIC_STOP: Final[frozenset[str]] = frozenset(
    {"今日", "明日", "昨日", "来週", "今週", "先週", "今度", "週末", "最近", "今年", "来年", "毎日", "ユーザー"}
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
        core = _TRAILING_PUNCT.sub("", sentence).strip()
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
    core = _TRAILING_PUNCT.sub("", core)
    core = _SENTENCE_ENDINGS.sub("", core)
    return _TRAILING_PUNCT.sub("", core).strip()


def content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for run in _CONTENT_RUN.findall(text):
        if run in {"ユーザー"}:
            continue
        if len(run) == 1:
            if run not in _SINGLE_KANJI_STOP and _KANJI.match(run):
                tokens.add(run)
            continue
        tokens.add(run)
        tokens.update(run[i : i + 2] for i in range(len(run) - 1))
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


def current_activity(schedule_pattern: str, now: datetime) -> str | None:
    """schedule_pattern（「平日: 9:00出社 / 19:00退社 ...」）から現在時刻の過ごし方を推定する。"""
    local = now.astimezone(JST)
    weekend = local.weekday() >= 5
    target_prefixes = ("休日", "土日", "週末", "休み") if weekend else ("平日",)
    line = next(
        (ln for ln in schedule_pattern.splitlines() if ln.strip().startswith(target_prefixes)),
        None,
    )
    if line is None:
        return None
    body = re.split(r"[:：]", line, maxsplit=1)[1] if re.search(r"^[^:：\d]*[:：]", line) else line
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


def _memory_reference(memory: RetrievedMemory, seed: int) -> str:
    core = memory_core(memory.content)
    options = (
        f"そういえば、{core}って言ってたよね。",
        f"{core}って話してくれたの、ちゃんと覚えてるよ。",
        f"前に{core}って言ってたけど、あれからどう？",
    )
    text = _pick(options, seed, 1)
    if memory.is_secret:
        text = "二人だけの秘密の話だけど、" + text
    return text


def _topic_word(message: str) -> str | None:
    """ユーザー発言から話題語を1つ選ぶ（長い語を優先。時間表現・一般的な1文字漢字は除く）。"""
    candidates: list[str] = [
        run
        for run in _CONTENT_RUN.findall(message)
        if run not in _TOPIC_STOP
        and (len(run) >= 2 or (_KANJI.match(run) is not None and run not in _SINGLE_KANJI_STOP))
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (-len(r), message.find(r)))[0]


_GREETING_WORDS: Final = re.compile(
    r"おはよ[うー]*|こんばんは|こんにちは|おやすみ(?:なさい)?|ただいま|おつかれ(?:さま)?|お疲れ(?:様)?"
)


def _has_substance(message: str) -> bool:
    """あいさつ語を除いた本文に、反応すべき内容（話題語・質問・感情語）があるか。"""
    stripped = _GREETING_WORDS.sub("", message)
    if _QUESTION_END.search(stripped):
        return True
    if any(w in stripped for w in (*_NEGATIVE_WORDS, *_POSITIVE_WORDS)):
        return True
    return _topic_word(stripped) is not None


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
    if _QUESTION_END.search(message) or message.rstrip().endswith(("の", "か")):
        activity = current_activity(persona.schedule_pattern, now) if persona.schedule_pattern else None
        if activity:
            return _pick(
                (
                    f"{fp}？いまは「{activity}」って感じの時間かな。",
                    f"えっとね、{fp}はいま「{activity}」の時間だよ。",
                ),
                seed,
                4,
            )
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
    topic = _topic_word(message)
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
        parts.append(_memory_reference(memory, seed))
    elif greeting is None or _has_substance(message):
        parts.append(_reaction(message, seed, persona, hints.now))
    if len(parts) < 3 and persona.speech.examples:
        parts.append(_pick(persona.speech.examples, seed, 8))
    return "".join(parts[:3])


def mock_summary(hints: MockHints) -> str:
    user_lines = [h.body for h in hints.history if h.sender_type == "user"]
    picked: list[str] = []
    for body in user_lines:
        for sentence in split_sentences(body):
            core = _TRAILING_PUNCT.sub("", sentence).strip()
            if core and score_sentence(core)[1] is not None and core not in picked:
                picked.append(core[:40])
    if not picked:
        picked = [_TRAILING_PUNCT.sub("", b).strip()[:40] for b in user_lines[:3] if b.strip()]
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


class MockLLM:
    """外部 API を呼ばない決定的な LLM。"""

    @property
    def model_name(self) -> str:
        return MOCK_MODEL_NAME

    async def complete(self, request: LLMRequest) -> LLMResult:
        started = time.perf_counter()
        hints = request.hints
        if hints is None:
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
