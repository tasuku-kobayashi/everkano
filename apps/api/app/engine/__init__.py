"""キャラクターエンジン v1.0（記憶 × カレンダー × 好感度 × 自発メッセージ。ENGINE_BRIEF / 仕様 §3）。

配置:
- `types.py`             契約（時計・語彙・値オブジェクト・各モジュールの Protocol）
- `context_assembler.py` 返答の前に各モジュールから文脈を並行に集め、予算内に収める
- `pipeline.py`          DM の返答パイプライン（`/chat` と `/chat/stream` で共通。E6 → Gate #1 → 文脈 → LLM
                         ストリーミング → 出力検査 → 保存 → 返答後のジョブ登録）
- `pipeline_flush.py`    ストリーミングの文単位のフラッシュと出力検査 / `pipeline_sse.py` SSE への変換
- `pipeline_driver.py`   評価ハーネス・テスト用: ManualClock で時間を早送りしながらチャット・ジョブ・定期実行を動かす
- `jobs/`                Postgres のジョブキュー（engine_jobs）・ワーカー・返答後のジョブ（post_turn など）
- `scheduler.py`         定期実行（engine_schedules・pg_try_advisory_lock によるリーダー選出）
- `safety/`              E6 の安全対応（自傷・希死念慮の検出・相談窓口）と出力の追加検査（E2 / E3）
- `memory/` `calendar/` `affinity/` `proactive/`  各モジュール（ファサードは app/container.py で組み立てる）

このパッケージの import 時には何も読み込まない（循環 import を避けるため、各モジュールを直接 import する）。
"""
