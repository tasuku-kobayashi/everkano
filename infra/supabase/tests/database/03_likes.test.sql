-- =============================================================================
-- 03: likes — 本人のいいねのみ参照・作成・削除。見えない投稿にはいいねできない
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(15);

-- ---------------------------------------------------------------------------
-- Fixtures
--   User A / User B
--   Char C1（有効）/ C2（無効）
--   P1, P1b: 公開済み（C1） / P2: 予約投稿（C1） / P3: 無効キャラの投稿（C2）
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
  ('c2c2c2c2-0000-4000-8000-000000000002', 'pgtap_inactive', 'テスト無効キャラ',
   'https://example.test/c2.png', 'pgtap_inactive', 'x', false);

insert into public.posts (id, character_id, image_url, published_at)
values
  ('d1000000-0000-4000-8000-000000000001', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p1.jpg', now() - interval '1 hour'),
  ('d1b00000-0000-4000-8000-00000000001b', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p1b.jpg', now() - interval '2 hours'),
  ('d2000000-0000-4000-8000-000000000002', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p2.jpg', now() + interval '1 day'),
  ('d3000000-0000-4000-8000-000000000003', 'c2c2c2c2-0000-4000-8000-000000000002',
   'https://example.test/p3.jpg', now() - interval '1 hour');

-- ---------------------------------------------------------------------------
-- User B: P1 にいいね
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';

select lives_ok(
  $$ insert into public.likes (user_id, post_id)
     values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'd1000000-0000-4000-8000-000000000001') $$,
  'B は公開投稿 P1 に自分のいいねを作成できる'
);

-- ---------------------------------------------------------------------------
-- User A
-- ---------------------------------------------------------------------------
reset role;
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select lives_ok(
  $$ insert into public.likes (user_id, post_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'd1000000-0000-4000-8000-000000000001') $$,
  'A は公開投稿 P1 に自分のいいねを作成できる'
);

select results_eq(
  $$ select user_id from public.likes $$,
  array['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid],
  'A には自分のいいねだけが見える（B のいいねは見えない）'
);

select throws_ok(
  $$ insert into public.likes (user_id, post_id)
     values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'd1b00000-0000-4000-8000-00000000001b') $$,
  '42501', 'new row violates row-level security policy for table "likes"',
  'A は B になりすましていいねを作成できない'
);

select throws_ok(
  $$ insert into public.likes (user_id, post_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'd2000000-0000-4000-8000-000000000002') $$,
  '42501', 'new row violates row-level security policy for table "likes"',
  '予約投稿（未公開）にはいいねできない'
);

select throws_ok(
  $$ insert into public.likes (user_id, post_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'd3000000-0000-4000-8000-000000000003') $$,
  '42501', 'new row violates row-level security policy for table "likes"',
  '無効キャラの投稿にはいいねできない'
);

select throws_ok(
  $$ update public.likes set created_at = now() - interval '1 year'
      where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '42501', 'permission denied for table likes',
  'likes は UPDATE できない'
);

select lives_ok(
  $$ delete from public.likes where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $$,
  'A による B のいいね削除はエラーにならない（RLS により 0 行）'
);

-- ---------------------------------------------------------------------------
-- postgres で確認
-- ---------------------------------------------------------------------------
reset role;

select is(
  (select count(*)::int from public.likes
    where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
      and post_id = 'd1000000-0000-4000-8000-000000000001'),
  1,
  'B のいいねは A に削除されていない'
);

select is(
  (select count(*)::int from public.likes
    where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
      and post_id = 'd1b00000-0000-4000-8000-00000000001b'),
  0,
  'なりすましのいいねは作成されていない'
);

select is(
  (select count(*)::int from public.likes
    where post_id in ('d2000000-0000-4000-8000-000000000002', 'd3000000-0000-4000-8000-000000000003')),
  0,
  '不可視投稿へのいいねは作成されていない'
);

-- ---------------------------------------------------------------------------
-- User A: 自分のいいねを取り消す
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select lives_ok(
  $$ delete from public.likes where post_id = 'd1000000-0000-4000-8000-000000000001' $$,
  'A は自分のいいねを削除できる'
);

select is_empty(
  $$ select 1 from public.likes $$,
  'A のいいねは 0 件になった'
);

reset role;

select is(
  (select count(*)::int from public.likes where post_id = 'd1000000-0000-4000-8000-000000000001'),
  1,
  'A の削除は自分のいいねにだけ作用し、B のいいねは残っている'
);

-- ---------------------------------------------------------------------------
-- User B: A のいいねは見えない（対称性）
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';

select results_eq(
  $$ select user_id, post_id from public.likes $$,
  $$ values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid, 'd1000000-0000-4000-8000-000000000001'::uuid) $$,
  'B には自分のいいねだけが見える'
);

reset role;

select * from finish();
rollback;
