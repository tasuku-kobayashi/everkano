-- =============================================================================
-- 読み取りの多いクエリに合わせた索引（レイテンシーの補修）
--
--   posts: キャラのプロフィール（GET /@handle）と自発メッセージのきっかけ（proactive の _RECENT_POST_SQL）は
--     `where character_id = $1 [and is_paid = $2] order by published_at desc, id desc` で読む。
--     既存の posts_character_id_is_paid_idx（character_id, is_paid）は絞り込みにしか使えず、
--     並べ替えはヒープを読んでから sort する。投稿が増えるキャラ（予定からの自動投稿）ほど遅くなるため、
--     絞り込みと並べ替えの両方に使える (character_id, published_at desc, id desc) を作る。
--     フィード全体（キャラを絞らない）は既存の posts_published_at_idx のまま。
--
--   conversations: 自発メッセージの走査（10 分ごと）は `where last_message_at > now() - N days` で
--     ユーザーを絞らずに全会話を読む。既存の (user_id, last_message_at desc) は先頭が user_id なので
--     範囲の絞り込みに使えず、会話の数だけ全件走査になる。last_message_at 単独の索引を作る。
--
--   どちらも通常の create index（テーブルは小さい前提）。本番で大きくなってから後追いで作る場合は
--   マイグレーション外で create index concurrently を使うこと（20260925000000_init.sql の注記と同じ）。
-- =============================================================================

create index posts_character_id_published_at_idx
  on public.posts (character_id, published_at desc, id desc);
comment on index public.posts_character_id_published_at_idx is
  'キャラ別の投稿一覧（プロフィール・自発メッセージのきっかけ）の絞り込みと並べ替え';

create index conversations_last_message_at_idx
  on public.conversations (last_message_at desc);
comment on index public.conversations_last_message_at_idx is
  '自発メッセージの走査（最近話した会話だけを読む）';
