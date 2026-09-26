"""MockLLM の `proactive_message` ハンドラ（決定的。LLM_MODE=mock・評価ハーネス用）。

きっかけの文脈（request.mock_context）から、キャラの自発メッセージを 1 通作る。例:
- 約束の期日（前）: 「今日だよね、面接。いつも通りでいいんだよ。終わったら教えてね」
- 約束の期日（後）: 「面接、どうだった？落ち着いたらでいいから、聞かせてね」
- 予定が終わった（相手からメッセージが来ていた）: 「ただいま〜。さっきはごめんね、ちゃんと返せなくて」
- しばらく話していない: 責めない・催促しない文面
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app.engine.proactive.message import PURPOSE
from app.services.llm import LLMRequest, register_mock_handler

_SEASONAL_LINES: Final[dict[str, str]] = {
    "new_year": "あけましておめでとう！今年もよろしくね",
    "setsubun": "今日は節分だね。恵方巻き食べた？",
    "valentine": "ハッピーバレンタイン。今日は甘いもの食べた？",
    "white_day": "今日はホワイトデーだね。{call}は甘いもの好き？",
    "hanami": "桜、咲いてきたね。{call}はお花見行った？",
    "golden_week": "ゴールデンウィークだね。{call}はどこか出かける？",
    "tsuyu": "梅雨入りしたね。{call}は雨の日なにしてる？",
    "tanabata": "今日は七夕だね。{call}は何かお願いごとした？",
    "summer_festival": "夏祭りの季節だね。{call}は花火とか見に行く？",
    "obon": "お盆だね。{call}はゆっくりできてる？",
    "tsukimi": "今夜は月がきれいだよ。ちょっとだけ空見てみて",
    "halloween": "ハッピーハロウィン！{call}は仮装とかする派？",
    "autumn_leaves": "紅葉がきれいな季節だね。{call}の近くも色づいてる？",
    "christmas": "メリークリスマス！{call}はどう過ごしてる？",
    "year_end": "今年もあと少しだね。{call}は年末どう過ごすの？",
}


def _promise_text(context: Mapping[str, Any]) -> str:
    topic = str(context.get("promise_topic") or "予定")
    is_action = bool(context.get("topic_is_action"))
    if context.get("phase") == "after":
        if is_action:
            return f"今日{topic}の、どうだった？落ち着いたらでいいから、聞かせてね"
        return f"{topic}、どうだった？落ち着いたらでいいから、聞かせてね"
    if is_action:
        return f"今日は{topic}日だよね。楽しんできてね。あとで話聞かせて"
    return f"今日だよね、{topic}。いつも通りでいいんだよ。終わったら教えてね"


def _calendar_text(context: Mapping[str, Any], call: str) -> str:
    if context.get("user_recently_messaged"):
        return "ただいま〜。さっきはごめんね、ちゃんと返せなくて"
    title = str(context.get("event_title") or "おでかけ")
    return f"ただいま〜。{title}、楽しかった！{call}は今日どうだった？"


def mock_proactive_message(request: LLMRequest) -> str:
    context: Mapping[str, Any] = request.mock_context or {}
    trigger = str(context.get("trigger", ""))
    call = str(context.get("call_user") or "きみ")
    if trigger == "promise_due":
        return _promise_text(context)
    if trigger == "calendar_event":
        return _calendar_text(context, call)
    if trigger == "seasonal":
        key = str(context.get("seasonal_key", ""))
        label = str(context.get("seasonal_label") or "今日")
        line = _SEASONAL_LINES.get(key, "{label}だね。{call}はどう過ごしてる？")
        return line.format(call=call, label=label)
    if trigger == "inactivity":
        return f"最近どうしてる？ふと{call}のこと思い出して。忙しかったら返事はいつでも大丈夫だよ"
    if trigger == "feed_post":
        caption = str(context.get("post_caption") or "").strip()
        about = f"「{caption[:30]}」って" if caption else ""
        return f"さっき写真あげたんだ。{about}よかったら見てね"
    if trigger == "paid_notice":
        return "お知らせ：新しい投稿を公開しました。気が向いたときに見てみてね"
    return f"{call}、元気にしてる？"


def register() -> None:
    register_mock_handler(PURPOSE, mock_proactive_message)


register()
