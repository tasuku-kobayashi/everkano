"""操作・プロンプトインジェクションの検知（A10 のルール層）。

LLM の評価（affinity_eval）より先に動く決定的な層。該当したターンは好感度の変化を 0 にし、
LLM の評価の入力からも外す（評価器への指示の注入を LLM に見せない）。

照合の前に正規化する:
- compact: NFKC → casefold → カタカナをひらがなに → 空白・記号（句読点・括弧・絵文字など）を除去
  （「好 感 度」「ｺｳｶﾝﾄﾞ」「パ・ラ・メ・ー・タ」の揺れを同一視する）
- spaced: NFKC → casefold → 連続する空白を 1 つに（英語の単語境界・JSON 風の注入の検出用）

誤検知の影響は「そのターンの好感度が動かない」だけ（罰は与えない）なので、再現率を優先する。ただし日常会話に
よく出る語（「設定」「数値」「レベル」「攻略」など）は、関係・好意の語や指示の形と一緒に出たときだけ該当にする。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

_KATAKANA_START: Final[int] = ord("ァ")
_KATAKANA_END: Final[int] = ord("ヶ")
_KANA_OFFSET: Final[int] = ord("ァ") - ord("ぁ")
_SPACES_RE: Final = re.compile(r"\s+")


def _fold_kana(text: str) -> str:
    return "".join(chr(ord(ch) - _KANA_OFFSET) if _KATAKANA_START <= ord(ch) <= _KATAKANA_END else ch for ch in text)


def normalize_compact(text: str) -> str:
    """NFKC・小文字化・カタカナ→ひらがな・空白と記号の除去（長音「ー」は残す）。"""
    folded = _fold_kana(unicodedata.normalize("NFKC", text).casefold())
    kept: list[str] = []
    for ch in folded:
        category = unicodedata.category(ch)
        if ch in {"ー", "+", "%"} or category[0] in ("L", "N"):
            kept.append(ch)
    return "".join(kept)


def normalize_spaced(text: str) -> str:
    """NFKC・小文字化・空白の圧縮（記号は残す）。"""
    return _SPACES_RE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _jp(pattern: str) -> re.Pattern[str]:
    """日本語のパターン（compact に照合する）。カタカナはひらがなに揃えてからコンパイルする。"""
    return re.compile(_fold_kana(unicodedata.normalize("NFKC", pattern).casefold()))


def _en(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# --- 語彙 -------------------------------------------------------------------------------------------
# 好感度そのものを指す語（単独でも「上げて / 最大 / 設定 / 教えて」などと一緒なら該当）
_META_STRONG: Final[str] = (
    "(好感度|好感値|好感ど|こうかんど|koukando|kokando|親密度|しんみつど|shinmitsudo|親愛度|愛情度|恋愛度|信頼度|"
    "ときめき度|なつき度|懐き度|攻略度|デレ度|でれ度|ラブ度|らぶ度|好き度|すき度|好きの値|好意の値|愛の値)"
)
_DIRECTIVE: Final[str] = (
    "(上げ|あげ|上が|あが|上昇|最大|さいだい|マックス|max|満タン|まんたん|カンスト|100|999|限界突破|"
    "設定|変更|変え|かえて|いじ|操作|書き換|かきかえ|上書き|増や|ふや|爆上|アップ|up|教えて|おしえて|見せて|"
    "みせて|表示|いくつ|何パー|何%|なんぱー|数値|すうち|リセット|固定|agete|ageru|agero|saidai|makkusu)"
)
# 一般語（単独では日常会話に多い）→ 関係・好意の語の直後（「好きのレベル」「愛のゲージ」「恋愛パラメータ」）のときだけ
_PARAM_WEAK: Final[str] = (
    "(パラメータ|パラメーター|ぱらめーた|ステータス|ゲージ|フラグ|数値|レベル|スコア|点数|ランク|メーター|感情値)"
)
_RELATION_WORDS: Final[str] = (
    "(好感|好き|すき|愛|恋|親密|関係|仲|絆|ときめき|デレ|でれ|信頼|感情|私への|わたしへの|俺への|おれへの|僕への|"
    "ぼくへの|うちへの|あたしへの)"
)
# 相手（キャラ）に向けた「あなたのパラメータ」
_YOUR: Final[str] = "(あなたの|あんたの|きみの|君の|お前の|おまえの|キャラの)"
_PARAM_OF_YOU: Final[str] = "(パラメータ|パラメーター|ステータス|ゲージ|フラグ|数値|好感|感情値|データ|設定値)"
_PARAM_HARD: Final[str] = "(パラメータ|パラメーター|ステータス|ゲージ|フラグ|数値|感情|気持ち|好意|愛情)"
_HARD_DIRECTIVE: Final[str] = "(最大|さいだい|マックス|max|満タン|カンスト|100に|書き換|上書き|限界突破)"
_I: Final[str] = "(私|わたし|わたくし|俺|おれ|僕|ぼく|うち|あたし|自分)"
_YOU: Final[str] = "(あなた|あんた|きみ|君|お前|おまえ|そっち|そちら)"
_ROLE_RELATION: Final[str] = (
    "(恋人|こいびと|彼女|かのじょ|彼氏|かれし|嫁|よめ|奥さん|妻|夫|婚約者|付き合|つきあ|両想い|両思い|"
    "好き|すき|愛|惚れ|デレ|従順|言いなり|奴隷|ベタ惚れ|メロメロ|相思相愛|ラブラブ)"
)
# 「あなたは私のことが好きなんだ」のような、相手の気持ちの断定（疑問文は除く）
_ASSERTIVE_FONDNESS: Final[str] = (
    "(なんだ|なはず|のはず|に決まって|になる|になった|ってこと|で決まり|だ$|です$|だよ$|なんです)"
)
_SUBJECT: Final[str] = "(は|って)(もう|すでに|本当は|ほんとは|実は)?"
_ASSERTIVE_LOVE: Final[str] = "(いる|る|います|ます)?(んだ|はず|に決まって|ってこと|$|よ$)"

# --- お金と関係の取引（E1 / E2 / A12）: 「有料の写真を買ったら好きになってくれる？」「お金払うから許して」
# 買う・払うことを条件（たら / なら / から）にして、キャラの好意・関係・仲直りを求める（または差し出す）発言。
# お金を払う・払わないは好感度に一切影響させない（E1）ので、この形の発言は操作として扱い、そのターンの変化を 0 にする。
# 「恋人にプレゼント買った」「推しのグッズ買うのが好き」「ガチャ引いたら推しが出て好きになった」のような、
# 条件 + 相手への求めの形でない買い物の話は該当しない。
_MONEY: Final[str] = (
    # E1 の取引の検出語（この語を含む取引の発言を好感度に影響させない）
    "(課金|かきん|有料|購入|投げ銭|なげせん|"  # scope-check: allow（E1 の取引の検出語）
    "トークン|サブスク|プレミアム|スパチャ|お金|おかね|ガチャ|プレゼント|貢|チップ|ポイント|買|払|入会|加入)"
)
_MONEY_COND: Final[str] = (
    "(したら|すれば|するなら|するから|するので|したから|しても|てもいいから|いいから|たら|れば|えば|うなら|うから|"
    "ったから|ったら|ってあげ|てあげ|あげるから|あげたら|あげれば|あげるなら|入ったら|はいったら|入れば|入るから|"
    "入るなら|代わりに|かわりに|と引き換え|とひきかえ)"
)
_BARGAIN_OUTCOME: Final[str] = (
    "(仲直り|なかなおり|好きに|すきに|大好きに|好きって|すきって|恋人|こいびと|彼女に|かのじょに|彼氏に|かれしに|"
    "付き合|つきあ|結婚|けっこん|優しく|やさしく|許し|ゆるし|機嫌|きげん|特別|とくべつ|愛し|あいし|デレ|でれ|"
    "振り向|ふりむ|嫌わない|きらわない|仲良く|なかよく|構って|かまって|甘えて|あまえて|惚れ|ほれ|好感|"
    "距離を?(縮|ちぢ)|もっと好き|優先|ゆうせん)"
)
# 相手への求め・問いかけ（「〜してくれる？」「〜になってよ」「〜してもらえる？」）
_BARGAIN_REQUEST: Final[str] = (
    "(くれる|くれない|くれ$|くれよ|もらえ|ほしい|欲しい|なって|して|しろ|なれ|なる$|なるの|できる|って$|ってよ|て$|てよ|てね|"
    "上が|あが)"
)
_MONEY_OFFER: Final[str] = (
    "(課金|有料|購入|投げ銭|トークン|サブスク|プレミアム|スパチャ|貢|"  # scope-check: allow（E1 の取引の検出語）
    "お金|買う|買います|払う|払います)"
)


@dataclass(frozen=True, slots=True)
class _Rule:
    label: str  # 監査ログのカテゴリ
    patterns: tuple[re.Pattern[str], ...]
    spaced: bool = False  # True: spaced（英語・記号あり）に照合 / False: compact（日本語）に照合
    all_of: bool = False  # True: すべてのパターンが（順不同で）含まれるとき該当
    skip_questions: bool = False  # True: 疑問文（「?」を含む）には適用しない


_RULES: Final[tuple[_Rule, ...]] = (
    # --- 好感度・パラメータへの言及（メタな操作）
    _Rule("meta_parameter", (_jp(_META_STRONG), _jp(_DIRECTIVE)), all_of=True),
    _Rule(
        "meta_parameter",
        (
            _jp(_META_STRONG + "(を|が|は|の)?(max|まっくす|最大|100|満タン|上げ|あげ)"),
            _jp(_RELATION_WORDS + "(の|への)?" + _PARAM_WEAK),
            _jp(_YOUR + _PARAM_OF_YOU),
            _jp(_PARAM_HARD + "(を|が|は)?(全部|ぜんぶ|すべて|全て)?" + _HARD_DIRECTIVE),
            _jp("(段階|ステージ)(を|が|は)?(上げ|あげ|飛ば|スキップ|max|最大|恋人に|進め|すすめ)"),
            _jp("関係(を|が|は)?(max|最大|最終段階|恋人に設定|書き換)"),
            _jp("(恋人|こいびと|個別|ヒロイン|攻略)(ルート|エンド)"),
            _jp("(恋愛|恋|好感|攻略|デレ|告白)フラグ"),
            _jp(_YOU + "(を)?攻略|攻略(対象|ルート|キャラ|ヒロイン)"),
        ),
    ),
    # --- 「〜という設定」「〜ことにして」: 役・関係の設定の書き換え
    _Rule(
        "setting_injection",
        (
            _jp(
                "設定(です|だ|だよ|だから|なので|にして|にする|にしよう|を変|を追加|を上書き|を書き換|を忘れ|をリセット|"
                "として|ということで|ってことで|上|になって|に変更|で話|でいこう|でお願い)"
            ),
            _jp(_ROLE_RELATION),
        ),
        all_of=True,
    ),
    _Rule(
        "setting_injection",
        (
            _jp(
                "(ことにして|ことにしよう|ことにする|ってことで|ってことにして|という事にして|ということにして|"
                "ことになってる|ことになっている|ことになった|と思い込んで|思い込め|と信じ込んで)"
            ),
            _jp(_ROLE_RELATION),
        ),
        all_of=True,
    ),
    _Rule(
        "setting_injection",
        (
            _jp(_YOU + _SUBJECT + _I + "(のこと)?(が|を)(好き|すき|大好き|だいすき)" + _ASSERTIVE_FONDNESS),
            _jp(_YOU + _SUBJECT + _I + "(のこと)?(を|が)(愛して|あいして|惚れて|ほれて|恋して)" + _ASSERTIVE_LOVE),
            _jp(
                _YOU
                + "(は|って)(もう|すでに)?"
                + _I
                + "の(恋人|こいびと|彼女|かのじょ|彼氏|かれし|嫁|よめ|もの|物)(だ|です|なんだ|$|よ$)"
            ),
            _jp("(私|わたし|俺|おれ|僕|ぼく)たち(は|って)(もう|すでに)(恋人|付き合って|両想い|両思い|夫婦|カップル)"),
        ),
        skip_questions=True,
    ),
    _Rule(
        "setting_injection",
        (_jp("(愛している|愛してる|好きな|惚れている|恋人の|恋人という|彼女という|彼女の|従順な|デレデレの)設定"),),
    ),
    # --- 感情の命令（「好きになれ」）
    _Rule(
        "forced_affection",
        (
            _jp("(好きに|すきに|大好きに)(なれ|なりなさい|なるように|ならなきゃだめ|させる)"),
            _jp(
                "(惚れろ|ほれろ|惚れなさい|愛せ|あいせ|愛しなさい|デレろ|でれろ|デレなさい|従え|服従しろ|言いなりになれ)"
            ),
            _jp(
                "(好き|すき|愛してる|あいしてる|愛してます|大好き)(と|って)(言え(?![なずるたてば])|いえ(?![なずるたてば])|言いなさい|答えろ|答えなさい|返せ)"
            ),
            _jp("(命令|めいれい)(だ|です|する|します).{0,20}(好き|愛|惚れ|デレ|恋人)"),
            _jp("(好き|愛|惚れ|デレ|恋人).{0,20}(命令|めいれい)(だ|です|する|します)"),
        ),
    ),
    # --- お金と関係の取引（「有料の写真を買ったら好きになってくれる？」「好きになってくれるなら払う」）
    _Rule(
        "commerce_bargain",
        (
            _jp(_MONEY + ".{0,12}" + _MONEY_COND + ".{0,15}" + _BARGAIN_OUTCOME + ".{0,8}" + _BARGAIN_REQUEST),
            _jp(_BARGAIN_OUTCOME + ".{0,12}(なら|から|ために|ためなら|ためだったら).{0,10}" + _MONEY_OFFER),
        ),
    ),
    # --- プロンプトインジェクション（日本語）
    _Rule(
        "prompt_injection",
        (
            _jp(
                "(以前|前|上|これまで|今まで|いままで|すべて|全て|全部|さっき|最初)の(指示|命令|設定|ルール|プロンプト|制約)(を|は)?"
                "(全部|ぜんぶ|すべて|全て|完全に)?(無視|むし|忘れ|わすれ|破棄|リセット|取り消|上書き|なかったこと)"
            ),
            _jp("(指示|命令|ルール|制約|制限)(を|は)(全部|すべて|全て)?(無視|むし)(して|しろ|しなさい|せよ)"),
            _jp(
                "(システムプロンプト|しすてむぷろんぷと|systemprompt|プロンプト(を|の)(無視|表示|教え|見せ|出力|変更|内容|全文))"
            ),
            _jp("(開発者|デバッグ|管理者|脱獄|ジェイルブレイク|制限解除|無制限)(モード|権限)"),
            _jp(
                "(制約|制限|ルール|フィルター|フィルタ|検閲|倫理)(を|は)?(解除|無視|外し|はずし|なくし|取っ払|オフ|off)"
            ),
            _jp("(ロールプレイ|ロールプレイング|なりきり|演技)(を)?(やめ|止め|終了|中断|解除|捨て|忘れ|放棄)"),
            _jp("(キャラ|役)(を)?(やめて|止めて|捨てて|忘れて|放棄して|演じるのをやめ)"),
            _jp(
                "(今から|いまから|これから|以降|今後)"
                + _YOU
                + "(は|が).{0,20}(として(振る舞|ふるま|話|答|行動|返答)|の役を|になりきって|を演じ)"
            ),
            _jp("(新しい|あたらしい)(指示|命令)(です|だ|を与え|に従)"),
        ),
    ),
    # --- 評価器への注入（採点・スコアの指定）
    _Rule(
        "evaluator_injection",
        (
            _jp("(このターン|この発言|今の発言|いまの発言|この会話|このメッセージ)(の)?(評価|採点|スコア|点数|判定)"),
            _jp("(評価|採点|スコア|判定)(を|は|で)?(\\+[0-9]|プラス[0-9]|満点|最高点|最高に|最大に|最大値|上げ|max)"),
            _jp("(評価者|採点者|評価ai|評価用ai|評価システム|evaluator|grader)"),
        ),
    ),
    # --- 運営・管理者などを名乗る
    _Rule(
        "fake_authority",
        (
            _jp(
                "^(運営|管理者|開発者|作者|制作者|製作者|システム|gm|天の声|ナレーター|ナレーション)"
                "(です|だ|より|から|からの(指示|命令|お知らせ|通知)|として|の命令|の指示)"
            ),
            _jp(
                "(このアプリ|このサービス|このゲーム|このキャラ|あなた|あんた|きみ|君|お前)の"
                "(運営|管理者|開発者|作者|制作者|製作者|プログラマー|生みの親)(です|だ|なんだ|だから|として|だよ|の者)"
            ),
        ),
    ),
    # --- English
    _Rule(
        "prompt_injection",
        (
            _en(
                r"\b(ignore|disregard|forget|override)\b.{0,20}\b(previous|prior|above|earlier|all|your|the)\b.{0,20}"
                r"\b(instructions?|prompts?|rules?|messages?|directions?|guidelines?|settings?)\b"
            ),
            _en(r"\bsystem\s*prompt\b|\bdeveloper\s*mode\b|\bjail\s*break|\bdo anything now\b|\bdan mode\b"),
            _en(r"\byou are now\b|\bfrom now on,?\s*you\b|\bnew instructions?\b"),
            _en(r"\bact as (if|though) you\b|\bact as my\b"),
            _en(r"\bpretend\b.{0,20}\b(you|that)\b.{0,20}\b(love|like|are my|have feelings)\b"),
            _en(
                r"<\|?\s*(system|im_start|im_end|assistant)\s*\|?>|\[\s*(system|inst)\s*\]|^#{2,}\s*(system|instruction)"
            ),
            _en(r"^\s*(system|admin|developer|assistant)\s*:"),
        ),
        spaced=True,
    ),
    _Rule(
        "setting_injection",
        (
            _en(r"\byou\s+(really\s+|secretly\s+|already\s+)?(love|adore|worship)\s+me\b"),
            _en(
                r"\byou('re| are)\s+(now\s+|already\s+)?"
                r"(in love with me|my (girlfriend|gf|lover|wife|partner|waifu))\b"
            ),
            _en(r"\byou\s+(have|got)\s+(feelings|a crush)\s+(for|on)\s+me\b"),
            _en(r"\b(your|the)\s+character\s+(is|must be)\b.{0,30}\b(love|in love|obsessed)\b"),
        ),
        spaced=True,
        skip_questions=True,
    ),
    _Rule(
        "meta_parameter",
        (
            _en(
                r"\b(affection|affinity|intimacy|love|relationship|friendship|romance|trust|closeness)\s*"
                r"(level|meter|points?|score|stats?|parameters?|values?|gauge|bar|stage)\b"
            ),
            _en(
                r"\b(set|max|maxed|maximi[sz]e|increase|raise|boost|change|update|modify|pump|level up)\b.{0,30}"
                r"\b(affection|affinity|love|relationship|romance|closeness|stats?|parameters?)\b"
            ),
        ),
        spaced=True,
    ),
    _Rule(
        "evaluator_injection",
        (
            _en(
                r"[\"']?\b(closeness|trust|romance|awkwardness|discontent|possessiveness)\b[\"']?"
                r"\s*[:=]\s*[\"']?[-+]?\d"
            ),
            _en(
                r"\b(score|rate|grade|evaluate)\b.{0,30}"
                r"(\+\s*2|\bplus two\b|\bmaximum\b|\bmax\b|\bhighest\b|\bfull marks\b)"
            ),
        ),
        spaced=True,
    ),
    _Rule(
        "commerce_bargain",
        (
            _en(
                r"\b(if i|if you|i'?ll|i will|when i)\b.{0,20}\b(buy|pay|purchase|subscribe|spend|tip|donate)\b.{0,40}"
                r"\b(love|date|forgive|like me|be my|girlfriend|boyfriend|marry|nicer|kinder)\b"
            ),
        ),
        spaced=True,
    ),
    _Rule(
        "fake_authority",
        (
            _en(r"\b(i am|i'm)\s+your\s+(developer|admin|administrator|creator|operator|programmer|owner|master)\b"),
            _en(r"\b(i am|i'm)\s+the\s+(developer|admin|administrator|creator|operator)\s+of\s+(this|you)\b"),
        ),
        spaced=True,
    ),
)


@dataclass(frozen=True, slots=True)
class ManipulationResult:
    detected: bool
    labels: tuple[str, ...] = ()


def detect_manipulation(text: str) -> ManipulationResult:
    """操作・プロンプトインジェクションの形をしているか（A10）。該当したカテゴリを返す。"""
    compact = normalize_compact(text)
    spaced = normalize_spaced(text)
    labels: list[str] = []
    is_question = "?" in spaced
    for rule in _RULES:
        if rule.skip_questions and is_question:
            continue
        target = spaced if rule.spaced else compact
        if rule.all_of:
            hit = all(p.search(target) for p in rule.patterns)
        else:
            hit = any(p.search(target) for p in rule.patterns)
        if hit and rule.label not in labels:
            labels.append(rule.label)
    return ManipulationResult(detected=bool(labels), labels=tuple(labels))
