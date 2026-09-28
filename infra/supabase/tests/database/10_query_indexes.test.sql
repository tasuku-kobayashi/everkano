-- =============================================================================
-- 10: 読み取りの多いクエリの索引（20260928000000_posts_conversations_indexes.sql）
--
--   キャラ別の投稿一覧（プロフィール・自発メッセージのきっかけ）と、自発メッセージの走査
--   （最近話した会話だけを読む）が、テーブルの全件走査や並べ替えにならず索引を使うこと。
--   09 と同じく、パラメータ付きの汎用プラン（実行時の値に依存しない）で確認する。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(4);

select has_index('public', 'posts', 'posts_character_id_published_at_idx',
  array['character_id', 'published_at', 'id'],
  'posts に (character_id, published_at, id) の索引がある（キャラ別の投稿一覧の絞り込みと並べ替え）');

select has_index('public', 'conversations', 'conversations_last_message_at_idx', array['last_message_at'],
  'conversations に last_message_at の索引がある（自発メッセージの走査）');

create function pg_temp.plan_of(query text)
returns text
language plpgsql
as $$
declare
  line record;
  result text := '';
begin
  for line in execute 'explain (costs off) ' || query loop
    result := result || line."QUERY PLAN" || E'\n';
  end loop;
  return result;
end;
$$;

set local enable_seqscan = off;
set local plan_cache_mode = force_generic_plan;

-- apps/web/lib/queries/feed.ts（characterId 指定）と proactive の _RECENT_POST_SQL と同じ形
prepare ek_character_posts(uuid) as
  select id from public.posts where character_id = $1
   order by published_at desc, id desc limit 20;
-- proactive の _PAIRS_SQL の絞り込み（ユーザーを指定しない走査）と同じ形
prepare ek_recent_conversations(timestamptz) as
  select id from public.conversations where last_message_at > $1;

select matches(
  pg_temp.plan_of('execute ek_character_posts(null)'),
  'posts_character_id_published_at_idx',
  'キャラ別の投稿一覧は posts_character_id_published_at_idx を使う（並べ替えの sort が要らない）'
);

select matches(
  pg_temp.plan_of('execute ek_recent_conversations(null)'),
  'conversations_last_message_at_idx',
  '最近話した会話の絞り込みは conversations_last_message_at_idx を使う'
);

deallocate ek_character_posts;
deallocate ek_recent_conversations;

select * from finish();
rollback;
