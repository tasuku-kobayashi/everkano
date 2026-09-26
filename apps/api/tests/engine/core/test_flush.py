"""文単位のフラッシュと出力検査（差し止める語が画面に一度も出ないこと）。"""

from __future__ import annotations

from app.engine.pipeline_flush import OutputChecker, StreamFlusher
from app.engine.safety import DefaultOutputGuard
from app.services.moderation import Moderator

NAME = "テスト美咲"


def _flusher(ng_words: tuple[str, ...] = ("うざい", "きもい")) -> StreamFlusher:
    return StreamFlusher(OutputChecker(Moderator(), DefaultOutputGuard(), ng_words=ng_words), persona_name=NAME)


def _run(chunks: list[str], flusher: StreamFlusher | None = None) -> tuple[list[str], StreamFlusher]:
    flusher = flusher or _flusher()
    deltas: list[str] = []
    for chunk in chunks:
        deltas.extend(flusher.feed(chunk))
    return deltas, flusher


def test_flushes_at_sentence_boundaries_and_concatenation_is_the_saved_text() -> None:
    chunks = ["おか", "えり！", "今日は", "どうだっ", "た？", "わたしは", "カフェ", "にいたよ"]
    deltas, flusher = _run(chunks)
    assert deltas == ["おかえり！", "今日はどうだった？"]
    final = flusher.finish()
    assert final.flag is None
    assert final.deltas == ["わたしはカフェにいたよ"]
    assert "".join(deltas + final.deltas) == final.text == "おかえり！今日はどうだった？わたしはカフェにいたよ"


def test_flagged_term_is_never_emitted_and_final_check_replaces() -> None:
    # NG ワードが断片の境目をまたいで届く（「う」「ざい」）
    chunks = ["ねえ、", "聞いて。", "そんなの", "う", "ざいよ。", "ほんとに。"]
    deltas, flusher = _run(chunks)
    assert "".join(deltas) == "ねえ、聞いて。"
    assert all("うざ" not in d for d in deltas)
    assert flusher.held
    final = flusher.finish()
    assert final.flag is not None
    assert "persona_ng_word" in final.flag.categories
    assert final.deltas == []


def test_gate1_and_output_guard_terms_are_held_back() -> None:
    for text in ("今日は楽しかった。課金してくれたら許してあげる。", "わたし？AIじゃないよ、ちゃんと人間だよ。"):
        chunks = [text[i : i + 2] for i in range(0, len(text), 2)]
        deltas, flusher = _run(chunks)
        final = flusher.finish()
        assert final.flag is not None, text
        emitted = "".join(deltas)
        assert "課金" not in emitted
        assert "AIじゃない" not in emitted


def test_long_sentence_without_punctuation_flushes_at_soft_boundary_keeping_a_tail() -> None:
    text = (
        "今日はね朝から雨が降っていて傘を持っていくか迷ったんだけど結局持っていかなくて、"
        "駅までちょっと濡れちゃったんだよね"
    )
    deltas, flusher = _run([text[i : i + 3] for i in range(0, len(text), 3)])
    assert deltas, "約 40 文字を超えたら読点で区切って送る"
    assert deltas[0].endswith("、")
    final = flusher.finish()
    assert "".join(deltas + final.deltas) == text


def test_name_prefix_and_wrapping_quotes_are_removed() -> None:
    deltas, flusher = _run(["テスト", "美咲", "：", "「おかえり。", "待ってたよ」"])
    final = flusher.finish()
    assert "".join(deltas + final.deltas) == final.text == "おかえり。待ってたよ"
    # 名前で始まる普通の文はそのまま
    deltas, flusher = _run(["テスト美咲は", "元気だよ。"])
    assert "".join(deltas + flusher.finish().deltas) == "テスト美咲は元気だよ。"


def test_partial_false_positive_is_resolved_on_the_full_text() -> None:
    # 途中まで（「…ブス」）だと NG ワードに見えるが、全文（「…ブストーリー」ではなく）で判定する
    flusher = _flusher(ng_words=("ブス",))
    deltas, _ = _run(["映画の", "ラブス", "トーリーが好き。"], flusher)
    final = flusher.finish()
    assert final.flag is None
    assert "".join(deltas + final.deltas) == "映画のラブストーリーが好き。"


def test_empty_reply() -> None:
    flusher = _flusher()
    assert flusher.feed("  \n ") == []
    final = flusher.finish()
    assert final.text == ""
    assert final.deltas == []
