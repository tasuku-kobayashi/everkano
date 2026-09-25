# 02. データフロー

主要な操作ごとに、どのコンポーネントがどの順番で何を読み書きするかを示す。構成要素と認証情報は [01-architecture.md](01-architecture.md)、
テーブルの詳細は [03-data-model.md](03-data-model.md)、API の仕様は [04-api.md](04-api.md)。

## 誰がどこに書くか（まとめ）

| テーブル              | クライアント（`authenticated`）                                 | Python API（`postgres`）                                   | DB トリガー / その他                          |
| --------------------- | --------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------- |
| `profiles`            | 本人の行を参照、`display_name` / `deleted_at`（退会）を更新      | 退会済みかの確認（参照のみ）                               | `auth.users` 作成時に自動作成                 |
| `characters`          | 公開列だけ参照                                                  | ペルソナ・`system_prompt` を参照                           | シード / 運用者の SQL                         |
| `posts`               | 公開済みを参照                                                  | コメント時に公開済みかを確認                               | シード / 運用者の SQL。`like_count` / `comment_count` はトリガー |
| `post_private_assets` | 不可                                                            | —（参照するコードは無い）                                  | シード / 運用者の SQL                         |
| `likes`               | 本人の行を参照・作成・削除                                      | —                                                          | —                                             |
| `comments`            | 参照、自分のコメントを削除                                      | ユーザーのコメント作成、キャラの返信作成                    | 削除を `audit_logs` に記録                    |
| `conversations`       | 本人の会話を参照、既読（RPC）                                    | 作成、`summary_cursor` の更新                              | `last_message_at` はトリガー                  |
| `messages`            | 本人の会話のものを参照（Realtime で購読）                        | ユーザー発言とキャラ返答を 1 トランザクションで保存、初回挨拶 | —                                           |
| `memories`            | 本人の記憶を参照（`embedding` 以外。UI は API の一覧を使う）      | 抽出・重複排除・要約・メモリパネルの CRUD                  | `updated_at` はトリガー                       |
| `audit_logs`          | 不可                                                            | すべてのイベント（チャットとは別接続）                     | `comment.delete`                              |

## 1. ログイン

[ADR-0012](../adr/0012-pwa-and-login-magic-link-otp.md)。リンクとコードのどちらでも完了する。

```mermaid
sequenceDiagram
  autonumber
  actor U as ユーザー
  participant L as Web /login（ブラウザ）
  participant A as Supabase Auth
  participant M as メール（本番は SMTP、ローカルは Mailpit）
  participant C as Web /auth/confirm（サーバー）
  participant DB as Postgres
  U->>L: メールアドレスを入力
  L->>A: signInWithOtp(email, emailRedirectTo = SITE_URL/auth/callback)
  A->>DB: 初回なら auth.users を作成（トリガーで profiles も作成）
  A->>M: Magic Link（初回は Confirm signup）テンプレートで送信
  Note over M: リンク /auth/confirm?token_hash=...&type=email&next=/ と 6 桁コード
  alt メールのリンクをタップ（どのブラウザでもよい）
    U->>C: GET /auth/confirm?token_hash=...
    C->>A: verifyOtp(token_hash, type)
    A-->>C: セッション（Cookie を発行）
    C->>DB: profiles.deleted_at を確認（RLS。本人の行）
    C-->>U: 303 → next（退会済みならサインアウトして /login?error=withdrawn）
  else 6 桁コードを入力（ホーム画面の PWA）
    U->>L: コードを入力（6 桁で自動送信）
    L->>A: verifyOtp(email, token, type = email)
    A-->>L: セッション
    L->>DB: profiles.deleted_at を確認
    L-->>U: ホームへ遷移
  end
  Note over L,A: 以後 middleware.ts が全リクエストでセッション Cookie を更新する
```

## 2. フィードの表示

ページはクライアントコンポーネントで、データはブラウザから PostgREST へ直接取得する（Next.js のサーバーは関与しない）。

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（HomeFeed）
  participant R as PostgREST
  participant DB as Postgres（RLS）
  participant CDN as 画像（StorageAdapter が解決した URL）
  B->>R: GET posts（投稿 + character 埋め込み + my_likes）order published_at desc, id desc limit 10
  R->>DB: authenticated として実行
  DB-->>R: published_at ≦ now() かつ有効キャラの投稿だけ（予約投稿は見えない）
  R-->>B: 10 件
  B->>CDN: 画像を lazy load（有料投稿はプレビュー画像 + CSS ぼかし + 鍵）
  B->>R: characters + 各キャラの最新投稿 1 件（ストーリーズの行）
  loop 下までスクロール
    B->>R: 次ページ（カーソル: 最後の投稿より published_at が古いもの。同時刻は id で比較）
    R-->>B: 10 件（0〜9 件なら最後のページ → 「すべて確認済みです」）
  end
```

- カーソル方式なので、途中で予約投稿が公開されて先頭に増えてもページ境界で重複・欠落しない。
- 有料投稿をタップするとロックモーダル（「購入する（準備中）」→ トースト）。本体画像はどこからも取得しない（[ADR-0006](../adr/0006-paid-post-private-assets.md)）。

## 3. いいね

テキストを含まないため、クライアントから Supabase へ直接書く（監査ログの対象外）。

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ
  participant R as PostgREST
  participant DB as Postgres
  B->>B: 楽観的更新（ハート表示・like_count を +1。ダブルタップでも同じ）
  B->>R: INSERT likes(user_id = 自分, post_id)
  R->>DB: RLS: user_id = auth.uid() かつ投稿が見える
  DB->>DB: トリガー likes_sync_post_like_count → posts.like_count + 1
  alt 失敗（ネットワーク・RLS 違反）
    R-->>B: エラー
    B->>B: ロールバックしてトースト
  else 既にいいね済み（23505）
    R-->>B: 一意制約違反 → 成功として扱う
  end
  Note over B,DB: 取り消しは DELETE likes（本人の行のみ）→ like_count - 1
```

## 4. コメントとキャラの自動返信

[ADR-0014](../adr/0014-comments-via-api-and-auto-reply.md)。

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（投稿詳細）
  participant RT as Supabase Realtime
  participant API as Python API
  participant DB as Postgres
  participant L as LLM
  B->>DB: コメント一覧を取得（PostgREST。created_at 昇順、created_at ≦ now() のみ）
  B->>RT: 購読 comments INSERT / DELETE（filter post_id = この投稿）
  B->>API: POST /comments {post_id, body, parent_comment_id}
  API->>API: JWT 検証・退会確認・レート制限（comments 10 / 分）
  API->>DB: 投稿が公開済みか・返信先が同じ投稿か（違えば 404）
  API->>API: Gate ＃1（入力）
  alt ヒット
    API->>DB: audit moderation.flag
    API-->>B: 422 moderation_blocked（保存しない → トースト）
  else 通過
    API->>DB: INSERT comments（author_type = user）→ comment_count + 1
    API->>DB: audit comment.create
    API-->>B: 201 {comment, reply_scheduled}
    Note over API: 確率 COMMENT_AUTO_REPLY_PROBABILITY で BackgroundTask を予約
    API->>L: comment_reply テンプレートで 1 文生成
    API->>API: Gate ＃1（出力、キャラ別 NG ワード込み）
    alt 通過
      API->>DB: INSERT comments（author_type = character, parent_comment_id = ユーザーのコメント）
      API->>DB: audit comment.generate（trigger = auto）
      DB-->>RT: INSERT イベント
      RT-->>B: キャラの返信を表示（RLS に一致する購読者だけ）
    else ヒット
      API->>DB: audit moderation.flag と comment.generate（moderated = true）、保存しない
    end
  end
```

- 自分のコメントの削除はブラウザから直接 `DELETE comments`（RLS で本人のみ）。返信は cascade で消え、トリガーが `comment.delete` を記録する。

## 5. DM を開く

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（/dm/[characterId]）
  participant API as Python API
  participant R as PostgREST / Realtime
  participant DB as Postgres
  B->>API: POST /conversations {character_id}
  alt 初回
    API->>DB: INSERT conversations + キャラの挨拶（persona.greeting）を messages に（1 トランザクション）
    API->>DB: audit conversation.create
    API-->>B: {conversation, created: true, greeting_message}
  else 既存
    API-->>B: {conversation, created: false, greeting_message: null}
  end
  B->>R: messages を新しい順に 30 件（上へスクロールで過去へ）
  B->>R: 購読 messages INSERT（filter conversation_id）
  B->>R: RPC mark_conversation_read(conversation_id)（未読バッジを消す）
```

- DM 一覧（`/dm`）は RPC `list_dm_threads()`（最新メッセージ・未読数）と、`messages` の INSERT の購読で更新する。

## 6. DM を送る（`POST /chat`、13 ステップ）

[ADR-0009](../adr/0009-memory-engine.md)（メモリ）、[ADR-0010](../adr/0010-gate1-moderation.md)（Gate #1）、[ADR-0019](../adr/0019-chat-deadline.md)（締め切り）。
番号は `apps/api/app/services/chat.py` の冒頭コメントと同じ。

```mermaid
sequenceDiagram
  participant B as ブラウザ（DM 画面）
  participant API as Python API
  participant DB as Postgres
  participant E as Embedding
  participant L as LLM
  B->>B: 自分の吹き出しを「送信中」で表示、0.4 秒後に「入力中…」
  B->>API: POST /chat {character_id, conversation_id, message}
  Note over API: (1) JWT 検証 → user_id、退会なら 403、レート制限（chat 20 / 分）
  API->>DB: (2) 会話がこのユーザー・このキャラのもので、キャラが有効か（違えば 404）
  API->>DB: (3) audit chat.request
  API->>API: (4) Gate ＃1（入力）
  alt 入力がヒット
    API->>DB: audit moderation.flag（stage = input）
    API->>DB: ユーザー発言と定型文（moderation_reply）を 1 トランザクションで保存
    API->>DB: audit chat.response（moderated = true、LLM・記憶なし）
    API-->>B: 200 ChatResponse（moderated = true）
  else 通過（ここから締め切り CHAT_DEADLINE_SECONDS）
    API->>DB: (5) 短期: 直近 60 件（30 ターン）
    API->>E: (6) 発言を埋め込み
    API->>DB: (6) このペアの記憶を厳密検索 → 上位 5 件を重要度で再ランク + 最新の要約 2 件
    API->>API: (7) ペルソナ YAML + dm_system テンプレート + 記憶 + 履歴でプロンプト組立
    par (8) 返答生成
      API->>L: chat（temperature 0.8）
    and (8) 記憶抽出
      API->>L: memory_extraction（temperature 0、JSON）
    end
    API->>API: (9) Gate ＃1（出力。ヒットなら定型文に差し替え、audit moderation.flag）
    API->>DB: (10) ユーザー発言 → キャラ返答を 1 トランザクションで INSERT（last_message_at はトリガー）
    API->>E: (11) 重要度 0.6 以上の候補を埋め込み
    API->>DB: (11) 重複排除（cos 0.92 以上: ユーザー編集済みはスキップ / それ以外は更新）→ 新規は INSERT、audit memory.create / update
    API->>DB: (13) audit chat.response（返答・モデル・レイテンシ・使用量・使った / 作った記憶・プロンプト全文）
    API-->>B: 200 ChatResponse（reply, memories_used, memories_created, user_message, character_message）
    Note over API,DB: (12) 応答後の BackgroundTask: 未要約が 100 件を超えたら古い部分を要約して summary 記憶を作り、summary_cursor を進める（audit memory.summary）
  end
  B->>B: 「入力中…」を最低 1.1 秒見せてから返答を表示。記憶が作られたら「〇〇があなたのことを覚えました」
  Note over B,DB: 他のタブ・端末には Realtime（messages INSERT）で届く
```

- **LLM の失敗（リトライ後）または締め切り超過**: audit `llm.error` を記録して **503 `llm_unavailable`**。メッセージは何も保存しない。
  画面では自分の吹き出しが「送信失敗」になり、タップで再送できる。
- 記憶の抽出が締め切りに間に合わない / 失敗した場合はチャットを成功させ、`llm.error`（`purpose = memory_extraction`）を記録する。

## 7. メモリパネルでの編集

DM 画面ヘッダーの「i」で開く。すべて Python API 経由（Gate #1 + 監査ログ）。

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（メモリパネル）
  participant API as Python API
  participant E as Embedding
  participant DB as Postgres
  B->>API: GET /memories?character_id=...
  API->>DB: 本人 × キャラの記憶（重要度 desc, created_at desc、最大 500 件）
  API-->>B: memories
  B->>API: POST /memories {character_id, content, importance, tags(secret)}
  API->>API: Gate ＃1（入力。ヒットなら 422 moderation_blocked）
  API->>E: 埋め込み（失敗なら 503）
  API->>DB: INSERT memories（is_user_edited = true）、audit memory.create（source = user）
  API-->>B: 201 MemoryDTO
  B->>API: PATCH /memories/{id} {importance / tags / content}
  API->>DB: 本人の記憶か（違えば 404）
  API->>API: 本文が変わるときだけ Gate ＃1 と再埋め込み
  API->>DB: UPDATE（is_user_edited = true）、audit memory.update（before / after）
  B->>API: DELETE /memories/{id}（確認ダイアログの後）
  API->>DB: DELETE（本人の記憶のみ）、audit memory.delete
  API-->>B: 204
  Note over B,DB: UI は楽観的更新 → 失敗時ロールバック → 最後に再取得
```

- 削除した記憶は次の `/chat` の検索対象から外れる（`memories_used` に出ない）。ユーザーが追加・編集した記憶は自動抽出の重複排除で上書きされない。
- `secret` タグ（「二人だけの秘密」）の記憶は、プロンプトで `（二人だけの秘密）` を付けて渡る。

## 8. 退会

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（/me）
  participant R as PostgREST
  participant DB as Postgres
  B->>R: UPDATE profiles set deleted_at = 現在時刻（本人の行。列 grant で許可）
  R->>DB: トリガー guard_profile_withdrawal（以後の変更・取り消しは 42501）
  B->>B: 全端末のセッションを無効化（signOut scope = global）→ /login?error=withdrawn
  Note over B,DB: 以後: ログイン直後と AccountGuard でサインアウト（/login?error=withdrawn）、API は 403 account_deleted
```

データ（会話・記憶）は削除されない。復旧・物理削除は運用者が行う（[06-operations.md](06-operations.md#定常作業)）。
