-- =============================================================================
-- 08: 事前乗っ取り（pre-account takeover）対策 — メール確認前に設定されたパスワードの破棄
--
--   攻撃: 他人のメールアドレスで POST /auth/v1/signup {email, password} → 未確認ユーザー（確認メール送信済み）。
--         本人が後からマジックリンク / 6桁コードでログインするとメール確認済みになり、同じユーザーに
--         攻撃者のパスワードが残る → 攻撃者がパスワードでログインし続けられる。
--   対策: on_auth_user_email_verified トリガーが、メールのトークン経由の「未確認 → 確認済み」で
--         encrypted_password を NULL にする（前提: config.toml の enable_confirmations = true）。
--
--   GoTrue が発行する SQL（models.User.Confirm / Recover / 確認メール送信）を postgres ロールで再現する。
--   パスワードのハッシュ値はダミー（検証はしない）。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(8);

select has_trigger('auth', 'users', 'on_auth_user_email_verified',
  'auth.users に確認時のパスワード破棄トリガー（on_auth_user_email_verified）がある');

-- ---------------------------------------------------------------------------
-- Fixtures
-- ---------------------------------------------------------------------------
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                        confirmation_token, confirmation_sent_at, recovery_token, recovery_sent_at,
                        raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values
  -- A: 攻撃者が /auth/v1/signup {email, password} で作った未確認ユーザー（確認メール送信済み）
  ('00000000-0000-0000-0000-000000000000', 'a8000000-0000-4000-8000-00000000000a', 'authenticated',
   'authenticated', 'pgtap-prehijack-a@example.test', '$2a$10$pgtapattackerpasswordhashaaaaaaaaaaaaaaaaaaaaaaaaaaaa', null,
   'pgtap-confirmation-token-a', now() - interval '1 minute', '', null,
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  -- B: A と同じだが、本人がマジックリンク（recovery_token）で確認する
  ('00000000-0000-0000-0000-000000000000', 'b8000000-0000-4000-8000-00000000000b', 'authenticated',
   'authenticated', 'pgtap-prehijack-b@example.test', '$2a$10$pgtapattackerpasswordhashbbbbbbbbbbbbbbbbbbbbbbbbbbbb', null,
   '', null, 'pgtap-recovery-token-b', now() - interval '1 minute',
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  -- C: 管理 API（service role）で email_confirm: true を指定して作るテスト用ユーザー
  --    GoTrue は INSERT（未確認）→ 同一トランザクションで Confirm する。メールのトークンは経由しない
  ('00000000-0000-0000-0000-000000000000', 'c8000000-0000-4000-8000-00000000000c', 'authenticated',
   'authenticated', 'pgtap-admin-created-c@example.test', '$2a$10$pgtapadminpasswordhashcccccccccccccccccccccccccccccccc', null,
   '', null, '', null,
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  -- D: 確認済みのユーザー（本人がログイン後にパスワードを設定した場合など）
  ('00000000-0000-0000-0000-000000000000', 'd8000000-0000-4000-8000-00000000000d', 'authenticated',
   'authenticated', 'pgtap-confirmed-d@example.test', '$2a$10$pgtapownerpasswordhashdddddddddddddddddddddddddddddddd', now() - interval '1 day',
   '', now() - interval '1 day', '', null,
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

-- ---------------------------------------------------------------------------
-- A: 確認メール（リンク / 6桁コード）での確認
-- ---------------------------------------------------------------------------
-- 未確認のまま確認メールを再送（本人が signInWithOtp を再実行した場合など）→ まだ確認前なので何もしない
update auth.users
   set confirmation_token = 'pgtap-confirmation-token-a2', confirmation_sent_at = now()
 where id = 'a8000000-0000-4000-8000-00000000000a';

select isnt(
  (select encrypted_password from auth.users where id = 'a8000000-0000-4000-8000-00000000000a'),
  null,
  '未確認のまま確認メールを再送してもトリガーは動かない（確認時にのみ破棄する）'
);

-- GoTrue models.User.Confirm: UPDATE ... SET confirmation_token = '', email_confirmed_at = now()
update auth.users
   set confirmation_token = '', email_confirmed_at = now(), updated_at = now()
 where id = 'a8000000-0000-4000-8000-00000000000a';

select is(
  (select encrypted_password from auth.users where id = 'a8000000-0000-4000-8000-00000000000a'),
  null,
  '確認メールで確認済みになると、確認前に設定されたパスワード（攻撃者のもの）が破棄される'
);

select isnt(
  (select email_confirmed_at from auth.users where id = 'a8000000-0000-4000-8000-00000000000a'),
  null,
  'メール確認そのものは妨げない（email_confirmed_at は設定される）'
);

-- ---------------------------------------------------------------------------
-- B: マジックリンク（recovery_token）での確認（GoTrue recoverVerify: Recover → Confirm）
-- ---------------------------------------------------------------------------
update auth.users set recovery_token = '' where id = 'b8000000-0000-4000-8000-00000000000b';
update auth.users
   set confirmation_token = '', email_confirmed_at = now(), updated_at = now()
 where id = 'b8000000-0000-4000-8000-00000000000b';

select is(
  (select encrypted_password from auth.users where id = 'b8000000-0000-4000-8000-00000000000b'),
  null,
  'マジックリンクで確認済みになったときも、確認前のパスワードが破棄される'
);

-- ---------------------------------------------------------------------------
-- C: 管理 API（email_confirm: true）で作ったユーザーはパスワードを保持する
-- ---------------------------------------------------------------------------
update auth.users
   set confirmation_token = '', email_confirmed_at = now(), updated_at = now()
 where id = 'c8000000-0000-4000-8000-00000000000c';

select is(
  (select encrypted_password from auth.users where id = 'c8000000-0000-4000-8000-00000000000c'),
  '$2a$10$pgtapadminpasswordhashcccccccccccccccccccccccccccccccc',
  '管理 API（email_confirm: true）の確認はメールのトークンを経由しないため、パスワードを保持する（テスト用ユーザー）'
);

-- ---------------------------------------------------------------------------
-- D: 確認済みユーザーの更新では何もしない
-- ---------------------------------------------------------------------------
update auth.users
   set encrypted_password = '$2a$10$pgtapnewownerpasswordhashddddddddddddddddddddddddddddd',
       email_confirmed_at = now(), updated_at = now()
 where id = 'd8000000-0000-4000-8000-00000000000d';

select is(
  (select encrypted_password from auth.users where id = 'd8000000-0000-4000-8000-00000000000d'),
  '$2a$10$pgtapnewownerpasswordhashddddddddddddddddddddddddddddd',
  '確認済みユーザーのパスワード変更・再確認ではパスワードを破棄しない'
);

-- ---------------------------------------------------------------------------
-- 関数の性質
-- ---------------------------------------------------------------------------
select is(
  (select prosecdef from pg_proc where oid = 'public.discard_unverified_password()'::regprocedure),
  false,
  'discard_unverified_password は security invoker（NEW を書き換えるだけで権限は不要）'
);

select * from finish();
rollback;
