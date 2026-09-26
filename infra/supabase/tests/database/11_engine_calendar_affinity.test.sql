-- =============================================================================
-- 11: カレンダー（§5）と好感度（§6）のテーブル
--
--   * character_events: クライアント不可。公開の予定は同じキャラで時間が重ならない（C11・排他制約）
--   * character_states: 有効キャラの表示用の列（status_label / busyness / updated_at）だけ参照可（DM ヘッダー）
--   * affinity_states / affinity_history: クライアント不可（A11: 数値・段階を見せない）
--   * E1: 好感度のテーブルは課金・有料投稿のテーブルと外部キーで結びつかない
--   * post_image_pool: クライアント不可
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(24);

-- ---------------------------------------------------------------------------
-- Fixtures
-- ---------------------------------------------------------------------------
insert into auth.users (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
                        raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
values
  ('00000000-0000-0000-0000-000000000000', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'authenticated',
   'authenticated', 'pgtap-user-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.characters (id, handle, name, avatar_url, persona_key, system_prompt, is_active)
values
  ('c1c1c1c1-0000-4000-8000-000000000001', 'pgtap_active', 'テスト有効キャラ',
   'https://example.test/c1.png', 'pgtap_active', 'x', true),
  ('c2c2c2c2-0000-4000-8000-000000000002', 'pgtap_inactive', 'テスト非公開キャラ',
   'https://example.test/c2.png', 'pgtap_inactive', 'x', false);

insert into public.character_events (id, character_id, kind, title, starts_at, ends_at, visibility, user_id, source)
values
  ('e1000000-0000-4000-8000-000000000001', 'c1c1c1c1-0000-4000-8000-000000000001', 'routine', '仕事',
   '2026-10-01T00:00:00Z', '2026-10-01T09:00:00Z', 'public', null, 'generator'),
  ('e1000000-0000-4000-8000-000000000002', 'c1c1c1c1-0000-4000-8000-000000000001', 'promise', '面接の日',
   '2026-10-01T03:00:00Z', '2026-10-01T04:00:00Z', 'user', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'promise');

insert into public.character_states (character_id, activity, location, mood, busyness, event_id, status_label)
values
  ('c1c1c1c1-0000-4000-8000-000000000001', '会社で仕事', '新宿のオフィス', 'ふつう', 2,
   'e1000000-0000-4000-8000-000000000001', '仕事中'),
  ('c2c2c2c2-0000-4000-8000-000000000002', '休み', null, null, 0, null, 'おやすみ中');

insert into public.affinity_states (user_id, character_id, closeness, trust, romance, stage)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', 40, 35, 10, 'friend');

insert into public.affinity_history (user_id, character_id, before, after, delta, stage_before, stage_after, evaluator)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'c1c1c1c1-0000-4000-8000-000000000001', '{}', '{}', '{}',
        'acquaintance', 'friend', 'rule');

insert into public.post_image_pool (character_id, tags, image_url)
values ('c1c1c1c1-0000-4000-8000-000000000001', '{cafe}', 'https://example.test/cafe.jpg');

-- ---------------------------------------------------------------------------
-- 制約（postgres = API・スケジューラのロール）
-- ---------------------------------------------------------------------------
select throws_ok(
  $$ insert into public.character_events (character_id, kind, title, starts_at, ends_at)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'oneoff', '同じ時間に別の場所',
             '2026-10-01T08:00:00Z', '2026-10-01T10:00:00Z') $$,
  '23P01', null,
  'C11: 同じキャラの公開の予定は時間が重ならない（排他制約）'
);

select lives_ok(
  $$ insert into public.character_events (character_id, kind, title, starts_at, ends_at)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'oneoff', '直後の予定',
             '2026-10-01T09:00:00Z', '2026-10-01T10:00:00Z') $$,
  '終わった時刻から始まる予定は重なりではない（[) の区間）'
);

select lives_ok(
  $$ insert into public.character_events (character_id, kind, title, starts_at, ends_at, status)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'oneoff', '取り消した予定',
             '2026-10-01T01:00:00Z', '2026-10-01T02:00:00Z', 'cancelled') $$,
  '取り消した予定は重なりの判定に含めない'
);

select throws_ok(
  $$ insert into public.character_events (character_id, kind, title, starts_at, ends_at, visibility)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'promise', 'ユーザー未指定の約束',
             '2026-10-02T00:00:00Z', '2026-10-02T01:00:00Z', 'user') $$,
  '23514', null,
  'visibility = user の予定には user_id が必要'
);

select throws_ok(
  $$ insert into public.character_events (character_id, kind, title, starts_at, ends_at)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'oneoff', '逆向き',
             '2026-10-03T02:00:00Z', '2026-10-03T01:00:00Z') $$,
  '23514', null,
  '予定の終了は開始より後'
);

select throws_ok(
  $$ update public.affinity_states set closeness = 101
      where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '23514', null,
  '好感度の各軸は 0〜100'
);

-- E1: 好感度のテーブルは課金・有料投稿・いいね等のテーブルを参照しない（外部キーは profiles / characters のみ）
select set_eq(
  $$ select distinct confrelid::regclass::text
       from pg_constraint
      where contype = 'f'
        and conrelid in ('public.affinity_states'::regclass, 'public.affinity_history'::regclass) $$,
  array['profiles', 'characters'],
  'E1: affinity_* の外部キーは profiles / characters だけ（課金・投稿のテーブルと結びつかない）'
);

select is_empty(
  $$ select conname
       from pg_constraint
      where contype = 'f'
        and confrelid in ('public.affinity_states'::regclass, 'public.affinity_history'::regclass) $$,
  'E1: affinity_* を参照する外部キーも無い'
);

-- ---------------------------------------------------------------------------
-- User A（authenticated）
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';

select results_eq(
  $$ select character_id, status_label, busyness::int from public.character_states
      where character_id in ('c1c1c1c1-0000-4000-8000-000000000001', 'c2c2c2c2-0000-4000-8000-000000000002') $$,
  $$ values ('c1c1c1c1-0000-4000-8000-000000000001'::uuid, '仕事中'::text, 2) $$,
  'character_states は有効キャラの表示用の列だけ参照できる（非公開キャラの状態は見えない）'
);

select throws_ok(
  $$ select activity from public.character_states $$,
  '42501', 'permission denied for table character_states',
  'character_states.activity（今どこで何をしているか）は非公開'
);

select throws_ok(
  $$ select location from public.character_states $$,
  '42501', 'permission denied for table character_states',
  'character_states.location は非公開'
);

select throws_ok(
  $$ update public.character_states set status_label = '改ざん' $$,
  '42501', 'permission denied for table character_states',
  'クライアントはキャラの状態を変えられない'
);

select throws_ok(
  $$ select id from public.character_events $$,
  '42501', 'permission denied for table character_events',
  'character_events（キャラの予定・ユーザーとの約束）はクライアントから参照できない'
);

select throws_ok(
  $$ insert into public.character_events (character_id, kind, title, starts_at, ends_at)
     values ('c1c1c1c1-0000-4000-8000-000000000001', 'oneoff', '偽の予定', now(), now() + interval '1 hour') $$,
  '42501', 'permission denied for table character_events',
  'クライアントは予定を作れない'
);

select throws_ok(
  $$ select stage from public.affinity_states $$,
  '42501', 'permission denied for table affinity_states',
  'A11: 好感度の段階・数値はクライアントから参照できない'
);

select throws_ok(
  $$ update public.affinity_states set closeness = 100 $$,
  '42501', 'permission denied for table affinity_states',
  'A10: クライアントは好感度を直接変えられない'
);

select throws_ok(
  $$ select id from public.affinity_history $$,
  '42501', 'permission denied for table affinity_history',
  '好感度の履歴はクライアントから参照できない'
);

select throws_ok(
  $$ select nextval('public.affinity_history_id_seq') $$,
  '42501', 'permission denied for sequence affinity_history_id_seq',
  'affinity_history のシーケンスも使えない'
);

select throws_ok(
  $$ select image_url from public.post_image_pool $$,
  '42501', 'permission denied for table post_image_pool',
  'post_image_pool（投稿用の画像プール）はクライアントから参照できない'
);

-- ---------------------------------------------------------------------------
-- anon
-- ---------------------------------------------------------------------------
reset role;
set local role anon;
set local request.jwt.claims to '{"role":"anon"}';

select throws_ok(
  $$ select status_label from public.character_states $$,
  '42501', 'permission denied for table character_states',
  'anon: character_states は参照できない'
);

select throws_ok(
  $$ select id from public.character_events $$,
  '42501', 'permission denied for table character_events',
  'anon: character_events は参照できない'
);

select throws_ok(
  $$ select stage from public.affinity_states $$,
  '42501', 'permission denied for table affinity_states',
  'anon: affinity_states は参照できない'
);

select throws_ok(
  $$ select id from public.affinity_history $$,
  '42501', 'permission denied for table affinity_history',
  'anon: affinity_history は参照できない'
);

select throws_ok(
  $$ select id from public.post_image_pool $$,
  '42501', 'permission denied for table post_image_pool',
  'anon: post_image_pool は参照できない'
);

select * from finish();
rollback;
