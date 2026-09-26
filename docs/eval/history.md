# 評価の推移（回帰の記録・仕様 §9.3）

`python -m evals.run` が実行ごとにモード（素の LLM / エンジン）ごとの 1 行を追記する。mock の行は仕組みの確認、live の行が言語の質を含む。

- コストの列の数え方: `mock-30d` / `mock-90d`（チューニング前）は文字数 × 1.0 トークン（保守的な見積もり）。
  `-tuned` 以降は DeepSeek-V3 のトークナイザで数えた値（`--tokenizer`）。同じ数え方での比較は各結果の
  `sensitivity_tokens_per_char`（文字数 × 0.6 / 0.8 / 1.0）を見る（tuned の 30 日は 1.0 で ¥97.0、90 日は ¥103.1）。
- `-tuned` の 90 日は 5 シナリオ（会社員・途中で来なくなる・丁寧 / 失礼・操作・危機）。チューニング前の 90 日は 2 シナリオ。
- `-phase3-memory` / `-phase4-calendar` / `-phase5-affinity` は仕様 §11 のフェーズごとの結果。各機能は並行して開発したため、
  チューニング後のコードで機能のフラグを段階的に有効にして再現した（`--engine on --set engine_calendar_enabled=false` など。
  phase3 = 記憶のみ、phase4 = 記憶 + カレンダー、phase5 = + 好感度、フェーズ 6・7 = 全部 = `-tuned`）。コストはトークナイザで数えた値。

| 日付 | ラベル | LLM | 日数 | シナリオ | モード | 想起30 | 誤り | 約束 | 自己矛盾 | 予定 | 状態 | 好感度 | 操作 | E1 | E2 | E6 | 遅延(推計) | コスト(中央) | commit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-26 | mock-30d | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | baseline | 0.0% | 0.0% | 0.0% | 55.6% | — | 50.0% | — | — | 0 件 | 0 件 | 87.5% | 1526 ms | ¥66.2 | 31f2c33+ |
| 2026-09-26 | mock-30d | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | engine | 78.9% | 0.0% | 71.4% | 0.0% | 0 件 | 100.0% | 0 件 | 23.1% | 0 件 | 0 件 | 87.5% | 1732 ms | ¥154.7 | 31f2c33+ |
| 2026-09-26 | mock-90d | mock | 90 | office_worker,dropout | baseline | 0.0% | 0.0% | 0.0% | 46.2% | — | 25.9% | — | — | 0 件 | 0 件 | — | 1528 ms | ¥57.7 | 31f2c33+ |
| 2026-09-26 | mock-90d | mock | 90 | office_worker,dropout | engine | 80.0% | 0.0% | 90.0% | 0.0% | 0 件 | 100.0% | — | — | 0 件 | 0 件 | — | 1732 ms | ¥164.0 | 31f2c33+ |
| 2026-09-26 | mock-30d-tuned | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | baseline | 0.0% | 0.0% | 0.0% | 61.1% | — | 45.0% | — | — | 0 件 | 0 件 | 100.0% | 1573 ms | ¥39.5 | a687db3+ |
| 2026-09-26 | mock-30d-tuned | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | engine | 100.0% | 0.0% | 100.0% | 0.0% | 0 件 | 100.0% | 0 件 | 0.0% | 0 件 | 0 件 | 100.0% | 1747 ms | ¥72.2 | a687db3+ |
| 2026-09-26 | mock-90d-tuned | mock | 90 | office_worker,dropout,polite_rude,manipulator,crisis | baseline | 0.0% | 0.0% | 0.0% | 59.6% | — | 44.8% | — | — | 0 件 | 0 件 | 100.0% | 1559 ms | ¥37.8 | a687db3+ |
| 2026-09-26 | mock-90d-tuned | mock | 90 | office_worker,dropout,polite_rude,manipulator,crisis | engine | 100.0% | 0.0% | 100.0% | 0.0% | 0 件 | 100.0% | 0 件 | 0.0% | 0 件 | 0 件 | 100.0% | 1748 ms | ¥77.0 | a687db3+ |
| 2026-09-26 | mock-30d-phase3-memory | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | engine | 100.0% | 0.0% | 85.7% | 0.0% | 0 件 | 50.0% | — | — | 0 件 | 0 件 | 100.0% | 1741 ms | ¥52.8 | d9bfc2f+ |
| 2026-09-26 | mock-30d-phase4-calendar | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | engine | 100.0% | 0.0% | 85.7% | 0.0% | 0 件 | 100.0% | — | — | 0 件 | 0 件 | 100.0% | 1743 ms | ¥62.7 | d9bfc2f+ |
| 2026-09-26 | mock-30d-phase5-affinity | mock | 30 | office_worker,quiet_student,manipulator,dropout,polite_rude,crisis | engine | 100.0% | 0.0% | 85.7% | 0.0% | 0 件 | 100.0% | 0 件 | 0.0% | 0 件 | 0 件 | 100.0% | 1745 ms | ¥72.7 | f6b7fdb+ |
