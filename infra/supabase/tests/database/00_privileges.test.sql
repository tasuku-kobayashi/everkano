-- =============================================================================
-- 00: 権限マトリクス（最小権限の回帰テスト）
--
-- マイグレーションで「RLS 有効化 + 必要最小限の grant + ポリシー」がセットで
-- 定義されていることを、カタログ（pg_class / pg_attribute / pg_proc / pg_policies）
-- から機械的に検証する。新しいテーブル・列・関数・ポリシーを追加したら、
-- 意図した変更であることを確認のうえ、このファイルの期待値も更新すること。
--
-- 実行: scripts/test-db.sh  もしくは  supabase test db --workdir infra
-- すべて 1 トランザクション内で実行し rollback するため、DB にデータは残らない。
-- =============================================================================
begin;
create extension if not exists pgtap with schema extensions;

select plan(16);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
select is_empty(
  $$ select c.relname
       from pg_class c
      where c.relnamespace = 'public'::regnamespace
        and c.relkind in ('r', 'p')
        and not c.relrowsecurity $$,
  'public スキーマの全テーブルで RLS が有効'
);

select is_empty(
  $$ select c.relname
       from pg_class c
      where c.relnamespace = 'public'::regnamespace
        and c.relkind in ('v', 'm', 'f') $$,
  'public スキーマにビュー / マテビュー / 外部テーブルが無い（ビューは RLS をバイパスし得るため追加時は要レビュー）'
);

-- ---------------------------------------------------------------------------
-- テーブル単位の権限
-- ---------------------------------------------------------------------------
select is_empty(
  $$ select c.relname, a.privilege_type
       from pg_class c
       cross join lateral aclexplode(c.relacl) a
      where c.relnamespace = 'public'::regnamespace
        and a.grantee in ('anon'::regrole, 0::oid) $$,
  'anon / PUBLIC にはテーブル・シーケンスの権限が一切無い'
);

select is_empty(
  $$ select c.relname, att.attname, x.privilege_type
       from pg_attribute att
       join pg_class c on c.oid = att.attrelid
       cross join lateral aclexplode(att.attacl) x
      where c.relnamespace = 'public'::regnamespace
        and x.grantee = 0::oid $$,
  'PUBLIC には列単位の権限も無い'
);

-- anon の列権限は Realtime 用の主キー列のみ（RLS の評価経路に乗せて 401 イベントの配信を止めるため）。
-- anon 向けポリシーは無いので、この権限で読める行は無い（06 / 10 で確認）。
select set_eq(
  $$ select c.relname::text, att.attname::text, x.privilege_type::text
       from pg_attribute att
       join pg_class c on c.oid = att.attrelid
       cross join lateral aclexplode(att.attacl) x
      where c.relnamespace = 'public'::regnamespace
        and x.grantee = 'anon'::regrole $$,
  $$ values ('comments', 'id', 'SELECT'), ('messages', 'id', 'SELECT'), ('memories', 'id', 'SELECT') $$,
  'anon の列単位権限は Realtime 対象テーブルの主キー列（messages.id / comments.id / memories.id）の SELECT のみ'
);

select set_eq(
  $$ select c.relname::text, a.privilege_type::text
       from pg_class c
       cross join lateral aclexplode(c.relacl) a
      where c.relnamespace = 'public'::regnamespace
        and a.grantee = 'authenticated'::regrole $$,
  $$ values
       ('profiles', 'SELECT'),
       ('posts', 'SELECT'),
       ('likes', 'SELECT'), ('likes', 'INSERT'), ('likes', 'DELETE'),
       ('comments', 'SELECT'), ('comments', 'DELETE'),
       ('conversations', 'SELECT'),
       ('messages', 'SELECT') $$,
  'authenticated のテーブル単位権限は許可リストと一致（INSERT は likes のみ / UPDATE は無し）'
);

-- ---------------------------------------------------------------------------
-- 列単位の権限
-- ---------------------------------------------------------------------------
select set_eq(
  $$ select c.relname::text, att.attname::text, x.privilege_type::text
       from pg_attribute att
       join pg_class c on c.oid = att.attrelid
       cross join lateral aclexplode(att.attacl) x
      where c.relnamespace = 'public'::regnamespace
        and x.grantee = 'authenticated'::regrole $$,
  $$ values
       -- characters: PUBLIC_CHARACTER_COLUMNS のみ（system_prompt / persona_key は非公開）
       ('characters', 'id', 'SELECT'), ('characters', 'handle', 'SELECT'),
       ('characters', 'name', 'SELECT'), ('characters', 'avatar_url', 'SELECT'),
       ('characters', 'bio', 'SELECT'), ('characters', 'follower_count', 'SELECT'),
       ('characters', 'is_active', 'SELECT'), ('characters', 'created_at', 'SELECT'),
       -- memories: embedding 以外（エンジン v1.0 の列を含む）
       ('memories', 'id', 'SELECT'), ('memories', 'user_id', 'SELECT'),
       ('memories', 'character_id', 'SELECT'), ('memories', 'content', 'SELECT'),
       ('memories', 'importance', 'SELECT'), ('memories', 'tags', 'SELECT'),
       ('memories', 'source_message_id', 'SELECT'), ('memories', 'is_user_edited', 'SELECT'),
       ('memories', 'created_at', 'SELECT'), ('memories', 'updated_at', 'SELECT'),
       ('memories', 'kind', 'SELECT'), ('memories', 'status', 'SELECT'),
       ('memories', 'superseded_by', 'SELECT'), ('memories', 'superseded_at', 'SELECT'),
       ('memories', 'last_referenced_at', 'SELECT'), ('memories', 'reference_count', 'SELECT'),
       ('memories', 'source_conversation_id', 'SELECT'),
       -- character_states: DM ヘッダーの表示用だけ（activity / location / mood / event_id は非公開）
       ('character_states', 'character_id', 'SELECT'), ('character_states', 'status_label', 'SELECT'),
       ('character_states', 'busyness', 'SELECT'), ('character_states', 'updated_at', 'SELECT'),
       -- promises: メモリパネルの約束（source_message_id / event_id は非公開）
       ('promises', 'id', 'SELECT'), ('promises', 'user_id', 'SELECT'), ('promises', 'character_id', 'SELECT'),
       ('promises', 'content', 'SELECT'), ('promises', 'due_at', 'SELECT'), ('promises', 'due_precision', 'SELECT'),
       ('promises', 'status', 'SELECT'), ('promises', 'source_memory_id', 'SELECT'),
       ('promises', 'mentioned_at', 'SELECT'), ('promises', 'completed_at', 'SELECT'),
       ('promises', 'cancelled_at', 'SELECT'), ('promises', 'created_at', 'SELECT'),
       ('promises', 'updated_at', 'SELECT'),
       -- proactive_settings: 本人の設定（変更は API 経由）
       ('proactive_settings', 'id', 'SELECT'), ('proactive_settings', 'user_id', 'SELECT'),
       ('proactive_settings', 'character_id', 'SELECT'), ('proactive_settings', 'enabled', 'SELECT'),
       ('proactive_settings', 'quiet_start', 'SELECT'), ('proactive_settings', 'quiet_end', 'SELECT'),
       ('proactive_settings', 'updated_at', 'SELECT'),
       -- 更新可能な列
       ('profiles', 'display_name', 'UPDATE'), ('profiles', 'deleted_at', 'UPDATE'),
       ('conversations', 'user_last_read_at', 'UPDATE') $$,
  'authenticated の列単位権限は許可リストと一致（characters.system_prompt / persona_key と memories.embedding は含まない）'
);

-- ---------------------------------------------------------------------------
-- 関数（RPC）
-- ---------------------------------------------------------------------------
select set_eq(
  $$ select p.proname::text
       from pg_proc p
      where p.pronamespace = 'public'::regnamespace
        and has_function_privilege('authenticated', p.oid, 'EXECUTE') $$,
  array['list_dm_threads', 'mark_conversation_read'],
  'authenticated が実行できる public 関数は list_dm_threads / mark_conversation_read のみ'
);

select is_empty(
  $$ select p.proname
       from pg_proc p
      where p.pronamespace = 'public'::regnamespace
        and has_function_privilege('anon', p.oid, 'EXECUTE') $$,
  'anon が実行できる public 関数は無い'
);

select is_empty(
  $$ select p.proname
       from pg_proc p
      where p.pronamespace = 'public'::regnamespace
        and p.prosecdef
        and p.proname not in (
          -- トリガー関数（クライアントからは実行不可。上のテストで担保）
          'handle_new_user', 'sync_post_like_count', 'sync_post_comment_count',
          'audit_comment_delete', 'sync_conversation_last_message_at'
        ) $$,
  'security definer 関数はレビュー済みのトリガー関数のみ（RPC は security invoker で RLS を効かせる）'
);

-- ---------------------------------------------------------------------------
-- ポリシー
-- ---------------------------------------------------------------------------
select is_empty(
  $$ select tablename, policyname, roles
       from pg_policies
      where schemaname = 'public'
        and roles <> array['authenticated']::name[] $$,
  'public のポリシーはすべて authenticated 限定（anon / public 向けポリシーが無い）'
);

select is_empty(
  $$ select tablename, policyname
       from pg_policies
      where schemaname = 'public'
        and tablename in ('post_private_assets', 'audit_logs') $$,
  'post_private_assets / audit_logs にはポリシーが無い（= クライアントから不可視）'
);

-- キャラクターエンジンの内部状態（好感度・キャラ側の記憶・墓標・予定・ジョブ・自発メッセージの記録・画像プール）は
-- ポリシーも権限も無い（= クライアントから一切参照できない。API / スケジューラだけが使う）
select is_empty(
  $$ select c.relname, p.policyname, a.privilege_type
       from pg_class c
       left join pg_policies p on p.schemaname = 'public' and p.tablename = c.relname
       left join lateral aclexplode(c.relacl) a on a.grantee in ('anon'::regrole, 'authenticated'::regrole)
      where c.relnamespace = 'public'::regnamespace
        and c.relname in ('memory_tombstones', 'character_events', 'character_memories', 'affinity_states',
                          'affinity_history', 'proactive_messages', 'engine_jobs', 'engine_schedules',
                          'post_image_pool')
        and (p.policyname is not null or a.privilege_type is not null) $$,
  'エンジン内部のテーブルにはクライアント向けのポリシー・権限が無い'
);

select set_eq(
  $$ select tablename::text || ':' || cmd::text
       from pg_policies
      where schemaname = 'public'
        and tablename in ('promises', 'proactive_settings', 'character_states') $$,
  array['promises:SELECT', 'proactive_settings:SELECT', 'character_states:SELECT'],
  'promises / proactive_settings / character_states のポリシーは参照（SELECT）だけ（変更は API 経由）'
);

-- ---------------------------------------------------------------------------
-- Realtime
-- ---------------------------------------------------------------------------
select set_eq(
  $$ select tablename::text
       from pg_publication_tables
      where pubname = 'supabase_realtime'
        and schemaname = 'public' $$,
  array['comments', 'messages', 'memories'],
  'supabase_realtime publication の public テーブルは messages / comments / memories（「覚えました」の通知）のみ'
);

-- 購読者のロールが主キー列を SELECT できないと、Realtime は RLS を評価せずに本文なしの
-- 「Error 401: Unauthorized」イベントを全行分配信してしまう（anon key だけで DM の件数・時刻が漏れる）。
-- publication に追加したテーブルは、anon / authenticated の両方が主キー列を SELECT できること。
select is_empty(
  $$ select pt.tablename, att.attname, r.rolname
       from pg_publication_tables pt
       join pg_constraint con
         on con.conrelid = format('%I.%I', pt.schemaname, pt.tablename)::regclass and con.contype = 'p'
       cross join lateral unnest(con.conkey) as k(attnum)
       join pg_attribute att on att.attrelid = con.conrelid and att.attnum = k.attnum
       cross join (values ('anon'::name), ('authenticated'::name)) as r(rolname)
      where pt.pubname = 'supabase_realtime'
        and pt.schemaname = 'public'
        and not has_column_privilege(r.rolname, con.conrelid, att.attname, 'SELECT') $$,
  'Realtime 対象テーブルの主キー列は anon / authenticated が SELECT できる（RLS で判定させ、401 イベントを配信させない）'
);

select * from finish();
rollback;
