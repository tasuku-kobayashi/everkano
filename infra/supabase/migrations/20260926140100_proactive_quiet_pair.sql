-- =============================================================================
-- 自発メッセージの「送らない時間帯」（E4 / P3）の保存値をそろえる
--   proactive_settings の全体設定（character_id = null）の quiet_start / quiet_end は、
--     - 両方 null  = サーバーの既定（ENGINE_PROACTIVE_QUIET_START / END。既定 0〜7 時）に従う
--     - 両方が値   = 利用者が決めた時間帯（start == end なら制限なし）
--   のどちらかにする。片方だけ null の行があると、API（GET は null を既定で補う）と DB の値が食い違い、
--   既定を変えたときに利用者の時間帯が意図せず変わる（統合で見つかった不具合）。
--   キャラ別の行（character_id あり）は時間帯を持たない（両方 null）。
--
--   * 既存の片方だけ null の行は、仕様の既定（0 時 / 7 時）で補ってから制約を付ける
--     （API の既定は ENGINE_PROACTIVE_QUIET_* と同じ値。既定を変えた環境でも、補うのはこの時点の行だけ）。
--   * クライアントの grant は変えない（select のみ。書き込みは API 経由で監査ログに残す）。
-- =============================================================================

update public.proactive_settings
   set quiet_start = coalesce(quiet_start, 0),
       quiet_end = coalesce(quiet_end, 7)
 where character_id is null
   and (quiet_start is null) <> (quiet_end is null);

update public.proactive_settings
   set quiet_start = null, quiet_end = null
 where character_id is not null
   and (quiet_start is not null or quiet_end is not null);

alter table public.proactive_settings
  add constraint proactive_settings_quiet_pair
    check ((quiet_start is null) = (quiet_end is null)),
  add constraint proactive_settings_quiet_global_only
    check (character_id is null or (quiet_start is null and quiet_end is null));

comment on column public.proactive_settings.quiet_start is
  '送らない時間帯の開始（JST の時）。quiet_end と両方 null = サーバーの既定（ENGINE_PROACTIVE_QUIET_START、既定 0 時）。全体設定の行だけ。';
comment on column public.proactive_settings.quiet_end is
  '送らない時間帯の終了（JST の時。この時刻から送ってよい）。quiet_start と両方 null = サーバーの既定（既定 7 時）。';
