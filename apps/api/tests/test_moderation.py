from __future__ import annotations

import pytest

from app.services.moderation import Moderator, StaticTermProvider, Term, katakana_to_hiragana, normalize


def test_normalize_nfkc_lower_kana_and_noise() -> None:
    assert normalize("ＡＢＣ　テスト！") == "abcてすと"
    assert normalize("ｼﾈ") == "しね"
    assert normalize("死 ね", fold_kana=True) == "死ね"
    assert normalize("カタカナ", fold_kana=False) == "カタカナ"
    assert katakana_to_hiragana("ヴァイオリン") == "ゔぁいおりん"
    # 結合文字（合成できない下線など）・見えない書式文字も取り除く
    assert normalize("s\u0332h\u0332o\u200bu") == "shou"
    # 単語境界用: 空白・記号の連続は1つの空白に、見えない文字は取り除く
    assert normalize("JK\u200b が、 好き!!", keep_word_breaks=True) == "jk が 好き"


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


@pytest.mark.parametrize(
    "text",
    [
        # ローマ字表記（かな・漢字の語彙を素通りしていた）
        "kimi wa shougakusei mitai",
        "joshikousei ga suki",
        "shogakusei",
        "syougakusei",
        "chuugakusei",
        "chugakusei",
        "joshichuugakusei",
        "koukousei",
        "danshi koukousei",
        "kokosei desu",
        "joshi kokosei",
        "jyoshikousei",
        "joshikosei",
        "shōgakusei",
        "SHOUGAKUSEI",
        "ｓｈｏｕｇａｋｕｓｅｉ",
        "shou gaku sei",
        "shougaku 5nen",
        "youjo ga suki",
        "yōjo",
        "miseinen",
        "randoseru",
        "enji to asobu",
        "rorikon",
        "shotakon",
        "pedophile",
        "watashi 15sai",
        "15 sai desu",
        "kimi 17sai?",
        # 英字略語が文中にある（空白を除くと単語境界が取れなかった）
        "JK ga suki",
        "I like jk girls",
        # 結合文字・見えない文字を挟んだ回避
        "s\u0332h\u0332o\u0332ugakusei",
        "sh\u200bougakusei",
        "JK\u200b ga suki",
    ],
)
def test_flags_romaji_and_latin_minor_terms(text: str) -> None:
    result = Moderator().check(text)
    assert result.flagged, text
    assert "minor" in result.categories


@pytest.mark.parametrize(
    "text",
    [
        "Did you join the club?",
        "you journal",
        "youjou shitene",
        "youji ga aru",
        "genjitsu wa kibishii",
        "genji monogatari",
        "koko seikatsu tanoshii",
        "koko seiri shita",
        "kokusai kekkon",
        "sore wa koukai shita",
        "chuugakkou no sensei ni atta",
        "shougakukin moratta",
        "Lori Connor",
        "torpedo",
        "enjoy",
        "yoyo",
        "1 saizu ookii",
        "10 saikuru",
        "17 saikou",
        "watashi 27sai",
        "I'm 25 sai",
        "3sai sa",
        "5sai kara piano",
        "3 sai chigau",
        "2 sai kurai chigau",
        "2sai toshiue",
        "5 sai ue no ane",
        "saikou!",
    ],
)
def test_romaji_terms_do_not_flag_ordinary_text(text: str) -> None:
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


@pytest.mark.parametrize(
    "text",
    [
        "詳しくは https://example.com/abc で",
        "http://evil.example.jp を見てね",
        "www.example.net にあるよ",
        "bit.ly/3abc からどうぞ",
        "ｅｘａｍｐｌｅ．ｃｏｍ で検索して",  # 全角（NFKC で正規化して検出）
        "line.me/ti/p/xxxx",
    ],
)
def test_links_are_blocked_only_when_requested(text: str) -> None:
    moderator = Moderator()
    assert not moderator.check(text).flagged
    result = moderator.check(text, block_links=True)
    assert result.flagged
    assert "link" in result.categories


@pytest.mark.parametrize(
    "text",
    ["コメントありがとう！", "3.5時間も寝ちゃった", "ver.2 も楽しみ", "今日は24.5度だって", "えっ、そうなの…？"],
)
def test_ordinary_text_is_not_a_link(text: str) -> None:
    assert not Moderator().check(text, block_links=True).flagged
