-- =============================================================================
-- 07: トリガー — profiles 自動作成 / like_count / comment_count / last_message_at /
--                memories.updated_at
--   カウンタ系トリガーは security definer のため、UPDATE 権限の無い authenticated の
--   操作（いいね・コメント削除）でも posts のカウンタが更新されることを確認する。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(19);

-- ---------------------------------------------------------------------------
-- profiles 自動作成（on_auth_user_created）
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

-- email の無いユーザー（電話番号認証等を想定）
insert into auth.users (instance_id, id, aud, role, phone, created_at, updated_at)
values ('00000000-0000-0000-0000-000000000000', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'authenticated',
        'authenticated', '819000000000', now(), now());

select results_eq(
  $$ select id, display_name, deleted_at from public.profiles
      where id in ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
      order by id $$,
  $$ values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, 'pgtap-user-a'::text, null::timestamptz),
            ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid, 'pgtap-user-b'::text, null::timestamptz) $$,
  'auth.users 作成時に profiles が自動作成され、display_name は email のローカル部になる'
);

select is(
  (select count(*)::int from public.profiles where id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
  1,
  'email の無いユーザーでも profiles が作成される'
);

-- ---------------------------------------------------------------------------
-- Fixtures（投稿・会話・メモリ）
-- ---------------------------------------------------------------------------
insert into public.characters (id, handle, name, avatar_url, persona_key, system_prompt, is_active)
values ('c1c1c1c1-0000-4000-8000-000000000001', 'pgtap_active', 'テスト有効キャラ',
        'https://example.test/c1.png', 'pgtap_active', 'x', true);

insert into public.posts (id, character_id, image_url, published_at)
values ('d1000000-0000-4000-8000-000000000001', 'c1c1c1c1-0000-4000-8000-000000000001',
        'https://example.test/p1.jpg', now() - interval '1 hour');

-- ---------------------------------------------------------------------------
-- like_count
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';
insert into public.likes (user_id, post_id)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'd1000000-0000-4000-8000-000000000001');

reset role;
set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';
insert into public.likes (user_id, post_id)
values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'd1000000-0000-4000-8000-000000000001');

select results_eq(
  $$ select like_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001' $$,
  array[2],
  'authenticated のいいね 2 件で like_count = 2（トリガーは security definer）'
);

delete from public.likes where post_id = 'd1000000-0000-4000-8000-000000000001';

reset role;

select is(
  (select like_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  1,
  'いいね取り消しで like_count が 1 減る（他人のいいねは消えない）'
);

delete from public.likes where post_id = 'd1000000-0000-4000-8000-000000000001';

select is(
  (select like_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  0,
  '全いいね削除で like_count = 0'
);

-- カウンタがずれていても負にならない（greatest(..., 0)）
insert into public.likes (user_id, post_id)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'd1000000-0000-4000-8000-000000000001');
update public.posts set like_count = 0 where id = 'd1000000-0000-4000-8000-000000000001';

select lives_ok(
  $$ delete from public.likes where post_id = 'd1000000-0000-4000-8000-000000000001' $$,
  'like_count が 0 の状態でいいねを削除してもエラーにならない'
);

select is(
  (select like_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  0,
  'like_count は負にならない'
);

-- ---------------------------------------------------------------------------
-- comment_count
-- ---------------------------------------------------------------------------
insert into public.comments (id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body)
values ('e1000000-0000-4000-8000-0000000000a1', 'd1000000-0000-4000-8000-000000000001', null,
        'user', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, 'A のコメント');
insert into public.comments (id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body)
values
  ('e1000000-0000-4000-8000-0000000000c1', 'd1000000-0000-4000-8000-000000000001',
   'e1000000-0000-4000-8000-0000000000a1', 'character', null, 'c1c1c1c1-0000-4000-8000-000000000001', '返信'),
  ('e1000000-0000-4000-8000-0000000000b1', 'd1000000-0000-4000-8000-000000000001', null,
   'user', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', null, 'B のコメント');

select is(
  (select comment_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  3,
  'コメント 3 件（返信を含む）で comment_count = 3'
);

set local role authenticated;
set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';
delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000b1';
reset role;

select is(
  (select comment_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  2,
  'authenticated が自分のコメントを削除すると comment_count が 1 減る'
);

set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';
delete from public.comments where id = 'e1000000-0000-4000-8000-0000000000a1';
reset role;

select is(
  (select comment_count from public.posts where id = 'd1000000-0000-4000-8000-000000000001'),
  0,
  '返信付きコメントを削除すると cascade 分も含めて comment_count が減る'
);

select is(
  (select count(*)::int from public.audit_logs
    where event_type = 'comment.delete'
      and payload ->> 'post_id' = 'd1000000-0000-4000-8000-000000000001'),
  3,
  'コメント削除 3 件（cascade 含む）がすべて audit_logs に記録された'
);

-- ---------------------------------------------------------------------------
-- conversations.last_message_at
-- ---------------------------------------------------------------------------
insert into public.conversations (id, user_id, character_id, last_message_at)
values ('f0a00000-0000-4000-8000-00000000000a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'c1c1c1c1-0000-4000-8000-000000000001', '2000-01-01 00:00:00+00');

insert into public.messages (id, conversation_id, sender_type, body)
values ('10a00000-0000-4000-8000-000000000001', 'f0a00000-0000-4000-8000-00000000000a', 'user', 'こんにちは');

select is(
  (select last_message_at from public.conversations where id = 'f0a00000-0000-4000-8000-00000000000a'),
  (select created_at from public.messages where id = '10a00000-0000-4000-8000-000000000001'),
  'メッセージ追加で conversations.last_message_at がそのメッセージの created_at になる'
);

insert into public.messages (id, conversation_id, sender_type, body)
values ('10a00000-0000-4000-8000-000000000002', 'f0a00000-0000-4000-8000-00000000000a', 'character', 'やっほー');

select ok(
  (select created_at from public.messages where id = '10a00000-0000-4000-8000-000000000002')
    > (select created_at from public.messages where id = '10a00000-0000-4000-8000-000000000001'),
  '同一トランザクション内でも messages.created_at は挿入順に増加する（clock_timestamp）'
);

select is(
  (select last_message_at from public.conversations where id = 'f0a00000-0000-4000-8000-00000000000a'),
  (select created_at from public.messages where id = '10a00000-0000-4000-8000-000000000002'),
  '後続メッセージで last_message_at が進む'
);

insert into public.messages (id, conversation_id, sender_type, body, created_at)
values ('10a00000-0000-4000-8000-000000000003', 'f0a00000-0000-4000-8000-00000000000a', 'user', '過去の発言',
        '2001-01-01 00:00:00+00');

select is(
  (select last_message_at from public.conversations where id = 'f0a00000-0000-4000-8000-00000000000a'),
  (select created_at from public.messages where id = '10a00000-0000-4000-8000-000000000002'),
  '過去時刻のメッセージを追加しても last_message_at は巻き戻らない'
);

-- ---------------------------------------------------------------------------
-- memories.updated_at
-- ---------------------------------------------------------------------------
insert into public.memories (id, user_id, character_id, content, created_at, updated_at)
values ('20a00000-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'c1c1c1c1-0000-4000-8000-000000000001', '最初の記憶', '2000-01-01 00:00:00+00', '2000-01-01 00:00:00+00');

update public.memories set content = '更新後の記憶' where id = '20a00000-0000-4000-8000-000000000001';

select is(
  (select updated_at from public.memories where id = '20a00000-0000-4000-8000-000000000001'),
  now(),
  'memories を更新すると updated_at が now() になる'
);

select is(
  (select created_at from public.memories where id = '20a00000-0000-4000-8000-000000000001'),
  '2000-01-01 00:00:00+00'::timestamptz,
  'memories の created_at は更新で変わらない'
);

-- ---------------------------------------------------------------------------
-- ユーザー削除の cascade（auth.users → profiles → likes / conversations / messages / memories）
-- ---------------------------------------------------------------------------
delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

select is(
  (select count(*)::int from public.profiles where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  0,
  'auth.users を削除すると profiles も削除される'
);

select is(
  (select count(*)::int from public.conversations where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    + (select count(*)::int from public.messages where conversation_id = 'f0a00000-0000-4000-8000-00000000000a')
    + (select count(*)::int from public.memories where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  0,
  'auth.users を削除すると会話・メッセージ・メモリも cascade で削除される'
);

select * from finish();
rollback;
