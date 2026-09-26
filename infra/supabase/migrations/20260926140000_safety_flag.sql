-- =============================================================================
-- E6: 安全対応（相談窓口の案内）をしたキャラの返答の印
--   自傷・希死念慮のシグナルを検知したとき、チャットのパイプラインは LLM を使わずにキャラの声で気づかいの言葉と
--   相談窓口を返す。これまで「どの返答が安全対応だったか」は返答を受け取った端末だけが覚えていたため、履歴の
--   読み込み・別の端末・アプリの入れ直しでは相談窓口のカードが出なかった。返答の行に印を残し、どの端末でも
--   その返答の下に相談窓口のカードを出せるようにする（窓口の一覧は GET /safety/resources）。
--
--   * 書き込みは API（postgres ロール）だけ。クライアントは既存の messages の select 権限（本人の会話だけ・RLS）で読む
--     （列単位の grant は不要。anon は従来どおり id だけ）。
--   * Realtime（messages は supabase_realtime publication）の INSERT にも列が含まれる。
--   * 印を付けられるのはキャラの発言だけ（check 制約）。既存の行は false（安全対応の記録は audit_logs の safety.trigger）。
-- =============================================================================

alter table public.messages
  add column safety_triggered boolean not null default false,
  add constraint messages_safety_triggered_character_only
    check (not safety_triggered or sender_type = 'character');

comment on column public.messages.safety_triggered is
  'E6: 自傷・希死念慮のシグナルに安全対応（相談窓口の案内）をしたキャラの返答。true の返答の下に相談窓口のカードを出す。';
