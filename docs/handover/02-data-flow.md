# 02. データフロー

主要な操作ごとに、どのコンポーネントがどの順番で何を読み書きするかを示す。構成要素と認証情報は [01-architecture.md](01-architecture.md)、
テーブルの詳細は [03-data-model.md](03-data-model.md)、API の仕様は [04-api.md](04-api.md)。キャラクターエンジンの処理（6〜11）は
返答の経路（API）・返答の後のジョブ（worker）・定期実行（worker のスケジューラ）に分かれる（[ADR-0035](../adr/0035-character-engine-architecture.md)・
[ADR-0036](../adr/0036-engine-job-queue-and-scheduler.md)）。

## 誰がどこに書くか（まとめ）

「API・worker」は Python API（`app` プロセス）と worker プロセス（返答の後のジョブ・定期実行）。どちらも `postgres` ロールで RLS をバイパスし、クエリを user_id でスコープする。

| テーブル              | クライアント（`authenticated`）                                 | API・worker（`postgres`）                                  | DB トリガー / その他                          |
| --------------------- | --------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------- |
| `profiles`            | 本人の行を参照、`display_name` / `deleted_at`（退会）を更新      | 退会済みかの確認（参照のみ）                               | `auth.users` 作成時に自動作成                 |
| `characters`          | 公開列だけ参照                                                  | ペルソナ・`system_prompt` を参照                           | シード / 運用者の SQL                         |
| `posts`               | 公開済みを参照                                                  | コメント時に公開済みかを確認。**予定からの投稿を作成**（`calendar.tick`、`source_event_id`） | シード / 運用者の SQL。`like_count` / `comment_count` はトリガー |
| `post_private_assets` | 不可                                                            | —（参照するコードは無い）                                  | シード / 運用者の SQL                         |
| `likes`               | 本人の行を参照・作成・削除                                      | —                                                          | —                                             |
| `comments`            | 参照、自分のコメントを削除                                      | ユーザーのコメント作成、キャラの返信作成                    | 削除を `audit_logs` に記録                    |
| `conversations`       | 本人の会話を参照、既読（RPC）                                    | 作成、`summary_cursor`・`analyzed_until` の更新            | `last_message_at` はトリガー                  |
| `messages`            | 本人の会話のものを参照（Realtime で購読）                        | ユーザー発言とキャラ返答を 1 トランザクションで保存（`safety_triggered`）、初回挨拶、**自発メッセージ**（`is_proactive`） | —                    |
| `memories`            | 本人の記憶を参照（`embedding` 以外。Realtime で INSERT を購読。UI の一覧は API） | 返答の後の分析・統合・置き換え・要約・メモリパネルの CRUD・参照の記録 | `updated_at` はトリガー（内容が変わったときだけ） |
| `memory_tombstones`   | 不可                                                            | メモリパネルの削除で作成、自動抽出で照合                    | —                                             |
| `promises`            | 本人の約束を参照                                                | 返答の後の分析で作成・状態の変更、ユーザーの完了・取り消し、自発メッセージで `mentioned` | `updated_at` はトリガー             |
| `character_memories`  | 不可                                                            | 返答の後の分析（キャラの発言）、`calendar.tick`（終わった予定） | —                                         |
| `character_events`    | 不可                                                            | `calendar.ensure_schedules`（生成）・`calendar.tick`（完了）・約束の予定化 | 重なりは排他制約で拒否                 |
| `character_states`    | 有効キャラの `status_label` / `busyness` / `updated_at` を参照    | `calendar.tick` で更新                                     | —                                             |
| `affinity_states` / `affinity_history` | 不可（A11）                                    | 返答の後の評価・日次の減衰・会話のたびの `last_interaction_at` | —                                          |
| `proactive_messages`  | 不可                                                            | `proactive.scan` で送信の記録、ユーザーの発言で `replied_at` | —                                           |
| `proactive_settings`  | 本人の設定を参照                                                | `PUT /proactive/settings` で作成・更新                      | `updated_at` はトリガー                       |
| `engine_jobs` / `engine_schedules` | 不可                                               | ジョブの登録（API）・実行（worker）/ 定期実行の記録         | —                                             |
| `post_image_pool`     | 不可                                                            | 投稿の画像を選ぶ（参照のみ）                               | シード（`seed_engine.sql`）/ 運用者の SQL     |
| `audit_logs`          | 不可                                                            | すべてのイベント（チャットとは別接続）                     | `comment.delete`                              |

## 1. ログイン

[ADR-0012](../adr/0012-pwa-and-login-magic-link-otp.md)・[ADR-0026](../adr/0026-magic-link-confirm-page.md)・[ADR-0033](../adr/0033-auth-hardening-password-otp-captcha.md)。リンクとコードのどちらでも完了し、
どちらでもログイン前に開こうとしていたページ（`next`）へ戻る。リンクとコードの有効期限は 15 分。

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
  L->>A: signInWithOtp(email, emailRedirectTo = SITE_URL/auth/callback?next=（ログイン前のページ）)
  A->>DB: 初回なら auth.users を作成（トリガーで profiles も作成）
  A->>M: Magic Link（初回は Confirm signup）テンプレートで送信
  Note over M: リンク /auth/confirm?token_hash=...&type=email&redirect_to=（emailRedirectTo の値）と 6 桁コード
  alt メールのリンクをタップ（どのブラウザでもよい）
    U->>C: GET /auth/confirm?token_hash=...
    C-->>U: 確認画面「everkano にログインしますか？」（トークンはまだ検証しない。別のアカウントでログイン中なら切り替わると表示）
    U->>C: 「ログインする」→ POST /auth/confirm/verify（同一オリジンのフォーム送信のみ受け付ける。ADR-0026）
    C->>A: verifyOtp(token_hash, type)
    A-->>C: セッション（Cookie を発行）
    C->>DB: profiles.deleted_at を確認（RLS。本人の行）
    C-->>U: 303 → next（退会済みならサインアウトして /login?error=withdrawn）
  else 6 桁コードを入力（ホーム画面の PWA）
    U->>L: コードを入力（6 桁で自動送信）
    L->>A: verifyOtp(email, token, type = email)
    A-->>L: セッション
    L->>DB: profiles.deleted_at を確認
    L-->>U: next（ログイン前に開こうとしていたページ。無ければホーム）へ遷移
  end
  Note over L,A: 以後 middleware.ts が全リクエストでセッション Cookie を更新する
```

- コード入力待ちの状態（メールアドレスと送信時刻。コードは保存しない）は端末に 15 分保存する。メールアプリへ切り替えている間に iOS がホーム画面の
  PWA を再起動しても、コード入力画面から続けられる。送信間隔の制限（`over_email_send_rate_limit`）に当たった場合も、送信済みのコードの入力画面へ進む。
- 使用済み・期限切れのリンクは `/login?error=link&next=<元のページ>`。ログイン後に使えなくなったセッション（ユーザーの削除・利用停止）は、端末の
  セッションを消してから `/login?error=session` / `?error=banned` へ（`/` ⇄ `/login` のループにしない）。

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
- 先頭で Home タブ・ロゴを再タップ、下に引っ張る、5 分以上バックグラウンドにいた後に先頭で復帰、のいずれかで 1 ページ目から取り直す
  （ホーム画面の PWA にはブラウザの再読み込みが無いため。[ADR-0031](../adr/0031-web-ui-accessibility-and-dev-only-pages.md)）。
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
    API->>API: Gate ＃1（出力、キャラ別 NG ワード込み。公開されるので URL・ドメイン名も差し止め）
    alt 通過
      API->>DB: 同じコメントへの返信を直列化（アドバイザリーロック）し、返信がまだ無ければ INSERT comments（author_type = character, parent_comment_id = ユーザーのコメント）
      API->>DB: audit comment.generate（trigger = auto）
      DB-->>RT: INSERT イベント
      RT-->>B: キャラの返信を表示（RLS に一致する購読者だけ）
    else ヒット
      API->>DB: audit moderation.flag と comment.generate（moderated = true）、保存しない
    end
  end
```

- 自分のコメントの削除はブラウザから直接 `DELETE comments`（RLS で本人のみ）。返信は cascade で消え、トリガーが `comment.delete` を記録する。
- キャラの返信はコメント 1 件につき 1 件まで。`POST /comments/generate`（Web の画面からは未使用）は自分のコメントだけに使え、既に返信があれば
  LLM を呼ばずにそれを返す（[ADR-0027](../adr/0027-comment-reply-generation-limits.md)）。
- 一覧は新しい方から 500 件を取り、昇順に並べる（それより古いコメントは表示しない旨を出す）。返信の @メンションは公開名（`user_xxxxxx` /
  キャラのハンドル）だけで、自分のコメントへの返信にはメンションを入れない（表示名・メールアドレスを公開しない）。

## 5. DM を開く

既存の会話は Python API を経由せずに開く（[ADR-0030](../adr/0030-web-data-fetching-dm-and-prefetch.md)）。API が止まっていても既存の会話の履歴は読める。

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（/dm/[characterId]）
  participant R as PostgREST / Realtime
  participant API as Python API
  participant DB as Postgres
  Note over B: DM 一覧の行・「DMする」に触れた時点でキャラ情報（会話があれば最新ページ）を先読み
  B->>R: 自分の会話（user_id = 自分, character_id）+ 最新 30 件のメッセージを埋め込みで取得（RLS）
  alt 会話がある
    R-->>B: 会話 + メッセージ（1 往復で表示）
  else 会話がまだ無い
    B->>API: POST /conversations {character_id}
    API->>DB: INSERT conversations + キャラの挨拶（persona.greeting）を messages に（1 トランザクション）
    API->>DB: audit conversation.create
    API-->>B: {conversation, created: true, greeting_message}
  end
  B->>R: 購読 messages INSERT（filter conversation_id）
  B->>R: 差分の取得（キャッシュの最新より新しいものだけ。購読開始・再接続・画面復帰のたび）
  B->>R: RPC mark_conversation_read(conversation_id)（未読バッジを消す）
  Note over B,R: 上へスクロールで 30 件ずつ過去へ
```

- DM ヘッダーの 2 行目（キャラの今の状況）は `character_states` の `status_label` / `busyness` を PostgREST で直接読む（有効なキャラだけ。DM の先読みで一緒に取り、
  画面を開いている間は 2 分ごと・画面への復帰時に取り直す。[ADR-0047](../adr/0047-web-engine-ui.md)）。キャラからの自発メッセージも通常のメッセージとして履歴に入る。
- DM 一覧（`/dm`）は RPC `list_dm_threads()`（最新メッセージ・未読数）と、**自分の会話に絞った** `messages` の INSERT の購読
  （`conversation_id=in.(...)`、最大 100 件）、30 秒ごとのポーリングで更新する。

## 6. DM を送る（`POST /chat/stream`・`POST /chat`）

[ADR-0037](../adr/0037-chat-streaming-sse.md)（ストリーミング）、[ADR-0043](../adr/0043-safety-e6-and-output-guard.md)（E6・出力の検査）、[ADR-0035](../adr/0035-character-engine-architecture.md)（文脈）、
[ADR-0019](../adr/0019-chat-deadline.md)（締め切り）、[ADR-0010](../adr/0010-gate1-moderation.md)（Gate #1）。番号は `apps/api/app/engine/pipeline.py` の冒頭コメントと同じ。
`/chat` は同じパイプラインを最後まで読み、`done` の内容を JSON で返す。

```mermaid
sequenceDiagram
  participant B as ブラウザ（DM 画面）
  participant API as Python API（app）
  participant DB as Postgres
  participant E as Embedding
  participant L as LLM
  B->>B: 自分の吹き出しを「送信中」で表示、0.4 秒後に「入力中…」
  B->>API: POST /chat/stream {character_id, conversation_id, message}（fetch で SSE を読む）
  Note over API: (1) JWT 検証・退会なら 403・レート制限（chat 20 / 分）。会話・キャラの確認（違えば 404）。ここまでのエラーは通常の JSON
  API-->>B: 200 text/event-stream（`: ok`。以後イベントが無い間は 10 秒ごとに `: keep-alive`）
  API->>DB: (2) audit chat.request
  API->>API: (3) E6 の検出（Gate #1 より前）
  alt 自傷・希死念慮のシグナル
    API-->>B: replace {reason: safety, キャラの声の気づかい + 相談窓口}（LLM なし）
    API->>DB: 発言と返答を保存（返答に safety_triggered = true）、audit safety.trigger
  else Gate #1（入力）がヒット
    API->>DB: audit moderation.flag（stage = input）
    API-->>B: replace {reason: moderated, キャラの断り文}
    API->>DB: 発言と断り文を保存
  else 通過（ここから締め切り CHAT_DEADLINE_SECONDS）
    par (5) Context Assembler（締め切り ENGINE_CONTEXT_TIMEOUT_SECONDS。間に合わない要素は省いて audit engine.context_degraded）
      API->>DB: 短期の履歴（Gate #1 の発言は置き換え）
    and
      API->>E: 発言の埋め込み（EMBEDDING_TIMEOUT_SECONDS。失敗なら検索を省略）
      API->>DB: 記憶の厳密検索とランキング・期日の近い約束・キャラ側の記憶
    and
      API->>DB: 世界の時間・キャラの今の状態（予定）
    and
      API->>DB: ふたりの関係の指針（好感度の段階。値は見せない）
    end
    API->>L: (6) chat（stream: true）。system（静的）→ 直近の会話 → 〔今の状況〕+ 今回の発言
    loop 文の区切りごと
      API->>API: それまでの全文を Gate #1 + NG ワード + OutputGuard（E2 / E3）で検査
      API-->>B: delta {text}
    end
    alt 最終判定でヒット
      API->>DB: audit moderation.flag（stage = output）
      API-->>B: replace {reason: moderated, キャラの断り文}
    end
    API->>DB: (7) ユーザー発言 → キャラ返答を 1 トランザクションで INSERT（時刻は時計から。返答 = 発言 + 1ms）
  end
  API->>DB: (8) proactive_messages.replied_at（未返信の自発メッセージ）・affinity_states.last_interaction_at
  API->>DB: (9) engine_jobs に post_turn と memory.summarize を登録（会話ごとに 1 件。既にあれば実行時刻を後ろへ）
  API->>DB: (10) audit chat.response（ttft_ms・文脈の予算・使った記憶・状態・段階・usage・プロンプト全文）
  API-->>B: done（ChatResponse: 保存済みの 2 件。memories_created は常に空。E6 なら safety.resources）
  Note over API,DB: (11) 返答の後: 使った記憶の last_referenced_at / reference_count（バックグラウンド）
  B->>B: 最初の文字は送信から 1 秒後以降に表示。done で保存済みのメッセージに置き換え。E6 なら返答の下に相談窓口のカード
  B->>DB: RPC mark_conversation_read（1 往復につき 1 回）
  Note over B,DB: 他のタブ・端末には Realtime（messages INSERT）で届く。記憶は 7 の後に Realtime（memories INSERT）で「覚えました」
```

- **LLM の失敗（リトライ後）または締め切り超過**: `delta` を送った後でも `error`（503 `llm_unavailable`）を送り、**メッセージは何も保存しない**（`llm.error`）。
  画面では受信中の吹き出しを消し、自分の吹き出しが「送信できませんでした・タップで再送」になる。`/chat` は 503 を返す。
- クライアントが接続を切っても、生成・保存・ジョブの登録は最後まで行う（次に画面を開いたとき・Realtime で届く）。
- ストリーミングを使えない環境・`/chat/stream` の無い API では、Web は `POST /chat` にフォールバックする（[ADR-0047](../adr/0047-web-engine-ui.md)）。
- 端末がオフラインなら送信はすぐに失敗し、通信エラーのトーストが出る（[ADR-0029](../adr/0029-web-network-failure-policy.md)）。
- 返答待ちのまま画面を離れて戻っても、送った発言と受信中の返答は残り、同じタブからは二重に送れない（別のタブ・端末からの同時送信は防いでいない）。

## 7. 返答の後のジョブ（`post_turn`・`memory.summarize`）

worker のワーカーが処理する（[ADR-0036](../adr/0036-engine-job-queue-and-scheduler.md)・[ADR-0038](../adr/0038-memory-engine-v2.md)・[ADR-0041](../adr/0041-affinity-engine.md)）。
`post_turn` は返答から `ENGINE_POST_TURN_DELAY_SECONDS`（既定 180 秒）後に実行でき、続けて話すあいだは後ろにずれる（最大 18 分）。
**「覚えました」の通知と約束の登録は、ユーザーが話すのをやめてから約 3 分後（話し続ければ最大 18 分後）** になる。
通知はそのキャラの DM の画面を開いている間だけ出る（Realtime の購読は DM の画面。閉じた後に作られた記憶は、次にメモリパネルを開いたときに見える）。

```mermaid
sequenceDiagram
  autonumber
  participant W as worker（ワーカー）
  participant DB as Postgres
  participant E as Embedding
  participant L as LLM
  W->>DB: engine_jobs から実行時刻の来たジョブを 1 件取る（FOR UPDATE SKIP LOCKED）
  W->>DB: conversations.analyzed_until より新しいターン（最大 ENGINE_POST_TURN_MAX_TURNS）
  Note over W: Gate #1・E6 のターンは記憶・好感度から外す。操作の発言は置き換え（記憶経由の注入の防止）。相づちだけなら LLM を呼ばない
  W->>E: ユーザーの文と候補の本文を埋め込み
  W->>DB: 似ている既存の記憶・未達の約束
  W->>L: memory_analysis（JSON。add / update / supersede / noop・約束・約束の更新・キャラの発言）
  W->>DB: 1 トランザクション（ペアのロック）: 墓標・重複・ユーザー編集の保護・件数の上限を確かめて memories / promises / character_memories に書く
  W->>DB: audit memory.* / promise.* / character_memory.create / memory.analysis
  W->>DB: 新しい約束を character_events（visibility = user, kind = promise）に（calendar.promise_event）
  W->>W: 好感度: 操作の検知（ルール）→ 残りを affinity_eval で採点
  W->>L: affinity_eval（JSON。ターンごと・軸ごとに -2〜+2）
  W->>DB: 行ロックの中で上限・段階を計算して affinity_states / affinity_history を更新、audit affinity.*
  W->>DB: analyzed_until を進める → ジョブを done（残りがあれば続けて登録）
  Note over DB: memories の INSERT は Realtime で本人のブラウザへ →「〇〇があなたのことを覚えました」
```

- 途中で失敗した場合は、終えた手順を記録して再試行する（指数バックオフ。上限 `ENGINE_JOB_MAX_ATTEMPTS` で `dead`、audit `engine.job_dead`）。最後の試行でも失敗した手順は飛ばす。
- `memory.summarize`: 未要約のメッセージが 100 件（`MEMORY_SUMMARY_TRIGGER_TURNS` × 2）を超えたら、古い順にチャンクで要約して `kind = summary` の記憶を作る（[ADR-0028](../adr/0028-llm-input-budgets-and-summary-retries.md)）。

## 8. メモリパネルでの編集と約束

DM 画面ヘッダーの「i」で開く。変更はすべて Python API 経由（Gate #1 + 監査ログ）（[ADR-0038](../adr/0038-memory-engine-v2.md)・[ADR-0039](../adr/0039-user-edited-memory-protection.md)）。

```mermaid
sequenceDiagram
  autonumber
  participant B as ブラウザ（メモリパネル）
  participant API as Python API
  participant E as Embedding
  participant DB as Postgres
  B->>API: GET /memories?character_id=...&include_superseded=true
  API->>DB: 本人 × キャラの記憶（有効 + 置き換えられた履歴）
  API-->>B: memories（kind・status・superseded_by など）
  B->>API: GET /promises?character_id=...
  API-->>B: 未達の約束（期日の近い順）
  B->>API: POST /memories {character_id, content, importance, tags, kind}
  API->>API: レート制限（30 / 分、PATCH と共有）。summary のタグ・種類の指定は 422
  API->>DB: ペアの記憶の件数（500 件に達していたら 422）
  API->>API: Gate ＃1（入力。ヒットなら 422 moderation_blocked）
  API->>E: 埋め込み（10 秒で打ち切り。失敗なら 503 と audit llm.error = user_memory）
  API->>DB: ペア単位のロックを取って件数を数え直し、INSERT memories（is_user_edited = true）、audit memory.create（source = user）
  API-->>B: 201 MemoryDTO
  B->>API: PATCH /memories/{id} {importance / tags / content / kind}
  API->>DB: 本人の記憶か（違えば 404）。本文が変わるときだけ Gate ＃1 と再埋め込み
  API->>DB: UPDATE（is_user_edited = true）、audit memory.update（before / after）
  B->>API: DELETE /memories/{id}（確認ダイアログの後）
  API->>DB: この記憶から作られた未達の約束を取り消し（カレンダーの予定も取り消し。audit promise.status_change）
  API->>DB: 墓標（本文のハッシュと埋め込み）を memory_tombstones に → 記憶の行を削除、audit memory.delete
  API-->>B: 204
  B->>API: PATCH /promises/{id} {status: done | cancelled}
  API->>DB: 状態を変更（取り消しなら予定を取り消し、元の記憶を履歴に）、audit promise.status_change
  Note over B,DB: UI は楽観的更新 → 失敗時ロールバック（追加・編集の入力内容は消さずに戻す）→ 最後に再取得
```

- 削除した記憶は次の返答の検索対象から外れ、**自動抽出でも作り直されない**（墓標。ユーザーが追加し直すのは可）。ユーザーが追加・編集した記憶は自動処理で上書き・置き換えされない（E5）。
- `secret` タグ（「二人だけの秘密」）の記憶は、プロンプトで `（二人だけの秘密）` を付けて渡る。

## 9. キャラの予定と状態（`calendar.ensure_schedules`・`calendar.tick`）

worker のスケジューラ（リーダーの 1 台）が動かす（[ADR-0040](../adr/0040-character-calendar.md)）。LLM を使うのはフィードのキャプションだけ。

```mermaid
sequenceDiagram
  autonumber
  participant S as worker（スケジューラ）
  participant DB as Postgres
  participant L as LLM
  Note over S: 1 時間ごと: calendar.ensure_schedules
  S->>DB: 有効なキャラごとにアドバイザリーロック → 昨日〜7 日先の未生成の日を、ペルソナのテンプレートから決定的に生成
  S->>DB: 既存の予定と重なる分は切り取り、INSERT character_events（排他制約で重なりを拒否したものは audit calendar.conflict）、audit calendar.generate
  Note over S: 5 分ごと: calendar.tick
  S->>DB: 今の予定から状態を計算 → character_states を更新（予定が変わったら audit calendar.state_change）
  S->>DB: 終わった予定を done（calendar.event_done）→ 目立つ出来事を character_memories（全ユーザー共通）に（character_memory.create）
  S->>L: 投稿すると決まった予定のキャプション（feed_caption。1 キャラ 1 日 2 件まで）
  S->>S: Gate #1・NG ワード・リンク・OutputGuard（E2 / E3）
  S->>DB: post_image_pool から画像を選び INSERT posts（published_at = 終わりの 10〜60 分後、source_event_id）、audit calendar.post_create
  Note over DB: ブラウザは character_states（status_label）を DM ヘッダーに出す。posts は公開時刻を過ぎるとフィードに出る
```

## 10. 自発メッセージ（`proactive.scan`）

10 分ごと（[ADR-0042](../adr/0042-proactive-messenger.md)）。

```mermaid
sequenceDiagram
  autonumber
  participant S as worker（スケジューラ）
  participant DB as Postgres
  participant L as LLM
  participant B as ブラウザ
  S->>DB: 会話のあるペア（停止していない・30 日以内に話した）と、今日の送信数・最後の送信
  S->>S: ユーザー単位の制限（送らない時間帯・1 日 3 通・未返信の自発メッセージ・間隔）→ ペア単位の上限（段階 × ペルソナの頻度）
  S->>DB: きっかけ（約束の期日・終わった予定・行事・しばらく話していない・最近の投稿）
  S->>S: スコア → ユーザーごとに 1 件
  S->>L: proactive_message（ペルソナの口調・段階・状態・記憶）
  S->>S: Gate #1・OutputGuard・責める言い方の検査（ヒットなら送らず audit proactive.dropped）
  S->>DB: 1 トランザクション: 会話をロックして上限を数え直し → INSERT messages（is_proactive = true）+ proactive_messages、audit proactive.send
  S->>DB: 約束なら promises.status = mentioned
  DB-->>B: Realtime（messages INSERT）→ DM 一覧で未読・通常の吹き出し
```

## 11. 日次の処理（`affinity.daily`・`jobs.cleanup`）

| タスク | 時刻（JST） | 内容 |
| --- | --- | --- |
| `affinity.daily` | 毎日 4 時（`ENGINE_AFFINITY_DAILY_HOUR_JST`） | 気まずさ・不満・独占欲の減衰（audit `affinity.decay`）、日数の経過による段階の遷移（`affinity.stage_change`）。好意の軸は減らさない |
| `jobs.cleanup` | 毎日 3 時 | `running` のまま止まったジョブを戻す（上限なら `dead`）、完了から `ENGINE_JOB_RETENTION_DAYS` たったジョブを削除 |

## 12. 退会

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
