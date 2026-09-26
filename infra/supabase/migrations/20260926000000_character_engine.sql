-- =============================================================================
-- キャラクターエンジン v1.0（記憶 × カレンダー × 好感度 × 自発メッセージ）
-- 仕様: project-p-character-engine-spec.md §8 / 設計判断: docs/adr/0035〜
--
-- 方針（初期マイグレーションと同じ）:
--   * すべて RLS 有効。クライアントに見せるものだけ列単位で grant し、書き込みは Python API 経由。
--   * エンジン内部の状態（好感度・キャラ側の記憶・ジョブ）はクライアントから一切参照できない。
--   * E1: 好感度のテーブルは、課金・有料投稿に関わるテーブル（posts / post_private_assets / 将来の課金テーブル）と
--     外部キーや結合を持たない。好感度の更新処理はこのファイルの affinity_* と messages / memories だけを使う。
--   * 時刻はすべて timestamptz（UTC 保存）。キャラの世界は日本時間で動く（アプリ側で Asia/Tokyo に変換）。
--   * 評価ハーネスの「時間の早送り」のため、エンジンが作る行の日時はアプリの時計（Clock）から明示的に渡す。
-- =============================================================================

create extension if not exists btree_gist with schema extensions;

-- -----------------------------------------------------------------------------
-- messages: 自発メッセージ（Proactive Messenger, P6）の印
-- -----------------------------------------------------------------------------
alter table public.messages
  add column is_proactive boolean not null default false;
comment on column public.messages.is_proactive is 'キャラからの自発メッセージ（ユーザーの発言への返答ではない）。';

-- -----------------------------------------------------------------------------
-- conversations: 返答後の非同期処理（記憶の抽出・好感度の評価）の処理済み位置
-- -----------------------------------------------------------------------------
alter table public.conversations
  add column analyzed_until timestamptz; -- この時刻以前のメッセージは post_turn ジョブで処理済み
comment on column public.conversations.analyzed_until is '返答後の非同期処理（記憶・好感度・約束）で処理済みのメッセージの最新の created_at。';

-- -----------------------------------------------------------------------------
-- memories の拡張（§4 M2〜M4・M11 / §8）
-- -----------------------------------------------------------------------------
alter table public.memories
  add column kind text not null default 'fact'
    check (kind in ('fact', 'preference', 'episode', 'promise', 'emotion', 'relationship', 'summary')),
  add column status text not null default 'active'
    check (status in ('active', 'superseded')),
  add column superseded_by uuid references public.memories(id) on delete set null,
  add column superseded_at timestamptz,
  add column last_referenced_at timestamptz,
  add column reference_count int not null default 0 check (reference_count >= 0),
  add column source_conversation_id uuid references public.conversations(id) on delete set null,
  add constraint memories_superseded_consistency check (
    (status = 'active' and superseded_at is null) or (status = 'superseded' and superseded_at is not null)
  );

comment on column public.memories.kind is '記憶の種類: fact(事実) / preference(好み) / episode(出来事) / promise(約束) / emotion(感情) / relationship(関係性の変化・呼び方) / summary(中期要約)。';
comment on column public.memories.status is 'active = 有効。superseded = 新しい情報と矛盾して置き換えられた（履歴として残す, M4）。';
comment on column public.memories.superseded_by is '置き換えた新しい記憶（M4）。';
comment on column public.memories.last_referenced_at is '最後にプロンプトへ注入された日時（検索の新しさ・参照頻度の計算に使う）。';

-- 既存の要約（tags に summary）を kind に移す
update public.memories set kind = 'summary' where 'summary' = any(tags);

create index memories_active_pair_kind_idx on public.memories (user_id, character_id, kind)
  where status = 'active';
create index memories_superseded_by_idx on public.memories (superseded_by) where superseded_by is not null;
create index memories_source_conversation_id_idx on public.memories (source_conversation_id)
  where source_conversation_id is not null;

-- クライアント（メモリパネル）に新しい列も見せる。embedding は引き続き非公開
grant select (kind, status, superseded_by, superseded_at, last_referenced_at, reference_count, source_conversation_id)
  on public.memories to authenticated;

-- -----------------------------------------------------------------------------
-- memory_tombstones: ユーザーが削除した記憶の「墓標」（E5: 勝手に復活させない）
--   内容（本文）は保存しない。自動抽出が同じ内容を再び作らないよう、埋め込みとハッシュだけを残す。
-- -----------------------------------------------------------------------------
create table public.memory_tombstones (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  kind text not null,
  content_hash text not null, -- 正規化した本文の SHA-256
  embedding extensions.vector(1536),
  deleted_at timestamptz not null default now()
);
create index memory_tombstones_pair_idx on public.memory_tombstones (user_id, character_id);
create index memory_tombstones_character_id_idx on public.memory_tombstones (character_id);
comment on table public.memory_tombstones is 'ユーザーが削除した記憶の墓標（本文は持たない）。自動抽出による復活を防ぐ（E5）。クライアント非公開。';

-- -----------------------------------------------------------------------------
-- character_events: キャラの予定（§5 C2〜C4・C8・C10・C11）
-- -----------------------------------------------------------------------------
create table public.character_events (
  id uuid primary key default gen_random_uuid(),
  character_id uuid not null references public.characters(id) on delete cascade,
  kind text not null check (kind in ('routine', 'oneoff', 'seasonal', 'promise')),
  title text not null check (char_length(title) between 1 and 200),
  description text check (description is null or char_length(description) <= 1000),
  location text check (location is null or char_length(location) <= 200),
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  mood text check (mood is null or char_length(mood) <= 100),
  busyness smallint not null default 1 check (busyness between 0 and 3), -- 0=暇 1=ふつう 2=忙しい 3=手が離せない（睡眠など）
  visibility text not null default 'public' check (visibility in ('public', 'user')),
  user_id uuid references public.profiles(id) on delete cascade, -- visibility = 'user'（特定ユーザーとの約束）のとき
  participants uuid[] not null default '{}', -- 一緒に過ごすキャラ（C10。MVP では構造のみ）
  status text not null default 'scheduled' check (status in ('scheduled', 'done', 'cancelled')),
  source text not null default 'generator' check (source in ('generator', 'seasonal', 'promise', 'manual')),
  source_key text, -- 生成元のキー（ペルソナの予定 ID・行事キーなど。冪等性と監査用）
  generated_for date, -- 生成単位の日付（JST）。同じ日を二重に生成しない
  post_id uuid references public.posts(id) on delete set null, -- この予定から生成したフィード投稿（C7）
  meta jsonb not null default '{}',
  created_at timestamptz not null default now(),
  check (ends_at > starts_at),
  check ((visibility = 'user') = (user_id is not null)),
  -- C11: 公開の予定（キャラの実際の居場所）は同じキャラで時間が重ならない（DB で保証）
  constraint character_events_no_overlap exclude using gist (
    character_id with =,
    tstzrange(starts_at, ends_at, '[)') with &&
  ) where (visibility = 'public' and status <> 'cancelled')
);
create index character_events_character_starts_idx on public.character_events (character_id, starts_at);
create index character_events_user_idx on public.character_events (user_id, starts_at) where user_id is not null;
create index character_events_generated_for_idx on public.character_events (character_id, generated_for);
create index character_events_post_id_idx on public.character_events (post_id) where post_id is not null;
comment on table public.character_events is 'キャラの予定。public = キャラの実際の生活（同時刻の重複は制約で禁止）、user = 特定ユーザーとの約束。';

-- 予定から生成した投稿の印（C7）
alter table public.posts
  add column source_event_id uuid references public.character_events(id) on delete set null;
create index posts_source_event_id_idx on public.posts (source_event_id) where source_event_id is not null;

-- -----------------------------------------------------------------------------
-- character_states: キャラの現在の状態（§5 C5。スケジューラが更新するキャッシュ）
-- -----------------------------------------------------------------------------
create table public.character_states (
  character_id uuid primary key references public.characters(id) on delete cascade,
  activity text not null check (char_length(activity) between 1 and 200),
  location text,
  mood text,
  busyness smallint not null default 1 check (busyness between 0 and 3),
  event_id uuid references public.character_events(id) on delete set null,
  status_label text check (status_label is null or char_length(status_label) <= 40), -- UI 表示用（例: 「仕事中」）
  updated_at timestamptz not null default now()
);
create index character_states_event_id_idx on public.character_states (event_id) where event_id is not null;
comment on table public.character_states is 'キャラの今の状態（今どこで何をしているか・気分・忙しさ）。status_label だけクライアントに公開（DM ヘッダー）。';

-- -----------------------------------------------------------------------------
-- promises: ユーザーとキャラの約束（§4 M6 / §5 C8）
-- -----------------------------------------------------------------------------
create table public.promises (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  content text not null check (char_length(content) between 1 and 500),
  due_at timestamptz, -- 期日（日付だけ分かる場合は JST のその日の 12:00）。不明なら null
  due_precision text not null default 'day' check (due_precision in ('datetime', 'day', 'week', 'month', 'unknown')),
  status text not null default 'pending' check (status in ('pending', 'mentioned', 'done', 'cancelled')),
  source_memory_id uuid references public.memories(id) on delete set null,
  source_message_id uuid references public.messages(id) on delete set null,
  event_id uuid references public.character_events(id) on delete set null, -- カレンダーに登録した予定（C8）
  mentioned_at timestamptz,
  completed_at timestamptz,
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index promises_pair_status_due_idx on public.promises (user_id, character_id, status, due_at);
create index promises_character_id_idx on public.promises (character_id);
create index promises_due_pending_idx on public.promises (due_at) where status in ('pending', 'mentioned');
create index promises_source_memory_id_idx on public.promises (source_memory_id) where source_memory_id is not null;
create index promises_source_message_id_idx on public.promises (source_message_id) where source_message_id is not null;
create index promises_event_id_idx on public.promises (event_id) where event_id is not null;

create trigger promises_touch_updated_at
  before update on public.promises
  for each row execute function public.touch_updated_at();

-- -----------------------------------------------------------------------------
-- character_memories: キャラ側の記憶（§4 M8 / §5 C9）
-- -----------------------------------------------------------------------------
create table public.character_memories (
  id uuid primary key default gen_random_uuid(),
  character_id uuid not null references public.characters(id) on delete cascade,
  user_id uuid references public.profiles(id) on delete cascade, -- null = 全ユーザー共通（予定由来など）
  kind text not null default 'self_statement' check (kind in ('self_statement', 'event', 'fact')),
  content text not null check (char_length(content) between 1 and 1000),
  occurred_at timestamptz, -- その出来事があった日時
  source_event_id uuid references public.character_events(id) on delete set null,
  source_message_id uuid references public.messages(id) on delete set null,
  embedding extensions.vector(1536),
  created_at timestamptz not null default now()
);
create index character_memories_character_user_idx on public.character_memories (character_id, user_id, created_at desc);
create index character_memories_user_id_idx on public.character_memories (user_id) where user_id is not null;
create index character_memories_source_event_id_idx on public.character_memories (source_event_id) where source_event_id is not null;
create index character_memories_source_message_id_idx on public.character_memories (source_message_id) where source_message_id is not null;
comment on table public.character_memories is 'キャラが自分について話したこと・過ごした予定（自己矛盾の防止）。user_id が null のものは全ユーザー共通。クライアント非公開。';

-- -----------------------------------------------------------------------------
-- affinity_states / affinity_history: 好感度（§6）
--   E1: 課金・有料投稿のテーブルへの外部キーを持たない（持たせてはならない）。
-- -----------------------------------------------------------------------------
create table public.affinity_states (
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  closeness numeric(5, 2) not null default 10 check (closeness between 0 and 100),       -- 親しさ
  trust numeric(5, 2) not null default 10 check (trust between 0 and 100),               -- 信頼
  romance numeric(5, 2) not null default 0 check (romance between 0 and 100),            -- ときめき
  awkwardness numeric(5, 2) not null default 0 check (awkwardness between 0 and 100),    -- 気まずさ（緊張の軸）
  discontent numeric(5, 2) not null default 0 check (discontent between 0 and 100),      -- 不満（マイナスの軸）
  possessiveness numeric(5, 2) not null default 0 check (possessiveness between 0 and 100), -- 独占欲（ペルソナで有効な場合のみ動く）
  stage text not null default 'acquaintance' check (stage in ('acquaintance', 'friend', 'close', 'lover')),
  stage_changed_at timestamptz,
  stage_candidate text check (stage_candidate is null or stage_candidate in ('acquaintance', 'friend', 'close', 'lover')),
  stage_candidate_since timestamptz, -- ヒステリシス: 候補の段階の条件を満たし始めた日時
  last_interaction_at timestamptz,
  daily_date date, -- daily_delta の集計日（JST）
  daily_delta jsonb not null default '{}', -- その日の軸ごとの変化量の合計（1日あたりの上限 A5）
  evaluated_until timestamptz, -- 評価済みのメッセージの最新の created_at
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, character_id)
);
create index affinity_states_character_id_idx on public.affinity_states (character_id);
comment on table public.affinity_states is '好感度（ユーザー×キャラ）。複数の軸と関係の段階。課金データとは一切結合しない（E1）。クライアント非公開（A11）。';

create trigger affinity_states_touch_updated_at
  before update on public.affinity_states
  for each row execute function public.touch_updated_at();

create table public.affinity_history (
  id bigserial primary key,
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  before jsonb not null,
  after jsonb not null,
  delta jsonb not null,
  stage_before text not null,
  stage_after text not null,
  reason text, -- 評価の要約
  evaluator text not null, -- 'llm:<model>' / 'rule' / 'decay' など
  manipulation_detected boolean not null default false, -- A10: 操作の試みを検知して変化を 0 にした
  source_message_ids uuid[] not null default '{}',
  created_at timestamptz not null default now()
);
create index affinity_history_pair_created_idx on public.affinity_history (user_id, character_id, created_at desc);
create index affinity_history_character_id_idx on public.affinity_history (character_id);
comment on table public.affinity_history is '好感度の変化の履歴（前後・理由・元の会話）。クライアント非公開。';

-- -----------------------------------------------------------------------------
-- proactive_messages / proactive_settings: 自発メッセージ（§7）
-- -----------------------------------------------------------------------------
create table public.proactive_messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid not null references public.characters(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  message_id uuid references public.messages(id) on delete set null,
  trigger text not null check (trigger in ('calendar_event', 'promise_due', 'seasonal', 'inactivity', 'feed_post', 'paid_notice')),
  trigger_ref text not null, -- 冪等キー（予定 ID・約束 ID・行事キー + 日付など）
  sent_at timestamptz not null default now(),
  replied_at timestamptz, -- ユーザーが次に発言した日時（P4: 返信がないのに連投しない）
  meta jsonb not null default '{}',
  unique (user_id, character_id, trigger, trigger_ref)
);
create index proactive_messages_user_sent_idx on public.proactive_messages (user_id, sent_at desc);
create index proactive_messages_conversation_idx on public.proactive_messages (conversation_id, sent_at desc);
create index proactive_messages_character_id_idx on public.proactive_messages (character_id);
create index proactive_messages_message_id_idx on public.proactive_messages (message_id) where message_id is not null;

create table public.proactive_settings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  character_id uuid references public.characters(id) on delete cascade, -- null = 全キャラ共通の設定
  enabled boolean not null default true,
  quiet_start smallint check (quiet_start between 0 and 23), -- 送らない時間帯（JST の時）。null = 既定（0 時）
  quiet_end smallint check (quiet_end between 0 and 23),     -- null = 既定（7 時）
  updated_at timestamptz not null default now(),
  constraint proactive_settings_user_character_key unique nulls not distinct (user_id, character_id)
);
create index proactive_settings_character_id_idx on public.proactive_settings (character_id) where character_id is not null;

create trigger proactive_settings_touch_updated_at
  before update on public.proactive_settings
  for each row execute function public.touch_updated_at();

-- -----------------------------------------------------------------------------
-- engine_jobs / engine_schedules: 非同期ジョブと定期実行（Postgres キュー。ADR-0036）
-- -----------------------------------------------------------------------------
create table public.engine_jobs (
  id bigserial primary key,
  kind text not null check (char_length(kind) between 1 and 60),
  dedupe_key text, -- 同じ kind + dedupe_key の未処理ジョブは 1 件だけ（デバウンス・冪等）
  payload jsonb not null default '{}',
  run_at timestamptz not null default now(),
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed', 'dead')),
  attempts int not null default 0,
  max_attempts int not null default 5,
  last_error text,
  locked_at timestamptz,
  locked_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);
create unique index engine_jobs_dedupe_idx on public.engine_jobs (kind, dedupe_key)
  where dedupe_key is not null and status in ('queued', 'running');
create index engine_jobs_ready_idx on public.engine_jobs (run_at) where status = 'queued';
create index engine_jobs_status_finished_idx on public.engine_jobs (status, finished_at);

create trigger engine_jobs_touch_updated_at
  before update on public.engine_jobs
  for each row execute function public.touch_updated_at();

create table public.engine_schedules (
  name text primary key,
  last_run_at timestamptz,
  next_run_at timestamptz,
  last_error text,
  updated_at timestamptz not null default now()
);

-- -----------------------------------------------------------------------------
-- post_image_pool: 予定に沿った投稿用の事前生成画像（§5 C7。画像生成は次フェーズ）
-- -----------------------------------------------------------------------------
create table public.post_image_pool (
  id uuid primary key default gen_random_uuid(),
  character_id uuid references public.characters(id) on delete cascade, -- null = 全キャラ共通
  tags text[] not null default '{}', -- 例: {cafe, food} / {izakaya} / {sakura}
  image_url text not null, -- StorageAdapter で解決する URL またはオブジェクトキー
  created_at timestamptz not null default now()
);
create index post_image_pool_character_idx on public.post_image_pool (character_id);
create index post_image_pool_tags_idx on public.post_image_pool using gin (tags);

-- =============================================================================
-- Row Level Security / 権限
-- =============================================================================
alter table public.memory_tombstones enable row level security;
alter table public.character_events enable row level security;
alter table public.character_states enable row level security;
alter table public.promises enable row level security;
alter table public.character_memories enable row level security;
alter table public.affinity_states enable row level security;
alter table public.affinity_history enable row level security;
alter table public.proactive_messages enable row level security;
alter table public.proactive_settings enable row level security;
alter table public.engine_jobs enable row level security;
alter table public.engine_schedules enable row level security;
alter table public.post_image_pool enable row level security;

-- 新しいテーブルは既定で非公開（初期マイグレーションの default privileges）。念のため明示的に取り消す
revoke all on public.memory_tombstones, public.character_events, public.character_states, public.promises,
  public.character_memories, public.affinity_states, public.affinity_history, public.proactive_messages,
  public.proactive_settings, public.engine_jobs, public.engine_schedules, public.post_image_pool
  from anon, authenticated;
revoke all on sequence public.affinity_history_id_seq, public.engine_jobs_id_seq from anon, authenticated;

-- ---- character_states: 有効キャラの表示用ラベルだけ参照可（DM ヘッダーの「仕事中」など）
grant select (character_id, status_label, busyness, updated_at) on public.character_states to authenticated;

create policy "character_states: 有効キャラの状態を参照" on public.character_states
  for select to authenticated
  using (exists (select 1 from public.characters ch where ch.id = character_states.character_id and ch.is_active));

-- ---- promises: 本人の約束のみ参照（メモリパネル）。変更は API 経由
grant select (id, user_id, character_id, content, due_at, due_precision, status, source_memory_id,
              mentioned_at, completed_at, cancelled_at, created_at, updated_at)
  on public.promises to authenticated;

create policy "promises: 本人のみ参照" on public.promises
  for select to authenticated
  using (user_id = (select auth.uid()));

-- ---- proactive_settings: 本人の設定のみ参照（変更は API 経由・監査ログに残す）
grant select (id, user_id, character_id, enabled, quiet_start, quiet_end, updated_at)
  on public.proactive_settings to authenticated;

create policy "proactive_settings: 本人のみ参照" on public.proactive_settings
  for select to authenticated
  using (user_id = (select auth.uid()));

-- ---- 以下はポリシー無し（= クライアント不可。API / スケジューラのみ）:
--   memory_tombstones, character_events, character_memories, affinity_states, affinity_history,
--   proactive_messages, engine_jobs, engine_schedules, post_image_pool

-- =============================================================================
-- Realtime: メモリの作成通知（「覚えました」表示。記憶の抽出は返答の後に非同期で行うため）
--   ADR-0016: publication に加えるテーブルは anon に主キー列だけ grant する（内容の無い INSERT イベントを防ぐ）
-- =============================================================================
grant select (id) on public.memories to anon;

do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.memories;
  end if;
end;
$$;
