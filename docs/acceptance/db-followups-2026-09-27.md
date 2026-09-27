# DB の申し送り事項（2026-09-27 受け入れレビュー）

2026-09-27 の受け入れレビューで挙がった DB（`infra/supabase`）に関する指摘のうち、**マイグレーションの追加が必要なもの**をまとめる。
今回の修正ではマイグレーションを追加していない（作業環境に Postgres が無く、適用と pgTAP で検証できないため）。
各項目に、確認した事実（ファイル:行）・影響・次のマイグレーションで適用する SQL / pgTAP・確認の方法を書く。
検査の方法と全体の結果は [inspection-report.md](inspection-report.md)、スキーマの説明は [docs/handover/03-data-model.md](../handover/03-data-model.md)、
pgTAP の書き方は [infra/supabase/tests/README.md](../../infra/supabase/tests/README.md)。

| 項目 | 内容                                                                                                                                                       |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 対象 | コミット `64d4577` 時点の `infra/supabase/migrations/`（7 ファイル）と `infra/supabase/tests/database/`（14 ファイル）、関連する `apps/api` / `apps/web` のクエリ |
| 方法 | マイグレーション・pgTAP・アプリのコードの読み合わせ（静的）。DB での再現は、次のマイグレーションの適用時に各項目の「確認」の手順で行う                          |
| 判定 | 指摘ごとに「確認」（事実どおり）か「一部反証」（前提の一部が成り立たない。成り立つ部分だけを書く）。成り立たない部分は末尾の [反証](#4-反証) にまとめる          |

## 1. 一覧

| ID  | 重大度 | 項目                                                                 | 判定                                                                                   |
| --- | ------ | -------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| S5  | medium | RLS のポリシーが `profiles.deleted_at`（退会・利用停止）を見ない        | 一部反証（他人の行は読めない。退会・利用停止した **本人** のクライアントからのアクセスが続く） |
| S6  | medium | テーブル単位の GRANT が後から足した列を自動で公開する                   | 確認                                                                                   |
| C1  | low    | pgTAP にユーザー削除の cascade の網羅が無い                             | 確認                                                                                   |
| C2  | low    | pgTAP に SECURITY DEFINER 関数の `search_path` の検査が無い             | 確認（現在の関数はすべて固定済み。検査だけが無い）                                       |
| C3  | low    | マイグレーションが要約（summary）の `updated_at` を書き換えた           | 確認（影響は表示のみ。本番の公開前で実データへの影響は無い）                             |
| L1  | low    | `memories` が Realtime の publication に入っている                      | 確認                                                                                   |
| L2  | low    | 自発メッセージの走査が `conversations` を全件読む                       | 確認                                                                                   |

適用の順序の目安: S6 → S5（ポリシーの書き換え。00 の期待値を同時に更新）→ C1・C2（pgTAP の追加。マイグレーションは不要）→ L2 → L1 → C3（データの修正。任意）。

## 2. セキュリティ（S5 / S6）

### S5. RLS のポリシーが `profiles.deleted_at` を見ない

**事実**

- 論理削除の列を持つのは `profiles.deleted_at` だけ（`20260925000000_init.sql:27`）。他のテーブルに論理削除は無い。
- `to authenticated` のポリシー（`init.sql:497-592`、`20260926000000_character_engine.sql:356-375`）はすべて `auth.uid()` との一致か公開条件だけを見て、
  `profiles.deleted_at is null` を条件にしない。RPC `list_dm_threads` / `mark_conversation_read`（`init.sql:409-470`、security invoker）も同じ。
- API は `profiles.deleted_at` を見て 403 `account_deleted` を返し（`apps/api/app/core/security.py:280-292`）、Web も画面を開くたびに確認して
  `/login?error=withdrawn` に送る（`apps/web/lib/auth/withdrawn.ts`、`middleware.ts`）。しかし supabase-js（PostgREST / Realtime）は Auth のトークンだけで通り、
  Supabase Auth は `profiles.deleted_at` を知らないのでトークンの更新（refresh）も止まらない。
- 運用手順は利用停止（ban）を即時に効かせる手段としても `profiles.deleted_at` を使う（[06-operations.md](../handover/06-operations.md) の「ユーザーの利用停止」）。

**影響**: 退会した本人、または `deleted_at` で即時停止したアカウントが、画面を通さずに anon key と自分のセッションで、自分の会話・メッセージ・記憶・約束・設定を読み続けられ、
いいねの作成・削除、自分のコメントの削除、`display_name` の更新もできる。他人の行は従来どおり読めない（A13 は保たれる）。

**次のマイグレーション**（案: `20260928000000_withdrawn_rls.sql`）

```sql
-- 退会・利用停止（profiles.deleted_at）したアカウントのクライアントからのアクセスを RLS でも止める。
-- security invoker: 本人の profiles の行はポリシーで読めるので definer は不要（00_privileges の definer 関数の一覧も変えない）
create or replace function public.is_active_user()
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
  select exists (
    select 1 from public.profiles p
     where p.id = auth.uid() and p.deleted_at is null
  );
$$;
revoke all on function public.is_active_user() from public, anon;
grant execute on function public.is_active_user() to authenticated;

-- profiles の select は残す（Web が「退会済み」の判定に使う）。更新は退会前の行だけ（退会の操作自体 null → 値 は通る）
alter policy "profiles: 本人のみ更新" on public.profiles
  using (id = (select auth.uid()) and deleted_at is null);

alter policy "conversations: 本人のみ参照" on public.conversations
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "conversations: 本人のみ既読更新" on public.conversations
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "messages: 本人の会話のみ参照" on public.messages
  using ((select public.is_active_user()) and exists (
    select 1 from public.conversations c
     where c.id = messages.conversation_id and c.user_id = (select auth.uid())));
alter policy "memories: 本人のみ参照" on public.memories
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "likes: 本人のみ参照" on public.likes
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "likes: 本人のみ作成" on public.likes
  with check (user_id = (select auth.uid()) and (select public.is_active_user())
    and exists (select 1 from public.posts p where p.id = likes.post_id));
alter policy "likes: 本人のみ削除" on public.likes
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "comments: 本人のコメントのみ削除" on public.comments
  using (author_type = 'user' and author_user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "promises: 本人のみ参照" on public.promises
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
alter policy "proactive_settings: 本人のみ参照" on public.proactive_settings
  using (user_id = (select auth.uid()) and (select public.is_active_user()));
-- 公開コンテンツ（characters / posts / comments の select、character_states）も止めるなら、同じく and (select public.is_active_user()) を足す
```

**pgTAP**（案: `14_withdrawn_access.test.sql`。フィクスチャは 05 と同じ固定 UUID）

```sql
update public.profiles set deleted_at = now() where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
set local role authenticated;
set local request.jwt.claims to '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}';
select is_empty($$ select 1 from public.conversations $$, '退会した A は自分の会話を読めない');
select is_empty($$ select 1 from public.messages $$, '退会した A は自分のメッセージを読めない');
select is_empty($$ select id from public.memories $$, '退会した A は自分の記憶を読めない');
select is_empty($$ select * from public.list_dm_threads() $$, '退会した A の list_dm_threads は 0 件');
select throws_ok(
  $$ insert into public.likes (user_id, post_id) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '<公開投稿の id>') $$,
  '42501', 'new row violates row-level security policy for table "likes"', '退会した A はいいねできない');
select results_eq($$ select deleted_at is not null from public.profiles $$, array[true], 'A は自分の profiles（退会済み）は読める（画面の判定用）');
reset role;
-- B（退会していない）は従来どおり読める・書けることも確認する
```

**確認**: `scripts/test-db.sh`（`00_privileges.test.sql` の「authenticated が実行できる public 関数」の期待値に `is_active_user` を追加）。
手動: Web で退会したあと、`curl "$SUPABASE_URL/rest/v1/conversations?select=id" -H "apikey: $ANON_KEY" -H "Authorization: Bearer <そのセッションのアクセストークン>"` が `[]` を返す。
代替案: 退会・停止時に `auth.users.banned_until` も設定してトークンの更新を止める（Auth 側で完結するが、クライアントの `profiles` の UPDATE から `auth.users` を触るには
security definer のトリガーが要り、影響範囲が広い）。RLS の修正を主とし、こちらは運用手順（ban）の補足に留める。

### S6. テーブル単位の GRANT が後から足した列を自動で公開する

**事実**

- テーブル単位の grant: `grant select on public.profiles`（`init.sql:494`）、`posts`（`:509`）、`select, insert, delete on likes`（`:522`）、`select, delete on comments`（`:539`）、
  `conversations`（`:554`）、`messages`（`:567`）。列単位なのは `characters`（`:503`）・`memories`（`:589`）・`character_states` / `promises` / `proactive_settings`
  （`character_engine.sql:354, 361, 370`）。
- 後から足した列はそのまま authenticated から読める: `conversations.summary_cursor`（`init.sql:127`）・`analyzed_until`（`character_engine.sql:29`）はエンジン内部の
  処理位置でクライアントに不要。`messages.is_proactive`（`:20`）・`safety_triggered`（`20260926140000_safety_flag.sql:16`）は意図して公開。
  `posts.source_event_id`（`character_engine.sql:124`）は非公開テーブル `character_events` への参照（単体では無害）。
- `00_privileges.test.sql:83-95` はテーブル単位の権限の **有無** だけを固定し、列の追加では失敗しない。列単位のテーブルは `:98-135` で列名まで固定される。

**影響**: 将来 `profiles` に連絡先や `posts` に原価などを足すと、pgTAP も CI も何も言わずに authenticated から読める（PostgREST は `select=` で任意の列を指定できる）。
Web は `select('*')` を使っておらず、`POST_SELECT` / `MESSAGE_COLUMNS` / `COMMENT_SELECT` などで列を明示している（`apps/web/lib/queries/*.ts`）ので、列単位に切り替えても画面は動く。
`public` にビューを置くことは `00_privileges.test.sql:29-36` が禁じているので、ビューではなく列単位の grant で対応する。

**次のマイグレーション**（案: `20260928000100_column_grants.sql`。SELECT だけを置き換え、INSERT / DELETE / UPDATE の権限は変えない）

```sql
revoke select on public.profiles from authenticated;
grant select (id, display_name, created_at, deleted_at) on public.profiles to authenticated;

revoke select on public.posts from authenticated;
grant select (id, character_id, image_url, caption, is_paid, price_tokens, like_count, comment_count, published_at, created_at)
  on public.posts to authenticated;                      -- source_event_id は非公開にする

revoke select on public.likes from authenticated;
grant select (user_id, post_id, created_at) on public.likes to authenticated;

revoke select on public.comments from authenticated;
grant select (id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body, created_at)
  on public.comments to authenticated;

revoke select on public.conversations from authenticated;
grant select (id, user_id, character_id, last_message_at, user_last_read_at, created_at)
  on public.conversations to authenticated;              -- summary_cursor / analyzed_until は非公開にする

revoke select on public.messages from authenticated;
grant select (id, conversation_id, sender_type, body, created_at, is_proactive, safety_triggered)
  on public.messages to authenticated;
```

**pgTAP**（`00_privileges.test.sql` の更新）: テーブル単位の期待値を `('likes','INSERT'), ('likes','DELETE'), ('comments','DELETE')` だけにし、
列単位の期待値に上の 6 テーブルの列を足す。さらに次を追加する。

```sql
select is_empty(
  $$ select c.relname
       from pg_class c
       cross join lateral aclexplode(c.relacl) a
      where c.relnamespace = 'public'::regnamespace
        and a.grantee = 'authenticated'::regrole
        and a.privilege_type = 'SELECT' $$,
  'authenticated にテーブル単位の SELECT は無い（列を足しても自動で公開されない）'
);
```

**確認**: `scripts/test-db.sh`、Web の E2E（`pnpm --filter @everkano/web e2e`。列の指定が grant の範囲に収まっていること）、
`supabase-js` の `insert` は `return=minimal`（`.select()` を付けていない）なので `likes` の INSERT はそのまま動く。Realtime は列単位でも配信される（`memories` が既に列単位）。

## 3. テストの網羅・性能（C1 / C2 / C3 / L1 / L2）

### C1. pgTAP にユーザー削除の cascade の網羅が無い

**事実**: `07_triggers.test.sql:222-238` は `auth.users` → `profiles` → `conversations` / `messages` / `memories` だけを確認する。`profiles(id)` を参照する外部キーは 12 本:
`likes.user_id`（`init.sql:83`, cascade）、`comments.author_user_id`（`:98`, set null。check 制約と矛盾し、ユーザーのコメントがあると削除が失敗する。
[ADR-0004](../adr/0004-schema-changes-from-spec.md)、[06-operations.md](../handover/06-operations.md) の「ユーザーの物理削除」）、`conversations.user_id`（`:122`）、
`memories.user_id`（`:149`）、`memory_tombstones.user_id`（`character_engine.sql:71`）、`character_events.user_id`（`:97`, null 可）、`promises.user_id`（`:146`）、
`character_memories.user_id`（`:178`, null 可）、`affinity_states.user_id`（`:198`）、`affinity_history.user_id`（`:227`）、`proactive_messages.user_id`（`:249`）、
`proactive_settings.user_id`（`:267`）。`messages` / `conversations` 経由の set null（`memories.source_message_id` / `source_conversation_id`、`promises.source_message_id`、
`character_memories.source_message_id`、`proactive_messages.message_id`）と cascade（`proactive_messages.conversation_id`）も未確認。`09_foreign_key_indexes` は索引だけを見る。

**影響**: 将来のテーブルで `on delete` を書き忘れたり、`comments` のように制約と矛盾しても、ユーザーの物理削除の運用時に初めて分かる。

**pgTAP**（案: `14_user_delete_cascade.test.sql`、または 07 の末尾に追加）

```sql
-- フィクスチャ: A の行を likes / memory_tombstones / character_events(visibility='user') / promises / character_memories /
--   affinity_states / affinity_history / proactive_messages / proactive_settings と、A のコメント 1 件に作る（postgres ロール）
select throws_ok(
  $$ delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' $$,
  '23514', null, 'ユーザーのコメントが残っていると auth.users の削除は check 制約違反で失敗する（先にコメントを消す運用）');
delete from public.comments where author_user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
select is((select count(*)::int from public.likes where user_id = 'aaaaaaaa-...'), 0, 'likes が cascade で消える');
-- memory_tombstones / character_events / promises / character_memories / affinity_states / affinity_history /
-- proactive_messages / proactive_settings も同様に 0 件、B の行は残っていることを確認する
select is_empty(
  $$ select conrelid::regclass, conname
       from pg_constraint
      where contype = 'f' and confrelid = 'public.profiles'::regclass
        and confdeltype not in ('c', 'n') $$,
  'profiles を参照する外部キーはすべて on delete cascade / set null（新しいテーブルの書き忘れを検出）');
```

**確認**: `scripts/test-db.sh infra/supabase/tests/database/14_user_delete_cascade.test.sql`。マイグレーションの追加は不要。

### C2. pgTAP に SECURITY DEFINER 関数の `search_path` の検査が無い

**事実**: `00_privileges.test.sql:157-166` は security definer の関数を 5 つ（`handle_new_user` / `sync_post_like_count` / `sync_post_comment_count` / `audit_comment_delete` /
`sync_conversation_last_message_at`）に固定するだけで、`proconfig`（`set search_path = ''`）は検査しない。現在のマイグレーションの関数 13 個
（上の 5 つと `discard_unverified_password` / `ignore_password_update` / `guard_profile_withdrawal` / `touch_updated_at` / `list_dm_threads` / `mark_conversation_read`、
`20260926100000_memory.sql` の `touch_memory_updated_at` / `touch_promise_updated_at`）はすべて `set search_path = ''` を付けている。

**影響**: `create or replace function` で書き直したときに `set search_path` が落ちても pgTAP は通る（Supabase の linter は `function_search_path_mutable` として警告するが CI には無い）。
definer 関数は postgres の権限で動くので、`search_path` が可変だと呼び出し側のスキーマの同名オブジェクトに乗っ取られ得る。

**pgTAP**（`00_privileges.test.sql` に追加。`set search_path = ''` は `proconfig` に `search_path=""` として保存される）

```sql
select is_empty(
  $$ select p.proname
       from pg_proc p
      where p.pronamespace = 'public'::regnamespace
        and not ('search_path=""' = any(coalesce(p.proconfig, '{}'::text[]))) $$,
  'public の関数はすべて set search_path = '''' を持つ（security definer のトリガー関数は特に必須）'
);
```

**確認**: 先に `select proname, prosecdef, proconfig from pg_proc where pronamespace = 'public'::regnamespace;` で保存形式を見て（`{search_path=""}`）、
`scripts/test-db.sh infra/supabase/tests/database/00_privileges.test.sql`。マイグレーションの追加は不要。

### C3. マイグレーションが要約（summary）の `updated_at` を書き換えた

**事実**: `20260926000000_character_engine.sql:53` の `update public.memories set kind = 'summary' where 'summary' = any(tags);` は、当時のトリガー
`memories_touch_updated_at` → `touch_updated_at()`（`init.sql:392-406`。条件なしで `updated_at = now()`）が有効なまま実行された（内容で判定する
`touch_memory_updated_at()` への差し替えは後の `20260926100000_memory.sql:60-63`）。その結果、既存の要約の `updated_at` がすべてマイグレーションの適用時刻になった。

**影響**: `updated_at` はメモリパネルに「内容が変わった日時」として出る（`MemoryDTO.updated_at`）ので、既存の要約の表示だけが変わる。
容量超過の入れ替え（`apps/api/app/engine/memory/capacity.py:45`、`updated_at asc`）は要約を対象にしない（`kind <> 'summary'`）、
検索の並び（`store.py:42`）は `created_at` / `last_referenced_at` を使うため、動作には影響しない。本番は公開前で、影響があるのは開発・評価用の DB だけ。

**次のマイグレーション（任意）と運用の規則**

```sql
-- 1) 影響した行を見つける（1 つの時刻に集中していればそれが適用時刻）
select updated_at, count(*) from public.memories where kind = 'summary' group by 1 order by 2 desc limit 5;
-- 2) 元の値は失われているので created_at で代用する（要約はユーザーが編集できないので is_user_edited は常に false）。
--    新しいトリガーはアプリが明示した updated_at を尊重する（20260926100000_memory.sql:18-21）ので、そのまま UPDATE できる
update public.memories set updated_at = created_at
 where kind = 'summary' and status = 'active' and not is_user_edited and updated_at = '<1) の時刻>';
```

今後、`memories` の内容の列（`content` / `importance` / `tags` / `kind` / `status` …）をマイグレーションで一括更新するときは、
`alter table public.memories disable trigger memories_touch_updated_at;` … `enable trigger` で囲む（新しいトリガーも `kind` の変更で `updated_at` を進めるため）。

**確認**: 1) の SQL で時刻の集中が消えていること。`10_engine_memory.test.sql` の `updated_at` のトリガーの検査は変更不要。

### L1. `memories` が Realtime の publication に入っている

**事実**: `character_engine.sql:385-393` で `memories` を `supabase_realtime` に追加（anon に `id` の SELECT）。Web は「〇〇があなたのことを覚えました」の表示のために
`memories` の INSERT を `character_id` で絞って購読する（`apps/web/lib/queries/memories.ts:622-629`）。UPDATE / DELETE の購読はどのテーブルにも無い
（`messages` は INSERT、`comments` は INSERT / DELETE）。

**影響**

- `memories` への全変更が WAL 経由で Realtime に届き、購読ごとに `realtime.apply_rls` が評価される。INSERT のほか、返答のたびに使った記憶ごとの
  `last_referenced_at` / `reference_count` の UPDATE（`apps/api/app/engine/memory/service.py:787`）、置き換え（supersede）の UPDATE、容量超過の DELETE が流れる。
- 行には `embedding`（1536 次元。テキストで数十 KB）が含まれ、Realtime のデコードとメモリを使う（配信の JSON には列権限で含まれない）。
- Realtime は 1 本の論理レプリケーションで publication の変更を順に処理するため、`memories` の UPDATE の山が DM の `messages` の配信を遅らせ得る。

**次のマイグレーション**

1. 即効（購読の無い UPDATE を publication から外す。3 テーブルすべてに効く）:

   ```sql
   alter publication supabase_realtime set (publish = 'insert, delete');   -- delete は comments の削除の同期に必要
   ```

2. 本命（`memories` を外し、「覚えました」は軽い通知テーブルで配る。`apps/api` の post_turn で 1 行 insert、Web の購読先を変える、古い行は `jobs.cleanup` で消す）:

   ```sql
   alter publication supabase_realtime drop table public.memories;
   revoke select (id) on public.memories from anon;
   create table public.memory_notices (
     id bigserial primary key,
     user_id uuid not null references public.profiles(id) on delete cascade,
     character_id uuid not null references public.characters(id) on delete cascade,
     memory_id uuid not null references public.memories(id) on delete cascade,
     created_at timestamptz not null default now()
   );
   create index memory_notices_user_created_idx on public.memory_notices (user_id, created_at desc);
   create index memory_notices_character_id_idx on public.memory_notices (character_id);
   create index memory_notices_memory_id_idx on public.memory_notices (memory_id);
   alter table public.memory_notices enable row level security;
   revoke all on public.memory_notices from anon, authenticated;
   revoke all on sequence public.memory_notices_id_seq from anon, authenticated;
   grant select (id, user_id, character_id, memory_id, created_at) on public.memory_notices to authenticated;
   grant select (id) on public.memory_notices to anon;   -- ADR-0016: publication のテーブルは anon に主キー列だけ
   create policy "memory_notices: 本人のみ参照" on public.memory_notices
     for select to authenticated using (user_id = (select auth.uid()));
   alter publication supabase_realtime add table public.memory_notices;
   ```

3. 代替: PostgreSQL 15 以降の publication の列リスト・行フィルタ（`alter publication supabase_realtime add table public.memories (id, user_id, character_id, kind, created_at)
   where (status = 'active')`）。Supabase Realtime が列リストを尊重するかはローカルで要検証のため、2 を優先する。

**確認**: `select pubname, pubinsert, pubupdate, pubdelete from pg_publication where pubname = 'supabase_realtime';`、
`select * from pg_publication_tables where pubname = 'supabase_realtime';`、`00_privileges.test.sql` の anon の列権限の期待値（`memories.id` → `memory_notices.id`）と
`09_foreign_key_indexes` の更新、E2E `memory.spec.ts`（「覚えました」のトースト）。

### L2. 自発メッセージの走査が `conversations` を全件読む

**事実**: `apps/api/app/engine/proactive/service.py:90-124` の `_PAIRS_SQL` は `from public.conversations c` に絞り込みが無く（`$3` のユーザー指定は評価ハーネス用で本番は null）、
全会話に `characters` / `profiles` / `affinity_states` / `proactive_settings` × 2 を結合し、会話ごとに `messages` への lateral 参照を 2 回（`messages_conversation_id_created_at_idx`
で 1 回ずつは軽い）行ってから、`lu.created_at > $1 - dormant_days`（30 日。`config.py:83`）で捨てる。実行は 10 分ごと（`ENGINE_PROACTIVE_SCAN_INTERVAL_SECONDS=600`）。

**影響**: 走査の時間が休眠中の会話を含む全会話数に比例する（会話 10 万件なら 10 分ごとに 10 万 × 2 回の索引参照と結合）。

**次のマイグレーション**（案: `20260928000200_conversations_last_message_at_idx.sql`）と対になる `apps/api` の変更

```sql
-- conversations.last_message_at は messages の INSERT トリガー（init.sql:374-390）で更新される。
-- 既存の conversations_user_id_last_message_at_idx は (user_id, last_message_at) で、last_message_at 単独の範囲には使えない
create index if not exists conversations_last_message_at_idx on public.conversations (last_message_at desc);
```

`_PAIRS_SQL` の `where` に `and c.last_message_at > $1 - make_interval(days => $2)` を足す（最後のユーザー発言 ≤ `last_message_at` なので、
正確な条件 `lu` の上位集合で安全）。マイグレーションはトランザクション内で走るため `create index concurrently` は使えない。本番のテーブルが大きければ
手で `concurrently` で作ってから適用する（`if not exists` で二重には作らない）。

**確認**: `explain (analyze, buffers)` で `_PAIRS_SQL` が `Index Scan using conversations_last_message_at_idx` になること、
`uv run pytest tests/engine/proactive -q`（`test_scan_integration.py`）、`12_engine_proactive_jobs.test.sql`。

## 4. 反証

- **S5「論理削除された行が他のユーザーから読める」**: 成り立たない。論理削除の列は `profiles.deleted_at` だけで、退会したユーザーの `profiles` の行を読めるのは本人だけ
  （`init.sql:497-499`）。退会したユーザーのコメントが公開投稿に残るのは仕様（[ADR-0004](../adr/0004-schema-changes-from-spec.md)）。問題は「退会・停止した本人のアクセスが続く」ことに限られる（上の S5）。
- **C3「動作に影響する」**: 表示以外には影響しない（入れ替えは要約を対象にしない、検索は `updated_at` を使わない）。

## 5. 補足（DB 以外。今回は変更していない）

- `packages/shared/src/domain.ts:28` の `PUBLIC_MEMORY_COLUMNS`（10 列）は、DB の grant（`embedding` 以外の 17 列。`00_privileges.test.sql:104-113`）と一致せず、
  `apps/web` からも参照されていない（メモリパネルは `GET /memories` を使う。[ADR-0002](../adr/0002-data-access-split.md)）。`05_dm_isolation.test.sql:130` のメッセージだけが名前を挙げる。
  削除するか、grant と同じ列に揃える（コードの変更のため今回の範囲外）。
