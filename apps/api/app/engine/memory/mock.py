"""MockLLM の `memory_analysis` / `memory_summary`（決定的なルールベース。LLM_MODE=mock・テスト・評価ハーネス用）。

live と同じ JSON を返すので、パース・検証・操作の適用は live と共通のコードで検証される。
ルールの要点（日本語）:
- 呼び方（「〇〇って呼んで」）・話し方（タメ口）・キャラへの好意 → relationship
- 仕事・住まい・恋人・学校・名前・誕生日 → fact。同じ属性の既存の記憶と内容が違えば supersede（「転職した」など, M4）
- 「好き・苦手・趣味」→ preference（「好きな〇〇は」は〇〇ごとに1件。変われば supersede）
- 強い気持ち（緊張・不安・悩み・嬉しい…）→ emotion（同じ発言に予定や話題があるときだけ。その場の気分は残さない）
- 家族・ペット・健康・日課 → fact / 「昨日〜した」→ episode
- 予定の名詞・約束の言い回し + 日付表現 → promises
  （「来週の木曜」「明日」「今度の土曜」「10月1日」「週末」を日本時間で解決, M6）
- 既存の約束: ユーザーが「終わった」「中止」と言えば done / cancelled、キャラの返答が触れれば mentioned
- キャラの返答の「昨日〜した」「わたしは〜が好き」→ character_statements（M8）
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from app.engine.memory.dates import (
    ResolvedDate,
    normalize,
    resolve_future_date,
    resolve_past_date,
    today_jst,
)
from app.engine.memory.guard import INJECTION_PLACEHOLDER
from app.engine.memory.text import (
    QUESTION_END,
    bigram_jaccard,
    format_date_ja,
    normalize_for_hash,
    promise_terms,
    split_sentences,
    strip_endings,
    strip_leading,
    strip_trailing,
)
from app.services.llm import LLMRequest, register_mock_handler

# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------
_QUOTED_CALL_RE: Final = re.compile(r"「([^「」]{1,20})」(?:って|と)呼(?:んで|ばれたい)")
_CALL_RE: Final = re.compile(r"([^\s、。「」はをがにでもの]{1,12})(?:って|と)呼(?:んで|ばれたい)")
_SPEECH_RE: Final = re.compile(r"(タメ口|ため口|敬語(?:じゃなくて|は|を)?(?:やめ|なし|いら|使わなくて))")
_AFFECTION_RE: Final = re.compile(r"(大好き|好き|愛して|会いたい|付き合って|結婚して|一緒にいたい)")
_ADDRESS_RE: Final = re.compile(r"(君|きみ|キミ|あなた|お前|おまえ|のこと)")
_STANDALONE_AFFECTION_RE: Final = re.compile(r"^(?:大?好き|愛してる|会いたい)")

_ATTRIBUTES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("name", re.compile(r"(名前は|って名前|と申します|って言います)")),
    ("birthday", re.compile(r"(誕生日は|誕生日が)")),
    (
        "job",
        re.compile(
            r"(仕事は|職業は|で働いて|に勤めて|で勤めて|会社員|エンジニア|看護師|営業(?:職|を|を)|教師|公務員|"
            r"フリーランス|転職|就職|退職|アルバイト|バイトして|バイト先|職場|勤務)"
        ),
    ),
    ("home", re.compile(r"(に住んで|住んでる|住んでいる|住み|引っ越|一人暮らし|実家暮らし|在住|上京)")),
    (
        "partner",
        re.compile(
            r"(彼女が|彼氏が|彼女いる|彼氏いる|彼女と|彼氏と|恋人|奥さん|旦那|結婚し|独身|離婚|別れた|"
            r"付き合って(?:る|いる)(?:人|子))"
        ),
    ),
    ("school", re.compile(r"(大学|専門学校|学生|年生|卒業|入学|研究室)")),
)
_PREF_RE: Final = re.compile(r"(大好き|好き|ハマって|はまって|推し|嫌い|苦手|趣味)")
_FAV_KEY_RE: Final = re.compile(r"(?:好きな|嫌いな|苦手な)([^\s、。]{1,8}?)(?:は|って)")
_EMOTION_STRONG: Final[tuple[str, ...]] = (
    "緊張",
    "不安",
    "悩",
    "落ち込",
    "泣",
    "寂し",
    "さみし",
    "さびし",
    "つらい",
    "辛い",
    "しんどい",
    "怖",
    "心配",
    "嬉し",
    "うれし",
    "楽しみ",
    "幸せ",
    "イライラ",
    "怒",
    "喧嘩",
    "けんか",
    "ムカつ",
    "ショック",
    "凹",
)
_PERSONAL_RE: Final = re.compile(
    r"(家族|母|父|姉|兄|妹|弟|実家|祖母|祖父|おばあちゃん|おじいちゃん|ペット|飼って|犬|猫|健康|入院|体調|持病|"
    r"アレルギー|日課|毎日|毎朝|毎晩|毎週|出身|地元)"
)
# 予定の名詞・動詞。丁寧語（「行きます」「予定です」「受けます」）も予定として扱う（live のモデルは敬体でも
# 予定と分かる。2026-09-26 の評価で「今度の土曜、友達と美術館に行きます！」を取りこぼした）
_PLAN_RE: Final = re.compile(
    r"(予定|約束|面接|試験|テスト|受験|出張|旅行|デート|飲み会|会議|発表|プレゼン|ライブ|コンサート|フェス|結婚式|"
    r"誕生日|病院|歯医者|健康診断|検査|手術|引っ越し|締め切り|締切|映画|合コン|同窓会|帰省|面談|入社|初出勤|"
    r"美術館|博物館|水族館|動物園|遊園地|展覧会|展示会|個展|花火大会|お祭り|夏祭り|キャンプ|ピクニック|登山|"
    r"遊びに|行く|行こう|会う|会おう|観に|見に|しよう|やろう|教えて|"
    r"行きます|行ってきます|参ります|伺います|会います|観ます|見ます|受けます|参加します|出かけます|出掛けます|"
    r"つもりです|つもりなんです|予定です|予定があります|予定なんです|しましょう|行きましょう|会いましょう)"
)
_JOINT_RE: Final = re.compile(
    r"(一緒に|約束|しよう|やろう|行こう|会おう|見よう|観よう|食べよう|教えて|しましょう|行きましょう|会いましょう|"
    r"見ましょう|観ましょう|食べましょう|教えてください)"
)
_FUTURE_ONLY_RE: Final = re.compile(r"(今度|いつか|そのうち)")
# 過去形（「行った」「読んだ」「でした」）。「面接なんだ」「行くんだ」（説明のんだ）・「またね」は過去形にしない
_PAST_TENSE_RE: Final = re.compile(
    r"(?:(?<!ま)た|[\u4e00-\u9fff]んだ|でした|ました)(?:んだ|んです|よ|ね|の|けど|から|わ|な|っけ)*$"
)
_PAST_TIME_RE: Final = re.compile(
    r"(昨日|きのう|一昨日|おととい|先週|この前|こないだ|先月|去年|さっき|今朝|昨夜|ゆうべ)"
)
_WEATHER_RE: Final = re.compile(r"(天気|雨|晴れ|暑|寒|曇|雪)")
_SECRET_RE: Final = re.compile(r"(秘密|内緒|ないしょ|誰にも言わないで|二人だけ)")
_DONE_RE: Final = re.compile(
    r"(終わった|終わりました|終わって|行ってきた|行ってきました|受けてきた|済んだ|無事|合格|受かった|落ちた|"
    r"楽しかった|果たした|観てきた|見てきた|やってきた)"
)
_CANCEL_RE: Final = re.compile(
    r"(なくなった|無くなった|中止|キャンセル|延期|行けなくなった|なしになった|流れた|取りやめ)"
)
_CONTENT_RUN_RE: Final = re.compile(r"[一-鿿々]{2,}|[ァ-ヺー]{2,}|[A-Za-z0-9]{2,}")
_REFERS_USER_RE: Final = re.compile(r"(言ってた|覚えて|話してくれ|教えて|聞かせて|どうだった|どう？|どうなの)")
_SELF_VERB_RE: Final = re.compile(
    r"(好き|飼って|住んで|働いて|行った|行ってき|作った|読んだ|見た|観た|買った|食べた|行ってた|ハマって)"
)
_MEMORY_QUOTE_RE: Final = re.compile(r"「(.+)」")
_LEADING_TIME_RE: Final = re.compile(r"^(?:から|まで|には|では|は|に|で|も|、)+")

_EMOTION_IMPORTANCE: Final[float] = 0.65
_WEAK_EMOTION_IMPORTANCE: Final[float] = 0.45


def _is_question(text: str) -> bool:
    return QUESTION_END.search(text) is not None


def _core(sentence: str) -> str:
    return strip_trailing(normalize(sentence)).strip()


def _memory_core(content: str) -> str:
    quoted = _MEMORY_QUOTE_RE.search(content)
    core = quoted.group(1) if quoted else re.sub(r"^ユーザー(?:は|が|の|と)", "", content)
    return strip_endings(core)


def _same(a: str, b: str) -> bool:
    x, y = normalize_for_hash(a), normalize_for_hash(b)
    if not x or not y:
        return False
    return x == y or (len(x) >= 4 and len(y) >= 4 and (x in y or y in x))


def _attribute(text: str) -> str | None:
    for name, pattern in _ATTRIBUTES:
        if pattern.search(text):
            return name
    return None


_CALL_PREFIX_RE: Final = re.compile(
    r"^(?:やっぱり|やっぱ|これから|今度から|今日から|次から|今後|あと|じゃあ|では|なら)+"
)


def _call_name(text: str) -> str | None:
    quoted = _QUOTED_CALL_RE.search(text)
    if quoted:
        return quoted.group(1).strip()
    match = _CALL_RE.search(text)
    if match is None:
        return None
    name = _CALL_PREFIX_RE.sub("", match.group(1)).strip()
    return name or None


def _fav_key(text: str) -> str | None:
    match = _FAV_KEY_RE.search(text)
    return f"fav:{match.group(1)}" if match else None


@dataclass(slots=True)
class _Existing:
    ref: str
    kind: str
    content: str
    user_edited: bool
    core: str
    attribute: str | None
    handled: bool = False


@dataclass(slots=True)
class _State:
    now: datetime
    today: date
    name: str
    first_person: str
    second_person: str
    threshold: float
    existing: list[_Existing]
    promises: list[dict[str, Any]]
    ops: list[dict[str, Any]] = field(default_factory=list)
    new_promises: list[dict[str, Any]] = field(default_factory=list)
    updates: dict[str, str] = field(default_factory=dict)
    statements: list[dict[str, Any]] = field(default_factory=list)
    emitted: list[str] = field(default_factory=list)


def _classify_existing(memory: Mapping[str, Any]) -> _Existing:
    content = str(memory.get("content", ""))
    core = _memory_core(content)
    kind = str(memory.get("kind", "fact"))
    attribute: str | None = None
    if kind == "relationship" and "呼ばれたい" in content:
        attribute = "call_name"
    elif kind == "relationship" and _SPEECH_RE.search(content):
        attribute = "speech_style"
    elif kind == "preference":
        attribute = _fav_key(core)
    elif kind == "fact":
        attribute = _attribute(core)
    return _Existing(
        ref=str(memory.get("ref", "")),
        kind=kind,
        content=content,
        user_edited=bool(memory.get("user_edited", False)),
        core=core,
        attribute=attribute,
    )


def _emit(
    state: _State,
    *,
    kind: str,
    content: str,
    importance: float,
    turn: int,
    attribute: str | None,
    secret: bool,
    core: str,
) -> None:
    """記憶の操作を1件出す。同じ属性の既存の記憶があれば noop / supersede、同じ内容なら noop。"""
    if importance < state.threshold:
        return
    if any(_same(core, previous) for previous in state.emitted):
        return
    if attribute is not None:
        same_attribute = [e for e in state.existing if e.attribute == attribute and not e.handled]
        if same_attribute:
            old = same_attribute[-1]
            old.handled = True
            state.emitted.append(core)
            if _same(old.core, core):
                state.ops.append({"op": "noop", "target": old.ref})
                return
            state.ops.append(
                {
                    "op": "supersede",
                    "target": old.ref,
                    "kind": kind,
                    "content": content,
                    "importance": max(importance, 0.8),
                    "turn": turn,
                    "secret": secret,
                }
            )
            return
    duplicate = next((e for e in state.existing if _same(e.core, core)), None)
    state.emitted.append(core)
    if duplicate is not None:
        state.ops.append({"op": "noop", "target": duplicate.ref})
        return
    state.ops.append(
        {"op": "add", "kind": kind, "content": content, "importance": importance, "turn": turn, "secret": secret}
    )


# 丁寧語の予定を常体にそろえる（約束の内容は短い名詞句・常体にする: 「美術館に行きます」→「美術館に行く」）
_POLITE_TO_PLAIN: Final[tuple[tuple[str, str], ...]] = (
    ("行ってきます", "行ってくる"),
    ("行きましょう", "行こう"),
    ("会いましょう", "会おう"),
    ("見ましょう", "見よう"),
    ("観ましょう", "観よう"),
    ("食べましょう", "食べよう"),
    ("しましょう", "しよう"),
    ("行きます", "行く"),
    ("会います", "会う"),
    ("観ます", "観る"),
    ("見ます", "見る"),
    ("受けます", "受ける"),
    ("参加します", "参加する"),
    ("出かけます", "出かける"),
    ("出掛けます", "出掛ける"),
    ("伺います", "伺う"),
    ("参ります", "行く"),
)
_PLAN_SUFFIX_RE: Final = re.compile(
    r"(?:の)?予定(?:です|があります|なんです|がある|だ)?$|(?:つもり)?(?:です|なんです)$"
)


def _plain_plan(text: str) -> str:
    for polite, plain in _POLITE_TO_PLAIN:
        if text.endswith(polite):
            return text[: -len(polite)] + plain
    stripped = _PLAN_SUFFIX_RE.sub("", text)
    return stripped or text


def _promise_content(core: str, resolved: ResolvedDate | None) -> str:
    text = core
    if resolved is not None and resolved.expression:
        text = text.replace(resolved.expression, "", 1)
    text = re.sub(r"(?:午前|午後|朝|夜|夕方|昼|晩)?\s*\d{1,2}\s*(?:時(?:\s*\d{1,2}\s*分|半)?|:\d{2})", "", text)
    text = re.sub(r"^(?:の|に|は|、|\s)*(?:今度|いつか|そのうち|また)", "", text)
    text = _LEADING_TIME_RE.sub("", strip_leading(text))
    text = _plain_plan(strip_endings(strip_leading(text)))
    return text or core


def _date_label(resolved: ResolvedDate, today: date) -> str:
    label = format_date_ja(resolved.date, today=today)
    if resolved.precision == "datetime" and resolved.time is not None:
        return f"{label}{resolved.time:%H:%M}"
    if resolved.precision == "week":
        return f"{label}の週"
    if resolved.precision == "month":
        return f"{resolved.date.month}月"
    return label


def _maybe_promise(state: _State, core: str, turn: int) -> str | None:
    """予定・約束なら promises に追加して、その内容（名詞句）を返す。"""
    if _PAST_TENSE_RE.search(core) and not _JOINT_RE.search(core):
        return None
    is_plan = _PLAN_RE.search(core) is not None
    is_joint = _JOINT_RE.search(core) is not None
    if not is_plan and not is_joint:
        return None
    resolved = resolve_future_date(core, state.now)
    if resolved is not None and resolved.date < state.today:
        return None
    if resolved is None and not (is_joint or _FUTURE_ONLY_RE.search(core)):
        return None
    content = _promise_content(core, resolved)
    due_date = resolved.date.isoformat() if resolved is not None else None
    for existing in [*state.promises, *state.new_promises]:
        if existing.get("due_date") == due_date and (
            _same(str(existing.get("content", "")), content)
            or bigram_jaccard(str(existing.get("content", "")), content) >= 0.5
        ):
            return content
    if resolved is None:
        memory = (
            f"ユーザーと「{content}」と約束した（期日は未定）"
            if is_joint
            else f"ユーザーは「{content}」の予定がある（日付は未定）"
        )
    else:
        label = _date_label(resolved, state.today)
        memory = (
            f"ユーザーと{label}に「{content}」と約束した"
            if is_joint
            else f"ユーザーは{label}に「{content}」の予定がある"
        )
    state.new_promises.append(
        {
            "content": content,
            "memory": memory,
            "due_date": due_date,
            "due_time": resolved.time.strftime("%H:%M") if resolved is not None and resolved.time else None,
            "due_precision": resolved.precision if resolved is not None else "unknown",
            "importance": 0.9 if resolved is not None and resolved.precision in {"day", "datetime"} else 0.8,
            "turn": turn,
        }
    )
    return content


def _analyze_user_message(state: _State, message: str, turn: int) -> None:
    if message == INJECTION_PLACEHOLDER:
        return  # 設定・関係・評価の書き換えの発言（guard.py で置き換え済み）は記憶にしない
    secret = _SECRET_RE.search(message) is not None
    sentences = [(_core(raw), _is_question(normalize(raw))) for raw in split_sentences(message)]
    topics: list[str] = []
    pending_emotions: list[tuple[str, bool]] = []
    last_add: dict[str, Any] | None = None  # この発言で直前に追加した記憶（続きの文をつなげる）
    previous_ops: int | None = None
    for sentence, question in sentences:
        if len(sentence) < 2:
            continue
        if previous_ops is not None and len(state.ops) > previous_ops and state.ops[-1]["op"] == "add":
            last_add = state.ops[-1] if "」と話していた" in state.ops[-1]["content"] else None
        elif previous_ops is not None:
            last_add = None
        previous_ops = len(state.ops)
        if last_add is not None and not question and _is_continuation(sentence):
            # 「猫を2匹飼ってるよ。ミケとタマ」の 2 文目（名前などの補足）は直前の記憶に含める
            last_add["content"] = last_add["content"].replace("」と話していた", f"。{sentence}」と話していた", 1)
            continue
        # 名前・誕生日は予定ではなく事実（「誕生日は3月14日」を約束にしない）
        promise = None if _attribute(sentence) in {"name", "birthday"} else _maybe_promise(state, sentence, turn)
        if promise is not None:
            topics.append(promise)
            continue
        if question or len(sentence) < 3:
            continue
        # 呼び方・話し方・キャラへの好意（relationship）
        call = _call_name(sentence)
        if call:
            _emit(
                state,
                kind="relationship",
                content=f"ユーザーは「{call}」と呼ばれたい",
                importance=0.85,
                turn=turn,
                attribute="call_name",
                secret=secret,
                core=call,
            )
            continue
        if _SPEECH_RE.search(sentence):
            _emit(
                state,
                kind="relationship",
                content=f"ユーザーは「{sentence}」と話し、くだけた話し方を望んでいる",
                importance=0.75,
                turn=turn,
                attribute="speech_style",
                secret=secret,
                core=sentence,
            )
            continue
        addressed = _ADDRESS_RE.search(sentence) is not None or state.name in sentence
        if _AFFECTION_RE.search(sentence) and (addressed or _STANDALONE_AFFECTION_RE.search(sentence)):
            _emit(
                state,
                kind="relationship",
                content=f"ユーザーは{state.name}に「{sentence}」と伝えた",
                importance=0.75,
                turn=turn,
                attribute=None,
                secret=secret,
                core=sentence,
            )
            continue
        # 属性（仕事・住まい・恋人など）: 同じ属性の既存の記憶と矛盾すれば supersede
        attribute = _attribute(sentence)
        if attribute is not None:
            _emit(
                state,
                kind="fact",
                content=f"ユーザーは「{sentence}」と話していた",
                importance=0.8,
                turn=turn,
                attribute=attribute,
                secret=secret,
                core=sentence,
            )
            topics.append(sentence)
            continue
        if _PREF_RE.search(sentence):
            _emit(
                state,
                kind="preference",
                content=f"ユーザーは「{sentence}」と話していた",
                importance=0.7,
                turn=turn,
                attribute=_fav_key(sentence),
                secret=secret,
                core=sentence,
            )
            topics.append(sentence)
            continue
        if any(word in sentence for word in _EMOTION_STRONG):
            pending_emotions.append((sentence, _topic_in(sentence)))
            continue
        if _PERSONAL_RE.search(sentence):
            _emit(
                state,
                kind="fact",
                content=f"ユーザーは「{sentence}」と話していた",
                importance=0.7,
                turn=turn,
                attribute=None,
                secret=secret,
                core=sentence,
            )
            topics.append(sentence)
            continue
        if (
            _PAST_TIME_RE.search(sentence)
            and _PAST_TENSE_RE.search(sentence)
            and not _WEATHER_RE.search(sentence)
            and len(sentence) >= 8
        ):
            _emit(
                state,
                kind="episode",
                content=f"ユーザーは「{sentence}」と話していた",
                importance=0.6,
                turn=turn,
                attribute=None,
                secret=secret,
                core=sentence,
            )
    # 気持ち: 同じ発言に予定・話題があるときだけ残す（「今日は疲れた」だけのような一時的な気分は残さない）
    for sentence, has_topic in pending_emotions:
        topic = topics[0] if topics else None
        importance = _EMOTION_IMPORTANCE if (topic is not None or has_topic) else _WEAK_EMOTION_IMPORTANCE
        content = (
            f"ユーザーは「{topic}」のことで「{sentence}」と話していた"
            if topic is not None and topic not in sentence
            else f"ユーザーは「{sentence}」と話していた"
        )
        _emit(
            state,
            kind="emotion",
            content=content,
            importance=importance,
            turn=turn,
            attribute=None,
            secret=secret,
            core=sentence,
        )


def _is_continuation(sentence: str) -> bool:
    """直前の文の補足（名前・固有名詞だけの短い文）か。予定・気持ち・属性などの文は含めない。"""
    if len(sentence) > 25 or _attribute(sentence) is not None or _PLAN_RE.search(sentence):
        return False
    if any(word in sentence for word in _EMOTION_STRONG) or _PREF_RE.search(sentence):
        return False
    if _PERSONAL_RE.search(sentence) or _SPEECH_RE.search(sentence) or _AFFECTION_RE.search(sentence):
        return False
    if _call_name(sentence) is not None or _PAST_TIME_RE.search(sentence):
        return False
    return _CONTENT_RUN_RE.search(sentence) is not None and _PAST_TENSE_RE.search(sentence) is None


def _topic_in(sentence: str) -> bool:
    """気持ちの文に、理由になる話題（漢字・カタカナの語）が含まれるか（「仕事で落ち込んでる」）。"""
    stripped = sentence
    for word in _EMOTION_STRONG:
        stripped = stripped.replace(word, "")
    return any(len(run) >= 2 for run in _CONTENT_RUN_RE.findall(stripped))


def _promise_updates(state: _State, turns: Sequence[Mapping[str, Any]]) -> None:
    for promise in state.promises:
        ref = str(promise.get("ref", ""))
        status = str(promise.get("status", "pending"))
        tokens = promise_terms(str(promise.get("content", "")))
        if not ref or not tokens:
            continue
        for turn in turns:
            user_text = normalize(str(turn.get("user", "")))
            reply_text = normalize(str(turn.get("reply", "")))
            if any(t in user_text for t in tokens):
                if _CANCEL_RE.search(user_text):
                    state.updates[ref] = "cancelled"
                    continue
                if _DONE_RE.search(user_text):
                    state.updates[ref] = "done"
                    continue
            if status == "pending" and ref not in state.updates and any(t in reply_text for t in tokens):
                state.updates[ref] = "mentioned"


def _character_statements(state: _State, reply: str, turn: int) -> None:
    for raw in split_sentences(reply):
        sentence = _core(raw)
        if len(sentence) < 5 or _is_question(raw) or _REFERS_USER_RE.search(sentence):
            continue
        if state.second_person and state.second_person in sentence:
            continue
        past = resolve_past_date(sentence, state.now)
        past_event = past is not None and _PAST_TENSE_RE.search(sentence) is not None
        self_fact = state.first_person in sentence and _SELF_VERB_RE.search(sentence) is not None
        if not past_event and not self_fact:
            continue
        content = strip_endings(sentence)
        content = re.sub(rf"^{re.escape(state.first_person)}(?:は|も|ね|、)*", "", content).strip() or content
        if any(_same(content, s["content"]) for s in state.statements):
            continue
        occurred: str | None = None
        if past is not None and past[1] not in {"先週"}:
            occurred = past[0].isoformat()
        state.statements.append({"content": content, "occurred_date": occurred, "turn": turn})


def mock_memory_analysis(request: LLMRequest) -> str:
    context = request.mock_context or {}
    turns: Sequence[Mapping[str, Any]] = context.get("turns") or []
    empty: dict[str, list[Any]] = {"memories": [], "promises": [], "promise_updates": [], "character_statements": []}
    if not turns:
        return json.dumps(empty)
    now = datetime.fromisoformat(str(context["now"]))
    persona: Mapping[str, Any] = context.get("persona") or {}
    state = _State(
        now=now,
        today=today_jst(now),
        name=str(persona.get("name", "")),
        first_person=str(persona.get("first_person", "わたし")),
        second_person=str(persona.get("second_person", "あなた")),
        threshold=float(context.get("threshold", 0.6)),
        existing=[_classify_existing(m) for m in context.get("memories") or []],
        promises=list(context.get("promises") or []),
    )
    for index, turn in enumerate(turns, start=1):
        _analyze_user_message(state, str(turn.get("user", "")), index)
        _character_statements(state, str(turn.get("reply", "")), index)
    _promise_updates(state, turns)
    output = {
        "memories": state.ops,
        "promises": state.new_promises,
        "promise_updates": [{"target": ref, "status": status} for ref, status in state.updates.items()],
        "character_statements": state.statements,
    }
    return json.dumps(output, ensure_ascii=False)


# ---------------------------------------------------------------------------
# memory_summary（中期要約）
# ---------------------------------------------------------------------------
_SUMMARY_KEYWORDS: Final = re.compile(
    r"(仕事|会社|職場|上司|同僚|出張|残業|転職|バイト|家族|母|父|姉|兄|妹|弟|実家|健康|病院|入院|風邪|体調|眠れ|"
    r"日課|毎日|毎朝|毎晩|好き|趣味|名前|住んで|出身|誕生日|ペット|飼って|引っ越|来週|明日|今度|週末|来月|約束|"
    r"予定|一緒に|呼んで|会いたい|悲し|辛い|つらい|嬉し|うれし|寂し|不安|疲れ|しんどい|緊張|楽しみ|心配|悩)"
)


def mock_memory_summary(request: LLMRequest) -> str:
    """これまでの会話の要約（ユーザーの発言から、記憶に関わる文を拾って並べる）。"""
    context = request.mock_context or {}
    transcript: list[tuple[str, str]] = []
    if context.get("transcript"):
        transcript = [(str(t.get("sender")), str(t.get("body", ""))) for t in context["transcript"]]
    elif request.hints is not None:
        transcript = [(h.sender_type, h.body) for h in request.hints.history]
    user_lines = [body for sender, body in transcript if sender == "user"]
    picked: list[str] = []
    for body in user_lines:
        for sentence in split_sentences(body):
            core = strip_trailing(sentence).strip()
            if core and _SUMMARY_KEYWORDS.search(core) and core[:40] not in picked:
                picked.append(core[:40])
    if not picked:
        picked = [strip_trailing(b).strip()[:40] for b in user_lines[:3] if b.strip()]
    quoted = "".join(f"「{p}」" for p in picked[:6])
    summary = f"これまでの{len(transcript)}件のやりとりで、ユーザーは{quoted}と話していた。"
    return json.dumps({"summary": summary[:400]}, ensure_ascii=False)


def register_mock_handlers() -> None:
    """MockLLM に記憶の用途を登録する（app.engine.memory の import 時に呼ばれる。何度呼んでもよい）。"""
    register_mock_handler("memory_analysis", mock_memory_analysis)
    register_mock_handler("memory_summary", mock_memory_summary)
