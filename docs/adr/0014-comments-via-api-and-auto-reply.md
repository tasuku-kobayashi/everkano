# ADR-0014: コメント投稿の API 経由化とキャラ自動返信

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §5.3・§7 `POST /comments/generate`・§10 / [ADR-0002](0002-data-access-split.md) / [ADR-0010](0010-gate1-moderation.md) / 実装: `apps/api/app/services/comments.py`, `apps/api/app/routers/comments.py`, `apps/web/lib/queries/comments.ts`, `packages/prompts/templates/comment_reply.ja.txt`

## コンテキスト

- 仕様書 §5.3: 投稿詳細でユーザーがコメントでき、キャラクターもコメントできる（自動コメント生成）。§7 は `POST /comments/generate`
  （投稿者キャラが指定コメントに返信する任意機能）を定義しているが、ユーザーのコメント作成 API は定義していない。
- ユーザーのコメントもユーザー由来のテキストなので、Gate #1 と監査ログを通したい（[ADR-0002](0002-data-access-split.md)）。
- 「コメントするとキャラが返してくれる」ことは「生きている」感（§18）に直結する。

## 決定

- **`POST /comments`** を追加する（クライアントは `comments` に INSERT できない）:
  1. 投稿が公開済み（`published_at <= now()`）かつ有効キャラのものか、返信先（`parent_comment_id`）が同じ投稿のコメントかを確認（違えば 404）。
  2. Gate #1（入力）。ヒットなら `moderation.flag` を記録して **422 `moderation_blocked`（保存しない）**。
  3. 保存して `comment.create` を記録し、`CreateCommentResponse{comment, reply_scheduled}` を 201 で返す。
  4. 確率 `COMMENT_AUTO_REPLY_PROBABILITY`（既定 1.0）で、**投稿者キャラの返信を FastAPI の BackgroundTask として予約**する。
     バックグラウンドでは `comment_reply.ja.txt` で 1 文の返信を生成し（最大 120 トークン・1 行目のみ・200 文字まで）、Gate #1（出力。
     キャラ別 NG ワード込み）を通して `parent_comment_id` = ユーザーのコメントで保存する（`comment.generate`、`trigger=auto`）。
     差し止めたら保存しない（`comment.generate` に `moderated=true` を残す）。LLM の失敗は `llm.error` に残す。
  5. 返信は **Supabase Realtime（`comments` の INSERT）** で画面に届く。クライアントは返信をポーリングしない。
- **`POST /comments/generate`**（仕様書 §7）も実装する: 指定コメントに今すぐ返信する（`trigger=manual`）。キャラ自身のコメントへの返信は 422。
  出力が差し止められたら `{comment: null}`、LLM 障害は 503。現在の Web の画面からは呼んでいない（API クライアントには `generateCommentReply` がある）。
- 2 つのエンドポイントはユーザー単位のレート制限 `RATE_LIMIT_COMMENTS_PER_MINUTE`（10 / 分）を共有する（[ADR-0018](0018-in-process-rate-limit.md)）。
- **削除**は本人のコメントだけクライアントから直接 DELETE できる（RLS）。返信（子コメント）は `on delete cascade` で一緒に消え、
  どちらも DB トリガーが `comment.delete` を記録する。
- 他人のコメントの作成者は `user_` + ID 先頭 6 桁で匿名表示、自分のコメントは自分の `display_name` で表示する。

## 結果・トレードオフ

- コメントも DM と同じく Gate #1 と監査ログを必ず通る。E2E（`post-detail.spec.ts`）で、REST の再取得を遮断した状態でも自動返信が Realtime で
  表示されること、422 のコメントが保存されないことを確認している。
- BackgroundTask はプロセス内で動く。**返信の生成中にマシンが再起動・デプロイされると、その返信は失われる**（再試行のキューは無い）。
- 既定の確率 1.0 だとコメントごとに LLM を 1 回呼ぶ。コストを抑えるなら確率を下げる（`reply_scheduled` で UI 側も判定できる）。
- 返信は 1 段（キャラ → ユーザーのコメント）だけ。キャラ同士の会話や、キャラの返信へのユーザーの返信の連鎖は生成しない。

## 代替案

- **クライアントが投稿後に `/comments/generate` を呼ぶ**: 返信の有無がクライアントの実装次第になり、連打もしやすい。サーバー側で予約する方が確実。
- **DB トリガー + `pg_net` で LLM を呼ぶ**: プロンプト・モデレーション・監査ログのロジックが DB と API に分散する。
- **ジョブキュー（Redis / Cloud Tasks 等）**: 再起動に強いが、MVP には構成要素が多い。取りこぼしが問題になったら導入する。
