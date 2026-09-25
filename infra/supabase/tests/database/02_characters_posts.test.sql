-- =============================================================================
-- 02: characters / posts — 公開列のみ参照可、無効キャラ・予約投稿は不可視、書き込み不可
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(20);

-- ---------------------------------------------------------------------------
-- Fixtures
--   User A    : aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
--   Char C1   : c1c1c1c1-0000-4000-8000-000000000001（有効）
--   Char C2   : c2c2c2c2-0000-4000-8000-000000000002（無効 is_active=false）
--   Post P1   : 公開済み（C1）
--   Post P2   : 予約投稿（published_at が未来 / C1）
--   Post P3   : 無効キャラ C2 の公開済み投稿
--   Post P4   : 有料投稿（C1）+ post_private_assets
--   Post P5   : published_at = now() ちょうど（境界値 / C1）
-- ---------------------------------------------------------------------------
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                        raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-0000-0000-000000000000', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'authenticated',
   'authenticated', 'pgtap-user-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.characters (id, handle, name, avatar_url, bio, persona_key, system_prompt, is_active)
values
  ('c1c1c1c1-0000-4000-8000-000000000001', 'pgtap_active', 'テスト有効キャラ',
   'https://example.test/c1.png', 'bio', 'pgtap_active', 'INTERNAL SYSTEM PROMPT C1', true),
  ('c2c2c2c2-0000-4000-8000-000000000002', 'pgtap_inactive', 'テスト無効キャラ',
   'https://example.test/c2.png', 'bio', 'pgtap_inactive', 'INTERNAL SYSTEM PROMPT C2', false);

insert into public.posts (id, character_id, image_url, caption, is_paid, price_tokens, published_at)
values
  ('d1000000-0000-4000-8000-000000000001', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p1.jpg', '公開済み', false, 0, now() - interval '1 hour'),
  ('d2000000-0000-4000-8000-000000000002', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p2.jpg', '予約投稿', false, 0, now() + interval '1 day'),
  ('d3000000-0000-4000-8000-000000000003', 'c2c2c2c2-0000-4000-8000-000000000002',
   'https://example.test/p3.jpg', '無効キャラの投稿', false, 0, now() - interval '1 hour'),
  ('d4000000-0000-4000-8000-000000000004', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p4-preview.jpg', '有料投稿', true, 300, now() - interval '2 hours'),
  ('d5000000-0000-4000-8000-000000000005', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p5.jpg', '境界値', false, 0, now());

insert into public.post_private_assets (post_id, image_url)
values ('d4000000-0000-4000-8000-000000000004', 'https://example.test/p4-full.jpg');

-- ---------------------------------------------------------------------------
-- User A として
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

-- ---- characters
select lives_ok(
  $$ select id, handle, name, avatar_url, bio, follower_count, is_active, created_at
       from public.characters $$,
  'characters の公開列（PUBLIC_CHARACTER_COLUMNS）は参照できる'
);

select throws_ok(
  $$ select system_prompt from public.characters $$,
  '42501', 'permission denied for table characters',
  'characters.system_prompt は参照できない'
);

select throws_ok(
  $$ select persona_key from public.characters $$,
  '42501', 'permission denied for table characters',
  'characters.persona_key は参照できない'
);

select throws_ok(
  $$ select * from public.characters $$,
  '42501', 'permission denied for table characters',
  'characters の select * は失敗する（列を明示する必要がある）'
);

select throws_ok(
  $$ select id from public.characters where system_prompt like '%INTERNAL%' $$,
  '42501', 'permission denied for table characters',
  'WHERE 句経由でも system_prompt を参照できない'
);

select results_eq(
  $$ select handle from public.characters
      where id in ('c1c1c1c1-0000-4000-8000-000000000001', 'c2c2c2c2-0000-4000-8000-000000000002') $$,
  array['pgtap_active'],
  '無効キャラ（is_active=false）は見えない'
);

select throws_ok(
  $$ update public.characters set name = 'x' where id = 'c1c1c1c1-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table characters',
  'characters は更新できない'
);

select throws_ok(
  $$ insert into public.characters (handle, name, avatar_url, persona_key, system_prompt)
     values ('pgtap_evil', 'x', 'x', 'x', 'x') $$,
  '42501', 'permission denied for table characters',
  'characters に INSERT できない'
);

-- ---- posts
select results_eq(
  $$ select id from public.posts where id = 'd1000000-0000-4000-8000-000000000001' $$,
  array['d1000000-0000-4000-8000-000000000001'::uuid],
  '公開済み投稿は見える'
);

select is_empty(
  $$ select 1 from public.posts where id = 'd2000000-0000-4000-8000-000000000002' $$,
  'published_at が未来の投稿（予約投稿）は見えない'
);

select is_empty(
  $$ select 1 from public.posts where id = 'd3000000-0000-4000-8000-000000000003' $$,
  '無効キャラの投稿は見えない'
);

select results_eq(
  $$ select is_paid, price_tokens, image_url from public.posts
      where id = 'd4000000-0000-4000-8000-000000000004' $$,
  $$ values (true, 300, 'https://example.test/p4-preview.jpg'::text) $$,
  '有料投稿はプレビュー画像 URL のみ見える'
);

select results_eq(
  $$ select id from public.posts where id = 'd5000000-0000-4000-8000-000000000005' $$,
  array['d5000000-0000-4000-8000-000000000005'::uuid],
  'published_at = now() の投稿は見える（境界値）'
);

select throws_ok(
  $$ insert into public.posts (character_id, image_url)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'https://example.test/x.jpg') $$,
  '42501', 'permission denied for table posts',
  'ユーザーは投稿を作成できない（H2）'
);

select throws_ok(
  $$ update public.posts set like_count = 9999 where id = 'd1000000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table posts',
  'posts は更新できない（like_count の改ざん不可）'
);

select throws_ok(
  $$ update public.posts set is_paid = false where id = 'd4000000-0000-4000-8000-000000000004' $$,
  '42501', 'permission denied for table posts',
  '有料フラグを外すことはできない'
);

select throws_ok(
  $$ delete from public.posts where id = 'd1000000-0000-4000-8000-000000000001' $$,
  '42501', 'permission denied for table posts',
  'posts は削除できない'
);

-- ---- post_private_assets（有料投稿の本体）
select throws_ok(
  $$ select image_url from public.post_private_assets $$,
  '42501', 'permission denied for table post_private_assets',
  '有料投稿の本体画像（post_private_assets）は参照できない'
);

-- ---------------------------------------------------------------------------
-- postgres で実データを確認
-- ---------------------------------------------------------------------------
reset role;

select is(
  (select like_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  0,
  'like_count は改ざんされていない'
);

select is(
  (select count(*)::int from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  1,
  '投稿は削除されていない'
);

select * from finish();
rollback;
