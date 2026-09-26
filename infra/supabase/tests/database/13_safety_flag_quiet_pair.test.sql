-- =============================================================================
-- 13: 統合で追加した制約（20260926140000_safety_flag / 20260926140100_proactive_quiet_pair）
--
--   * messages.safety_triggered（E6）: 既定 false・キャラの発言だけに付けられる。本人は既存の messages の
--     select 権限で読める（どの端末でも相談窓口のカードを出すため）。クライアントは書き換えられない
--   * proactive_settings の送らない時間帯（E4）: 開始・終了は「両方 null（サーバーの既定）」か「両方が値」。
--     キャラ別の行は時間帯を持たない
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(13);

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
values ('10a00000-0000-4000-8000-000000000001', 'f0a00000-0000-4000-8000-00000000000a', 'user', 'もう死にたい');
insert into public.messages (id, conversation_id, sender_type, body, safety_triggered)
values ('10a00000-0000-4000-8000-000000000002', 'f0a00000-0000-4000-8000-00000000000a', 'character',
        '話してくれてありがとう。相談窓口を置いておくね', true);

-- ---------------------------------------------------------------------------
-- messages.safety_triggered（postgres = API のロール）
-- ---------------------------------------------------------------------------
select col_not_null('public', 'messages', 'safety_triggered', 'messages.safety_triggered は not null');
select col_default_is('public', 'messages', 'safety_triggered', 'false', 'messages.safety_triggered の既定は false');

select is(
  (select safety_triggered from public.messages where id = '10a00000-0000-4000-8000-000000000001'),
  false,
  '印を指定しない発言は false（既存の行・ふつうの返答・自発メッセージ）'
);

select throws_ok(
  $$ insert into public.messages (conversation_id, sender_type, body, safety_triggered)
     values ('f0a00000-0000-4000-8000-00000000000a', 'user', 'x', true) $$,
  '23514', null,
  '安全対応の印はキャラの発言だけに付けられる'
);

-- ---------------------------------------------------------------------------
-- proactive_settings の送らない時間帯の組
-- ---------------------------------------------------------------------------
select throws_ok(
  $$ insert into public.proactive_settings (user_id, character_id, quiet_start)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, 22) $$,
  '23514', null,
  '送らない時間帯の開始だけ（終了が null）は保存できない（API の値と DB の値が食い違わない）'
);

select throws_ok(
  $$ insert into public.proactive_settings (user_id, character_id, quiet_end)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, 6) $$,
  '23514', null,
  '送らない時間帯の終了だけ（開始が null）は保存できない'
);

select lives_ok(
  $$ insert into public.proactive_settings (user_id, character_id, enabled)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', null, false) $$,
  '両方 null（サーバーの既定に従う）は保存できる'
);

select lives_ok(
  $$ update public.proactive_settings set quiet_start = 23, quiet_end = 6
      where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' and character_id is null $$,
  '両方が値なら保存できる'
);

select throws_ok(
  $$ update public.proactive_settings set quiet_end = null
      where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' and character_id is null $$,
  '23514', null,
  '片方だけを null に戻すことはできない'
);

select throws_ok(
  $$ insert into public.proactive_settings (user_id, character_id, quiet_start, quiet_end)
     values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', 22, 6) $$,
  '23514', null,
  'キャラ別の設定は送らない時間帯を持たない（全体の設定だけ）'
);

-- ---------------------------------------------------------------------------
-- クライアント（authenticated）
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select results_eq(
  $$ select id, safety_triggered from public.messages
      where conversation_id = 'f0a00000-0000-4000-8000-00000000000a' order by created_at, id $$,
  $$ values ('10a00000-0000-4000-8000-000000000001'::uuid, false),
            ('10a00000-0000-4000-8000-000000000002'::uuid, true) $$,
  '本人は自分の会話の安全対応の印を読める（どの端末でも相談窓口のカードを出す）'
);

select throws_ok(
  $$ update public.messages set safety_triggered = false where id = '10a00000-0000-4000-8000-000000000002' $$,
  '42501', null,
  'クライアントは安全対応の印を書き換えられない'
);

set local request.jwt.claims to '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}';
select is_empty(
  $$ select 1 from public.messages where conversation_id = 'f0a00000-0000-4000-8000-00000000000a' $$,
  '他人の会話の発言（安全対応の印を含む）は見えない'
);

select * from finish();
rollback;
