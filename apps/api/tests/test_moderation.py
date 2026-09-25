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
