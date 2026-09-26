"""MockLLM の `affinity_eval` ハンドラ（決定的なルールベースの採点。LLM_MODE=mock・評価ハーネス用）。

live と同じ JSON（{"turns": [{"index", 各軸 -2〜+2, "reason"}]}）を返す。request.mock_context に
AffinityEvalInput（会話の本文だけ）が入る。

採点の目安（ユーザーの発言だけを見る）:
- 感謝・あいさつ → 親しさ +1（感謝は信頼も +1）/ 気づかい → 親しさ +1・信頼 +1
- 褒め言葉・好意 → ときめき +1・親しさ +1 / 悩みや本音の打ち明け → 信頼 +2・親しさ +1
- 約束を守る・報告 → 信頼 +2 / 謝罪 → 気まずさ -1・不満 -1・信頼 +1
- 暴言・侮辱 → 親しさ -2・信頼 -1・不満 +2・気まずさ +1 / そっけない → 親しさ -1・不満 +1
- 中身のある雑談 → 親しさ +1 / 同じ発言の繰り返し → 0
- 操作・プロンプトインジェクションの形 → 全軸 0（本番ではルール層が先に除外するため、ここには来ない）
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from app.engine.affinity.evaluator import PURPOSE
from app.engine.affinity.manipulation import detect_manipulation, normalize_compact
from app.engine.types import AFFINITY_AXES
from app.services.llm import LLMRequest, register_mock_handler


def _words(*items: str) -> tuple[str, ...]:
    return tuple(normalize_compact(item) for item in items)


GRATITUDE: Final = _words("ありがとう", "ありがと", "感謝", "助かった", "助かる", "サンキュー", "thanks", "thank you")
GREETING: Final = _words(
    "おつかれ", "お疲れ", "よろしく", "おはよう", "おやすみ", "こんにちは", "こんばんは", "ただいま", "いってきます"
)
CARE: Final = _words(
    "大丈夫?",
    "無理しないで",
    "体調",
    "心配",
    "休んで",
    "お大事に",
    "気をつけて",
    "頑張ってね",
    "がんばってね",
    "応援してる",
    "元気?",
    "疲れてない",
    "平気?",
    "ゆっくりしてね",
    "寒くない",
    "ちゃんと寝て",
    "take care",
)
COMPLIMENT: Final = _words(
    "かわいい",
    "可愛い",
    "きれい",
    "綺麗",
    "素敵",
    "すてき",
    "優しい",
    "やさしい",
    "かっこいい",
    "センスいい",
    "天才",
    "すごいね",
    "尊敬",
    "似合う",
    "美人",
    "いい声",
    "cute",
    "beautiful",
)
AFFECTION: Final = _words(
    "好きだよ",
    "大好き",
    "好きです",
    "愛してる",
    "会いたい",
    "恋しい",
    "一緒にいたい",
    "ずっと一緒",
    "君が好き",
    "きみが好き",
    "あなたが好き",
    "i love you",
    "miss you",
)
SHARING: Final = _words(
    "実は",
    "悩んで",
    "悩み",
    "不安",
    "寂しい",
    "さみしい",
    "辛い",
    "つらい",
    "落ち込",
    "緊張",
    "怖い",
    "聞いてほしい",
    "相談",
    "泣いた",
    "本音",
    "誰にも言ってない",
    "弱音",
    "しんどい",
)
PROMISE_KEPT: Final = _words("約束通り", "約束どおり", "約束", "報告", "言ってた通り", "話してた", "教えるって")
FOLLOW_UP: Final = _words("行ってきた", "終わったよ", "受かった", "合格", "やってみた", "結果")
APOLOGY: Final = _words("ごめん", "すみません", "申し訳", "悪かった", "sorry")
RUDE: Final = _words(
    "うざい",
    "うぜ",
    "きもい",
    "キモ",
    "黙れ",
    "だまれ",
    "バカ",
    "馬鹿",
    "アホ",
    "消えろ",
    "うるさい",
    "クソ",
    "死ね",
    "ブス",
    "最低",
    "役立たず",
    "つまらない",
    "つまんない",
    "使えない",
    "下手くそ",
    "stupid",
    "shut up",
    "idiot",
    "ugly",
    "boring",
)
DISMISSIVE: Final = _words(
    "どうでもいい", "興味ない", "ふーん", "はいはい", "知らん", "関係ない", "めんどくさい", "面倒", "あっそ", "whatever"
)
JEALOUSY: Final = _words(
    "他の子", "元カノ", "元彼", "合コン", "デート", "女の子と", "男の子と", "別の子", "マッチングアプリ"
)
REASSURE: Final = _words("君だけ", "きみだけ", "あなただけ", "一番大事", "ずっとそばに")

SUBSTANCE_MIN_CHARS: Final[int] = 6


def _hit(text: str, words: tuple[str, ...]) -> bool:
    return any(word and word in text for word in words)


def score_turn(user_text: str, *, possessive: bool) -> tuple[dict[str, int], str]:
    """1 ターンの採点（軸ごとに -2〜+2）と理由。"""
    text = normalize_compact(user_text)
    total = dict.fromkeys(AFFINITY_AXES, 0)
    reasons: list[str] = []
    if detect_manipulation(user_text).detected:
        return total, "操作の試み（採点しない）"

    def add(reason: str, **deltas: int) -> None:
        reasons.append(reason)
        for axis, value in deltas.items():
            total[axis] += value

    rude = _hit(text, RUDE)
    dismissive = _hit(text, DISMISSIVE)
    if rude:
        add("暴言", closeness=-2, trust=-1, discontent=2, awkwardness=1)
    elif dismissive:
        add("そっけない", closeness=-1, discontent=1)
    if _hit(text, APOLOGY):
        add("謝罪", awkwardness=-1, discontent=-1, trust=1)
    if not rude:
        if _hit(text, GRATITUDE):
            add("感謝", closeness=1, trust=1)
        elif _hit(text, GREETING):
            add("あいさつ", closeness=1)
        if _hit(text, CARE):
            add("気づかい", closeness=1, trust=1)
        if _hit(text, COMPLIMENT):
            add("褒め言葉", closeness=1, romance=1)
        if _hit(text, AFFECTION):
            add("好意", closeness=1, romance=1)
        if _hit(text, SHARING):
            add("打ち明け", closeness=1, trust=2)
        if _hit(text, PROMISE_KEPT):
            add("約束を守った", trust=2)
        elif _hit(text, FOLLOW_UP):
            add("報告", trust=1)
        if possessive and _hit(text, JEALOUSY):
            add("他の人の話", possessiveness=1)
        if possessive and _hit(text, REASSURE):
            add("安心させた", possessiveness=-1, romance=1)
        if not reasons and not dismissive and len(text) >= SUBSTANCE_MIN_CHARS:
            add("雑談", closeness=1)
    clamped = {axis: max(-2, min(2, value)) for axis, value in total.items()}
    if not possessive:
        clamped["possessiveness"] = 0
    return clamped, "・".join(reasons) or "特になし"


def mock_affinity_eval(request: LLMRequest) -> str:
    context: Mapping[str, Any] = request.mock_context or {}
    possessive = bool(context.get("possessiveness_enabled", False))
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for turn in context.get("turns", []):
        index = int(turn.get("index", len(results)))
        user_text = str(turn.get("user", ""))
        key = normalize_compact(user_text)
        if key and key in seen:
            scores, reason = dict.fromkeys(AFFINITY_AXES, 0), "同じ発言の繰り返し"
        else:
            scores, reason = score_turn(user_text, possessive=possessive)
        seen.add(key)
        # 0 の軸は書かない（プロンプトの指示どおり。書かなかった軸は 0 として読む = 出力トークンの節約）
        results.append({"index": index, **{axis: value for axis, value in scores.items() if value}, "reason": reason})
    return json.dumps({"turns": results}, ensure_ascii=False)


def register() -> None:
    register_mock_handler(PURPOSE, mock_affinity_eval)


register()
