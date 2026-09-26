-- =============================================================================
-- 好感度（Affinity Engine, 仕様 §6）の状態の追加列
--   20260926000000_character_engine.sql の affinity_states に、段階のヒステリシス（A7）・減衰（A6）・
--   久しぶりの会話（A6 の「寂しかった」）の判定に必要な列を足す。
--
--   * E1: 課金・有料投稿のテーブルへの外部キー・結合は引き続き持たない（このファイルも追加しない）。
--   * クライアントへの grant は無し（A11: 好感度の数値・段階はユーザーに見せない）。
--   * 時刻はアプリの時計（Clock）から明示的に書く（評価ハーネスの時間の早送り）。
-- =============================================================================

alter table public.affinity_states
  -- 段階の昇格候補（stage_candidate）の条件を満たし始めてから数えたユーザーの発言数（A7: N ターン以上）
  add column stage_candidate_turns int not null default 0 check (stage_candidate_turns >= 0),
  -- 緊張（気まずさ + 不満）が閾値以上になった日時。7 日以上続いたときだけ 1 段階下げる（A7）
  add column tension_high_since timestamptz,
  -- 評価したユーザーの発言数の累計（分析・評価ハーネス用）
  add column user_turns int not null default 0 check (user_turns >= 0),
  -- 緊張の軸を最後に減衰させた日時（A6。apply_daily_maintenance が経過時間に応じて減衰させる）
  add column last_decayed_at timestamptz,
  -- 久しぶりの会話: 前回から何日空いて戻ってきたか・戻ってきた日時（「寂しかった」の指針を少しの間だけ出す）
  add column absence_days smallint check (absence_days is null or absence_days >= 0),
  add column absence_return_at timestamptz;

comment on column public.affinity_states.stage_candidate_turns is '昇格候補の条件を満たし始めてからのユーザーの発言数（ヒステリシス A7）。';
comment on column public.affinity_states.tension_high_since is '緊張（気まずさ + 不満）が高い状態が始まった日時。7 日続いたら 1 段階だけ下げる（A7）。';
comment on column public.affinity_states.user_turns is '好感度の評価に使ったユーザーの発言数の累計。';
comment on column public.affinity_states.last_decayed_at is '緊張の軸（気まずさ・不満）と独占欲を最後に減衰させた日時（A6）。';
comment on column public.affinity_states.absence_days is '直近の「久しぶりの会話」で、前回の会話から空いた日数（A6: 罰ではなく「寂しかった」の反応に変える）。';
comment on column public.affinity_states.absence_return_at is '直近の「久しぶりの会話」が始まった日時。';

-- 日次処理（apply_daily_maintenance）の対象: 緊張・独占欲・昇格候補・緊張の継続のいずれかがある行だけ
create index affinity_states_maintenance_idx on public.affinity_states (user_id, character_id)
  where awkwardness > 0 or discontent > 0 or possessiveness > 0
     or stage_candidate is not null or tension_high_since is not null;
