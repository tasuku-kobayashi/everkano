-- =============================================================================
-- 12: 自発メッセージ（§7）とジョブ基盤のテーブル
--
--   * proactive_settings: 本人の設定だけ参照できる。変更は API 経由（監査ログに残す, E4）
--   * proactive_messages: クライアント不可（送信の記録。P6 のメッセージ本体は messages.is_proactive）
--   * engine_jobs / engine_schedules: クライアント不可
--   * 一意性: 設定は (user, character) ごと 1 行（character = null の全体設定も 1 行）、自発メッセージの
--     きっかけは冪等（同じ trigger_ref で二重に送らない）、未処理のジョブは kind + dedupe_key で 1 件（デバウンス）
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(22);

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

insert into public.messages (id, conversation_id, sender_type, body, is_proactive)
values ('10a00000-0000-4000-8000-000000000001', 'f0a00000-0000-4000-8000-00000000000a', 'character',
        '今日だよね、面接。いつも通りでいいんだよ', true);

insert into public.proactive_settings (id, user_id, character_id, enabled, quiet_start, quiet_end)
values
  ('50a00000-0000-4000-8000-000000000001', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, true, 23, 8),
  ('50a00000-0000-4000-8000-000000000002', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
   'c1c1c1c1-0000-4000-8000-000000000001', false, null, null),
  ('50b00000-0000-4000-8000-000000000001', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', null, true, null, null);

insert into public.proactive_messages (user_id, character_id, conversation_id, message_id, trigger, trigger_ref)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001',
        'f0a00000-0000-4000-8000-00000000000a', '10a00000-0000-4000-8000-000000000001', 'promise_due',
        'promise:30a00000-0000-4000-8000-000000000001:morning');

insert into public.engine_jobs (kind, dedupe_key, payload)
values ('post_turn', 'pgtap-conversation', '{}');

insert into public.engine_schedules (name) values ('pgtap.schedule');

-- ---------------------------------------------------------------------------
-- 制約（postgres = API・スケジューラのロール）
-- ---------------------------------------------------------------------------
select throws_ok(
  $$ insert into public.proactive_settings (user_id, character_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null) $$,
  '23505', null,
  '全体の設定（character_id = null）はユーザーごとに 1 行（unique nulls not distinct）'
);

select throws_ok(
  $$ insert into public.proactive_settings (user_id, character_id)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001') $$,
  '23505', null,
  'キャラごとの設定は (user, character) ごとに 1 行'
);

select throws_ok(
  $$ update public.proactive_settings set quiet_start = 24 where id = '50a00000-0000-4000-8000-000000000001' $$,
  '23514', null,
  '送らない時間帯は 0〜23 時'
);

select throws_ok(
  $$ insert into public.proactive_messages (user_id, character_id, conversation_id, trigger, trigger_ref)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001',
             'f0a00000-0000-4000-8000-00000000000a', 'promise_due',
             'promise:30a00000-0000-4000-8000-000000000001:morning') $$,
  '23505', null,
  '同じきっかけ（trigger_ref）の自発メッセージは二重に送れない（冪等）'
);

select throws_ok(
  $$ insert into public.proactive_messages (user_id, character_id, conversation_id, trigger, trigger_ref)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001',
             'f0a00000-0000-4000-8000-00000000000a', 'buy_now', 'x') $$,
  '23514', null,
  '自発メッセージのきっかけは語彙（calendar_event / promise_due / … / paid_notice）のみ'
);

select throws_ok(
  $$ insert into public.engine_jobs (kind, dedupe_key) values ('post_turn', 'pgtap-conversation') $$,
  '23505', null,
  '未処理のジョブは kind + dedupe_key で 1 件（返答の後の処理のデバウンス）'
);

update public.engine_jobs set status = 'done', finished_at = now() where dedupe_key = 'pgtap-conversation';
select lives_ok(
  $$ insert into public.engine_jobs (kind, dedupe_key) values ('post_turn', 'pgtap-conversation') $$,
  '処理済みのジョブがあっても、同じ dedupe_key で新しいジョブを登録できる'
);

-- ---------------------------------------------------------------------------
-- User A（authenticated）
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select set_eq(
  $$ select id from public.proactive_settings $$,
  array['50a00000-0000-4000-8000-000000000001', '50a00000-0000-4000-8000-000000000002']::uuid[],
  'A には自分の自発メッセージの設定だけが見える'
);

select is_empty(
  $$ select 1 from public.proactive_settings where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' $$,
  'A は B の設定を user_id 指定でも取得できない'
);

select throws_ok(
  $$ insert into public.proactive_settings (user_id, character_id, enabled)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, false) $$,
  '42501', 'permission denied for table proactive_settings',
  'クライアントは設定を直接作れない（PUT /proactive/settings 経由）'
);

select throws_ok(
  $$ update public.proactive_settings set enabled = true where id = '50a00000-0000-4000-8000-000000000002' $$,
  '42501', 'permission denied for table proactive_settings',
  'クライアントは設定を直接変えられない（監査ログに残らない変更を防ぐ）'
);

select throws_ok(
  $$ delete from public.proactive_settings $$,
  '42501', 'permission denied for table proactive_settings',
  'クライアントは設定を削除できない'
);

select results_eq(
  $$ select id, is_proactive from public.messages where conversation_id = 'f0a00000-0000-4000-8000-00000000000a' $$,
  $$ values ('10a00000-0000-4000-8000-000000000001'::uuid, true) $$,
  'A は自分の会話の自発メッセージ（messages.is_proactive）を参照できる（P6: 未読として表示）'
);

select throws_ok(
  $$ select trigger from public.proactive_messages $$,
  '42501', 'permission denied for table proactive_messages',
  'proactive_messages（送信の記録）はクライアントから参照できない'
);

select throws_ok(
  $$ select kind from public.engine_jobs $$,
  '42501', 'permission denied for table engine_jobs',
  'engine_jobs はクライアントから参照できない'
);

select throws_ok(
  $$ insert into public.engine_jobs (kind) values ('post_turn') $$,
  '42501', 'permission denied for table engine_jobs',
  'クライアントはジョブを登録できない'
);

select throws_ok(
  $$ select nextval('public.engine_jobs_id_seq') $$,
  '42501', 'permission denied for sequence engine_jobs_id_seq',
  'engine_jobs のシーケンスも使えない'
);

select throws_ok(
  $$ select name from public.engine_schedules $$,
  '42501', 'permission denied for table engine_schedules',
  'engine_schedules はクライアントから参照できない'
);

-- ---------------------------------------------------------------------------
-- anon
-- ---------------------------------------------------------------------------
reset role;
set local role anon;
set local request.jwt.claims to '{"role":"anon"}';

select throws_ok(
  $$ select enabled from public.proactive_settings $$,
  '42501', 'permission denied for table proactive_settings',
  'anon: proactive_settings は参照できない'
);

select throws_ok(
  $$ select id from public.proactive_messages $$,
  '42501', 'permission denied for table proactive_messages',
  'anon: proactive_messages は参照できない'
);

select throws_ok(
  $$ select id from public.engine_jobs $$,
  '42501', 'permission denied for table engine_jobs',
  'anon: engine_jobs は参照できない'
);

select throws_ok(
  $$ select name from public.engine_schedules $$,
  '42501', 'permission denied for table engine_schedules',
  'anon: engine_schedules は参照できない'
);

select * from finish();
rollback;
