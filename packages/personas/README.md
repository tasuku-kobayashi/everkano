# @everkano/personas — キャラクター設定（ペルソナYAML）とシードデータ

AIキャラクター10体の人格定義と、フィード用のシード（投稿・コメント）を管理するパッケージ。

- `packages/personas/<key>.yaml` … 1キャラ1ファイルのペルソナ定義。Python API（`apps/api`）が読み込み、
  システムプロンプト（`packages/prompts`）・DMの最初の挨拶・コメント返信・モデレーション時の返答に使う
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
`?blur=10` のぼかしプレビューで、本体は `post_private_assets.image_url`。

## キャラクターを追加する手順

1. **YAML を作る**: `packages/personas/<key>.yaml`（既存ファイルをコピーすると早い）。`key` はファイル名と同じにする。
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

## コンテンツの方針（キャラを書くときのルール）

- **全員成人**（`age` ≥ 20）。学生時代・制服・学校生活を現在形で描かない。ペットや他人も含め「〜歳」で20歳未満の年齢を書かない。
- **PG-13 まで**。甘い・からかう・照れる程度。性的に露骨な表現は書かない。有料投稿も「オフショット」「部屋着」「ドレス」程度。
- **ヤンデレ**は嫉妬・独占欲を「拗ねる」「おねだり」で表現する。暴力・脅し・自傷の表現は一切使わない。
- **人妻**は気さくなご近所さん。夫を大切にしており、不倫・浮気を匂わせる関係にはしない。
- **実在の人物名・実在ブランドの推奨は書かない**（地名は可）。
- Gate #1 の部分一致に引っかかる語を避ける（例: カタカナの「ロリ」を含む語は、ひらがな化すると「ろり」になり、
  「ほろり」「とろり」なども一致しうる）。
