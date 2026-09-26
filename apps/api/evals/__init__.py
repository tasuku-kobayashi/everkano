"""キャラクターエンジン v1.0 の長期評価ハーネス（仕様 §9 / docs/eval/README.md）。

`cd apps/api && uv run python -m evals.run --days 30` で、シミュレーションユーザーとキャラの会話を
ManualClock で早送りしながら（HTTP を通さずにアプリのサービスを直接動かす）、§9.2 の指標を計算する。

構成:
- `timeline.py`   シミュレーションの暦（1 日目 = 開始日。JST）と相対日付（来週の木曜など）の正解の計算
- `scenarios/`    6 種類のシミュレーションユーザー（台本・事実・約束・プローブ・不在）
- `world.py`      評価専用のユーザー・キャラ（シード済みキャラのペルソナの複製）の作成と後片付け
- `driver.py`     時計を進めながら定期実行・ジョブ・チャット（ストリーミング。最初の文字までの時間を計測）を動かす
- `meter.py`      LLM 呼び出しの計測（用途ごとのトークン・円換算）と費用の上限（--max-cost-jpy）
- `simuser.py`    シミュレーションユーザーの発言（mock: 台本 / live: LLM が言い換える）
- `judges.py`     判定（mock: 正規化したキーワードの照合 / live: LLM の判定。プロンプトは docs/eval/prompts/）
- `harness.py`    1 つのモード（エンジン有効 / 素の LLM）を最初から最後まで動かして記録する
- `metrics.py`    §9.2 の全指標（純粋関数）と合格ライン
- `e1.py` `cost.py` `report.py`  E1 の検査・コストの推計・結果の書き出し（docs/eval/results/・history.md）
"""
