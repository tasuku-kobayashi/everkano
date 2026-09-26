-- =============================================================================
-- キャラクターカレンダー（Calendar Engine, 仕様 §5）の追加索引
--
-- スケジューラの calendar.tick（5 分ごと）が使う問い合わせ用の部分索引。予定は 1 キャラ 1 日あたり数件〜十数件
-- 増え続けるが、未完了（scheduled）の予定と投稿待ちの予定は常に少数なので、部分索引で一定の速さに保つ。
--   * 過ぎた予定の完了: where character_id = $1 and status = 'scheduled' and ends_at <= $now
--   * 投稿待ちの予定:   where character_id = $1 and status = 'done' and post_id is null and meta->>'post_state' = 'pending'
-- スキーマ（列・権限）は変えない（DB 型の再生成は不要）。
-- =============================================================================

create index if not exists character_events_scheduled_ends_idx
  on public.character_events (character_id, ends_at)
  where status = 'scheduled';

create index if not exists character_events_post_pending_idx
  on public.character_events (character_id, ends_at)
  where post_id is null and (meta->>'post_state') = 'pending';
