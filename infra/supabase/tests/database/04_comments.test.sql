-- =============================================================================
-- 04: comments — 公開投稿のコメントのみ参照、作成は API 経由のみ、本人のコメントのみ削除
--                 削除は audit_logs（comment.delete）に記録される（H6）
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(17);

-- ---------------------------------------------------------------------------
-- Fixtures
--   User A / User B, Char C1（有効）/ C2（無効）
--   P1: 公開済み（C1） / P2: 予約投稿（C1） / P3: 無効キャラの投稿（C2）
--   CA1: A のコメント（P1）      CR1: CA1 へのキャラ返信（P1, parent=CA1）
--   CB1: B のコメント（P1）      CC1: キャラのコメント（P1）
--   CF1: キャラのコメント（P2）  CI1: B のコメント（P3）
--   ※ コメントの作成は本来 Python API（postgres ロール）が行うため、ここでも postgres で作成する
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
  ('d2000000-0000-4000-8000-000000000002', 'c1c1c1c1-0000-4000-8000-000000000001',
   'https://example.test/p2.jpg', now() + interval '1 day'),
  ('d3000000-0000-4000-8000-000000000003', 'c2c2c2c2-0000-4000-8000-000000000002',
   'https://example.test/p3.jpg', now() - interval '1 hour');

insert into public.comments (id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body)
values
  ('e1000000-0000-4000-8000-0000000000a1', 'd1000000-0000-4000-8000-000000000001', null,
   'user', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, 'A のコメント');
insert into public.comments (id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body)
values
  ('e1000000-0000-4000-8000-0000000000c1', 'd1000000-0000-4000-8000-000000000001',
   'e1000000-0000-4000-8000-0000000000a1', 'character', null, 'c1c1c1c1-0000-4000-8000-000000000001',
   'A へのキャラ返信'),
  ('e1000000-0000-4000-8000-0000000000b1', 'd1000000-0000-4000-8000-000000000001', null,
   'user', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', null, 'B のコメント'),
  ('e1000000-0000-4000-8000-0000000000c2', 'd1000000-0000-4000-8000-000000000001', null,
   'character', null, 'c1c1c1c1-0000-4000-8000-000000000001', 'キャラのコメント'),
  ('e1000000-0000-4000-8000-0000000000f1', 'd2000000-0000-4000-8000-000000000002', null,
   'character', null, 'c1c1c1c1-0000-4000-8000-000000000001', '予約投稿へのコメント'),
  ('e1000000-0000-4000-8000-0000000000d1', 'd3000000-0000-4000-8000-000000000003', null,
   'user', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', null, '無効キャラ投稿へのコメント');

-- ---------------------------------------------------------------------------
-- User A
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select set_eq(
  $$ select id from public.comments
      where post_id in ('d1000000-0000-4000-8000-000000000001', 'd2000000-0000-4000-8000-000000000002',
                        'd3000000-0000-4000-8000-000000000003') $$,
  array[
    'e1000000-0000-4000-8000-0000000000a1', 'e1000000-0000-4000-8000-0000000000c1',
    'e1000000-0000-4000-8000-0000000000b1', 'e1000000-0000-4000-8000-0000000000c2'
  ]::uuid[],
  '公開投稿のコメント（他ユーザー・キャラのものを含む）は見える'
);

select is_empty(
  $$ select 1 from public.comments where post_id = 'd2000000-0000-4000-8000-000000000002' $$,
  '予約投稿（未公開）のコメントは見えない'
);

select is_empty(
  $$ select 1 from public.comments where post_id = 'd3000000-0000-4000-8000-000000000003' $$,
  '無効キャラの投稿のコメントは見えない'
);

select throws_ok(
  $$ insert into public.comments (post_id, author_type, author_user_id, body)
     values ('d1000000-0000-4000-8000-000000000001', 'user', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '直接投稿') $$,
  '42501', 'permission denied for table comments',
  'クライアントは comments に直接 INSERT できない（API 経由でモデレーション必須）'
);

select throws_ok(
  $$ insert into public.comments (post_id, author_type, author_character_id, body)
     values ('d1000000-0000-4000-8000-000000000001', 'character', 'c1c1c1c1-0000-4000-8000-000000000001', 'なりすまし') $$,
  '42501', 'permission denied for table comments',
  'キャラになりすましたコメントも作成できない'
);

select throws_ok(
  $$ update public.comments set body = '改ざん' where id = 'e1000000-0000-4000-8000-0000000000a1' $$,
  '42501', 'permission denied for table comments',
  '自分のコメントでも UPDATE（編集）はできない'
);

select lives_ok(
  $$ delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000b1' $$,
  'A による B のコメント削除はエラーにならない（RLS により 0 行）'
);

select lives_ok(
  $$ delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000c2' $$,
  'A によるキャラのコメント削除もエラーにならない（RLS により 0 行）'
);

-- ---------------------------------------------------------------------------
-- postgres で確認
-- ---------------------------------------------------------------------------
reset role;

select is(
  (select count(*)::int from public.comments
    where id in ('e1000000-0000-4000-8000-0000000000b1', 'e1000000-0000-4000-8000-0000000000c2')),
  2,
  '他人のコメント・キャラのコメントは削除されていない'
);

select is(
  (select count(*)::int from public.audit_logs
    where event_type = 'comment.delete'
      and payload ->> 'comment_id' in ('e1000000-0000-4000-8000-0000000000b1',
                                       'e1000000-0000-4000-8000-0000000000c2')),
  0,
  '削除されなかったコメントの監査ログは無い'
);

-- ---------------------------------------------------------------------------
-- User A: 自分のコメントを削除
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select lives_ok(
  $$ delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000a1' $$,
  'A は自分のコメントを削除できる'
);

reset role;

select is_empty(
  $$ select id from public.comments
      where id in ('e1000000-0000-4000-8000-0000000000a1', 'e1000000-0000-4000-8000-0000000000c1') $$,
  '自分のコメントと、それへのキャラ返信（parent_comment_id の cascade）が削除された'
);

select results_eq(
  $$ select event_type, user_id, character_id, payload ->> 'author_type', payload ->> 'author_user_id',
            payload ->> 'body', payload ->> 'deleted_by_role'
       from public.audit_logs
      where payload ->> 'comment_id' = 'e1000000-0000-4000-8000-0000000000a1' $$,
  $$ values ('comment.delete'::text, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, null::uuid, 'user'::text,
             'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::text, 'A のコメント'::text, 'authenticated'::text) $$,
  'ユーザーによるコメント削除が audit_logs に comment.delete として記録される'
);

select results_eq(
  $$ select event_type, user_id, character_id, payload ->> 'author_type'
       from public.audit_logs
      where payload ->> 'comment_id' = 'e1000000-0000-4000-8000-0000000000c1' $$,
  $$ values ('comment.delete'::text, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
             'c1c1c1c1-0000-4000-8000-000000000001'::uuid, 'character'::text) $$,
  'cascade で消えたキャラ返信も comment.delete として記録される'
);

-- ---------------------------------------------------------------------------
-- User B: 残ったコメントの見え方（対称性）と B 自身の削除
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';

select set_eq(
  $$ select id from public.comments where post_id = 'd1000000-0000-4000-8000-000000000001' $$,
  array['e1000000-0000-4000-8000-0000000000b1', 'e1000000-0000-4000-8000-0000000000c2']::uuid[],
  'B には P1 の残りのコメント（B 自身とキャラ）が見える'
);

select lives_ok(
  $$ delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000b1' $$,
  'B は自分のコメントを削除できる'
);

reset role;

-- ---------------------------------------------------------------------------
-- 既知の問題（TODO）
--   audit_comment_delete() は coalesce(current_setting('request.jwt.claims', true), '{}')::jsonb
--   を評価するが、同一セッションで一度でも request.jwt.claims を設定したことがあると、
--   トランザクション終了後の値は NULL ではなく空文字 '' になり、''::jsonb が失敗する。
--   その結果、そのセッション（例: set_config で claims を設定したことのある API / テストの接続）
--   から claims 無しでコメント（や投稿・キャラの cascade）を削除するとエラーになる。
--   修正案: coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
--   マイグレーション修正後、このテストは "TODO passed" になるので todo_start/todo_end を外すこと。
-- ---------------------------------------------------------------------------
set local request.jwt.claims to '';

select todo_start('audit_comment_delete の空文字 claims 対応（マイグレーション修正待ち）');
select lives_ok(
  $$ delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000c2' $$,
  'request.jwt.claims が空文字のセッションからでもコメントを削除できる'
);
select todo_end();

select * from finish();
rollback;
