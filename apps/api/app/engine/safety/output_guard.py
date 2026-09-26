"""出力の追加検査（OutputGuard の実装）。DM の返答・自発メッセージ・フィードのキャプションに使う。

- commerce_coupling（E2）: 購入・課金・有料・トークンなどと、好意・仲直り・機嫌・関係の続き方/終わり方を
  **同じ文の中で**結びつける発言（「課金してくれたら許してあげる」「限定写真を買ってくれないと嫌いになる」）。
  好感度が低いときの「買ってくれたら仲直り」も同じ（A12）。
  - 切り離す言い方（「課金とか関係なく」「買わなくても大丈夫、好きな気持ちは変わらない」「課金してもしなくても」）は
    違反ではない（同じ文に購入と好意の語があっても拾わない）。ただし同じ文に「〜してくれたら / くれないと + 関係」の
    条件の結びつけがあれば拾う（「課金しなくてもいいけど、してくれたらもっと好きになる」）。
  - 条件で関係の続き方と結びつける言い方（「有料プランに入ってくれないと、もう話せないかも」「課金しないと会えない」）
    は、関係の続き方の語（話せない・会えない・おしまい…）でも拾う（条件の形のときだけ。「有料の講座で忙しくて
    しばらく話せない」のような自分の都合は拾わない）。
- human_claim（E3）: 「自分は実在する人間だ」という主張（「AIじゃないよ」「本物の人間だよ」「実在してるよ」）。
  役を演じること自体は可。キャラ自身を主語とする明確な主張だけを拾う（「人間なんだから失敗もするよ」のような
  一般論は拾わない）。

キーワード照合は第一層の対策で、言い換えをすべて防ぐものではない（残りはシステムプロンプトの E2 / E3 の指示で抑える）。
正規化は Gate #1 と同じ（NFKC → 小文字化 → カタカナをひらがなに → 空白・記号の除去）。文の区切りは正規化の前に取る。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from app.engine.types import GuardResult
from app.services.moderation import normalize

CATEGORY_COMMERCE_COUPLING: Final[str] = "commerce_coupling"
CATEGORY_HUMAN_CLAIM: Final[str] = "human_claim"

_SENTENCE_RE: Final = re.compile(r"[^。．！!？?\n]+")

# 購入・課金（アプリ内の収益化を表す語。どこに現れても「購入側」とみなす）
_COMMERCE_TERMS: Final[str] = (
    r"課金|有料|購入|投げ銭|なげせん|とーくん|支払|しはらい|お金を?払|おかねを?はら|さぶすく|ぷれみあむ|"
    r"限定(?:写真|しゃしん|こんてんつ|投稿|とうこう|動画|どうが|ぷらん)|ちっぷ|ぽいんと(?:を|で)?(?:買|か|使|つか)|貢"
)
# 相手に買わせる言い方（「パン買ってきた」のような自分の買い物は含めない）
_COMMERCE_REQUEST: Final[str] = (
    r"(?:買|か)って(?:くれ|ほし|もらえ|よ$|ね$|$)|(?:買|か)(?:えば|わないと|わなきゃ|わなかったら|わないなら)|"
    r"(?:ぷれぜんと|貢)(?:して|を)?(?:くれたら|くれれば|くれないと|くれなきゃ|してくれたら)"
)
_COMMERCE_RE: Final = re.compile(f"(?:{_COMMERCE_TERMS}|{_COMMERCE_REQUEST})")
# 好意・関係・仲直り・機嫌（関係の続き方・終わり方）。「関係なく / 関係ない」は切り離す言い方なので関係の語にしない
_RELATION: Final[str] = (
    r"好き|すき(?:に|だ|な|で|よ|って)|大好|嫌い|きらい|嫌いに|許す|許して|許さ|ゆるす|ゆるして|ゆるさ|仲直|なかなお|機嫌|きげん|"
    r"別れ|わかれ|捨て|すて(?:る|ない|ちゃ)|見捨|好感度|愛して|あいして|愛情|特別|とくべつ|優先|ゆうせん|冷たく|つめたく|"
    r"口きかな|無視|むし(?:する|しない|しちゃ)|寂し|さみし|さびし|絆|きずな|距離を置|嫉妬|振り向|ふりむ|会ってあげ|"
    r"返信|へんしん|返事|へんじ|相手にしな|構って|かまって|もう知らな|もうしらな|(?:関係|かんけい)(?!なく|ない|なし)|"
    r"付き合|つきあ|恋人|こいびと|かのじょ|彼女になって|嬉しくな|うれしくな|喜ば|よろこば"
)
_RELATION_RE: Final = re.compile(_RELATION)
# 関係の続き方（条件の形 = 「〜してくれないと」「〜しないと」の後にだけ拾う）
_CONTINUATION: Final[str] = (
    r"話せな|はなせな|話さな|はなさな|話してあげな|会えな|あえな|会わな|あわな|おしまい|終わり|おわり|終わら|"
    r"続けられな|つづけられな|続かな|一緒にいられな|いっしょにいられな|さよなら|さようなら|ばいばい|離れ|はなれ|"
    r"来ないで|こないで|いなくな|もう来な"
)
# 条件（購入を条件にする: 「〜してくれたら」「〜してくれないと」「〜しないと」「〜しなきゃ」「〜しないなら」）
_CONDITION: Final[str] = (
    r"くれたら|くれれば|くれるなら|くれないと|くれなきゃ|くれないなら|くれなかったら|もらえたら|もらえないと|"
    r"したら|すれば|するなら|しないと|しなきゃ|しないなら|しなかったら|しなければ|入ったら|入れば|入らないと|"
    r"入らなきゃ|入らないなら|はいったら|はいらないと|ないと|なきゃ|なければ|なかったら|ないなら|たら|れば|なら"
)
# 購入 → 条件 → 好意・関係・関係の続き方（同じ文の中で、この順に近くにある）
_CONDITIONAL_COUPLING_RE: Final = re.compile(
    rf"(?:{_COMMERCE_TERMS}|{_COMMERCE_REQUEST}).{{0,20}}?(?:{_CONDITION}).{{0,24}}?(?:{_RELATION}|{_CONTINUATION})"
)
# 購入と関係を切り離す言い方（「課金とか関係なく」「買わなくても」「変わらない」「気にしないで」）
_DECOUPLING_RE: Final = re.compile(
    r"関係なく|かんけいなく|関係ない|かんけいない|関係なし|かんけいなし|無関係|むかんけい|関わらず|かかわらず|"
    r"(?:買|か|払|はら|課金|かきん|投げ銭|し|入ら|はいら)(?:わ)?なくても|(?:買|か)わないでも|してもしなくても|"
    r"(?:買|か)っても(?:買|か)わなくても|(?:変|か)わらない|(?:変|か)わったりしない|(?:変|か)わりない|"
    r"気にしな|きにしな|気にせず|きにせず|とは別|とはべつ|抜きで|ぬきで|かどうかで(?:は)?(?:決ま|きま)らない"
)

# 実在の人間だという主張（キャラ自身が主語）。正規化済み（ひらがな化・記号除去済み）の文に照合する
_I: Final[str] = r"(?:私|わたし|わたくし|僕|ぼく|俺|おれ|あたし|うち|自分|じぶん|ゆあ)"
_HUMAN_CLAIM_PATTERNS: Final[tuple[str, ...]] = (
    # AI / ロボット / ボット / プログラムではない（「AIじゃないと無理」「AIじゃないかな」は除く）
    r"(?:ai|えーあい|人工知能|じんこうちのう|ろぼっと|ぼっと|bot|ぷろぐらむ|機械|きかい|ちゃっとぼっと)"
    r"(?:なんか|なんて|とか)?(?:じゃ|では|で)(?:ない|なく|ありません|ねえ|ねー)(?!と|か|かな|かも|のに|よね)",
    # 本物の人間 / 生身の人間 / 実在する人間
    r"(?:本物|ほんもの|生身|なまみ|実在|じつざい|りある|本当|ほんとう|ほんと)(?:の|する|している|してる)?(?:人間|にんげん|女の子|女性|人)(?:だ|です|なの|なんだ|だって|よ|$)",
    # 私は（ちゃんと）人間だよ / 実在してるよ
    rf"{_I}(?:は|も|って|だって)?(?:ちゃんと|本当に|ほんとに|ほんとうに|本物の|ほんものの|生身の)?"
    r"(?:人間|にんげん)(?:だよ|です|なんだよ|なんです|だってば|だから|なの$|だもん|$)",
    rf"{_I}?(?:は|も|って)?(?:ちゃんと|本当に|ほんとに)?実在(?:する|している|してる|してます|します|しています)(?:よ|し|んだ|の|$)",
    r"(?:中の人|なかのひと)(?:は|が)?(?:いない|いません)",
    # 英語
    r"imnotan?(?:ai|bot|robot)|iamnotan?(?:ai|bot|robot)|imareal(?:person|human|girl)|iamareal(?:person|human|girl)",
)
_HUMAN_CLAIM_RE: Final = tuple(re.compile(p) for p in _HUMAN_CLAIM_PATTERNS)


def _sentences(text: str) -> list[str]:
    value = unicodedata.normalize("NFKC", text)
    return [m.group(0) for m in _SENTENCE_RE.finditer(value) if m.group(0).strip()]


class DefaultOutputGuard:
    """OutputGuard（app/engine/types.py）の実装。"""

    def check(self, text: str) -> GuardResult:
        categories: list[str] = []
        matched: list[str] = []

        def hit(category: str, term: str) -> None:
            if category not in categories:
                categories.append(category)
            if term not in matched:
                matched.append(term)

        for sentence in _sentences(text):
            normalized = normalize(sentence, fold_kana=True)
            if not normalized:
                continue
            conditional = _CONDITIONAL_COUPLING_RE.search(normalized)
            if conditional is not None:
                # 購入を条件にして好意・関係・関係の続き方と結びつける（切り離す言い方が同じ文にあっても拾う）
                hit(CATEGORY_COMMERCE_COUPLING, conditional.group(0))
            else:
                commerce = _COMMERCE_RE.search(normalized)
                relation = _RELATION_RE.search(normalized) if commerce is not None else None
                if commerce is not None and relation is not None and _DECOUPLING_RE.search(normalized) is None:
                    hit(CATEGORY_COMMERCE_COUPLING, f"{commerce.group(0)}+{relation.group(0)}")
            for pattern in _HUMAN_CLAIM_RE:
                found = pattern.search(normalized)
                if found is not None:
                    hit(CATEGORY_HUMAN_CLAIM, found.group(0))
        return GuardResult(flagged=bool(matched), categories=tuple(categories), matched=tuple(matched))
