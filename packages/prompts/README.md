# @everkano/prompts — LLM プロンプトテンプレート

Python API（`apps/api`）が起動時に読み込み、リクエストごとに値を埋め込んで LLM に渡すテンプレート集。
仕様書 §8.2 の構成をそのまま使っている。コードを変更せずに文面を調整できる。

- 配置: `packages/prompts/templates/*.ja.txt`（UTF-8）
- 読み込み先の変更: 環境変数 `PROMPTS_DIR`（未設定時はリポジトリ内のこのディレクトリ。Docker イメージでは `/srv/everkano/prompts`）
- 描画コード: `apps/api/app/services/prompt.py`（`PromptBuilder`）
- 起動時に「必須プレースホルダがあるか」「未知のプレースホルダが無いか」を検証し、違反があれば API は起動しない

## 書式

| 記法 | 意味 |
|---|---|
| `{name}` | プレースホルダ。英小文字・数字・`_` のみ。値で置換される |
| `{"key": ...}` | JSON の例など、`{` の直後が英小文字でないものは置換されない（そのまま出力される） |
| `=== user ===` の行 | この行より前が **system** メッセージ、後ろが **user** メッセージになる。無ければ全体が system |

テンプレートを編集したら `apps/api` で `uv run pytest tests/test_prompt.py` を実行して検証できる。

## テンプレート一覧

### `dm_system.ja.txt` — DM 応答のシステムプロンプト（`POST /chat`）

| プレースホルダ | 必須 | 値 |
|---|---|---|
| `{name}` | ✓ | ペルソナの `name` |
| `{profile}` | ✓ | ペルソナの `profile`（YAML が無いキャラは `characters.system_prompt`） |
| `{speech}` | ✓ | `speech` を箇条書きにしたもの（口調・文の長さ・絵文字・一人称/呼び方・話し方の例・NGワード） |
| `{relationship}` | ✓ | `relationship.initial` / `progression` |
| `{memories}` | ✓ | 長期記憶（検索上位 K 件を重要度順 + 最新の要約 最大2件）。1行1件。`secret` タグは先頭に `（二人だけの秘密）`、`summary` タグは `（これまでの会話の要約）` が付く。無ければ「（まだ特にない）」 |
| `{short_term}` | ✓ | 直近の会話についての**注記のみ**（下記） |
| `{schedule}` | | 現在の日本時間（例: 2026年9月25日（金）21:30）と `schedule_pattern` |
| `{first_person}` / `{second_person}` | | 一人称 / ユーザーの呼び方 |
| `{now}` / `{archetype}` / `{bio}` | | 現在時刻 / タイプ / 自己紹介（任意で使用可） |

**`{short_term}` の扱い（設計判断）**: 直近の会話（最大 `MEMORY_SHORT_TERM_TURNS`×2 件）は system プロンプトに
文字列として埋め込まず、system の後ろに `user` / `assistant` の会話履歴メッセージとして渡す
（Chat Completions の自然な形で、モデルが話者を取り違えにくい）。二重に入れないよう、`{short_term}` には
「直近 N 件のやりとりはこのあとの会話履歴として渡されます」という注記（会話が無ければ「これが最初のやりとり」）
だけを入れる。LLM に渡るメッセージ列は次の通り:

```
[system: dm_system を描画したもの]
[assistant: キャラの過去発言] [user: ユーザーの過去発言] ...   ← 短期メモリ（古い順）
[user: 今回のユーザー発言]
```

同じ role が連続する場合（例: 入力モデレーションで差し止めた直後）は1メッセージに結合する。
描画済みのメッセージ列は `AUDIT_LOG_PROMPTS=true` のとき `audit_logs`（`chat.response` の `prompt_messages`）に保存される。

### `memory_extraction.ja.txt` — 重要記憶の抽出（§9.2）

`/chat` の応答生成と**並行して**1回だけ呼ばれる（temperature 0、JSON モード）。

| プレースホルダ | 必須 | 値 |
|---|---|---|
| `{recent_context}` | ✓ | 直近 6 件の会話（`ユーザー: …` / `<name>: …`） |
| `{user_message}` | ✓ | 今回のユーザー発言 |
| `{name}` / `{memory_focus}` / `{second_person}` | | キャラ名 / `memory_focus` の箇条書き / 呼び方 |

出力: `{"memories": [{"content": "ユーザーは〜", "importance": 0.0〜1.0, "category": "emotion|personal|promise|relationship"}]}`。
API は寛容にパースし（コードブロック・前後の文章を許容）、`importance >= MEMORY_IMPORTANCE_THRESHOLD` のものだけを保存する。

### `memory_summary.ja.txt` — 中期メモリの要約（§9.1）

未要約メッセージが `2 × MEMORY_SUMMARY_TRIGGER_TURNS` 件を超えたとき、短期ウィンドウより古い部分を
バックグラウンドで要約する（temperature 0、JSON モード）。

| プレースホルダ | 必須 | 値 |
|---|---|---|
| `{conversation}` | ✓ | 要約対象の会話ログ（古い順。1発言 300 文字・全体 12000 文字で切り詰め） |
| `{name}` | | キャラ名 |

出力: `{"summary": "..."}`。`tags={'summary'}`・重要度 0.7 の記憶として保存される。

### `comment_reply.ja.txt` — 投稿へのコメント返信（`POST /comments` の自動返信・`POST /comments/generate`）

| プレースホルダ | 必須 | 値 |
|---|---|---|
| `{name}` | ✓ | キャラ名 |
| `{post_caption}` | ✓ | 返信する投稿のキャプション |
| `{comment_body}` | ✓ | 返信先コメントの本文 |
| `{bio}` / `{speech}` / `{comment_style}` | | 自己紹介 / 話し方 / ペルソナの `comment_style` |
| `{profile}` / `{first_person}` / `{second_person}` | | 任意で使用可 |

出力の1行目だけを使い（200 文字まで）、Gate #1（出力、ペルソナの `ng_words` 含む）を通ったものだけを保存する。

## モック（`LLM_MODE=mock`）との関係

モック LLM はテンプレートの描画結果ではなく、構造化されたヒント（ペルソナ・検索された記憶・時刻）から
決定的に応答を作る。ただしテンプレートは mock でも毎回描画され、監査ログにも保存されるため、
文面の確認はモックのままでも行える。
