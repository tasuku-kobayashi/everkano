-- =============================================================================
-- 06: anon（未ログイン）は全テーブル・RPC にアクセス不可。
--     post_private_assets / audit_logs は authenticated からも一切アクセス不可。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(24);

-- ---------------------------------------------------------------------------
-- Fixtures（アクセスを試みる対象が存在する状態にしておく）
-- ---------------------------------------------------------------------------
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                        raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-0000-0000-000000000000', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'authenticated',
   'authenticated', 'pgtap-user-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.characters (id, handle, name, avatar_url, persona_key, system_prompt, is_active)
values ('c1c1c1c1-0000-4000-8000-000000000001', 'pgtap_active', 'テスト有効キャラ',
        'https://example.test/c1.png', 'pgtap_active', 'x', true);

insert into public.posts (id, character_id, image_url, is_paid, price_tokens, published_at)
values ('d4000000-0000-4000-8000-000000000004', 'c1c1c1c1-0000-4000-8000-000000000001',
        'https://example.test/p4-preview.jpg', true, 300, now() - interval '1 hour');

insert into public.post_private_assets (post_id, image_url)
values ('d4000000-0000-4000-8000-000000000004', 'https://example.test/p4-full.jpg');

insert into public.conversations (id, user_id, character_id)
values ('f0a00000-0000-4000-8000-00000000000a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'c1c1c1c1-0000-4000-8000-000000000001');

insert into public.audit_logs (event_type, user_id, payload)
values ('chat.request', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '{"request_id":"pgtap"}');

-- ---------------------------------------------------------------------------
-- anon（ログインしていないクライアント = anon key のみ）
-- ---------------------------------------------------------------------------
set local role anon;
set local request.jwt.claims to '{"role":"anon"}';

select throws_ok($$ select * from public.profiles $$, '42501', 'permission denied for table profiles',
  'anon: profiles 不可');
select throws_ok($$ select id, handle, name from public.characters $$, '42501',
  'permission denied for table characters', 'anon: characters（公開列でも）不可');
select throws_ok($$ select * from public.posts $$, '42501', 'permission denied for table posts',
  'anon: posts 不可');
select throws_ok($$ select * from public.post_private_assets $$, '42501',
  'permission denied for table post_private_assets', 'anon: post_private_assets 不可');
select throws_ok($$ select * from public.likes $$, '42501', 'permission denied for table likes',
  'anon: likes 不可');
select throws_ok($$ select * from public.comments $$, '42501', 'permission denied for table comments',
  'anon: comments 不可');
select throws_ok($$ select * from public.conversations $$, '42501',
  'permission denied for table conversations', 'anon: conversations 不可');
select throws_ok($$ select * from public.messages $$, '42501', 'permission denied for table messages',
  'anon: messages 不可');
select throws_ok($$ select id, content from public.memories $$, '42501',
  'permission denied for table memories', 'anon: memories 不可');
select throws_ok($$ select * from public.audit_logs $$, '42501', 'permission denied for table audit_logs',
  'anon: audit_logs 不可');

select throws_ok($$ select * from public.list_dm_threads() $$, '42501',
  'permission denied for function list_dm_threads', 'anon: list_dm_threads を実行できない');
select throws_ok($$ select public.mark_conversation_read('f0a00000-0000-4000-8000-00000000000a') $$, '42501',
  'permission denied for function mark_conversation_read', 'anon: mark_conversation_read を実行できない');

select throws_ok(
  $$ insert into public.likes (user_id, post_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'd4000000-0000-4000-8000-000000000004') $$,
  '42501', 'permission denied for table likes', 'anon: likes に INSERT できない');
select throws_ok(
  $$ insert into public.messages (conversation_id, sender_type, body)
     values ('f0a00000-0000-4000-8000-00000000000a', 'user', 'x') $$,
  '42501', 'permission denied for table messages', 'anon: messages に INSERT できない');
select throws_ok(
  $$ update public.profiles set deleted_at = now() $$,
  '42501', 'permission denied for table profiles', 'anon: profiles を UPDATE できない');

-- ---------------------------------------------------------------------------
-- authenticated: 非公開テーブル
-- ---------------------------------------------------------------------------
reset role;
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select throws_ok($$ select * from public.post_private_assets $$, '42501',
  'permission denied for table post_private_assets', 'authenticated: post_private_assets を参照できない');
select throws_ok(
  $$ insert into public.post_private_assets (post_id, image_url)
     values ('d4000000-0000-4000-8000-000000000004', 'https://example.test/evil.jpg') $$,
  '42501', 'permission denied for table post_private_assets', 'authenticated: post_private_assets に INSERT できない');
select throws_ok(
  $$ update public.post_private_assets set image_url = 'x' $$,
  '42501', 'permission denied for table post_private_assets', 'authenticated: post_private_assets を UPDATE できない');
select throws_ok(
  $$ delete from public.post_private_assets $$,
  '42501', 'permission denied for table post_private_assets', 'authenticated: post_private_assets を DELETE できない');

select throws_ok($$ select * from public.audit_logs $$, '42501', 'permission denied for table audit_logs',
  'authenticated: audit_logs を参照できない（自分のログも不可）');
select throws_ok(
  $$ insert into public.audit_logs (event_type, payload) values ('chat.request', '{}') $$,
  '42501', 'permission denied for table audit_logs', 'authenticated: audit_logs に書き込めない（偽装不可）');
select throws_ok(
  $$ update public.audit_logs set payload = '{}' $$,
  '42501', 'permission denied for table audit_logs', 'authenticated: audit_logs を改ざんできない');
select throws_ok(
  $$ delete from public.audit_logs $$,
  '42501', 'permission denied for table audit_logs', 'authenticated: audit_logs を削除できない');
select throws_ok(
  $$ select nextval('public.audit_logs_id_seq') $$,
  '42501', 'permission denied for sequence audit_logs_id_seq', 'authenticated: audit_logs のシーケンスを使えない');

reset role;

select * from finish();
rollback;
