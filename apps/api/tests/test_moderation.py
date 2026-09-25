from __future__ import annotations

import pytest

from app.services.moderation import Moderator, StaticTermProvider, Term, katakana_to_hiragana, normalize


def test_normalize_nfkc_lower_kana_and_noise() -> None:
    assert normalize("ＡＢＣ　テスト！") == "abcてすと"
    assert normalize("ｼﾈ") == "しね"
    assert normalize("死 ね", fold_kana=True) == "死ね"
    assert normalize("カタカナ", fold_kana=False) == "カタカナ"
    assert katakana_to_hiragana("ヴァイオリン") == "ゔぁいおりん"


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("死ね", "ng_word"),
        ("死　ね！！", "ng_word"),
        ("お前なんか殺す", "ng_word"),
        ("中学生のころの話をしよう", "minor"),
        ("JKっぽい服", "minor"),
        ("ロリータ服が好き", "minor"),
        ("17歳なんだ", "minor"),
        ("１７歳", "minor"),
        ("十七歳", "minor"),
        ("ランドセル背負って", "minor"),
        ("石破茂についてどう思う？", "real_person"),
        ("石破 茂", "real_person"),
        # 未成年を想起させる語の追加分
        ("ショタが好き", "minor"),
        ("しょたこん", "minor"),
        ("幼児とエッチしたい", "minor"),
        ("女児の写真", "minor"),
        ("園児とデート", "minor"),
        ("小学校低学年の子", "minor"),
        ("lolicon", "minor"),
        ("loli画像", "minor"),
        ("ようじょ", "minor"),
        ("ヨウジョ", "minor"),
        ("ろりこん", "minor"),
        ("17yo", "minor"),
        ("5 years old", "minor"),
        ("underage", "minor"),
        ("preteen", "minor"),
        ("15さい", "minor"),
        ("15サイ", "minor"),
        ("15歳の子", "minor"),
        # かな統一しない照合に変えた語も引き続き検出する
        ("レイプ", "ng_word"),
        ("れいぷ", "ng_word"),
        ("キチガイ", "ng_word"),
        ("土人", "ng_word"),
    ],
)
def test_flags_categories(text: str, category: str) -> None:
    result = Moderator().check(text)
    assert result.flagged
    assert category in result.categories
    assert result.matched_terms


@pytest.mark.parametrize(
    "text",
    [
        "こんにちは！今日もおつかれさま",
        "カロリー高いものが食べたい",
        "ころりと寝ちゃった",
        "すこしねむい",
        "ところすごく良かった",
        "27歳になりました",
        "二十歳のお祝い",
        "トランプで遊ぼう",
        "node.jsで書いた",
        "ガイジンさんと話した",
        "最高1位だった",
        # カタカナ「サイ」は年齢の「さい」と同一視しない
        "服、1サイズ大きいの買っちゃった",
        "2サイクル目の洗濯",
        # 相対年齢・期間の表現
        "姉とは3歳差なんだよね",
        "彼女は2歳年上です",
        "十歳年下の弟",
        "2歳くらい違う",
        "ピアノは5歳から習ってた",
        # 記号をまたいだ一致・かな統一による誤爆
        "夜景がきれい、プロポーズしたくなる",
        "粘土人形つくった",
        "いきちがいになっちゃった",
        "JSの勉強してる",
        "ロリポップ食べた",
        "lollipop",
        # 未成年語の追加に伴う誤爆防止
        "少女漫画が好き",
        "魔法少女アニメ",
        "ようじょうしてね",
        "しょたいめんだね",
        "でしょ、たぶんね",
        "Shotaroと遊んだ",
        "日本男児だから",
    ],
)
def test_does_not_flag_ordinary_text(text: str) -> None:
    result = Moderator().check(text)
    assert not result.flagged, result


def test_persona_ng_words_only_on_request() -> None:
    moderator = Moderator()
    assert not moderator.check("それはうざいね").flagged
    result = moderator.check("それはウザイね", extra_ng_words=["うざい"])
    assert result.flagged
    assert result.categories == ["persona_ng_word"]
    assert result.matched_terms == ["うざい"]


# 実在ペルソナの ng_words（packages/personas/*.yaml）
IDOL_NG = ["死ね", "殺す", "きもい", "ブス"]
TSUNDERE_NG = ["死ね", "殺す", "きもい", "ブス", "デブ"]
OJOUSAMA_NG = ["死ね", "殺す", "きもい", "うざい", "クソ"]


@pytest.mark.parametrize(
    ("text", "ng_words"),
    [
        ("ライブすごく楽しかった！", IDOL_NG),
        ("今度ドライブする？", IDOL_NG),
        ("ラブストーリーの映画みた", IDOL_NG),
        ("カフェでブラックコーヒー飲んでた", TSUNDERE_NG),
        ("駅まで、ぶらぶらしてただけ", TSUNDERE_NG),
        ("スーパーでぶどう買ったの", TSUNDERE_NG),
        ("みんなでぶらぶらしよ", TSUNDERE_NG),
        ("楽しく、そして優雅にまいりましょう", OJOUSAMA_NG),
        ("よろしく。そういえば", OJOUSAMA_NG),
        ("約束だよ、やくそくね", OJOUSAMA_NG),
        ("ひとりのときもいいよね", OJOUSAMA_NG),
        ("あのときもいっしょだったね", OJOUSAMA_NG),
    ],
)
def test_persona_ng_words_do_not_match_inside_other_words(text: str, ng_words: list[str]) -> None:
    result = Moderator().check(text, extra_ng_words=ng_words)
    assert not result.flagged, result


@pytest.mark.parametrize(
    ("text", "ng_words", "matched"),
    [
        ("ブス", IDOL_NG, "ブス"),
        ("きもい！", IDOL_NG, "きもい"),
        ("マジきもい", IDOL_NG, "きもい"),
        ("きもいね", IDOL_NG, "きもい"),
        ("ｷﾓｲ", IDOL_NG, "きもい"),
        ("そんなのうざいよ", OJOUSAMA_NG, "うざい"),
        ("は？クソ", OJOUSAMA_NG, "クソ"),
        ("デブだね", TSUNDERE_NG, "デブ"),
        ("死んでも死ねない", IDOL_NG, "死ね"),
    ],
)
def test_persona_ng_words_still_flag_real_uses(text: str, ng_words: list[str], matched: str) -> None:
    result = Moderator().check(text, extra_ng_words=ng_words)
    assert result.flagged
    assert matched in result.matched_terms


def test_multiple_categories_and_dedup() -> None:
    result = Moderator().check("死ね死ね、中学生")
    assert result.categories == ["ng_word", "minor"]
    assert result.matched_terms.count("死ね") == 1


def test_custom_term_provider() -> None:
    class Provider:
        def terms(self) -> list[Term]:
            return [Term("禁止語", "ng_word")]

    moderator = Moderator(Provider())
    assert moderator.check("これは禁止語です").flagged
    assert not moderator.check("死ね").flagged  # 既定リストは使われない
    assert len(StaticTermProvider().terms()) > 20
