-- =============================================================================
-- Project P MVP v0.1 — 初期スキーマ
--
-- 方針:
--   * すべての public テーブルで RLS を有効化する（開発依頼書 §6）。
--   * クライアント（Next.js / supabase-js, role=authenticated）は「読み取り」と
--     一部の軽微な更新（いいね・既読・退会）のみ直接行う。
--   * ユーザー由来のテキスト（DM・コメント・メモリ）の書き込みはすべて
--     Python API（FastAPI）経由で行い、Gate #1 モデレーションと監査ログを通す。
--     Python API は DATABASE_URL（postgres ロール）で接続し RLS をバイパスするため、
--     API 側のクエリは必ず user_id で所有者チェックを行うこと。
--   * 仕様書のテーブル定義に対する追加カラム/テーブルには「[追加]」コメントを付け、
--     理由を docs/adr/ に記録している。
-- =============================================================================

create extension if not exists vector with schema extensions;

-- -----------------------------------------------------------------------------
-- profiles: auth.users の拡張
-- -----------------------------------------------------------------------------
create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  display_name text,
  created_at timestamptz not null default now(),
  deleted_at timestamptz -- 退会（論理削除）日時
);
comment on table public.profiles is 'ユーザー（auth.users の拡張）。deleted_at が非NULLなら退会済み（論理削除）。';

-- -----------------------------------------------------------------------------
-- characters: AIキャラクター
-- -----------------------------------------------------------------------------
create table public.characters (
  id uuid primary key default gen_random_uuid(),
  handle text unique not null check (handle ~ '^[a-z0-9_.]{2,30}$'),
  name text not null,
  avatar_url text not null, -- StorageAdapter で解決するURLまたはオブジェクトキー
  bio text,
  persona_key text not null, -- packages/personas/<persona_key>.yaml
  system_prompt text not null, -- YAML が読めない場合のフォールバック用。クライアントには非公開
  follower_count int not null default 0 check (follower_count >= 0), -- [追加] 表示専用（フォロー機能は無し）
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);
comment on table public.characters is 'AIキャラクター。system_prompt / persona_key はクライアントに公開しない（列権限で制御）。';
comment on column public.characters.follower_count is '[追加] プロフィール表示用のフォロワー数。フォロー機能はスコープ外のため静的値。';

-- -----------------------------------------------------------------------------
-- posts: キャラクターの投稿（ユーザーは投稿不可 / H2）
-- -----------------------------------------------------------------------------
create table public.posts (
  id uuid primary key default gen_random_uuid(),
  character_id uuid not null references public.characters(id) on delete cascade,
  image_url text not null, -- 有料投稿の場合は「プレビュー（ぼかし前提の低解像度）画像」。本体は post_private_assets
  caption text,
  is_paid boolean not null default false,
  price_tokens int not null default 0 check (price_tokens >= 0),
  like_count int not null default 0 check (like_count >= 0),
  comment_count int not null default 0 check (comment_count >= 0),
  published_at timestamptz not null default now(), -- 未来日時は予約投稿（RLSで公開前は不可視）
  created_at timestamptz not null default now(),
  check (not is_paid or price_tokens > 0)
);
create index posts_published_at_idx on public.posts (published_at desc);
create index posts_character_id_is_paid_idx on public.posts (character_id, is_paid);
comment on column public.posts.image_url is '無料投稿は本体画像。有料投稿はプレビュー画像（本体は post_private_assets に保持しクライアントから到達不能）。';
comment on column public.posts.published_at is '公開日時。未来日時の投稿は RLS により公開時刻まで表示されない（予約投稿）。';

-- [追加] 有料投稿の本体アセット。RLS有効・ポリシー無し = クライアントから一切読めない。
-- 決済実装時に「購入済みユーザーのみ署名URLを発行する」API を追加する想定（本MVPでは未実装 / H3）。
create table public.post_private_assets (
  post_id uuid primary key references public.posts(id) on delete cascade,
  image_url text not null,
  created_at timestamptz not null default now()
);
comment on table public.post_private_assets is '[追加] 有料投稿の本体画像。クライアント非公開（RLSポリシー無し）。決済は本MVPスコープ外。';

-- -----------------------------------------------------------------------------
-- likes
-- -----------------------------------------------------------------------------
create table public.likes (
  user_id uuid not null references public.profiles(id) on delete cascade,
  post_id uuid not null references public.posts(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, post_id)
);
create index likes_post_id_idx on public.likes (post_id);

-- -----------------------------------------------------------------------------
-- comments: ユーザー or キャラクター
-- -----------------------------------------------------------------------------
create table public.comments (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.posts(id) on delete cascade,
  parent_comment_id uuid references public.comments(id) on delete cascade, -- [追加] 返信先
  author_type text not null check (author_type in ('user', 'character')),
  author_user_id uuid references public.profiles(id) on delete set null,
  author_character_id uuid references public.characters(id) on delete set null,
  body text not null check (char_length(body) between 1 and 1000),
  created_at timestamptz not null default now(),
  check (
    (author_type = 'user' and author_user_id is not null and author_character_id is null) or
    (author_type = 'character' and author_character_id is not null and author_user_id is null)
  )
);
-- NOTE: 仕様の check 制約は on delete set null と矛盾し得る（作成者削除時に制約違反）。
-- profiles は auth.users の cascade で消えるため、ユーザー削除時は comments も
-- 事前に削除される運用とする（退会は論理削除なので通常は発生しない）。詳細は ADR-0004。
create index comments_post_id_created_at_idx on public.comments (post_id, created_at);
create index comments_parent_comment_id_idx on public.comments (parent_comment_id);
comment on column public.comments.parent_comment_id is '[追加] 返信先コメント（POST /comments/generate でキャラが返信する際に使用）。';

-- -----------------------------------------------------------------------------
-- conversations / messages: DM
-- -----------------------------------------------------------------------------
create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  last_message_at timestamptz not null default now(),
  user_last_read_at timestamptz not null default now(), -- [追加] 未読バッジ計算用
  summary_cursor timestamptz, -- [追加] 中期メモリ: この時刻以前のメッセージは要約済み
  created_at timestamptz not null default now(),
  unique (user_id, character_id)
);
create index conversations_user_id_last_message_at_idx on public.conversations (user_id, last_message_at desc);
comment on column public.conversations.user_last_read_at is '[追加] ユーザーが最後にこの会話を開いた時刻。これより新しいキャラ発言が未読。';
comment on column public.conversations.summary_cursor is '[追加] 中期メモリ（要約）の処理済み位置。created_at <= summary_cursor のメッセージは要約済み。';

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  sender_type text not null check (sender_type in ('user', 'character')),
  body text not null check (char_length(body) between 1 and 4000),
  created_at timestamptz not null default clock_timestamp()
);
-- clock_timestamp(): 同一トランザクションでユーザー発言→キャラ返答を保存しても順序が保たれるようにする
create index messages_conversation_id_created_at_idx on public.messages (conversation_id, created_at);

-- -----------------------------------------------------------------------------
-- memories: 長期メモリ（pgvector）
-- -----------------------------------------------------------------------------
create table public.memories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  content text not null check (char_length(content) between 1 and 1000),
  importance numeric(3, 2) not null default 0.5 check (importance >= 0 and importance <= 1),
  tags text[] not null default '{}', -- 例: {'secret'} = 二人だけの秘密, {'summary'} = 中期要約
  embedding extensions.vector(1536),
  source_message_id uuid references public.messages(id) on delete set null,
  is_user_edited boolean not null default false, -- true の記憶は自動処理で上書きしない
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now() -- [追加]
);
create index memories_user_id_character_id_idx on public.memories (user_id, character_id, created_at desc);
-- 仕様は ivfflat だが、空テーブルに作成した ivfflat はリストの重心が学習されず再現率が
-- 著しく劣化するため HNSW を採用（ADR-0005）。なお DM 応答時の検索は
-- user×character に絞った厳密検索（exact scan）で行い、この索引は横断検索用。
create index memories_embedding_hnsw_idx on public.memories using hnsw (embedding extensions.vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- audit_logs: 監査ログ（H6）。クライアントからは一切アクセス不可。
-- -----------------------------------------------------------------------------
create table public.audit_logs (
  id bigserial primary key,
  event_type text not null, -- 'chat.request' / 'chat.response' / 'moderation.flag' ...
  user_id uuid,
  character_id uuid,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
create index audit_logs_event_type_created_at_idx on public.audit_logs (event_type, created_at desc);
create index audit_logs_user_id_created_at_idx on public.audit_logs (user_id, created_at desc);

-- =============================================================================
-- 関数・トリガー
-- =============================================================================

-- 初回ログイン（auth.users 作成）時に profiles を自動作成
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, split_part(coalesce(new.email, ''), '@', 1))
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 退会（論理削除）は一方向。クライアント（authenticated）からの deleted_at の解除・変更を禁止する。
-- 復旧が必要な場合は運用者が postgres ロールで deleted_at を NULL に戻す（docs/handover 参照）。
create or replace function public.guard_profile_withdrawal()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.deleted_at is not null
     and new.deleted_at is distinct from old.deleted_at
     and current_user in ('authenticated', 'anon') then
    raise exception 'withdrawn profile cannot be restored by the user'
      using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger profiles_guard_withdrawal
  before update on public.profiles
  for each row execute function public.guard_profile_withdrawal();

-- likes → posts.like_count
create or replace function public.sync_post_like_count()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    update public.posts set like_count = like_count + 1 where id = new.post_id;
    return new;
  elsif tg_op = 'DELETE' then
    update public.posts set like_count = greatest(like_count - 1, 0) where id = old.post_id;
    return old;
  end if;
  return null;
end;
$$;

create trigger likes_sync_post_like_count
  after insert or delete on public.likes
  for each row execute function public.sync_post_like_count();

-- comments → posts.comment_count
create or replace function public.sync_post_comment_count()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    update public.posts set comment_count = comment_count + 1 where id = new.post_id;
    return new;
  elsif tg_op = 'DELETE' then
    update public.posts set comment_count = greatest(comment_count - 1, 0) where id = old.post_id;
    return old;
  end if;
  return null;
end;
$$;

create trigger comments_sync_post_comment_count
  after insert or delete on public.comments
  for each row execute function public.sync_post_comment_count();

-- ユーザーによるコメント削除は Supabase 直結のため、DB側で監査ログに残す（H6）
create or replace function public.audit_comment_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.audit_logs (event_type, user_id, character_id, payload)
  values (
    'comment.delete',
    auth.uid(),
    old.author_character_id,
    jsonb_build_object(
      'comment_id', old.id,
      'post_id', old.post_id,
      'author_type', old.author_type,
      'author_user_id', old.author_user_id,
      'body', old.body,
      'deleted_by_role', coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'role'
    )
  );
  return old;
end;
$$;

create trigger comments_audit_delete
  after delete on public.comments
  for each row execute function public.audit_comment_delete();

-- messages → conversations.last_message_at
create or replace function public.sync_conversation_last_message_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.conversations
     set last_message_at = greatest(last_message_at, new.created_at)
   where id = new.conversation_id;
  return new;
end;
$$;

create trigger messages_sync_conversation_last_message_at
  after insert on public.messages
  for each row execute function public.sync_conversation_last_message_at();

-- memories.updated_at
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger memories_touch_updated_at
  before update on public.memories
  for each row execute function public.touch_updated_at();

-- DM一覧（会話済みキャラ・最新メッセージ・未読数）。security invoker = RLS が適用される。
create or replace function public.list_dm_threads()
returns table (
  conversation_id uuid,
  character_id uuid,
  character_handle text,
  character_name text,
  character_avatar_url text,
  last_message_body text,
  last_message_sender_type text,
  last_message_at timestamptz,
  unread_count int
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    c.id,
    ch.id,
    ch.handle,
    ch.name,
    ch.avatar_url,
    lm.body,
    lm.sender_type,
    coalesce(lm.created_at, c.last_message_at),
    (
      select count(*)::int
        from public.messages m
       where m.conversation_id = c.id
         and m.sender_type = 'character'
         and m.created_at > c.user_last_read_at
    )
  from public.conversations c
  join public.characters ch on ch.id = c.character_id
  left join lateral (
    select m.body, m.sender_type, m.created_at
      from public.messages m
     where m.conversation_id = c.id
     order by m.created_at desc
     limit 1
  ) lm on true
  where c.user_id = auth.uid()
  order by coalesce(lm.created_at, c.last_message_at) desc;
$$;

-- 会話を既読にする（DM画面を開いた / 新着を表示した時）
create or replace function public.mark_conversation_read(p_conversation_id uuid)
returns void
language sql
security invoker
set search_path = ''
as $$
  update public.conversations
     set user_last_read_at = now()
   where id = p_conversation_id
     and user_id = auth.uid();
$$;

-- =============================================================================
-- Row Level Security
-- =============================================================================
alter table public.profiles enable row level security;
alter table public.characters enable row level security;
alter table public.posts enable row level security;
alter table public.post_private_assets enable row level security;
alter table public.likes enable row level security;
alter table public.comments enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.memories enable row level security;
alter table public.audit_logs enable row level security;

-- 既定権限をリセットし、必要な権限のみ明示的に付与する（最小権限）
revoke all on all tables in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated, public;
revoke all on all sequences in schema public from anon, authenticated;

-- 今後のマイグレーションで作成されるテーブル・関数も既定で非公開にする。
-- 新規テーブルを追加する際は「RLS有効化 + 必要な権限の明示 grant + ポリシー」を必ずセットで書くこと。
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke execute on functions from anon, authenticated, public;

-- ---- profiles: 本人のみ参照・更新（display_name / deleted_at のみ）
grant select on public.profiles to authenticated;
grant update (display_name, deleted_at) on public.profiles to authenticated;

create policy "profiles: 本人のみ参照" on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));

create policy "profiles: 本人のみ更新" on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

-- ---- characters: 有効なキャラを参照可能。system_prompt / persona_key は列権限で非公開
grant select (id, handle, name, avatar_url, bio, follower_count, is_active, created_at)
  on public.characters to authenticated;

create policy "characters: 有効キャラを参照" on public.characters
  for select to authenticated
  using (is_active);

-- ---- posts: 公開済み（published_at <= now()）かつ有効キャラの投稿のみ
grant select on public.posts to authenticated;

create policy "posts: 公開済み投稿を参照" on public.posts
  for select to authenticated
  using (
    published_at <= now()
    and exists (
      select 1 from public.characters ch
       where ch.id = posts.character_id and ch.is_active
    )
  );

-- ---- post_private_assets: ポリシー無し（= 誰も読めない。service role / postgres のみ）

-- ---- likes: 本人のいいねのみ参照・作成・削除
grant select, insert, delete on public.likes to authenticated;

create policy "likes: 本人のみ参照" on public.likes
  for select to authenticated
  using (user_id = (select auth.uid()));

create policy "likes: 本人のみ作成" on public.likes
  for insert to authenticated
  with check (
    user_id = (select auth.uid())
    and exists (select 1 from public.posts p where p.id = likes.post_id) -- posts の RLS が効く
  );

create policy "likes: 本人のみ削除" on public.likes
  for delete to authenticated
  using (user_id = (select auth.uid()));

-- ---- comments: 公開投稿のコメントを参照。作成は API 経由のみ（モデレーション必須）。本人は削除可
grant select, delete on public.comments to authenticated;

create policy "comments: 公開投稿のコメントを参照" on public.comments
  for select to authenticated
  using (
    created_at <= now() -- 予約投稿に付けたシードコメントは、その時刻になってから順に表示される
    and exists (select 1 from public.posts p where p.id = comments.post_id)
  );

create policy "comments: 本人のコメントのみ削除" on public.comments
  for delete to authenticated
  using (author_type = 'user' and author_user_id = (select auth.uid()));

-- ---- conversations: 本人の会話のみ参照。作成は API 経由。既読更新のみ可
grant select on public.conversations to authenticated;
grant update (user_last_read_at) on public.conversations to authenticated;

create policy "conversations: 本人のみ参照" on public.conversations
  for select to authenticated
  using (user_id = (select auth.uid()));

create policy "conversations: 本人のみ既読更新" on public.conversations
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

-- ---- messages: 本人の会話のメッセージのみ参照。作成は API 経由のみ
grant select on public.messages to authenticated;

create policy "messages: 本人の会話のみ参照" on public.messages
  for select to authenticated
  using (
    exists (
      select 1 from public.conversations c
       where c.id = messages.conversation_id
         and c.user_id = (select auth.uid())
    )
  );

-- ---- memories: 本人のメモリのみ参照（embedding は非公開）。編集は API 経由のみ
grant select (id, user_id, character_id, content, importance, tags, source_message_id,
              is_user_edited, created_at, updated_at)
  on public.memories to authenticated;

create policy "memories: 本人のみ参照" on public.memories
  for select to authenticated
  using (user_id = (select auth.uid()));

-- ---- audit_logs: ポリシー無し（= クライアント不可）

-- ---- RPC
grant execute on function public.list_dm_threads() to authenticated;
grant execute on function public.mark_conversation_read(uuid) to authenticated;

-- =============================================================================
-- Realtime（DM とコメントの同期）
-- =============================================================================
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.messages;
    alter publication supabase_realtime add table public.comments;
  end if;
end;
$$;
