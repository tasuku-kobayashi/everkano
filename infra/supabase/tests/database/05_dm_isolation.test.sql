-- =============================================================================
-- 05: DM（conversations / messages / memories）のユーザー間分離 — 受け入れ基準 A13
--
--   * 他ユーザーの会話・メッセージ・メモリは取得できない
--   * クライアントは会話・メッセージ・メモリを作成 / 編集 / 削除できない（API 経由のみ）
--   * memories.embedding は参照できない
--   * RPC list_dm_threads / mark_conversation_read は本人の会話にしか作用しない
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(32);

-- ---------------------------------------------------------------------------
-- Fixtures
--   User A: aaaaaaaa-...  / User B: bbbbbbbb-...
--   C1: 有効キャラ / C3: 有効キャラ（2つ目）
--   convA : A × C1（既読 1時間前。以降にキャラ発言 2件 = 未読 2）
--   convA2: A × C3（メッセージ無し）
--   convB : B × C1（既読 1時間前。以降にキャラ発言 1件 = 未読 1）
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
values
  ('c1c1c1c1-0000-4000-8000-000000000001', 'pgtap_active', 'テスト有効キャラ',
   'https://example.test/c1.png', 'pgtap_active', 'x', true),
  ('c3c3c3c3-0000-4000-8000-000000000003', 'pgtap_second', 'テスト2人目',
   'https://example.test/c3.png', 'pgtap_second', 'x', true);

insert into public.conversations (id, user_id, character_id, user_last_read_at, last_message_at)
values
  ('f0a00000-0000-4000-8000-00000000000a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c1c1c1c1-0000-4000-8000-000000000001', now() - interval '1 hour', now() - interval '3 hours'),
  ('f0a20000-0000-4000-8000-0000000000a2', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c3c3c3c3-0000-4000-8000-000000000003', now() - interval '1 hour', now() - interval '3 hours'),
  ('f0b00000-0000-4000-8000-00000000000b', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
   'c1c1c1c1-0000-4000-8000-000000000001', now() - interval '1 hour', now() - interval '3 hours');

insert into public.messages (id, conversation_id, sender_type, body, created_at)
values
  ('10a00000-0000-4000-8000-000000000001', 'f0a00000-0000-4000-8000-00000000000a', 'character',
   'はじめまして', now() - interval '2 hours'),
  ('10a00000-0000-4000-8000-000000000002', 'f0a00000-0000-4000-8000-00000000000a', 'user',
   '来週大阪に出張するんだ', now() - interval '50 minutes'),
  ('10a00000-0000-4000-8000-000000000003', 'f0a00000-0000-4000-8000-00000000000a', 'character',
   'A への返信1', now() - interval '40 minutes'),
  ('10a00000-0000-4000-8000-000000000004', 'f0a00000-0000-4000-8000-00000000000a', 'character',
   'A への返信2', now() - interval '30 minutes'),
  ('10b00000-0000-4000-8000-000000000001', 'f0b00000-0000-4000-8000-00000000000b', 'user',
   'B の秘密の話', now() - interval '20 minutes'),
  ('10b00000-0000-4000-8000-000000000002', 'f0b00000-0000-4000-8000-00000000000b', 'character',
   'B への返信', now() - interval '10 minutes');

insert into public.memories (id, user_id, character_id, content, importance, tags, embedding,
                             source_message_id, is_user_edited)
values
  ('20a00000-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c1c1c1c1-0000-4000-8000-000000000001', 'ユーザーは「来週大阪に出張する」と話していた', 0.80, '{}',
   array_fill(0.01::real, array[1536])::extensions.vector,
   '10a00000-0000-4000-8000-000000000002', false),
  ('20b00000-0000-4000-8000-000000000001', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
   'c1c1c1c1-0000-4000-8000-000000000001', 'B との二人だけの秘密', 0.90, '{secret}',
   array_fill(0.02::real, array[1536])::extensions.vector,
   '10b00000-0000-4000-8000-000000000001', true);

-- ---------------------------------------------------------------------------
-- User A
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

-- ---- 参照の分離（A13）
select set_eq(
  $$ select id from public.conversations $$,
  array['f0a00000-0000-4000-8000-00000000000a', 'f0a20000-0000-4000-8000-0000000000a2']::uuid[],
  'A には自分の会話だけが見える'
);

select is_empty(
  $$ select 1 from public.conversations where id = 'f0b00000-0000-4000-8000-00000000000b' $$,
  'A は B の会話を ID 指定でも取得できない'
);

select set_eq(
  $$ select id from public.messages $$,
  array['10a00000-0000-4000-8000-000000000001', '10a00000-0000-4000-8000-000000000002',
        '10a00000-0000-4000-8000-000000000003', '10a00000-0000-4000-8000-000000000004']::uuid[],
  'A には自分の会話のメッセージだけが見える'
);

select is_empty(
  $$ select 1 from public.messages where conversation_id = 'f0b00000-0000-4000-8000-00000000000b' $$,
  'A は B の会話のメッセージを conversation_id 指定でも取得できない'
);

select is_empty(
  $$ select 1 from public.messages where body like '%秘密%' $$,
  'B のメッセージ本文は検索条件でもヒットしない'
);

select results_eq(
  $$ select id from public.memories $$,
  array['20a00000-0000-4000-8000-000000000001'::uuid],
  'A には自分のメモリだけが見える'
);

select is_empty(
  $$ select 1 from public.memories where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $$,
  'A は B のメモリを user_id 指定でも取得できない'
);

select throws_ok(
  $$ select embedding from public.memories $$,
  '42501', 'permission denied for table memories',
  'memories.embedding は参照できない'
);

select throws_ok(
  $$ select * from public.memories $$,
  '42501', 'permission denied for table memories',
  'memories の select * は失敗する（PUBLIC_MEMORY_COLUMNS を明示する必要がある）'
);

-- ---- 書き込み不可（API 経由のみ）
select throws_ok(
  $$ insert into public.conversations (user_id, character_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c3c3c3c3-0000-4000-8000-000000000003') $$,
  '42501', 'permission denied for table conversations',
  'クライアントは会話を作成できない（POST /conversations 経由）'
);

select throws_ok(
  $$ insert into public.messages (conversation_id, sender_type, body)
     values ('f0a00000-0000-4000-8000-00000000000a', 'character', 'キャラになりすまし') $$,
  '42501', 'permission denied for table messages',
  'クライアントは自分の会話にもメッセージを直接 INSERT できない（POST /chat 経由）'
);

select throws_ok(
  $$ insert into public.memories (user_id, character_id, content)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', '直接追加') $$,
  '42501', 'permission denied for table memories',
  'クライアントはメモリを直接 INSERT できない（POST /memories 経由）'
);

select throws_ok(
  $$ update public.messages set body = '改ざん' where id = '10a00000-0000-4000-8000-000000000003' $$,
  '42501', 'permission denied for table messages',
  'メッセージは UPDATE できない'
);

select throws_ok(
  $$ delete from public.messages where conversation_id = 'f0a00000-0000-4000-8000-00000000000a' $$,
  '42501', 'permission denied for table messages',
  'メッセージは DELETE できない'
);

select throws_ok(
  $$ update public.memories set content = '改ざん' where id = '20a00000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table memories',
  'メモリは直接 UPDATE できない（PATCH /memories/{id} 経由）'
);

select throws_ok(
  $$ delete from public.memories where id = '20a00000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table memories',
  'メモリは直接 DELETE できない（DELETE /memories/{id} 経由）'
);

select throws_ok(
  $$ delete from public.conversations where id = 'f0a00000-0000-4000-8000-00000000000a' $$,
  '42501', 'permission denied for table conversations',
  '会話は DELETE できない'
);

select throws_ok(
  $$ update public.conversations set summary_cursor = now() where id = 'f0a00000-0000-4000-8000-00000000000a' $$,
  '42501', 'permission denied for table conversations',
  'user_last_read_at 以外の列（summary_cursor）は更新できない'
);

select throws_ok(
  $$ update public.conversations set user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
      where id = 'f0b00000-0000-4000-8000-00000000000b' $$,
  '42501', 'permission denied for table conversations',
  '他人の会話を user_id 書き換えで乗っ取れない'
);

select lives_ok(
  $$ update public.conversations set user_last_read_at = now() + interval '1 day'
      where id = 'f0b00000-0000-4000-8000-00000000000b' $$,
  'A による B の会話の既読更新（直接 UPDATE）はエラーにならない（RLS により 0 行）'
);

select lives_ok(
  $$ select public.mark_conversation_read('f0b00000-0000-4000-8000-00000000000b') $$,
  'A が B の会話に mark_conversation_read を呼んでもエラーにならない（0 行）'
);

-- ---- RPC: list_dm_threads
select results_eq(
  $$ select conversation_id, character_id, character_handle, last_message_body, last_message_sender_type,
            unread_count
       from public.list_dm_threads() $$,
  $$ values
       ('f0a00000-0000-4000-8000-00000000000a'::uuid, 'c1c1c1c1-0000-4000-8000-000000000001'::uuid,
        'pgtap_active'::text, 'A への返信2'::text, 'character'::text, 2),
       ('f0a20000-0000-4000-8000-0000000000a2'::uuid, 'c3c3c3c3-0000-4000-8000-000000000003'::uuid,
        'pgtap_second'::text, null::text, null::text, 0) $$,
  'list_dm_threads は A の会話だけを新しい順に返し、未読数（既読以降のキャラ発言数）が正しい'
);

select results_eq(
  $$ select last_message_at from public.list_dm_threads()
      where conversation_id = 'f0a20000-0000-4000-8000-0000000000a2' $$,
  $$ values (now() - interval '3 hours') $$,
  'メッセージの無い会話は conversations.last_message_at を返す'
);

select lives_ok(
  $$ select public.mark_conversation_read('f0a00000-0000-4000-8000-00000000000a') $$,
  'A は自分の会話を既読にできる'
);

select results_eq(
  $$ select unread_count from public.list_dm_threads()
      where conversation_id = 'f0a00000-0000-4000-8000-00000000000a' $$,
  array[0],
  '既読にすると未読数が 0 になる'
);

-- ---------------------------------------------------------------------------
-- postgres で確認
-- ---------------------------------------------------------------------------
reset role;

select is(
  (select user_last_read_at from public.conversations where id = 'f0b00000-0000-4000-8000-00000000000b'),
  now() - interval '1 hour',
  'B の会話の user_last_read_at は A の操作で変わっていない'
);

select is(
  (select user_last_read_at from public.conversations where id = 'f0a00000-0000-4000-8000-00000000000a'),
  now(),
  'A の会話の user_last_read_at は mark_conversation_read で now() に更新された'
);

select is(
  (select count(*)::int from public.messages
    where conversation_id in ('f0a00000-0000-4000-8000-00000000000a', 'f0b00000-0000-4000-8000-00000000000b')),
  6,
  'メッセージは追加も削除もされていない'
);

-- ---------------------------------------------------------------------------
-- User B（対称性）
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';

select results_eq(
  $$ select id from public.conversations $$,
  array['f0b00000-0000-4000-8000-00000000000b'::uuid],
  'B には自分の会話だけが見える'
);

select set_eq(
  $$ select id from public.messages $$,
  array['10b00000-0000-4000-8000-000000000001', '10b00000-0000-4000-8000-000000000002']::uuid[],
  'B には自分の会話のメッセージだけが見える'
);

select results_eq(
  $$ select id, content, tags from public.memories $$,
  $$ values ('20b00000-0000-4000-8000-000000000001'::uuid, 'B との二人だけの秘密'::text, '{secret}'::text[]) $$,
  'B には自分のメモリだけが見える'
);

select results_eq(
  $$ select conversation_id, last_message_body, unread_count from public.list_dm_threads() $$,
  $$ values ('f0b00000-0000-4000-8000-00000000000b'::uuid, 'B への返信'::text, 1) $$,
  'list_dm_threads は B の会話だけを返す（未読 1）'
);

reset role;

select * from finish();
rollback;
