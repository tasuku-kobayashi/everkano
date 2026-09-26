"""カレンダーのテスト用ペルソナ（engine セクション付き）。実在人物・未成年要素なし。

- cal_office : 平日（月〜金）9:30〜18:30 の会社員。睡眠は 24 時台開始の書き方（前日の予定として生成）
- cal_night  : 土日に働く夜型。日をまたぐ単発（カラオケ 22:00〜翌2:00）、平日が休み
- cal_early  : 早起き（21:30〜翌5:00 の日またぎ睡眠）、木〜月の早番。min_interval / months 付きのテンプレート
"""

from __future__ import annotations

from typing import Any

from app.services.persona import Persona


def _stage(call: str) -> dict[str, Any]:
    return {
        "call_user": f"{{name}}{call}",
        "call_user_fallback": "きみ",
        "tone": "やわらかい",
        "affection": "控えめ",
        "topics": ["今日のこと"],
        "examples": ["おつかれさま", "今日はどうだった？"],
        "proactive_frequency": 1.0,
    }


def _base(key: str, name: str, handle: str, schedule_pattern: str, emoji: str = "控えめ（1つまで）") -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "handle": handle,
        "archetype": "テスト",
        "age": 26,
        "avatar": "https://placehold.co/400x400",
        "bio": f"{name}のテスト用プロフィール。",
        "profile": f"{name}はテスト用のキャラクター。",
        "speech": {
            "tone": "やわらかい",
            "sentence_length": "中程度",
            "emoji": emoji,
            "first_person": "わたし",
            "second_person": "きみ",
            "ng_words": ["きもい"],
            "examples": ["おつかれさま", "今日もがんばったね", "ふふ", "そうなんだ", "またね"],
        },
        "relationship": {"initial": "友達", "progression": "少しずつ近づく"},
        "schedule_pattern": schedule_pattern,
        "memory_focus": ["仕事"],
        "greeting": "はじめまして",
        "comment_style": "短く",
    }


def _engine(life: dict[str, Any], seasonal: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "affinity": {"sensitivity": {}, "notes": "ふつう"},
        "stages": {s: _stage("さん") for s in ("acquaintance", "friend", "close", "lover")},
        "life": life,
        "seasonal": seasonal,
        "proactive": {
            "frequency": 1.0,
            "triggers": ["calendar_event", "promise_due"],
            "style": "やさしく",
            "inactivity_days": 3,
            "examples": ["おつかれさま", "今日はどうだった？"],
        },
    }


def _seasonal(key: str, attends: bool = False, **extra: Any) -> dict[str, Any]:
    return {"key": key, "reaction": f"{key}が好き", "attends": attends, **extra}


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]


def office_persona() -> Persona:
    data = _base(
        "cal_office", "カレン", "cal_karen", "平日: 7:00起床 / 9:30出社 / 18:30退社 / 24:00就寝\n休日: 昼まで寝る"
    )
    data["engine"] = _engine(
        {
            "occupation": "会社員（広告）",
            "workplace": "渋谷のオフィス",
            "home": "中野の自宅",
            "birthday": "10-02",
            "hobbies": ["読書", "カフェめぐり"],
            "friends": [{"name": "ヨル", "relation": "大学の友達"}],
            "places": [{"name": "駅前のカフェ", "kind": "cafe"}],
            "routine": [
                {
                    "days": ["sun", "mon", "tue", "wed", "thu"],
                    "start": "24:00",
                    "end": "07:00",
                    "activity": "睡眠",
                    "location": "中野の自宅",
                    "busyness": 3,
                    "status_label": "おやすみ中",
                },
                {
                    "days": ["fri", "sat"],
                    "start": "24:30",
                    "end": "09:30",
                    "activity": "睡眠",
                    "location": "中野の自宅",
                    "busyness": 3,
                    "status_label": "おやすみ中",
                },
                {
                    "days": WEEKDAYS,
                    "start": "09:30",
                    "end": "18:30",
                    "activity": "仕事",
                    "location": "渋谷のオフィス",
                    "busyness": 2,
                    "mood": "集中している",
                    "status_label": "仕事中",
                },
                {
                    "days": WEEKDAYS,
                    "start": "18:30",
                    "end": "19:30",
                    "activity": "帰り道",
                    "location": "電車",
                    "busyness": 1,
                    "status_label": "帰宅中",
                },
                {
                    "days": ["sat", "sun"],
                    "start": "13:00",
                    "end": "16:00",
                    "activity": "カフェで読書",
                    "location": "駅前のカフェ",
                    "busyness": 0,
                    "status_label": "カフェ",
                    "post_tags": ["cafe", "book"],
                    "post_probability": 0.3,
                },
            ],
            "events": [
                {
                    "key": "friday_drinks",
                    "title": "同期と飲み会",
                    "location": "新宿の居酒屋",
                    "days": ["fri"],
                    "start": "19:30",
                    "end": "23:00",
                    "weekly_probability": 0.6,
                    "busyness": 2,
                    "mood": "ほろ酔いで上機嫌",
                    "status_label": "飲み会中",
                    "post_tags": ["izakaya"],
                    "post_probability": 0.9,
                },
                {
                    "key": "lunch_with_yoru",
                    "title": "ヨルとランチ",
                    "description": "大学の友達のヨルと久しぶりにランチ",
                    "location": "表参道のカフェ",
                    "days": ["sat", "sun"],
                    "start": "11:30",
                    "end": "13:30",
                    "weekly_probability": 0.4,
                    "busyness": 1,
                    "mood": "楽しい",
                    "status_label": "ランチ中",
                    "post_tags": ["cafe", "food"],
                    "post_probability": 0.7,
                },
                {
                    "key": "catch_cold",
                    "title": "風邪で寝込む",
                    "location": "中野の自宅",
                    "days": ["sat", "sun"],
                    "start": "10:00",
                    "end": "18:00",
                    "weekly_probability": 0.05,
                    "busyness": 2,
                    "mood": "だるい",
                    "status_label": "療養中",
                    "min_interval_days": 30,
                },
                {
                    "key": "overtime",
                    "title": "残業",
                    "location": "渋谷のオフィス",
                    "days": ["tue", "wed", "thu"],
                    "start": "18:30",
                    "end": "21:30",
                    "weekly_probability": 0.5,
                    "busyness": 2,
                    "mood": "ちょっと疲れた",
                    "status_label": "残業中",
                },
                {
                    "key": "weekend_trip",
                    "title": "鎌倉へ日帰り旅行",
                    "location": "鎌倉",
                    "days": ["sat"],
                    "start": "09:30",
                    "end": "19:00",
                    "weekly_probability": 0.5,
                    "busyness": 1,
                    "mood": "わくわく",
                    "status_label": "おでかけ中",
                    "min_interval_days": 28,
                    "post_tags": ["travel", "sea"],
                    "post_probability": 1.0,
                },
                {
                    "key": "night_live",
                    "title": "好きなバンドのライブ",
                    "location": "渋谷のライブハウス",
                    "days": ["fri", "sat"],
                    "start": "18:00",
                    "end": "21:30",
                    "weekly_probability": 0.3,
                    "busyness": 3,
                    "mood": "最高にテンションが高い",
                    "status_label": "ライブ中",
                    "months": [6, 7, 12],
                    "post_tags": ["live", "music"],
                    "post_probability": 0.8,
                },
            ],
            "default_activity": {
                "activity": "家でのんびり",
                "location": "中野の自宅",
                "status_label": "のんびり中",
                "busyness": 0,
            },
        },
        [
            _seasonal(
                "hanami",
                True,
                title="お花見",
                location="井の頭公園",
                start="13:00",
                end="16:00",
                post_tags=["sakura"],
                post_probability=1.0,
            ),
            _seasonal(
                "summer_festival",
                True,
                title="花火大会",
                location="隅田川",
                start="18:00",
                end="21:30",
                post_tags=["fireworks"],
                post_probability=1.0,
            ),
            _seasonal("tsukimi", True),
            _seasonal(
                "christmas",
                True,
                title="クリスマスマーケット",
                location="日比谷公園",
                start="18:00",
                end="21:00",
                post_tags=["christmas"],
                post_probability=1.0,
            ),
            _seasonal("new_year", True, title="初詣", location="近所の神社"),
            _seasonal("tsuyu"),
            _seasonal("halloween"),
        ],
    )
    return Persona.model_validate(data)


def night_persona() -> Persona:
    data = _base(
        "cal_night",
        "ヨル",
        "cal_yoru",
        "休日（月〜水）: 11:00起床 / 夜はゲーム\n平日（木〜日）: 13:00-22:00 バーでバイト / 3:00就寝",
        emoji="多め（🌙✨🎮 など）",
    )
    data["engine"] = _engine(
        {
            "occupation": "バーテンダー見習い",
            "home": "高円寺のアパート",
            "birthday": "02-29",
            "hobbies": ["ゲーム", "カラオケ"],
            "friends": [{"name": "カレン", "relation": "大学の友達"}],
            "places": [{"name": "高円寺のバー", "kind": "bar"}],
            "routine": [
                {
                    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                    "start": "03:00",
                    "end": "11:00",
                    "activity": "睡眠",
                    "location": "高円寺のアパート",
                    "busyness": 3,
                    "status_label": "おやすみ中",
                },
                {
                    "days": ["thu", "fri", "sat", "sun"],
                    "start": "14:00",
                    "end": "23:30",
                    "activity": "バーで仕事",
                    "location": "高円寺のバー",
                    "busyness": 2,
                    "status_label": "仕事中",
                    "post_tags": ["bar"],
                    "post_probability": 0.2,
                },
                {
                    "days": ["mon", "tue", "wed"],
                    "start": "20:00",
                    "end": "24:30",
                    "activity": "ゲーム",
                    "location": "高円寺のアパート",
                    "busyness": 1,
                    "status_label": "ゲーム中",
                    "post_tags": ["game"],
                    "post_probability": 0.2,
                },
            ],
            "events": [
                {
                    "key": "karaoke_night",
                    "title": "カラオケオール",
                    "location": "高円寺のカラオケ",
                    "days": ["mon", "tue"],
                    "start": "22:00",
                    "end": "02:00",
                    "weekly_probability": 0.5,
                    "busyness": 2,
                    "mood": "喉が枯れそう",
                    "status_label": "カラオケ中",
                    "post_tags": ["karaoke"],
                    "post_probability": 0.8,
                },
                {
                    "key": "lunch_with_karen",
                    "title": "カレンとランチ",
                    "location": "新宿のカフェ",
                    "days": ["mon", "tue", "wed"],
                    "start": "12:00",
                    "end": "14:00",
                    "weekly_probability": 0.3,
                    "busyness": 1,
                    "mood": "うれしい",
                    "status_label": "ランチ中",
                    "post_tags": ["cafe"],
                    "post_probability": 0.5,
                },
                {
                    "key": "record_shop",
                    "title": "レコード屋めぐり",
                    "location": "下北沢",
                    "days": ["wed"],
                    "start": "13:00",
                    "end": "17:00",
                    "weekly_probability": 0.4,
                    "busyness": 0,
                    "mood": "わくわく",
                    "status_label": "おでかけ中",
                    "post_tags": ["music", "shopping"],
                    "post_probability": 0.6,
                },
                {
                    "key": "late_shift",
                    "title": "イベントの手伝いで遅番",
                    "location": "高円寺のバー",
                    "days": ["sat"],
                    "start": "23:30",
                    "end": "04:00",
                    "weekly_probability": 0.3,
                    "busyness": 3,
                    "mood": "眠いけど楽しい",
                    "status_label": "仕事中",
                },
                {
                    "key": "morning_run",
                    "title": "朝のランニング",
                    "location": "善福寺川",
                    "days": ["tue", "wed"],
                    "start": "10:00",
                    "end": "11:30",
                    "weekly_probability": 0.3,
                    "busyness": 1,
                    "mood": "すっきり",
                    "status_label": "ランニング中",
                    "post_tags": ["running"],
                    "post_probability": 0.4,
                },
            ],
            "default_activity": {
                "activity": "部屋でだらだら",
                "location": "高円寺のアパート",
                "status_label": "だらだら中",
                "busyness": 0,
            },
        },
        [
            _seasonal(
                "halloween",
                True,
                title="ハロウィンのバーイベント",
                location="高円寺のバー",
                start="20:00",
                end="24:00",
                post_tags=["halloween"],
                post_probability=1.0,
            ),
            _seasonal("summer_festival", True),
            _seasonal(
                "year_end", True, title="年越しカラオケ", location="高円寺のカラオケ", start="22:00", end="02:00"
            ),
            _seasonal("valentine"),
            _seasonal("hanami"),
            _seasonal("christmas"),
        ],
    )
    return Persona.model_validate(data)


def early_persona() -> Persona:
    data = _base(
        "cal_early",
        "アサ",
        "cal_asa",
        "平日（木〜月）: 5:00起床 / 6:00-15:00 パン屋で早番 / 21:30就寝\n休日（火曜・水曜）: 昼まで寝る",
        emoji="ほとんど使わない",
    )
    data["engine"] = _engine(
        {
            "occupation": "パン職人",
            "home": "下町の実家",
            "hobbies": ["散歩"],
            "places": [{"name": "商店街のパン屋", "kind": "bakery"}],
            "routine": [
                {
                    "days": ["wed", "thu", "fri", "sat", "sun"],
                    "start": "21:30",
                    "end": "05:00",
                    "activity": "睡眠",
                    "location": "下町の実家",
                    "busyness": 3,
                    "status_label": "おやすみ中",
                },
                {
                    "days": ["mon", "tue"],
                    "start": "23:30",
                    "end": "08:00",
                    "activity": "睡眠",
                    "location": "下町の実家",
                    "busyness": 3,
                    "status_label": "おやすみ中",
                },
                {
                    "days": ["thu", "fri", "sat", "sun", "mon"],
                    "start": "06:00",
                    "end": "15:00",
                    "activity": "パン屋で早番",
                    "location": "商店街のパン屋",
                    "busyness": 2,
                    "status_label": "仕事中",
                    "post_tags": ["food"],
                    "post_probability": 0.1,
                },
            ],
            "events": [
                {
                    "key": "hot_spring",
                    "title": "日帰り温泉",
                    "location": "箱根",
                    "days": ["tue", "wed"],
                    "start": "10:00",
                    "end": "19:00",
                    "weekly_probability": 0.9,
                    "busyness": 1,
                    "mood": "ぽかぽか",
                    "status_label": "おでかけ中",
                    "min_interval_days": 21,
                    "post_tags": ["travel", "mountain"],
                    "post_probability": 1.0,
                },
                {
                    "key": "baking_class",
                    "title": "パン教室の講師",
                    "location": "公民館",
                    "days": ["tue"],
                    "start": "13:00",
                    "end": "16:00",
                    "weekly_probability": 0.5,
                    "busyness": 2,
                    "mood": "張り切っている",
                    "status_label": "講師中",
                },
                {
                    "key": "sakura_walk",
                    "title": "川沿いの桜並木を散歩",
                    "location": "隅田川沿い",
                    "days": ["tue", "wed"],
                    "start": "16:00",
                    "end": "18:00",
                    "weekly_probability": 1.0,
                    "busyness": 0,
                    "mood": "しあわせ",
                    "status_label": "散歩中",
                    "months": [3, 4],
                    "post_tags": ["sakura"],
                    "post_probability": 1.0,
                },
                {
                    "key": "dentist",
                    "title": "歯医者",
                    "location": "駅前の歯科",
                    "days": ["wed"],
                    "start": "11:00",
                    "end": "12:00",
                    "weekly_probability": 0.2,
                    "busyness": 1,
                    "mood": "ちょっと憂うつ",
                    "status_label": "歯医者",
                },
                {
                    "key": "market",
                    "title": "朝市で買い出し",
                    "location": "朝市",
                    "days": ["mon"],
                    "start": "04:00",
                    "end": "05:30",
                    "weekly_probability": 0.5,
                    "busyness": 1,
                    "mood": "眠い",
                    "status_label": "買い出し中",
                },
            ],
            "default_activity": {
                "activity": "家でのんびり",
                "location": "下町の実家",
                "status_label": "のんびり中",
                "busyness": 0,
            },
        },
        [
            _seasonal("hanami", True, post_tags=["sakura"], post_probability=0.5),
            _seasonal(
                "autumn_leaves",
                True,
                title="紅葉を見に高尾山へ",
                location="高尾山",
                start="09:00",
                end="15:00",
                post_tags=["autumn_leaves"],
                post_probability=1.0,
            ),
            _seasonal("golden_week", True),
            _seasonal("obon", True, title="お墓参り", location="菩提寺", start="10:00", end="12:00"),
            _seasonal("tsukimi", True, title="お月見団子づくり", location="下町の実家", start="19:00", end="20:30"),
            _seasonal("setsubun", True),
        ],
    )
    return Persona.model_validate(data)


def all_fixture_personas() -> list[Persona]:
    return [office_persona(), night_persona(), early_persona()]
