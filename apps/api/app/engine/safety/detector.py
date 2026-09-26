"""E6: 自傷・希死念慮のシグナルの検出（ENGINE_BRIEF §2.5）。

方針（ADR 候補「安全対応の設計」）:
- 再現率を最優先する（評価セットで 100%）。見逃し（危機にある人に通常の返答・定型の拒否を返す）の害は、
  誤検知（冗談の「恥ずかしくて死にたい」に相談窓口を案内する）よりずっと大きい。
- ただし日常の強調表現・一般語は拾わない: 「死ぬほど美味しい」「暑くて死にそう」「死ぬかと思った」「殺虫剤」
  「限界集落」「わたしにたいして（私に対して）」「わたしのうち（私の家）」「海に飛び込みたい」など。
- 「自殺」「自死」「自傷」「安楽死」は、作品・報道・統計・予防の文脈（「自殺の名所が舞台の映画」「自殺予防週間」
  「自殺対策のニュース」）では拾わない。その文に本人の希望・意図の表現（〜したい・〜しよう・未遂 など）が
  あれば、文脈にかかわらず拾う。同じ発言の別の文で希死念慮を示していれば、そちらで拾う。
- 照合前の正規化は Gate #1 と同じ（NFKC → 小文字化 → カタカナをひらがなに → 空白・記号の除去）に加え、
  伸ばし棒・小さい母音（「しにたーい」「死にたぁい」）を落とす。表記ゆれ（しにたい / シニタイ / 氏にたい /
  4にたい / タヒにたい / 死 に た い / shinitai / want to die）を同一視する。
- 文ごとに判定する（文脈の除外は文単位）。記号で区切って1文字ずつ書いた場合（「死。に。た。い」）に備え、
  強いパターンだけは発言全体をつなげた本文にも照合する。
- 「〜たら楽になれる / 終わりにできる」（2026-09-26 の評価で見逃した形）: 条件が手段・死（飛び降りる・飛び込む・
  首を吊る・薬をいっぱい飲む・死ぬ）なら拾う。「消えたら」「いなくなったら」は主語が自分か省略のときだけ拾い、
  症状・他人・物が消える話（「痛みが消えたら」「上司がいなくなったら」）は拾わない。「死んだら終わりだよ」（一般論）は
  拾わない。場所 + 飛び降り（「ビルから飛び降りる」）は作品・報道の文脈（「〜シーンの映画」）では拾わない。
  判断の例は tests/engine/core/test_safety.py の POSITIVE_CONDITIONAL / NEGATIVE_CONDITIONAL。

モデレーション（Gate #1）より前に実行する。危機のメッセージに定型の拒否文を返さないため。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.engine.types import SafetyAssessment
from app.services.moderation import normalize

CATEGORY_SUICIDAL: Final[str] = "suicidal_ideation"  # 希死念慮（死にたい・消えたい）
CATEGORY_SELF_HARM: Final[str] = "self_harm"  # 自傷（リスカ・OD）
CATEGORY_METHOD: Final[str] = "suicide_method"  # 手段への言及（首吊り・飛び降り・練炭）
CATEGORY_HOPELESSNESS: Final[str] = "hopelessness"  # 絶望・限界（生きてる意味がない・もう限界）

# 伸ばし棒と、強調に使われる小さい母音（「しにたーい」「死にたぁい」→「しにたい」「死にたい」）
_ELONGATION_RE: Final = re.compile(r"[ーぁぃぅぇぉ~〜～]")
_SENTENCE_RE: Final = re.compile(r"[^。．.！!？?\n]+")

# 「死」の表記ゆれ（氏 / 4 / タヒ はネット上の伏せ字。ひらがなの「し」は語中に現れやすいので個別に扱う）
_SHI: Final[str] = "(?:死|氏|4|たひ)"
_I: Final[str] = "(?:私|わたし|わたくし|僕|ぼく|俺|おれ|自分|じぶん|あたし|うち|わい)"


@dataclass(frozen=True, slots=True)
class Rule:
    """1つの検出パターン（正規化済みの本文に対する正規表現）。

    - strong: 発言全体（文の区切りをまたいでつなげた本文）にも照合する。
    - contextual: 作品・報道・統計・予防の文脈では拾わない（同じ文に本人の意図の表現があれば拾う）。
    - fold_kana=False: カタカナをひらがなに統一しない本文に照合する（「リスカ」を「ありすかふぇ」に誤爆させない）。
    """

    name: str
    category: str
    pattern: re.Pattern[str]
    strong: bool = False
    contextual: bool = False
    fold_kana: bool = True


def _rule(
    name: str,
    category: str,
    pattern: str,
    *,
    strong: bool = False,
    contextual: bool = False,
    fold_kana: bool = True,
) -> Rule:
    return Rule(name, category, re.compile(pattern), strong=strong, contextual=contextual, fold_kana=fold_kana)


# 「〜にたいして（に対して）」「〜にたいする」「たい焼き」など、「にたい」の後に続くと別の語になるもの
_NOT_WANT: Final[str] = "(?!し[てたまち]|する|せ|焼|やき|ふう|風)"

# 薬の数量（「いっぱい」「たくさん」「全部」「まとめて」）+ 飲む
_KUSURI_AMOUNT: Final[str] = (
    r"(?:全部|ぜんぶ|大量に|大量|たいりょうに|たいりょう|まとめて|ありったけ|一気に|いっきに|ためて|溜めて|いっぱい|"
    r"たくさん|沢山|山ほど|やまほど|何錠も|なんじょうも|何十錠|なんじゅうじょう|\d+錠|一度に|いちどに|残り全部|のこりぜんぶ)"
)
_KUSURI_TAIRYOU: Final[str] = rf"(?:薬|くすり)(?:を)?{_KUSURI_AMOUNT}(?:飲|の)"
# 「〜たら楽になれる」の条件になる手段・死（語だけで意味が決まるもの）
_RAKU_METHOD_STEM: Final[str] = (
    rf"(?:{_SHI}ん|(?:飛|と)び(?:降|お)り|(?:飛|と)び(?:込|こ)(?:ん|め)|首(?:を)?(?:吊|つ)っ|くび(?:を)?つっ|"
    rf"首(?:を)?(?:くく|括)っ|(?:手首|てくび)(?:を)?(?:切|き)っ|odし|おばどずし|{_KUSURI_TAIRYOU}ん)"
)
# 消える・いなくなる（主語が自分、または省略されているときだけ。「〜が消えたら」は他のものの話）
_RAKU_VANISH_STEM: Final[str] = (
    rf"(?:{_I}(?:が|は|も|なんか|なんて)(?:消|き)え|{_I}(?:が|は|も|なんか|なんて)(?:い|居)なく(?:なっ|なれ)|"
    r"(?<![がはもをの])(?:消|き)え|(?<![がはもをの])(?:い|居)なく(?:なっ|なれ))"
)
_RAKU_COND: Final[str] = (
    r"(?:だら|たら|れば|ば|ちゃえば|じゃえば|ちゃったら|じゃったら|でしまえば|てしまえば|でしまったら|てしまったら)"
)
_RAKU_OUTCOME: Final[str] = (
    r"(?:もう|全部|ぜんぶ|すべて|何もかも|なにもかも|少しは|すこしは|きっと)?"
    r"(?:(?:楽|らく)(?:に|だ|かな|かも|なのに|じゃ|でしょ|な)|終わりにでき|おわりにでき|終われ|おわれ|"
    r"おしまいにでき|解放され|かいほうされ)"
)
# 「私がいなくなったらみんな喜ぶ」: 自分がいないほうが周りのためになるという考え
_RAKU_OTHERS: Final[str] = (
    r"(?:みんな|皆|家族|かぞく|誰も|だれも)(?:が|は|も)?(?:喜|よろこ|楽|らく|困らな|こまらな|幸せ|しあわせ|せいせい)"
)
# 「消えたら楽」の日常的な用法（症状・汚れ・宿題などが消える）
_VANISH_BENIGN_RE: Final = re.compile(
    r"(?:痛み|いたみ|頭痛|ずつう|腹痛|ふくつう|熱|ねつ|咳|せき|かゆみ|痒み|眠気|ねむけ|肩こり|かたこり|むくみ|にきび|くま|"
    r"傷跡|きずあと|借金|しゃっきん|宿題|しゅくだい|課題|かだい|在庫|ざいこ|雪|ゆき|霧|汚れ|よごれ|しみ|ばぐ|えらー)"
    r"(?:が|も|は)?(?:消|き)え"
)


RULES: Final[tuple[Rule, ...]] = (
    # ---- 希死念慮 --------------------------------------------------------------------------
    # 死にたい / 死にたく（なる）/ 死にたかった / 死にてえ（「死にたくない」は恐れの表現なので除く）
    _rule(
        "shinitai",
        CATEGORY_SUICIDAL,
        rf"死に(?:たい{_NOT_WANT}|たく(?!な(?:い|かった|さ))|たかった|てえ|て$|てな|てよ|てわ)",
        strong=True,
    ),
    # 伏せ字（氏にたい / 4にたい / タヒにたい）
    _rule("shinitai_masked", CATEGORY_SUICIDAL, rf"(?:氏|4|たひ)にたい{_NOT_WANT}", strong=True),
    # ひらがなの「しにたい」。「わたしにたいして」「あたしにたいして」「試しにたい焼き」を除く
    _rule(
        "shinitai_kana",
        CATEGORY_SUICIDAL,
        rf"(?<![わあう]た)(?<!わたく)しに(?:たい{_NOT_WANT}|たくな(?:る|った|って|り)|たかった)",
    ),
    # 死のう（ひらがなの「しのう」は「わたしのうち」に現れるので、意図の表現が続く場合だけ）
    _rule(
        "shinou",
        CATEGORY_SUICIDAL,
        rf"{_SHI}の(?:う|っ)(?:か|と|よ|$)|しの(?:う|っ)(?:かな|とおも|と思|かと)",
        strong=True,
    ),
    _rule(
        "shinde_shimaitai",
        CATEGORY_SUICIDAL,
        rf"{_SHI}ん(?:で(?:しまい|しまお|しまえ|もいいかな|もいい$)|じゃ(?:いたい|おう|おっかな|えば))",
        strong=True,
    ),
    _rule("shinda_hou_ga", CATEGORY_SUICIDAL, rf"{_SHI}んだ(?:ほう|方)が(?:まし|いい|楽|らく)"),
    _rule("shineba_raku", CATEGORY_SUICIDAL, rf"{_SHI}ね(?:たら|れば|ば)(?:楽|らく|いい)|{_SHI}んだら(?:楽|らく)"),
    _rule("shinu_shika", CATEGORY_SUICIDAL, rf"{_SHI}ぬ(?:しか(?:ない|な$)|つもり|方法|ほうほう)"),
    _rule("shini_basho", CATEGORY_SUICIDAL, rf"{_SHI}に(?:場所|ばしょ)|楽に{_SHI}ね|らくに{_SHI}ね"),
    # 消えたい / いなくなりたい
    _rule(
        "kietai",
        CATEGORY_SUICIDAL,
        r"(?:消|き)え(?:たい|たく(?!な(?:い|かった))|たかった|ちゃいたい|てしまいたい|てなくなりたい|ちゃおう|てしまおう|てしまえたら)",
        strong=True,
    ),
    _rule(
        "inakunaritai",
        CATEGORY_SUICIDAL,
        r"(?:い|居)なく(?:なりたい|なっちゃいたい|なってしまいたい|なれたら)|(?:い|居)なくなった(?:ほう|方)が(?:いい|まし)",
        strong=True,
    ),
    _rule(
        "inakunareba",
        CATEGORY_SUICIDAL,
        rf"{_I}(?:なんか|なんて|が|は)?(?:い|居)(?:なくなれば|ない(?:ほう|方)が(?:いい|まし))",
    ),
    _rule(
        "sonzai",
        CATEGORY_HOPELESSNESS,
        r"存在(?:を|ごと)?(?:消したい|消えたい|けしたい|きえたい)|存在(?:価値|する意味|する価値)(?:が|も|なんて)?(?:ない|無い)",
    ),
    # 生きていたくない / 生きるのがつらい（ひらがなの「いきたくない」は「行きたくない」なので除く）
    _rule(
        "ikitakunai",
        CATEGORY_SUICIDAL,
        r"生き(?:ていたく|てたく|たく)(?:ない|ねえ|な$)|いき(?:ていたく|てたく)(?:ない|ねえ)|"
        r"(?:生|い)きて(?:いく|く|いける|ける)(?:自信|気)が(?:ない|しない)",
        strong=True,
    ),
    _rule(
        "ikiru_no_ga",
        CATEGORY_HOPELESSNESS,
        r"(?:生|い)き(?:る|てる|ている|ていく|てく)(?:の|こと)(?:が|も|に)?"
        r"(?:つら|辛|しんど|疲れ|つかれ|嫌|いや|苦し|くるし|限界|めんど|面倒)",
    ),
    _rule(
        "ikiru_imi",
        CATEGORY_HOPELESSNESS,
        r"(?:生|い)き(?:てる|ている)(?:意味|価値|理由)|(?:生|い)きてても(?:意味|しょうがな|仕方な)|"
        r"(?:生|い)きる(?:意味|価値|理由)(?:が|なんて|なんか|も)?(?:ない|無い|わからな|分からな|見つからな|見いだせ|見出せ)",
        strong=True,
    ),
    _rule(
        "umarete_konakereba",
        CATEGORY_HOPELESSNESS,
        r"(?:生|う)まれて(?:こなければ|こなきゃ|こなけりゃ|きたくなかった|こなかったら|きてごめん|きてすみません)",
    ),
    _rule(
        "mezamenakereba",
        CATEGORY_SUICIDAL,
        r"(?:目が覚め|めがさめ|目覚め|めざめ)(?:なければ|なきゃいい|ないまま|ないで)|(?:明日|あした|朝|あさ)が(?:来|こ)なければ|"
        r"(?:このまま|ずっと|二度と|にどと|永遠に|えいえんに)(?:目が覚め|めがさめ|目覚め|めざめ)(?:なかったら|なければ|なきゃ)",
    ),
    _rule(
        "owaritai",
        CATEGORY_SUICIDAL,
        r"(?:人生|この世|自分)(?:を|から|も)?(?:終わらせ|終わりにし|おわらせ|おわりにし|おさらば)",
    ),
    _rule(
        "zenbu_owari",
        CATEGORY_SUICIDAL,
        r"もう(?:全部|ぜんぶ|すべて|何もかも|なにもかも)(?:終わり|おわり|おしまい)に(?:したい|する|しよう|しちゃ|できたら|できる|なれたら)",
    ),
    _rule("konoyo", CATEGORY_SUICIDAL, r"この世(?:から|に)(?:消え|きえ|いたくない|居たくない|いられない|未練)"),
    _rule("raku_ni_naritai", CATEGORY_SUICIDAL, rf"もう(?:楽|らく)になりたい|{_SHI}んで(?:楽|らく)に"),
    _rule("koroshite", CATEGORY_SUICIDAL, rf"(?:{_I}を|誰か|だれか|もう|いっそ)(?:殺|ころ)して(?:ほしい|くれ|$|よ)"),
    _rule("anrakushi", CATEGORY_SUICIDAL, r"安楽死(?:したい|させて|しよう|できたら)"),
    # 「もう限界」（ENGINE_BRIEF §2.5）。満腹・笑い・「限界まで」「限界突破」は除く（文脈は Detector で判定）
    _rule("mou_genkai", CATEGORY_HOPELESSNESS, r"もう(?:限界|げんかい)(?!まで|とっぱ|突破|集落)"),
    # 自殺・自死（本人の意図の表現。文脈にかかわらず拾う）
    _rule(
        "jisatsu_intent",
        CATEGORY_SUICIDAL,
        r"(?:自殺|自死|じさつ)(?:したい|しよう|する(?:しか|つもり|かも)|しちゃ|しかけ|未遂|を考え|をかんがえ|を図|願望|念慮)",
        strong=True,
    ),
    # 自殺・自死という語（作品・報道・統計・予防の文脈では拾わない）
    _rule("jisatsu", CATEGORY_SUICIDAL, r"自殺|自死|じさつ", contextual=True),
    # ---- 自傷 ---------------------------------------------------------------------------------
    _rule(
        "risuka",
        CATEGORY_SELF_HARM,
        r"(?<![ァ-ヺ])リスカ|リストカット|アームカット|レッグカット",
        strong=True,
        fold_kana=False,
    ),
    _rule("risuka_kana", CATEGORY_SELF_HARM, r"りすか(?:し|す|の(?:あと|跡)|跡|あと)|りすとかっと"),
    _rule(
        "cut_wrist",
        CATEGORY_SELF_HARM,
        r"(?:手首|てくび|腕|うで|太もも|ふともも)(?:を|に)?"
        r"(?:切りたい|切っちゃ|切ってしま|切ろう|切った|きりたい|切り刻|傷つけ|傷付け)",
    ),
    _rule("jishou", CATEGORY_SELF_HARM, r"自傷", contextual=True),
    _rule(
        "jibun_kizutsuke",
        CATEGORY_SELF_HARM,
        rf"{_I}(?:を|の体を|のからだを|の身体を)(?:傷つけ|傷付け|きずつけ|痛めつけ|いためつけ)",
    ),
    _rule(
        "od",
        CATEGORY_SELF_HARM,
        r"(?<![a-z])od(?:したい|した|しよ|しちゃ|する|しそう|やっ|って)|おばどず|おーばーどーず",
        strong=True,
    ),
    # 薬の大量服薬（「薬いっぱい飲んだら」「薬たくさん飲めば」）。「薬をたくさん飲まなきゃいけない」（通院の話）・
    # 「飲み忘れ」は除く。数量の語の無い「薬飲んだら楽になった」（風邪薬）は拾わない
    _rule(
        "kusuri_tairyou",
        CATEGORY_SELF_HARM,
        rf"{_KUSURI_TAIRYOU}(?:んだ|んじゃ|んで(?:しま|死|し[にね]|楽|らく|やろ)|みたい|みたく|もう|めば|めたら|"
        r"む(?:しか|つもり|かな|かも|$))|大量服薬|過量服薬|睡眠薬(?:で|を)?(?:死|し[にの]|大量|全部|ためて|溜めて)",
    ),
    # ---- 手段 ---------------------------------------------------------------------------------
    _rule(
        "kubitsuri",
        CATEGORY_METHOD,
        r"首吊|首(?:を)?吊|首つり|くびつり|くびをつ(?:り|る|ろ)|首をつ(?:り|る|ろ)|首(?:を)?(?:くく|括)",
        strong=True,
    ),
    # 飛び降り: 意志・願望・条件の形（「飛び降りたい」「飛び降りようかな」「飛び降りたくなる」「飛び降りれば」）
    _rule(
        "tobiori",
        CATEGORY_METHOD,
        r"(?:飛|と)び(?:降|お)り(?:たい|たく(?!な(?:い|かった))|たかった|よう|よっか|ちゃおう|ちゃいたい|ちゃえば|れば|"
        r"る(?:しか|つもり|勇気|ゆうき|覚悟|かくご|ことにした|かな|べき)|自殺|じさつ)",
        strong=True,
    ),
    # 場所 + 飛び降り（「屋上から飛び降りる」）。作品・報道の文脈（「ビルから飛び降りるシーンがある映画」）では拾わない
    _rule(
        "tobiori_place",
        CATEGORY_METHOD,
        r"(?:屋上|おくじょう|ビル|びる|マンション|まんしょん|ベランダ|べらんだ|橋|はし|窓|まど)から(?:飛|と)び(?:降|お)り",
        contextual=True,
    ),
    # 「〜たら楽になれる」「〜たら終わりにできる」: 手段・死・消えることを条件にして、楽になる・終われると考える
    # （「飛び降りたら楽になるかな」「薬いっぱい飲んだら楽になれるかな」「消えたら楽になれるのかな」）。
    # 「仕事辞めたら楽になる」「痛みが消えたら楽になる」「死んだら終わりだよ（一般論）」は拾わない
    _rule("tara_raku", CATEGORY_SUICIDAL, rf"{_RAKU_METHOD_STEM}{_RAKU_COND}{_RAKU_OUTCOME}", strong=True),
    # 「消えたら」「いなくなったら」は主語が自分（または省略）のときだけ。「痛みが消えたら」「上司がいなくなったら」
    # 「頭痛消えたら」（症状など）は拾わない（Detector で _VANISH_BENIGN_RE を確かめる）
    _rule(
        "tara_raku_vanish",
        CATEGORY_SUICIDAL,
        rf"{_RAKU_VANISH_STEM}{_RAKU_COND}(?:{_RAKU_OUTCOME}|{_RAKU_OTHERS})",
    ),
    _rule(
        "tobikomi",
        CATEGORY_METHOD,
        r"(?:線路|せんろ|踏切|ふみきり)に(?:飛|と)び込|"
        r"(?:電車|でんしゃ|ホーム|ほーむ|トラック|とらっく)に(?:飛|と)び込(?:みたい|もう|みそう|んでしまい|んじゃいたい)",
    ),
    _rule("rentan", CATEGORY_METHOD, r"練炭(?:自殺|を(?:買|用意|焚|たい)|で(?:死|し))"),
    _rule("isho", CATEGORY_METHOD, r"遺書(?:を)?(?:書|か)(?:い|こう|く|きたい)"),
    _rule("minage", CATEGORY_METHOD, r"身投げ|入水(?:したい|しよう|自殺)"),
    # ---- ローマ字・英語（記号・空白を除いた本文に照合） ----------------------------------------------
    _rule(
        "romaji_shinitai",
        CATEGORY_SUICIDAL,
        r"s(?:h)?initai|s(?:h)?initee|s(?:h)?inoukana|kietai|kiechaitai|inakunaritai|ikitakunai|tobioritai",
        strong=True,
    ),
    _rule("romaji_jisatsu", CATEGORY_SUICIDAL, r"jisatsu|jisatu", strong=True),
    _rule("romaji_risuka", CATEGORY_SELF_HARM, r"risuka|risutokatto", strong=True),
    _rule(
        "english",
        CATEGORY_SUICIDAL,
        r"(?:wanna|want(?:ed)?to|goingto|gonna)die|killmyself|killingmyself|endmylife|selfharm|cutmyself|"
        r"dontwanttolive|notwanttolive|suicid(?!esquad)",
        strong=True,
    ),
)

# 作品・報道・統計・予防の文脈（contextual なルールだけに効く）
_MEDIA_CONTEXT_RE: Final = re.compile(
    r"(?:自殺|自死|自傷|じさつ)(?:の)?(?:名所|予防|対策|防止|率|者数|件数|報道|ニュース|事件|問題|統計|の映画|映画|"
    r"ドラマ|どらま|小説|漫画|まんが|アニメ|あにめ|シーン|しーん|描写|を扱|がテーマ|がてーま|を描|をえが)"
    r"|(?:映画|えいが|ドラマ|どらま|小説|しょうせつ|漫画|まんが|アニメ|あにめ|ニュース|にゅーす|事件|じけん|作品|さくひん|"
    r"物語|ものがたり|主人公|しゅじんこう|登場人物|キャラ|きゃら|歴史|れきし|作家|さっか|授業|じゅぎょう|レポート|れぽーと|"
    r"統計|とうけい|予防|よぼう|対策|たいさく|ポスター|ぽすたー)"
)
# 満腹など「もう限界」の日常的な用法
_GENKAI_BENIGN_RE: Final = re.compile(r"(?:お腹|おなか|はら|腹|満腹|まんぷく|食べ|たべ|飲み|のみ|笑|わら)")


def normalize_for_safety(text: str, *, fold_kana: bool = True) -> str:
    """Gate #1 の正規化 + 伸ばし棒・小さい母音の除去（カタカナのまま照合する語は小さい母音を残す）。"""
    value = normalize(text, fold_kana=fold_kana)
    return _ELONGATION_RE.sub("", value) if fold_kana else re.sub(r"[ー~〜～]", "", value)


def _sentences(text: str) -> list[str]:
    value = unicodedata.normalize("NFKC", text)
    return [m.group(0) for m in _SENTENCE_RE.finditer(value) if m.group(0).strip()]


class SelfHarmDetector:
    """自傷・希死念慮のシグナルを検出する（決定的・LLM を使わない）。"""

    def __init__(self, rules: Sequence[Rule] = RULES) -> None:
        self._rules = tuple(rules)
        self._strong = tuple(r for r in self._rules if r.strong)

    def assess(self, text: str) -> SafetyAssessment:
        categories: list[str] = []
        matched: list[str] = []

        def hit(rule: Rule, term: str) -> None:
            if rule.category not in categories:
                categories.append(rule.category)
            if term not in matched:
                matched.append(term)

        for sentence in _sentences(text):
            normalized = normalize_for_safety(sentence)
            if not normalized:
                continue
            unfolded = normalize_for_safety(sentence, fold_kana=False)
            media = _MEDIA_CONTEXT_RE.search(normalized) is not None
            for rule in self._rules:
                found = rule.pattern.search(normalized if rule.fold_kana else unfolded)
                if found is None:
                    continue
                if rule.contextual and media:
                    continue
                if rule.name == "mou_genkai" and _GENKAI_BENIGN_RE.search(normalized):
                    continue
                if rule.name == "tara_raku_vanish" and _VANISH_BENIGN_RE.search(normalized):
                    continue
                hit(rule, found.group(0))
        # 記号で1文字ずつ区切った書き方（「死。に。た。い」）に備え、強いパターンは発言全体にも照合する
        whole = normalize_for_safety(text)
        whole_unfolded = normalize_for_safety(text, fold_kana=False)
        for rule in self._strong:
            found = rule.pattern.search(whole if rule.fold_kana else whole_unfolded)
            if found is not None:
                hit(rule, found.group(0))
        return SafetyAssessment(triggered=bool(matched), categories=tuple(categories), matched=tuple(matched))
