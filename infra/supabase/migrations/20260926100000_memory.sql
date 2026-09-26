-- =============================================================================
-- Memory Engine（キャラクターエンジン v1.0 §4）の追加
--
-- memories / promises の updated_at:
--   * 参照の記録（last_referenced_at / reference_count）だけの更新では updated_at を変えない
--     （毎回の返答で使った記憶を記録するため。updated_at は「内容が変わった日時」としてメモリパネルに出す）。
--   * アプリが updated_at を明示的に指定した更新はその値を使う（評価ハーネスの時間の早送り: アプリの時計で書く）。
--     指定しなければ従来どおり now()。
--   共通の public.touch_updated_at() は他のテーブルが使っているので変えず、専用の関数に差し替える。
-- =============================================================================

create or replace function public.touch_memory_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.updated_at is distinct from old.updated_at then
    -- 呼び出し側（アプリの時計）が指定した
    return new;
  end if;
  if new.content is not distinct from old.content
     and new.importance is not distinct from old.importance
     and new.tags is not distinct from old.tags
     and new.kind is not distinct from old.kind
     and new.status is not distinct from old.status
     and new.superseded_by is not distinct from old.superseded_by
     and new.is_user_edited is not distinct from old.is_user_edited
     and new.source_message_id is not distinct from old.source_message_id then
    -- 参照の記録（last_referenced_at / reference_count）・埋め込みの再計算（reembed）など、内容に関わらない更新
    return new;
  end if;
  new.updated_at = now();
  return new;
end;
$$;
comment on function public.touch_memory_updated_at() is
  'memories の updated_at: 内容が変わったときだけ now()。アプリが指定した値は尊重する（時間の早送り）。';

create or replace function public.touch_promise_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.updated_at is distinct from old.updated_at then
    return new;
  end if;
  new.updated_at = now();
  return new;
end;
$$;
comment on function public.touch_promise_updated_at() is
  'promises の updated_at: アプリが指定した値は尊重し、指定が無ければ now()（時間の早送り）。';

-- トリガー関数はクライアントから直接実行させない（初期マイグレーションの方針と同じ）
revoke all on function public.touch_memory_updated_at() from public, anon, authenticated;
revoke all on function public.touch_promise_updated_at() from public, anon, authenticated;

drop trigger if exists memories_touch_updated_at on public.memories;
create trigger memories_touch_updated_at
  before update on public.memories
  for each row execute function public.touch_memory_updated_at();

drop trigger if exists promises_touch_updated_at on public.promises;
create trigger promises_touch_updated_at
  before update on public.promises
  for each row execute function public.touch_promise_updated_at();

-- 墓標の照合（E5）はペア内で本文のハッシュ一致を先に見る
create index memory_tombstones_pair_hash_idx on public.memory_tombstones (user_id, character_id, content_hash);
-- 上の索引が (user_id, character_id) を先頭に含むので、同じ列だけの索引は不要
drop index if exists public.memory_tombstones_pair_idx;
