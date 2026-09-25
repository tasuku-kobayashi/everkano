-- =============================================================================
-- 09: 外部キーの参照側の索引（親の削除で子テーブルを全件走査しない）
--
--   Postgres は外部キーの参照側（子テーブルの列）に索引を自動では作らない。親の行を削除すると
--   on delete cascade / set null のトリガーが子テーブルを外部キー列で検索するため、索引が無いと
--   親 1 行ごとに子テーブル全体を走査する。例: ユーザーの物理削除（docs/handover/06-operations.md）は
--   その人の全メッセージを cascade で削除し、メッセージ 1 行ごとに memories.source_message_id を検索する
--   （索引なしでは「メッセージ数 × 全ユーザーの memories」の走査になり、数秒〜数分かかる）。
--
--   外部キーを追加したら、索引を作るか、下の許可リストに理由を書いて追加すること。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(7);

select has_index('public', 'memories', 'memories_source_message_id_idx', array['source_message_id'],
  'memories.source_message_id に索引がある（messages 削除時の on delete set null 用）');

select has_index('public', 'comments', 'comments_author_user_id_idx', array['author_user_id'],
  'comments.author_user_id に索引がある（ユーザーの物理削除の手順 1 と on delete set null 用）');

-- ---------------------------------------------------------------------------
-- 索引の無い外部キーは、理由を確認済みの許可リストと一致する
--   「索引がある」= 外部キー列だけで先頭の列が構成される（順不同）、式を含まない有効な索引。
--   部分索引は述語が「列 IS NOT NULL」のものだけ認める（外部キーのトリガーの検索 col = $1 は NOT NULL を含意する）。
-- ---------------------------------------------------------------------------
select set_eq(
  $$ select c.conname::text
       from pg_constraint c
      where c.contype = 'f'
        and c.connamespace = 'public'::regnamespace
        and not exists (
          select 1
            from pg_index i
           where i.indrelid = c.conrelid
             and i.indisvalid
             and i.indexprs is null
             and (i.indkey::int2[])[0:cardinality(c.conkey) - 1] @> c.conkey
             and (i.indkey::int2[])[0:cardinality(c.conkey) - 1] <@ c.conkey
             and (i.indpred is null or pg_get_expr(i.indpred, i.indrelid) ~ '^\(\w+ IS NOT NULL\)$')
        ) $$,
  array[
    -- キャラクターの物理削除は運用で稀（通常は is_active = false で非公開にする）。
    -- 削除 1 件につき各テーブルを 1 回走査するだけなので、書き込みの負荷を増やす索引は作らない。
    'comments_author_character_id_fkey',
    'conversations_character_id_fkey',
    'memories_character_id_fkey'
  ],
  '索引の無い外部キーは許可リスト（キャラクターの参照のみ）と一致する'
);

-- ---------------------------------------------------------------------------
-- 部分索引が、外部キーのトリガーと同じ形のクエリ（パラメータ付きの汎用プラン）で実際に使われる
-- ---------------------------------------------------------------------------
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

-- RI_FKey_setnull_del が発行するクエリと同じ形
prepare ek_memories_set_null(uuid) as
  update only public.memories set source_message_id = null
   where $1 operator(pg_catalog.=) source_message_id;
prepare ek_comments_set_null(uuid) as
  update only public.comments set author_user_id = null
   where $1 operator(pg_catalog.=) author_user_id;
-- 06-operations.md「ユーザーの物理削除」の手順 1
prepare ek_comments_delete_by_user(uuid) as
  delete from public.comments where author_user_id = $1;

select matches(
  pg_temp.plan_of('execute ek_memories_set_null(null)'),
  'memories_source_message_id_idx',
  'messages 削除時の memories の on delete set null は memories_source_message_id_idx を使う'
);

select matches(
  pg_temp.plan_of('execute ek_comments_set_null(null)'),
  'comments_author_user_id_idx',
  'profiles 削除時の comments の on delete set null は comments_author_user_id_idx を使う'
);

select matches(
  pg_temp.plan_of('execute ek_comments_delete_by_user(null)'),
  'comments_author_user_id_idx',
  'ユーザーの物理削除の手順 1（delete from comments where author_user_id = ...）は comments_author_user_id_idx を使う'
);

deallocate ek_memories_set_null;
deallocate ek_comments_set_null;
deallocate ek_comments_delete_by_user;

-- ---------------------------------------------------------------------------
-- 外部キーの on delete が想定どおり（索引の要否の前提）
-- ---------------------------------------------------------------------------
select is(
  (select string_agg(conname::text || '=' || confdeltype::text, ', ' order by conname)
     from pg_constraint
    where connamespace = 'public'::regnamespace
      and conname in ('memories_source_message_id_fkey', 'comments_author_user_id_fkey')),
  'comments_author_user_id_fkey=n, memories_source_message_id_fkey=n',
  'source_message_id / author_user_id の外部キーは on delete set null（メッセージ・ユーザーの削除で行は残す）'
);

select * from finish();
rollback;
