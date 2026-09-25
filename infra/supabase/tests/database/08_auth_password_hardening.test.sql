-- =============================================================================
-- 08: 事前乗っ取り（pre-account takeover）対策 — メール確認前に設定されたパスワードの破棄
--
--   攻撃: 他人のメールアドレスで POST /auth/v1/signup {email, password} → 未確認ユーザー（確認メール送信済み）。
--         本人が後からマジックリンク / 6桁コードでログインするとメール確認済みになり、同じユーザーに
--         攻撃者のパスワードが残る → 攻撃者がパスワードでログインし続けられる。
--   対策: on_auth_user_email_verified トリガーが、メールのトークン経由の「未確認 → 確認済み」で
--         encrypted_password を NULL にする（前提: config.toml の enable_confirmations = true）。
--
--   攻撃: 有効なアクセストークン（1 時間）を一度でも入手した攻撃者が PUT /auth/v1/user {password} で
--         パスワードを設定する → サインアウト・リフレッシュトークンの失効後もパスワードでログインし続けられる。
--   対策: on_auth_user_password_update トリガーが、既存ユーザーの encrypted_password を「空でない別の値」に
--         変える UPDATE を元の値に戻す（パスワードを使わない運用を DB で強制）。消去（NULL / 空文字）と INSERT は対象外。
--
--   GoTrue が発行する SQL（models.User.Confirm / Recover / UpdatePassword / 確認メール送信）を postgres ロールで再現する。
--   パスワードのハッシュ値はダミー（検証はしない）。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(18);

select has_trigger('auth', 'users', 'on_auth_user_email_verified',
  'auth.users に確認時のパスワード破棄トリガー（on_auth_user_email_verified）がある');
select has_trigger('auth', 'users', 'on_auth_user_password_update',
  'auth.users にパスワードの設定・変更を無効にするトリガー（on_auth_user_password_update）がある');

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
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  -- E: マジックリンク / 6桁コードで登録した通常のユーザー（GoTrue は誰も知らないランダムなハッシュを入れる）
  ('00000000-0000-0000-0000-000000000000', 'e8000000-0000-4000-8000-00000000000e', 'authenticated',
   'authenticated', 'pgtap-passwordless-e@example.test', '$2a$10$pgtaprandomunknownhasheeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', now() - interval '1 day',
   '', now() - interval '1 day', '', null,
   '{"provider":"email","providers":["email"]}', '{}', now() - interval '1 day', now() - interval '1 day'),
  -- F: パスワードが NULL の確認済みユーザー（古い GoTrue で作られた / パスワードを破棄済み）
  ('00000000-0000-0000-0000-000000000000', 'f8000000-0000-4000-8000-00000000000f', 'authenticated',
   'authenticated', 'pgtap-nullpassword-f@example.test', null, now() - interval '1 day',
   '', now() - interval '1 day', '', null,
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  -- G: A と同じ（攻撃者の未確認ユーザー）。確認とパスワード変更が 1 つの UPDATE で起きる場合（トリガーの順序）の確認用
  ('00000000-0000-0000-0000-000000000000', '98000000-0000-4000-8000-000000000009', 'authenticated',
   'authenticated', 'pgtap-prehijack-g@example.test', '$2a$10$pgtapattackerpasswordhashgggggggggggggggggggggggggggg', null,
   'pgtap-confirmation-token-g', now() - interval '1 minute', '', null,
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
-- D〜F: 確認済みユーザーのパスワードの設定・変更は無効（on_auth_user_password_update）
--   GoTrue models.User.UpdatePassword: UPDATE ... SET encrypted_password = $1, updated_at = $2
--   （PUT /auth/v1/user {password} と PUT /auth/v1/admin/users/{id} {password} の両方）
-- ---------------------------------------------------------------------------
-- 再確認（email_confirmed_at の更新だけ）ではパスワードに触れない
update auth.users
   set email_confirmed_at = now(), updated_at = now()
 where id = 'd8000000-0000-4000-8000-00000000000d';

select is(
  (select encrypted_password from auth.users where id = 'd8000000-0000-4000-8000-00000000000d'),
  '$2a$10$pgtapownerpasswordhashdddddddddddddddddddddddddddddddd',
  '確認済みユーザーの再確認ではパスワードを破棄しない'
);

update auth.users
   set encrypted_password = '$2a$10$pgtapnewownerpasswordhashddddddddddddddddddddddddddddd', updated_at = now()
 where id = 'd8000000-0000-4000-8000-00000000000d';

select is(
  (select encrypted_password from auth.users where id = 'd8000000-0000-4000-8000-00000000000d'),
  '$2a$10$pgtapownerpasswordhashdddddddddddddddddddddddddddddddd',
  'パスワードを持つユーザー（管理 API で作ったテスト用）の変更は無効で、元のパスワードのまま（破棄もしない）'
);

-- E: アクセストークンを盗んだ攻撃者が PUT /auth/v1/user {password} で恒久的なパスワードを設定しようとする
update auth.users
   set encrypted_password = '$2a$10$pgtapattackerpersistentpasswordeeeeeeeeeeeeeeeeeeeeeee', updated_at = now()
 where id = 'e8000000-0000-4000-8000-00000000000e';

select is(
  (select encrypted_password from auth.users where id = 'e8000000-0000-4000-8000-00000000000e'),
  '$2a$10$pgtaprandomunknownhasheeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
  'マジックリンクで登録したユーザーに PUT /user でパスワードを設定しても無効（攻撃者のパスワードは保存されない）'
);

select is(
  (select updated_at from auth.users where id = 'e8000000-0000-4000-8000-00000000000e'),
  now(),
  'パスワード以外の列の更新（updated_at）は妨げない（UPDATE 自体は成功する）'
);

-- F: パスワードが NULL のユーザーにも設定できない
update auth.users
   set encrypted_password = '$2a$10$pgtapattackerpersistentpasswordfffffffffffffffffffffff', updated_at = now()
 where id = 'f8000000-0000-4000-8000-00000000000f';

select is(
  (select encrypted_password from auth.users where id = 'f8000000-0000-4000-8000-00000000000f'),
  null,
  'パスワードが NULL のユーザーにも PUT /user でパスワードを設定できない'
);

-- パスワードの消去（NULL / 空文字）は許可する（運用での破棄・on_auth_user_email_verified と同じ操作）
update auth.users set encrypted_password = '' where id = 'e8000000-0000-4000-8000-00000000000e';
update auth.users set encrypted_password = null where id = 'd8000000-0000-4000-8000-00000000000d';

select results_eq(
  $$ select id::text, encrypted_password from auth.users
      where id in ('d8000000-0000-4000-8000-00000000000d', 'e8000000-0000-4000-8000-00000000000e') order by id $$,
  $$ values ('d8000000-0000-4000-8000-00000000000d', null::varchar),
            ('e8000000-0000-4000-8000-00000000000e', ''::varchar) $$,
  'パスワードの消去（NULL / 空文字）は許可する'
);

-- 消去後に設定し直すこともできない
update auth.users
   set encrypted_password = '$2a$10$pgtapattackerpersistentpasswordeeeeeeeeeeeeeeeeeeeeeee'
 where id = 'e8000000-0000-4000-8000-00000000000e';

select is(
  (select encrypted_password from auth.users where id = 'e8000000-0000-4000-8000-00000000000e'),
  '',
  '空文字に消去したあとで設定し直すこともできない'
);

-- ---------------------------------------------------------------------------
-- G: 未確認ユーザーのパスワード変更と、確認を同時に行う UPDATE（2 つのトリガーの組み合わせ）
-- ---------------------------------------------------------------------------
-- 確認前に攻撃者がパスワードを変えようとしても無効（最初のパスワードのまま。確認時に破棄される）
update auth.users
   set encrypted_password = '$2a$10$pgtapattackersecondpasswordgggggggggggggggggggggggggg', updated_at = now()
 where id = '98000000-0000-4000-8000-000000000009';

select is(
  (select encrypted_password from auth.users where id = '98000000-0000-4000-8000-000000000009'),
  '$2a$10$pgtapattackerpasswordhashgggggggggggggggggggggggggggg',
  '未確認ユーザーのパスワード変更も無効'
);

-- 確認と同じ UPDATE でパスワードを設定しても、確認前のパスワードの破棄が優先される（新しい値も残らない）
update auth.users
   set encrypted_password = '$2a$10$pgtapattackerthirdpasswordggggggggggggggggggggggggggg',
       confirmation_token = '', email_confirmed_at = now(), updated_at = now()
 where id = '98000000-0000-4000-8000-000000000009';

select is(
  (select encrypted_password from auth.users where id = '98000000-0000-4000-8000-000000000009'),
  null,
  '確認と同時のパスワード設定でも、パスワードは破棄される（どちらの値も残らない）'
);

-- ---------------------------------------------------------------------------
-- 関数の性質
-- ---------------------------------------------------------------------------
select is(
  (select prosecdef from pg_proc where oid = 'public.discard_unverified_password()'::regprocedure),
  false,
  'discard_unverified_password は security invoker（NEW を書き換えるだけで権限は不要）'
);

select is(
  (select prosecdef from pg_proc where oid = 'public.ignore_password_update()'::regprocedure),
  false,
  'ignore_password_update は security invoker（NEW を書き換えるだけで権限は不要）'
);

select * from finish();
rollback;
