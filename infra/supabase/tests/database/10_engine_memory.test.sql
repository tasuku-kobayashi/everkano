-- =============================================================================
-- 10: Memory Engine（キャラクターエンジン v1.0 §4）のテーブル
--
--   * memories の新しい列（kind / status / superseded_* / 参照の記録）は本人だけが参照できる（M9・M11）
--   * promises は本人だけが参照でき、source_message_id / event_id は非公開。変更は API 経由のみ
--   * memory_tombstones（削除した記憶の墓標, E5）・character_memories（キャラ側の記憶, M8）はクライアント不可
--   * anon は Realtime 用に memories.id だけ SELECT できるが、行は 0 件（「覚えました」の通知は本人だけ）
--   * updated_at のトリガー: 参照の記録だけでは変えない / アプリの時計で指定した値を使う（時間の早送り）
--   * 列の check 制約（kind・status と superseded_at の整合）
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(30);

-- ---------------------------------------------------------------------------
-- Fixtures
-- ---------------------------------------------------------------------------
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                        raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-0000-0000-000000000000', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'authenticated',
   'authenticated', 'pgtap-user-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  ('00000000-0000-0000-0000-000000000000', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'authenticated',
   'authenticated', 'pgtap-user-b@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.characters (id, handle, name, avatar_url, persona_key, system_prompt, is_active)
values ('c1c1c1c1-0000-4000-8000-000000000001', 'pgtap_active', 'テスト有効キャラ',
        'https://example.test/c1.png', 'pgtap_active', 'x', true);

insert into public.conversations (id, user_id, character_id)
values ('f0a00000-0000-4000-8000-00000000000a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'c1c1c1c1-0000-4000-8000-000000000001');

insert into public.messages (id, conversation_id, sender_type, body)
values ('10a00000-0000-4000-8000-000000000001', 'f0a00000-0000-4000-8000-00000000000a', 'user',
        '来週の木曜、面接なんだよね');

-- A: 有効な記憶（転職後）と、置き換えられた古い記憶（履歴）/ B: 記憶 1 件
insert into public.memories (id, user_id, character_id, kind, content, importance, tags, embedding, status,
                             superseded_at, source_conversation_id, created_at, updated_at)
values
  ('20a00000-0000-4000-8000-000000000002', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c1c1c1c1-0000-4000-8000-000000000001', 'fact', 'ユーザーは銀行で働いている', 0.90, '{}',
   array_fill(0.01::real, array[1536])::extensions.vector, 'active', null,
   'f0a00000-0000-4000-8000-00000000000a', now() - interval '1 day', now() - interval '1 day'),
  ('20a00000-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c1c1c1c1-0000-4000-8000-000000000001', 'fact', 'ユーザーは広告代理店で働いている', 0.80, '{}',
   array_fill(0.02::real, array[1536])::extensions.vector, 'superseded', now() - interval '1 day',
   'f0a00000-0000-4000-8000-00000000000a', now() - interval '30 days', now() - interval '1 day'),
  ('20b00000-0000-4000-8000-000000000001', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
   'c1c1c1c1-0000-4000-8000-000000000001', 'preference', 'B の好きなもの', 0.70, '{}',
   array_fill(0.03::real, array[1536])::extensions.vector, 'active', null, null, now(), now());

update public.memories set superseded_by = '20a00000-0000-4000-8000-000000000002'
 where id = '20a00000-0000-4000-8000-000000000001';

insert into public.memory_tombstones (user_id, character_id, kind, content_hash, embedding)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', 'preference',
        repeat('a', 64), array_fill(0.04::real, array[1536])::extensions.vector);

insert into public.promises (id, user_id, character_id, content, due_at, due_precision, status,
                             source_memory_id, source_message_id, created_at, updated_at)
values
  ('30a00000-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c1c1c1c1-0000-4000-8000-000000000001', '面接', now() + interval '5 days', 'day', 'pending',
   '20a00000-0000-4000-8000-000000000002', '10a00000-0000-4000-8000-000000000001',
   now() - interval '1 day', now() - interval '1 day'),
  ('30b00000-0000-4000-8000-000000000001', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
   'c1c1c1c1-0000-4000-8000-000000000001', 'B の約束', null, 'unknown', 'pending', null, null, now(), now());

insert into public.character_memories (character_id, user_id, kind, content)
values
  ('c1c1c1c1-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'self_statement',
   '昨日カフェに行った'),
  ('c1c1c1c1-0000-4000-8000-000000000001', null, 'event', '同期と飲み会');

-- ---------------------------------------------------------------------------
-- 制約・トリガー（postgres = API のロール）
-- ---------------------------------------------------------------------------
select throws_ok(
  $$ insert into public.memories (user_id, character_id, content, kind)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', 'x', 'secret') $$,
  '23514', null, 'memories.kind は語彙（fact / preference / … / summary）以外を拒否する'
);

select throws_ok(
  $$ insert into public.memories (user_id, character_id, content, status)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', 'x', 'superseded') $$,
  '23514', null, 'superseded の記憶には superseded_at が必要（履歴の整合, M4）'
);

select throws_ok(
  $$ insert into public.promises (user_id, character_id, content, status)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', 'x', 'forgotten') $$,
  '23514', null, 'promises.status は pending / mentioned / done / cancelled のみ'
);

update public.memories
   set reference_count = reference_count + 1, last_referenced_at = now()
 where id = '20a00000-0000-4000-8000-000000000002';
select is(
  (select updated_at from public.memories where id = '20a00000-0000-4000-8000-000000000002'),
  now() - interval '1 day',
  '参照の記録（last_referenced_at / reference_count）だけの更新では memories.updated_at は変わらない'
);

update public.memories set importance = 0.95 where id = '20a00000-0000-4000-8000-000000000002';
select is(
  (select updated_at from public.memories where id = '20a00000-0000-4000-8000-000000000002'),
  now(),
  '内容（重要度など）が変わると memories.updated_at は now() になる'
);

update public.memories set content = '時間を早送りした更新', updated_at = '2030-01-01T00:00:00Z'
 where id = '20a00000-0000-4000-8000-000000000002';
select is(
  (select updated_at from public.memories where id = '20a00000-0000-4000-8000-000000000002'),
  '2030-01-01T00:00:00Z'::timestamptz,
  'アプリが指定した memories.updated_at はそのまま使う（評価ハーネスの時間の早送り）'
);

update public.promises set status = 'mentioned' where id = '30a00000-0000-4000-8000-000000000001';
select is(
  (select updated_at from public.promises where id = '30a00000-0000-4000-8000-000000000001'),
  now(),
  'promises.updated_at は指定が無ければ now()'
);

update public.promises set status = 'done', updated_at = '2030-01-02T00:00:00Z'
 where id = '30a00000-0000-4000-8000-000000000001';
select is(
  (select updated_at from public.promises where id = '30a00000-0000-4000-8000-000000000001'),
  '2030-01-02T00:00:00Z'::timestamptz,
  'アプリが指定した promises.updated_at はそのまま使う'
);

-- ---------------------------------------------------------------------------
-- User A（authenticated）
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select set_eq(
  $$ select id from public.memories $$,
  array['20a00000-0000-4000-8000-000000000001', '20a00000-0000-4000-8000-000000000002']::uuid[],
  'A には自分の記憶（置き換えられた履歴を含む）だけが見える'
);

select results_eq(
  $$ select kind, status, superseded_by, reference_count, source_conversation_id
       from public.memories where id = '20a00000-0000-4000-8000-000000000001' $$,
  $$ values ('fact'::text, 'superseded'::text, '20a00000-0000-4000-8000-000000000002'::uuid, 0,
             'f0a00000-0000-4000-8000-00000000000a'::uuid) $$,
  'A はメモリパネル用の新しい列（kind / status / superseded_by / reference_count / source_conversation_id）を参照できる'
);

select lives_ok(
  $$ select superseded_at, last_referenced_at from public.memories $$,
  'A は superseded_at / last_referenced_at を参照できる'
);

select throws_ok(
  $$ update public.memories set status = 'active', superseded_at = null
      where id = '20a00000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table memories',
  'クライアントは記憶の状態（履歴）を直接変えられない'
);

select results_eq(
  $$ select id, content, status from public.promises $$,
  $$ values ('30a00000-0000-4000-8000-000000000001'::uuid, '面接'::text, 'done'::text) $$,
  'A には自分の約束だけが見える'
);

select is_empty(
  $$ select 1 from public.promises where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $$,
  'A は B の約束を user_id 指定でも取得できない'
);

select throws_ok(
  $$ select source_message_id from public.promises $$,
  '42501', 'permission denied for table promises',
  'promises.source_message_id は非公開'
);

select throws_ok(
  $$ select event_id from public.promises $$,
  '42501', 'permission denied for table promises',
  'promises.event_id（カレンダーの予定）は非公開'
);

select throws_ok(
  $$ insert into public.promises (user_id, character_id, content)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', '偽の約束') $$,
  '42501', 'permission denied for table promises',
  'クライアントは約束を作れない（抽出は API のジョブ）'
);

select throws_ok(
  $$ update public.promises set status = 'cancelled' where id = '30a00000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table promises',
  'クライアントは約束の状態を直接変えられない（PATCH /promises/{id} 経由・監査ログに残す）'
);

select throws_ok(
  $$ delete from public.promises where id = '30a00000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table promises',
  'クライアントは約束を削除できない'
);

select throws_ok(
  $$ select id from public.memory_tombstones $$,
  '42501', 'permission denied for table memory_tombstones',
  'authenticated: memory_tombstones（削除した記憶の墓標）は参照できない'
);

select throws_ok(
  $$ delete from public.memory_tombstones $$,
  '42501', 'permission denied for table memory_tombstones',
  'authenticated: 墓標を消して削除した記憶を復活させることはできない（E5）'
);

select throws_ok(
  $$ select content from public.character_memories $$,
  '42501', 'permission denied for table character_memories',
  'authenticated: character_memories（キャラ側の記憶）は参照できない'
);

select throws_ok(
  $$ insert into public.character_memories (character_id, content)
     values ('c1c1c1c1-0000-4000-8000-000000000001', '偽の思い出') $$,
  '42501', 'permission denied for table character_memories',
  'authenticated: キャラ側の記憶を書き込めない'
);

-- ---------------------------------------------------------------------------
-- User B: Realtime（memories の INSERT）の RLS 判定で A の記憶は見えない
-- ---------------------------------------------------------------------------
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';

select is(
  (select exists (select 1 from public.memories where id = '20a00000-0000-4000-8000-000000000002')),
  false,
  'B: Realtime の RLS 判定（主キーで存在確認）は false → A の「覚えました」は B に配信されない'
);

select results_eq(
  $$ select id from public.promises $$,
  array['30b00000-0000-4000-8000-000000000001'::uuid],
  'B には自分の約束だけが見える'
);

-- ---------------------------------------------------------------------------
-- anon
-- ---------------------------------------------------------------------------
reset role;
set local role anon;
set local request.jwt.claims to '{"role":"anon"}';

select is(
  (select count(*)::int from public.memories),
  0,
  'anon: memories は主キー列だけ SELECT できるが、ポリシーが無いので 0 行'
);

select throws_ok(
  $$ select kind from public.memories $$,
  '42501', 'permission denied for table memories',
  'anon: memories の本文以外の列（kind）も参照できない'
);

select throws_ok(
  $$ select id from public.promises $$,
  '42501', 'permission denied for table promises',
  'anon: promises は参照できない'
);

select throws_ok(
  $$ select id from public.memory_tombstones $$,
  '42501', 'permission denied for table memory_tombstones',
  'anon: memory_tombstones は参照できない'
);

select throws_ok(
  $$ select id from public.character_memories $$,
  '42501', 'permission denied for table character_memories',
  'anon: character_memories は参照できない'
);

select * from finish();
rollback;
