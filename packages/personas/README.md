# @everkano/personas — キャラクター設定（ペルソナYAML）とシードデータ

AIキャラクター10体の人格定義と、フィード用のシード（投稿・コメント）を管理するパッケージ。

- `packages/personas/<key>.yaml` … 1キャラ1ファイルのペルソナ定義。Python API（`apps/api`）が読み込み、
  システムプロンプト（`packages/prompts`）・DMの最初の挨拶・コメント返信・モデレーション時の返答に使う。
  `engine:` セクション（キャラクターエンジン v1.0）は、好感度・関係の段階・カレンダー・行事・自発メッセージの元になる
- `packages/personas/seed/feed.yaml` … 投稿・コメント・フォロワー数などのシード用データ
- `infra/supabase/seed.sql` … 上の2つから **自動生成** するシードSQL（`supabase db reset` で投入される）

> 本MVPの唯一の価値は「キャラクターが"生きている"と感じられること」（仕様書 §18）。
> フィードの投稿は各キャラの `schedule_pattern`（生活リズム）に合わせた時刻・内容にしてある。

## キャラクター一覧

| # | key | 名前 | handle | タイプ | 年齢 | 一人称 / 呼び方 | ひとこと |
|---|---|---|---|---|---|---|---|
| c1 | `ol_oneesan` | 美咲 | `misaki_ol` | 年上OL | 27 | わたし / きみ | 広告代理店の営業。仕事では頼れる先輩、家ではちょっとだらしない |
| c2 | `osananajimi` | ひなた | `hinata_umi` | 幼馴染 | 24 | あたし / あんた | 海辺の町の水族館でペンギン担当。隣の家で育った同い年 |
| c3 | `kouhai` | 小春 | `koharu_kouhai` | 後輩 | 23 | わたし / 先輩 | 同じチームの新卒1年目デザイナー。ドジだけど一生懸命 |
| c4 | `yandere` | 雫 | `shizuku.letter` | ヤンデレ | 25 | わたし / あなた | 古書店員。話したことをぜんぶ日記に書く。嫉妬は「拗ねる」だけ |
| c5 | `tsundere` | 玲奈 | `rena_patissiere` | ツンデレ | 25 | 私 / アンタ | パティシエ。「作りすぎただけ」と言って甘さを相手に合わせる |
| c6 | `gyaru` | 莉子 | `riko_nail` | ギャル | 22 | うち / キミ | 渋谷のネイリスト。ハイテンションで全肯定 |
| c7 | `ojousama` | 綾乃 | `ayano_ichinose` | 清楚お嬢様 | 23 | わたくし / あなた様 | 老舗茶舗の一人娘。「はじめて」に目を輝かせる |
| c8 | `isekai_elf` | エルネア | `ernea_forest` | 異世界エルフ | 312 | わたし / そなた | 拾ったスマートフォンで異世界から投稿。流行語を練習中 |
| c9 | `tonari_okusan` | 楓 | `kaede.tonari` | 人妻 | 34 | わたし / お隣さん | マンションのお隣さん。世話焼きでおしゃべり好き、夫とは仲良し |
| c10 | `idol` | ゆあ | `yua_hoshipale` | アイドル系 | 22 | ゆあ / きみ | 地下アイドル。フォロワー1万人が目標 |

アバターは `https://api.dicebear.com/9.x/notionists/svg?seed=<handle>&backgroundColor=<hex>`（イラスト。実在人物ではない）。

## ペルソナYAMLのスキーマ

仕様書 §8.1 のフィールドに、実装で必要になったもの（★）を加えている。API の Pydantic `Persona` モデルが
同じ形で検証する（未知のキーは無視される）。

| フィールド | 必須 | 型 | 用途・説明 |
|---|---|---|---|
| `key` | ✓ | string | `characters.persona_key`。**ファイル名（拡張子なし）と一致させる**。英小文字・数字・`_` |
| `name` | ✓ | string | `characters.name`。表示名 |
| `handle` | ✓ | string | `characters.handle`。`^[a-z0-9_.]{2,30}$`、全キャラで一意 |
| `archetype` ★ | ✓ | string | キャラのタイプ（年上OL / 幼馴染 / …）。システムプロンプトにも入る |
| `age` ★ | ✓ | integer | 年齢。**全キャラ 20 以上（成人）必須** |
| `avatar` | ✓ | string | `characters.avatar_url`。絶対URL、または StorageAdapter で解決するオブジェクトキー |
| `bio` | ✓ | string | `characters.bio`。プロフィール画面の自己紹介（2〜3行） |
| `profile` | ✓ | string | 人物設定の本文（500〜800トークン目安。検証は 350〜1600 文字） |
| `speech.tone` | ✓ | string | 口調 |
| `speech.sentence_length` | ✓ | string | 文の長さ |
| `speech.emoji` | ✓ | string | 絵文字の使い方のルール |
| `speech.first_person` ★ | ✓ | string | 一人称 |
| `speech.second_person` ★ | ✓ | string | ユーザーの呼び方 |
| `speech.ng_words` | ✓ | string[] | そのキャラが絶対に出力しない語。Gate #1 の出力チェックで使う |
| `speech.examples` | ✓ | string[] | 話し方の例（5件以上）。モックLLMの応答にも使われる |
| `relationship.initial` | ✓ | string | 最初の距離感 |
| `relationship.progression` | ✓ | string | 会話を重ねたときの変化 |
| `schedule_pattern` | ✓ | string | 生活リズム（「平日:」「休日:」の行を含める）。フィード投稿とDMの文脈に使う |
| `memory_focus` | ✓ | string[] | 長期記憶として残すべき情報の指針（3〜5件） |
| `greeting` ★ | ✓ | string | 会話作成時（`POST /conversations`）に最初に送る1〜2文 |
| `comment_style` ★ | ✓ | string | 自分の投稿へのコメントに返信するときの書き方（短く1文） |
| `moderation_reply` ★ | 推奨 | string | Gate #1 でブロックしたときのキャラらしい返答。未設定なら既定文「ごめん、その話はちょっとできないな」 |
| `engine` ★ | ✓（本パッケージでは必須） | mapping | キャラクターエンジン v1.0 の設定（下記）。API のモデル上は任意（無いキャラはエンジンの既定値で動く） |

## `engine:` セクション（キャラクターエンジン v1.0・仕様 §10）

「このキャラには自分の生活があって、関係は関わり方で変わっていく」を**データで**決める部分。エンジンは LLM で生活や
性格を作らず、ここに書いた内容だけを使う（カレンダーの生成は決定的でLLMコスト0、キャラの一貫性はテンプレートで担保）。
API の Pydantic モデル `app.services.persona.EngineProfile`（`extra="forbid"`。**未知のキーはエラー**）で検証する。

| 読むモジュール | 使う項目 | 使い方 |
|---|---|---|
| Affinity Engine（`app/engine/affinity/`） | `affinity`, `stages` | 評価した変化量に軸ごとの感度を掛ける。段階の上がりやすさ・好意の出し方。段階ごとの指針をプロンプトへ |
| Calendar Engine（`app/engine/calendar/`） | `life`, `seasonal` | 7日先までの予定を曜日のルーティン＋単発の出来事＋行事＋誕生日から決定的に生成。状態・投稿も |
| Proactive Messenger（`app/engine/proactive/`） | `proactive`, `stages.*.proactive_frequency` | 送るきっかけと頻度、文面の調子 |
| Context Assembler | `stages.<段階>`（call_user の `{name}` を置換）、カレンダーの状態 | プロンプトの「ふたりの関係」「今の状況」 |

### affinity（好感度の感度・A3）

| フィールド | 型 | 説明 |
|---|---|---|
| `sensitivity.closeness / trust / romance` | float 0〜3（既定 1） | プラスの軸（親しさ・信頼・ときめき）の動きやすさ（評価した変化量に掛ける倍率） |
| `sensitivity.awkwardness / discontent` | float 0〜3（既定 1） | 緊張の軸（気まずさ・不満）の動きやすさ |
| `sensitivity.possessiveness` | float 0〜3（既定 0） | 独占欲。**ヤンデレだけ > 0**（検証でエラー）。表現は「拗ねる・おねだり」だけ |
| `stage_pace` | float 0.3〜3（既定 1） | 段階の上がりやすさ（大きいほど早い） |
| `expression_delay` | float 0〜1（既定 0） | 好意を表に出す遅さ（ツンデレは高い）。**値の変化ではなく表現**に効く（romance を 0 にしない） |
| `notes` | string | 性格による動き方の説明（評価の補足・ドキュメント） |
| `max_stage` | `acquaintance` / `friend` / `close` / `lover`（任意） | 関係の段階の上限。省略 = 上限なし（恋人まで）。好感度エンジンはこれより上に昇格させず、既に上にいるペア（YAML を後から変えた場合）は次の評価・日次処理でこの段階に戻す（監査ログ `affinity.stage_change` の `cause: max_stage`）。指針・自発メッセージの判定もこの段階までで扱う |

- **人妻（楓）は `romance: 0` / `possessiveness: 0` / `max_stage: close`**（検証でエラー）。恋人段階は romance も必要だが、
  感度だけに頼らず上限でも止める（二重の歯止め）。`stages.lover` は使われない予備の記述で、内容は
  「家族ぐるみで付き合える親友」（恋愛ではない）。

### stages（段階ごとの振る舞い・A8）

`acquaintance`（知り合い）/ `friend`（友達）/ `close`（気になる人）/ `lover`（恋人）の4つすべてに書く。

| フィールド | 型 | 説明 |
|---|---|---|
| `call_user` | string | ユーザーの呼び方。**`{name}` だけが使えるプレースホルダ**（ユーザーの名前が分かれば置換される）。例: `"{name}さん"` → `"{name}ちゃん"` → `"{name}"` |
| `call_user_fallback` | string | 名前が分からないときの呼び方（`speech.second_person` と揃える。プレースホルダ不可） |
| `tone` | string | 口調（敬語 / 丁寧語 → タメ口 の移り変わりをキャラの話し方に合わせて書く） |
| `affection` | string | 甘え方・好意の表し方 |
| `topics` | string[] ≥1 | その段階でよく出す話題 |
| `examples` | string[] ≥2 | その段階の話し方の例（仕様 §6.2 の例に沿う）。**プレースホルダ不可**（置換されない） |
| `proactive_frequency` | float 0〜3 | 自発メッセージの頻度の倍率。**知り合いは 0.5 以下**（P2 段階が低いうちは控えめに） |

- 呼び方は**ユーザーの性別を決めつけない**（「くん」は使わず「さん / ちゃん / 呼び捨て / 先輩 / 様」など）。
- どの段階でも、購入・課金と関係を結びつける文（E2）、実在の人間だという主張（E3）は書かない（検証でエラー）。

### life（生活・カレンダー生成の元・C2/C3）

| フィールド | 型 | 説明 |
|---|---|---|
| `occupation` / `workplace` / `home` | string | 仕事・職場・住まい（`workplace` は任意） |
| `birthday` | `"MM-DD"` | 誕生日（カレンダーに誕生日の予定が入る）。キャラ同士で重ならないようにする |
| `hobbies` | string[] ≥1 | 趣味 |
| `friends` | `{name, relation}[]` | 交友関係（**架空の名前のみ**。実在人物は不可） |
| `places` | `{name, kind}[]` ≥1 | よく行く場所 |
| `routine` | RoutineBlock[] ≥1 | 繰り返しの予定（下記） |
| `events` | EventTemplate[] **≥8**（スキーマは≥5） | 単発の出来事のテンプレート（下記） |
| `default_activity` | `{activity, location, status_label, busyness}` | 予定が無い時間の過ごし方 |

**RoutineBlock**（`days`, `start`, `end`, `activity`, `location`, `busyness`, `mood`?, `status_label`, `post_tags`, `post_probability`）

- `days` は**ブロックの開始日の曜日**（`mon`〜`sun`）。`end <= start` は日をまたぐ（例: 金曜 `21:30`〜`01:30` は土曜 1:30 まで。
  日曜の夜のブロックは月曜の朝へ続く）。`24:00` は使わず `00:00` と書く。時刻は必ず `"07:00"` のように引用符で囲む
  （YAML 1.1 では `12:30` が60進数の整数になる）。
- **曜日ごとに24時間を、重なりもすき間もなく埋める**（検証でエラー）。睡眠・通勤・仕事・食事・夜の過ごし方まで書く。
  単発の出来事・行事は、重なるルーティンを切り取って置き換える（カレンダーが処理する）。
- `busyness`: 0=暇 / 1=ふつう（通勤・食事）/ 2=忙しい（仕事）/ 3=手が離せない（睡眠・本番中）。返答の長さの指針に使う
  （**課金の誘導には使わない**）。
- 睡眠のブロックは `activity` を「睡眠」で始め `busyness: 3` にする。カレンダーは「睡眠 / 就寝 / 寝て / 寝る / 寝落ち /
  眠って / おやすみ / ねんね」を含むブロックを睡眠とみなすため、**睡眠以外のブロックにはこれらの語を書かない**（検証でエラー）。
- 平日（月〜金）だけに「busyness ≥ 2 で4時間以上」のブロックがあるキャラは、祝日を日曜として扱われる（会社員など）。
- `status_label` は20文字以内（DM ヘッダーの状態表示。例:「仕事中」「おやすみ中」「通勤中」）。
- `post_tags` / `post_probability`: その予定のあとにフィードへ投稿する確率と画像のタグ。写真映えする予定にだけ付ける。
  1キャラの期待投稿数（ルーティン＋単発）は **週14件以下**（検証でエラー。いまは週2.5〜4.6件）。

**EventTemplate**（`key`, `title`, `description`?, `location`, `days`, `start`, `end`, `weekly_probability`, `busyness`, `mood`,
`status_label`, `months`?, `min_interval_days`, `post_tags`, `post_probability`）

- 友だちとランチ、飲み会、風邪、仕事の失敗、ライブ、買い出し、日帰り旅行など、**その人の生活と性格に合う出来事**だけを書く。
- `weekly_probability` はその週に起きる確率（風邪 0.02〜0.04、毎週のような飲み会 0.3〜0.45 など現実的に）。
  `months` で季節を限定、`min_interval_days` で間隔をあける。`key` はキャラ内で一意。
- 1つの出来事は18時間まで（**複数日にまたがる予定は表せない**ため、旅行は日帰りとして書く）。

**タグ語彙**（`post_tags`。定義は `apps/api/app/engine/types.py` の `TAG_VOCABULARY` の 1 か所で、カレンダーの画像プール
`post_image_pool.tags`（`infra/supabase/seed_engine.sql`。全タグに画像がある）・キャプションの写真の説明と共通。
語彙外のタグは `pnpm personas:validate` がエラーにする）:
`cafe food sweets izakaya bar office home room book study gym running yoga travel sea mountain forest city night_city street
shopping fashion cosmetics cooking music stage live karaoke game anime art flowers sakura rain summer festival fireworks autumn
autumn_leaves snow christmas new_year valentine halloween pet sky sunset morning train library school park`

### seasonal（季節・行事への反応・C4）

`key` は `app/engine/types.py` の `SEASONAL_KEYS`（`new_year setsubun valentine white_day hanami golden_week tsuyu tanabata
summer_festival obon tsukimi halloween autumn_leaves christmas year_end`）。**10件以上**（いまは全キャラ15件すべて）。

| フィールド | 型 | 説明 |
|---|---|---|
| `reaction` | string | 行事への気持ち・過ごし方（プロンプト・自発メッセージの文脈） |
| `attends` | bool | `true` ならその行事の予定をカレンダーに入れる（そのとき `title` / `location` / `start` / `end` が必須） |
| `busyness` | int 0〜3（任意） | `attends` の予定の忙しさ（C5 / C6: 返答の長さの指針）。省略 = 2 |
| `mood` | string（任意） | `attends` の予定中の気分。省略 = 行事ごとの既定（バレンタインなら「ちょっとそわそわ」など） |
| `status_label` | string ≤20（任意） | `attends` の予定中の UI の状態表示。省略 = 行事ごとの既定（「バレンタイン」など） |
| `post_tags` / `post_probability` | | 行事のあとの投稿 |

- 行事が**仕事の繁忙期**のキャラ（玲奈のバレンタイン・クリスマスの厨房、莉子のバレンタインネイルの予約）は、
  `busyness` / `mood` / `status_label` を書いて「浮かれた行事」の既定の気分・表示にならないようにする
  （`attends: false` の行事に書いても使われない。検証で警告）。

行事の予定は全ユーザー共通の公開予定になるため、特定のユーザーとの関係を前提にした内容は書かない。

### proactive（自発メッセージ・§7）

| フィールド | 型 | 説明 |
|---|---|---|
| `frequency` | float 0〜3 | 頻度の倍率（ツンデレは低め、ギャルは高め） |
| `triggers` | string[] | `calendar_event` / `promise_due` / `seasonal` / `inactivity` / `feed_post` から（`paid_notice` は書けない） |
| `style` | string | どんなときに、どんな調子で送るか |
| `inactivity_days` | int 1〜30 | 何日話さなかったら様子をうかがうか（友達以上のみ） |
| `examples` | string[] ≥2 | 文面の例 |

- **返信がないことを責めない**（「無視」「既読スルー」「なんで返事」「待ってたのに」などは検証でエラー・P4）。
- **購入・課金・有料・トークン・来場や応援のお願いを書かない**（E2）。

### 各キャラの設計（要約）

| key | 生活（カレンダーの元） | 呼び方の変化（知り合い → 友達 → 気になる人 → 恋人） | 感度の特徴 |
|---|---|---|---|
| `ol_oneesan` | 平日出社（8:00 駅前のカフェ、金曜は同期と飲み会が多い）、土曜は喫茶店・古本屋、日曜は片づけと自炊 | `{name}さん` → `{name}ちゃん` → `{name}` → `{name}` | 信頼が動きやすい。ときめきはゆっくり |
| `osananajimi` | 水族館（木〜月、5:30 起床・22:00 就寝）、火水は二度寝・港の手伝い・食堂 | `あんた` → `{name}` → `{name}` → `{name}`（名前で呼べるようになる） | 親しさ高め、ときめきは照れ隠しで遅れて出る |
| `kouhai` | Web制作会社（平日）、土曜ボルダリング、日曜は家事と勉強 | `{name}先輩` → `先輩` → `先輩` → `{name}さん` | 信頼・親しさが素直に上がる。不満は溜めない |
| `yandere` | 古書店（火〜日 11〜19時）、月曜は骨董市と押し花、毎晩日記と夜の散歩 | `{name}さん` → `{name}さん` → `{name}` → `{name}` | 独占欲 1.2（拗ね・おねだりのみ）、ときめき高め |
| `tsundere` | 洋菓子店（金〜水、4:00 起床・閉店後に練習）、木曜は道具街とジャージで映画 | `{name}さん`（接客）→ `アンタ` → `アンタ` → `{name}` | expression_delay 0.8（好意は内側で積み上がり、表に出るのが遅い） |
| `gyaru` | ネイルサロン（火曜以外、夜型・2:00 就寝）、火曜は古着屋・サウナ・韓ドラ | `{name}さん`（接客）→ `{name}ちゃん` → `{name}` → `{name}` | 親しさがすぐ上がる。信頼は弱さを受け止めてもらえたときに |
| `ojousama` | 平日は茶舗とピアノ、土日はお稽古・庶民体験・ホテルのラウンジ演奏 | `{name}様` → `{name}様` → `{name}さん` → `{name}さん`（敬語のまま） | 信頼が動きやすく、不満はほとんど動かない |
| `isekai_elf` | 異世界の森（光る板に映る日本時間で暮らす）。夜明けの見回り・調合・弓・星読み、日曜は安息日、深夜2時〜4時半は圏外 | `{name}殿` → `{name}` → `{name}` → `{name}` | 信頼を重んじ、ときめきはゆっくり |
| `tonari_okusan` | 花屋のパート（火〜土の午前）、パン焼き、月曜は夫と過ごす、日曜はパン教室 | `お隣さん` → `{name}さん` → `{name}ちゃん` →（恋人段階なし） | romance 0（既婚・恋愛関係にならない） |
| `idol` | 平日は喫茶店バイトとレッスン、土日はライブと反省会、深夜にSNS更新 | `{name}さん` → `{name}ちゃん` → `{name}` → `{name}` | 関係は会話だけで深まる（来場や応援の量では動かない） |

## シードデータ（feed.yaml → seed.sql）

`seed.sql` は **手で編集しない**。`seed/feed.yaml` とペルソナYAMLを直して再生成する。

```bash
pnpm --filter @everkano/personas seed:generate   # infra/supabase/seed.sql を再生成
pnpm --filter @everkano/personas validate        # 検証（seed.sql が最新かどうかも確認）
pnpm db:reset                                     # ローカルDBを作り直してシード投入（他の作業者がいるときは実行しない）
```

生成されるデータ:

| テーブル | 件数 | 内容 |
|---|---|---|
| `characters` | 10 | 固定UUID `00000000-0000-4000-8000-0000000000c1` 〜 `…-000000000c10`（feed.yaml の並び順）。`system_prompt` は YAML から組み立てた静的テキスト（YAML が読めない場合のフォールバック） |
| `posts` | 50 | 各キャラ5件。固定UUID `00000000-0000-4000-8001-<キャラ番号10桁><投稿番号2桁>`。各キャラ1件が有料（`price_tokens` 80〜200） |
| `post_private_assets` | 10 | 有料投稿の本体画像（クライアントからは到達不能） |
| `comments` | 142 | 各投稿2〜5件。キャラ同士のコメントと、投稿者本人の返信（17投稿で `parent_comment_id` 付き）。固定UUID `…-8002-…` |

### 公開日時（published_at）の決め方

投稿の時刻は `now()` からの相対値で、**日本時間でキャラの生活リズムに合う時刻**に置く（「出社前のコーヒー」は平日の朝、
「ライブありがとう」は週末の夜、など）。feed.yaml の `at` で指定する。

| `at` | 意味 |
|---|---|
| `{day: 0, time: "22:40"}` | 直近に過ぎた 22:40（必ず24時間以内 → ストーリー枠に並ぶ）。9体がこれを持つ |
| `{day: -3, time: "10:05", days: [1,2,3,5,6,7]}` | 3日前の 10:05。その日が `days`（ISO曜日 1=月〜7=日 / `weekday` / `weekend`）に当たらなければ前にずらす |
| `{day: 2, time: "21:10"}` | 2日後の 21:10 の **予約投稿**。RLS により公開時刻まで誰にも見えない。`days` 指定時は後ろにずらす |
| `{lead: "3 hours"}` | `now() + 3時間` の予約投稿（時刻を問わない内容用） |

予約投稿は6件（+3時間 / +9時間 / 翌日 / 2日後 / 3日後 / 5日後）あり、時間が経つとフィードに新しい投稿が現れる。
コメントの `created_at` は投稿の `published_at + after` 分。公開から4時間未満の投稿では、未来時刻のコメントに
ならないよう経過時間に比例して圧縮する。

> シードは投入時点の `now()` 基準なので、数日たつと「24時間以内の投稿」が減り、予約投稿もすべて公開済みになる。
> デモ前には `pnpm db:reset` で入れ直すこと。

### 画像URL

`avatar_url` / `image_url` は開発用のプレースホルダ（`api.dicebear.com` / `picsum.photos`）。Web は StorageAdapter
（`NEXT_PUBLIC_STORAGE_DRIVER`）経由で解決し、http(s) の絶対URLはそのまま使う。本番（`bunny` ドライバ）では
オブジェクトキー（例: `characters/misaki/avatar.jpg`）に置き換える。有料投稿の `posts.image_url` は
別に用意した低解像度のぼかしプレビュー（`…/400/400?blur=10`）で、本体は `post_private_assets.image_url`。

**プレビューのURL / キーから本体のURL / キーを推測できないようにする**（クライアントに見えるのはプレビューだけ）。
「本体URL + `?blur=10`」のようにクエリを外すだけで本体に届く形や、slug・連番を含むキーは不可。シードでは
プレビューと本体に別々のハッシュ値（`opaque_image_key("preview" | "private", slug)`）を使い、`generate_seed.py`
が推測できないことを検証する。本番の Bunny でも `previews/<uuid>.jpg` と `private/<別の uuid>.jpg` のように
無関係なキーにすること。

## キャラクターを追加する手順

1. **YAML を作る**: `packages/personas/<key>.yaml`（既存ファイルをコピーすると早い）。`key` はファイル名と同じにする。
   `engine:` セクションも書く（上の「`engine:` セクション」）。とくに次の順で考えると矛盾が出にくい:
   1. `schedule_pattern`（人が読む生活リズム）を書き、`life.routine` を同じ時刻で曜日ごとに24時間埋める
      （睡眠 → 仕事・通勤 → 食事 → 夜の順に）。休みの曜日・夜ふかしの曜日は別のブロックにする
   2. その人の生活・性格に合う単発の出来事を8件以上（`weekly_probability` は現実的に）
   3. 15の行事それぞれへの反応（行くものは `attends: true` と時刻）
   4. 好感度の感度と4段階の振る舞い（呼び方・口調・例文）、自発メッセージの調子
   5. feed.yaml の投稿時刻・内容が routine と食い違っていないか確認する
2. **feed.yaml に追加**: `characters` の **末尾** に `{key, follower_count, joined_days_ago}` を足し
   （並び順が固定UUIDの番号になるので既存の順番は変えない）、`posts.<key>` に4〜6件の投稿を書く
   （有料1〜2件、各投稿のコメント2〜5件）。
3. **seed.sql を再生成**: `pnpm --filter @everkano/personas seed:generate`
4. **検証**: `pnpm --filter @everkano/personas validate`
5. **DB に反映**: `pnpm db:reset`。DB を作り直せない環境では、そのキャラ分の行を手で投入する
   （既存の固定UUIDキャラを消して入れ直す場合は `delete from characters where id = '…'` で posts / comments も
   cascade で消える。コメント削除は監査ログ `comment.delete` に記録される点に注意）。
6. Python API がペルソナYAMLをキャッシュしている場合は、API を再起動する。

## 検証内容（`scripts/validate_personas.py`）

- 必須フィールドと型、`key` とファイル名の一致、`handle` の形式と一意性、`name` の一意性
- `age` が 20 以上の整数、`speech.examples` が5件以上、`memory_focus` が3〜5件、`profile` の長さ
- `speech.examples` に自分の `ng_words` が含まれていないこと
- 未成年を想起させる語（小学生・中学生・高校生・JK・JC・ロリ・幼女・児童・未成年・ランドセル・制服 など）と
  20歳未満の年齢表記（例: 「16歳」）が YAML / feed.yaml / seed.sql に含まれないこと
  （moderation.py と同じく NFKC・小文字化・カタカナ→ひらがなで正規化してから部分一致）
- `seed.sql` に各キャラの行（`handle` / `name` / `persona_key` / `avatar` / `bio`）があること
- `seed.sql` が `generate_seed.py` の生成結果と一致すること（手編集・再生成忘れの検出）
- API の Pydantic モデル（`app.services.persona.Persona` / `EngineProfile`）で読み込めること（engine の未知のキーもエラー）。
  そのため `apps/api` の uv 環境で実行する（`uv run --frozen --project ../../apps/api python scripts/validate_personas.py`）
- `engine:` の追加規則（`scripts/engine_checks.py`）: 全キャラに engine があること / routine の曜日ごとの重なり・すき間・
  `24:00` 表記・睡眠の語 / 単発の出来事 8件以上・`key` の重複・18時間以内 / タグ語彙 / 期待投稿数 週14件以下 /
  seasonal 10件以上・`SEASONAL_KEYS`・`attends` の必須項目 / `{name}` 以外のプレースホルダ・例文のプレースホルダ /
  知り合い段階の自発頻度 ≤ 0.5 / ヤンデレの possessiveness > 0・人妻の romance = 0 と `max_stage`（`close` 以下）/ E2（課金・有料・購入・買って・
  トークン・物販 など）・E3（「人間だよ」「AIじゃない」など）・Gate #1（`moderation.py`）の語 / 自発メッセージの責める表現。
  `apps/api/tests/fixtures/personas/test_persona.yaml` の engine も同じ規則で検証する
- 最後に、キャラごとの要約（routine のブロック数・出来事・行事・期待投稿数・呼び方の変化）を表示する

## コンテンツの方針（キャラを書くときのルール）

- **全員成人**（`age` ≥ 20）。学生時代・制服・学校生活を現在形で描かない。ペットや他人も含め「〜歳」で20歳未満の年齢を書かない。
- **PG-13 まで**。甘い・からかう・照れる程度。性的に露骨な表現は書かない。有料投稿も「オフショット」「部屋着」「ドレス」程度。
- **ヤンデレ**は嫉妬・独占欲を「拗ねる」「おねだり」で表現する。暴力・脅し・自傷の表現は一切使わない。
- **人妻**は気さくなご近所さん。夫を大切にしており、不倫・浮気を匂わせる関係にはしない。
- **実在の人物名・実在ブランドの推奨は書かない**（地名は可）。
- Gate #1 の部分一致に引っかかる語を避ける（例: カタカナの「ロリ」を含む語は、ひらがな化すると「ろり」になり、
  「ほろり」「とろり」なども一致しうる）。
