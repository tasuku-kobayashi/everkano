-- =============================================================================
-- 01: profiles — 本人のみ参照・更新（display_name / deleted_at のみ）
--
-- ユーザーのなりすまし:
--   set local role authenticated;
--   set local request.jwt.claims to '{"sub":"<uuid>","role":"authenticated"}';
-- （PostgREST がリクエストごとに行う設定と同じ。auth.uid() はこの claims を読む）
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(23);

-- ---------------------------------------------------------------------------
-- Fixtures（postgres ロールで作成。rollback で消える）
--   User A: aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
--   User B: bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
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

-- ---------------------------------------------------------------------------
-- User A として
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select results_eq(
  $$ select id from public.profiles $$,
  array['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid],
  'A は自分の profiles 行だけが見える'
);

select is_empty(
  $$ select 1 from public.profiles where id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $$,
  'A から B の profiles 行は見えない'
);

select lives_ok(
  $$ update public.profiles set display_name = 'あおい' where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  'A は自分の display_name を更新できる'
);

select lives_ok(
  $$ update public.profiles set display_name = 'hacked', deleted_at = now()
      where id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $$,
  'A による B の更新はエラーにならない（RLS により 0 行。下で内容不変を確認）'
);

select throws_ok(
  $$ update public.profiles set created_at = now() - interval '1 year'
      where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '42501', 'permission denied for table profiles',
  'display_name / deleted_at 以外の列（created_at）は更新できない'
);

select throws_ok(
  $$ update public.profiles set id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
      where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '42501', 'permission denied for table profiles',
  'id 列は更新できない'
);

select throws_ok(
  $$ insert into public.profiles (id, display_name) values (gen_random_uuid(), 'x') $$,
  '42501', 'permission denied for table profiles',
  'profiles への INSERT はできない（auth.users トリガーのみが作成）'
);

select throws_ok(
  $$ delete from public.profiles where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '42501', 'permission denied for table profiles',
  'profiles の DELETE はできない（退会は deleted_at による論理削除）'
);

-- 表示名の長さ（Web の上限 30 文字 = コードポイント数）は DB でも強制する（クライアントが直接 UPDATE できるため）
select lives_ok(
  $$ update public.profiles set display_name = repeat('😀', 30) where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  'display_name は 30 文字（絵文字もコードポイントで 1 文字）まで設定できる'
);

select throws_ok(
  $$ update public.profiles set display_name = repeat('あ', 31) where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '23514', null,
  'display_name の 31 文字以上は check 制約で拒否される'
);

select throws_ok(
  $$ update public.profiles set display_name = '' where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '23514', null,
  'display_name の空文字は拒否される（未設定は NULL）'
);

select lives_ok(
  $$ update public.profiles set display_name = 'あおい' where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  'A は表示名を元に戻せる'
);

select lives_ok(
  $$ update public.profiles set deleted_at = now() where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  'A は自分の deleted_at を設定（退会）できる'
);

select throws_ok(
  $$ update public.profiles set deleted_at = null where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '42501', 'withdrawn profile cannot be restored by the user',
  '退会は一方向: A は自分の deleted_at を NULL に戻せない'
);

select throws_ok(
  $$ update public.profiles set deleted_at = now() + interval '1 day' where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '42501', 'withdrawn profile cannot be restored by the user',
  '退会後の deleted_at を別の値に書き換えることもできない'
);

-- ---------------------------------------------------------------------------
-- User B として
-- ---------------------------------------------------------------------------
reset role;
set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';

select results_eq(
  $$ select id, display_name from public.profiles $$,
  $$ values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid, 'pgtap-user-b'::text) $$,
  'B は自分の行だけが見え、A による更新は反映されていない'
);

select lives_ok(
  $$ update public.profiles set display_name = 'hacked-by-b' where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  'B による A の更新も 0 行で終わる'
);

-- ---------------------------------------------------------------------------
-- postgres（RLS バイパス）で実データを確認
-- ---------------------------------------------------------------------------
reset role;

select is(
  (select display_name from public.profiles where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  'あおい',
  'A の display_name は A 本人の更新だけが反映されている'
);

select ok(
  (select deleted_at is not null from public.profiles where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  'A の deleted_at が設定されている'
);

select is(
  (select display_name from public.profiles where id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
  'pgtap-user-b',
  'B の display_name は変更されていない'
);

select ok(
  (select deleted_at is null from public.profiles where id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
  'B の deleted_at は変更されていない'
);

select lives_ok(
  $$ update public.profiles set deleted_at = null where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '運用者（postgres ロール）は退会を取り消せる'
);

-- ローカル部の長いメールアドレスでもサインアップ（auth.users の作成）が失敗しない: 既定の表示名は 30 文字に切り詰める
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                        raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-0000-0000-000000000000', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'authenticated',
   'authenticated', repeat('x', 64) || '@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

select is(
  (select display_name from public.profiles where id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
  repeat('x', 30),
  '既定の表示名はメールのローカル部を 30 文字に切り詰めたもの'
);

select * from finish();
rollback;
