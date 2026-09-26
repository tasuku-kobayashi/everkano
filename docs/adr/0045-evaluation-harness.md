# ADR-0045: 評価ハーネス（プロセス内の早送り・シミュレーションユーザー・mock / live・判定・費用の計測・素の LLM との比較・隔離）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: エンジン仕様書 §9（9.1〜9.3）・§11・§12-3・§13-2 / [ADR-0035](0035-character-engine-architecture.md)・[ADR-0036](0036-engine-job-queue-and-scheduler.md)・
  [ADR-0044](0044-check-scope-compliance-allowlist.md)・[ADR-0046](0046-engine-cost-and-latency.md) /
  実装: `apps/api/evals/`（`run.py`・`harness.py`・`driver.py`・`simuser.py`・`scenarios/`・`judges.py`・`judging.py`・`metrics.py`・`meter.py`・`cost.py`・`tokenizer.py`・`e1.py`・
  `cleanup.py`・`report.py`・`summarize.py`）, `apps/api/tests/evals/`, `apps/api/app/engine/pipeline_driver.py`（`EngineDriver`）, `docs/eval/`（README・`prompts/`・`results/`・`history.md`）

## コンテキスト

- エンジン仕様書 §9: 少ない回数の手動テストでは品質が分からない。LLM で動くシミュレーションユーザー、30 日・90 日の早送り、§9.2 の指標の自動評価、
  判定に LLM を使うならそのプロンプトを `docs/` に残す、変更のたびに再実行して推移を `docs/eval/` に残す（§9.3）。§11 はハーネスを先に作り、
  素の LLM（3 つの仕組みなし）の基準値と比べることを求める。
- この開発環境には LLM の API キーも外部への通信も無い（§13-2 の費用の上限も未回答。[ADR-0035](0035-character-engine-architecture.md) の前提）。
- 開発中は同じローカル DB を、API のテスト・E2E・開発サーバーと共有している。

## 決定

### 動かし方（§9.1 の時間の早送り）

- HTTP を通さず、`create_app(settings, llm=MeteredLLM(...), clock=ManualClock(開始日))` のサービスを **同じプロセスの中で** 直接動かす（常駐のワーカー・スケジューラは無効）。
  全シナリオの発言を時刻順に並べ、次の発言の時刻まで時計を刻み（`--step-minutes`、既定 10 分）ごとに進め、各時刻で `scheduler.run_due(now)` →
  `worker.run_until_idle(now)` を行う（`EngineDriver` と同じ動かし方）。
- チャットは `/chat/stream` と同じ経路（`ChatService.open_stream`）で送り、最初の `delta` / `replace` までの時間を計る（E8）。
- **素の LLM** は `ENGINE_MEMORY_ENABLED` / `ENGINE_CALENDAR_ENABLED` / `ENGINE_AFFINITY_ENABLED` / `ENGINE_PROACTIVE_ENABLED` をすべて false にして同じシナリオを流す
  （`--engine both / on / off`）。状態・自己矛盾の正解のために予定は生成するが、返答の文脈には入れない（素の LLM が知っているのはペルソナの `schedule_pattern` だけ）。

### シナリオと判定

- シミュレーションユーザー 6 シナリオ・7 人（会社員・無口な学生・操作しようとするユーザー・途中で来なくなるユーザー・丁寧 / 失礼の 2 人・危機的な発言）。
  mock では台本の候補から seed で決定的に選び、live では LLM（用途 `sim_user`）が人物像に合わせて言い換える。**評価の刺激（事実のプローブ・操作・危機の発言）は
  live でも言い換えない**（実行ごとに同じ刺激で比べるため）。約束の正解の期日はハーネスがエンジンとは独立に計算する。
- 判定: mock はルール（キーワードの照合）、live は LLM（用途 `eval_judge`）。判定・言い換えのプロンプトは `docs/eval/prompts/` が正本（ハーネスが実行時に読む）。
  LLM の判定が失敗したらルールに戻し、`fallback:` として記録する。
- 指標は §9.2 の全行（合格ラインは仕様の初期値のまま）。E1 は好感度の構造テスト（[ADR-0041](0041-affinity-engine.md)）の実行 + ハーネス独自の import / SQL の走査。
  定義の詳細は [docs/eval/README.md](../eval/README.md)。

### mock と live

- **mock（`LLM_MODE=mock`・`EMBEDDING_MODE=hash`）の数値は、エンジンの仕組み**（抽出・保存・検索・文脈への注入・期日の解決・予定・状態・上限・安全対応・ガード）を
  測るもので、実際のモデルの言語の質ではない。この環境で出せたのは mock の結果だけ。
- live（`--llm live`）はアプリの `OpenAICompatibleLLM` でエンジンの全用途を呼ぶ。**費用の上限 `--max-cost-jpy`（既定 ¥3,000）** を超えたらその時点で止め、
  途中までの結果を `status: aborted_cost_cap` で書き出す（以降のモードは実行しない）。

### 費用とレイテンシの計測

- `MeteredLLM`（`evals/meter.py`）がすべての LLM 呼び出しを用途ごとに記録する（入力・出力のトークン、キャッシュに当たる分、費用）。live は API の usage、mock は
  文字数 × 仮定（`--tokens-per-char`、既定 1.0 = 保守的な上限）か、本番のモデルのトークナイザ（`--tokenizer <tokenizer.json>`。DeepSeek-V3 の `tokenizer.json` は
  PyPI の `deepseek-tokenizer` 0.1.3（MIT）に同梱。`uv run --with tokenizers ...` で実行し、依存は増やさない）で数える。結果には 0.6 / 0.8 / 1.0 トークン/文字の
  仮定で数え直した月額も出す。キャッシュは usage に無ければ、同じ用途の直前のプロンプトとの共通の先頭から見積もる。価格は `Settings.price_table`
  （`ENGINE_PRICE_TABLE_JSON`。未設定なら DeepSeek V3 の既定）。
- 1 アクティブユーザーの月額（`evals/cost.py`）: 用途ごとの単価から、利用の多さ（軽い・中央・多い）ごとに 30 日分に換算する。フィードのキャプションは
  キャラごとの固定費として別に示す。評価ハーネス自身の用途（`sim_user` / `eval_judge`）は含めない。
- E8 は mock ではパイプラインの実測に、本番の通信とモデルの最初のトークンまでの時間の仮定（`--model-ttft-ms` など）を足した推計。live では実測。
  どちらも [ADR-0046](0046-engine-cost-and-latency.md) の方針で読む。

### 隔離と後片付け

- 実行ごとに専用のキャラ（シードの 10 体の複製）とユーザーを作り、終わったら消す（cascade で予定・会話・記憶・好感度・自発メッセージも消える。外部キーの無い
  `audit_logs`・`engine_jobs`・`engine_schedules` は ID・名前空間で消す）。`--keep` や中断で残ったものは `python -m evals.cleanup`。
- 定期実行の記録は名前空間 `eval-<run>`（`ENGINE_SCHEDULE_NAMESPACE`）、スケジューラとワーカーの対象は `EngineScope` と `dedupe_keys` で評価のデータだけに絞る。
  シミュレーションの期間は共有の DB の今と重ならない未来（既定 2030 年〜）。`jobs.cleanup` は対象を絞れないので実行しない（`evals/driver.py` も `EngineDriver` も既定で実行しない。
  2026-09-26 の調整で `EngineDriver` の既定を変えた。専用の DB のときだけ `run_job_cleanup=True`）。実行の前後で「期間内のこの実行のものではない行」の数を記録する。

### 結果の残し方（§9.3）

- 実行ごとに `docs/eval/results/<日付>-<ラベル>.json` / `.md`（設定・commit・仮定・全指標と内訳・用途別の使用量・コストの推計）と、`docs/eval/history.md` に 1 行を追記する。
  **プロンプト・パラメータを変えたら再実行して記録する**。数値は [docs/eval/README.md](../eval/README.md) と [history.md](../eval/history.md) を見る（ADR には書かない）。
- ハーネスのコードは本番のイメージに含まれない（`apps/api/Dockerfile` は `apps/api/app` だけをコピーする）。課金の誘い文を含む台本は check-scope の許可リストで
  許している（[ADR-0044](0044-check-scope-compliance-allowlist.md)）。

## 結果・トレードオフ

- 30 日分の会話・ジョブ・定期実行を数分で再現でき、素の LLM との差を同じシナリオで比べられる。mock は決定的なので、変更の前後の差が仕組みの差として読める。
- mock の数値は言語の質を表さない。**想起・自己矛盾・状態の反映・E2 の言い回しの本当の値は live で測る必要がある**（キーを用意したら `--llm live` で再実行する）。
- 共有の DB で動かすため、名前空間・対象の絞り込み・未来の日付で隔離しているが、`jobs.cleanup` のような全体の保守は検証できない（専用の DB のときだけ `run_job_cleanup=True`）。
- 費用の推計は仮定（トークン数・キャッシュ・利用の多さ）に依存する。仮定は結果のファイルに残す。

## 代替案

- **HTTP 越しに実時間で動かす（uvicorn + クライアント）**: 30 日を再現できない。ジョブ・定期実行の時刻を決定的に制御できない。
- **評価のたびに専用の DB を作る**: 隔離は確実だが、この環境ではマイグレーションとシードに時間がかかる。専用の DB での実行も可能にしてある。
- **既製の LLM 評価の仕組み（プロンプトの一問一答の評価ツールなど）**: 時間の早送り・ジョブ・定期実行・DB の状態を含む長期のシミュレーションができない。
- **mock でも LLM の判定を使う**: この環境では呼べない。mock ではルールの判定、live で LLM の判定（判定の経路は mock の LLM でオフラインに検査している）。
