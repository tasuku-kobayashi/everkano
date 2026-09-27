# 受け入れ基準の実行結果（portrait-studio）— 2026-09-27

**実行環境**: クラウドの Linux コンテナ（GPU 無し・Docker デーモン無し・Hugging Face / Civitai へ到達不可・PyPI / npm / GitHub リリース資産は到達可）。
GPU に依存する項目は **未実施** と明記する。ComfyUI は `api/tools/mock_comfy.py`（HTTP + WebSocket のモック。顔マーカー入りの合成画像を返す）、
顔分析は `FACE_ENGINE=mock`（マーカーから顔と埋め込みを決定的に読む）で代替した。**モックは GPU・実モデルの代わりにはならない**。実機での再実行手順は末尾。

生の出力: [raw/acceptance-api-mock.txt](raw/acceptance-api-mock.txt)（項目 3〜17 の curl）、[raw/api-quality-gates.txt](raw/api-quality-gates.txt)、
[raw/web-quality-gates.txt](raw/web-quality-gates.txt)、[raw/e2e-playwright.txt](raw/e2e-playwright.txt)、[raw/repo-checks.txt](raw/repo-checks.txt)、スクリーンショット: [../screenshots/](../screenshots/)。

## ゲート別

| ゲート | 結果 | 根拠 |
| --- | --- | --- |
| G0 環境検証 | **未実施（この環境では不可能）** | `nvidia-smi` 無し、`/dev/nvidia*` 無し、Docker デーモン無し。`scripts/verify_env.sh` を作成（cu128 / sm_120 / ノード登録 / ワークフロー / 認証を 1 コマンドで検証し生の出力を表示） |
| G1 ComfyUI 単体 | **未実施** | `docker/Dockerfile`（`ARG PYTORCH_IMAGE=pytorch/pytorch:2.11.0-cuda12.8-cudnn9-devel`、カスタムノード 5 種、onnxruntime CPU）と compose を作成。`docker compose config` は通る（`127.0.0.1` バインド・shm 8gb・GPU 予約）。イメージのビルドと `/object_info` の確認は実機で |
| G2 品質確立 | **未実施** | チェックポイント選定・目視評価・VRAM 実測は GPU が必要。`scripts/measure_vram.py`（実測 → `vram_table.json`）を作成。同梱テーブルは全行 null（未実測は拒否） |
| G3 顔一貫性 | **実装済み・実測は未実施** | PuLID / FaceID / InstantID のワークフロー（title ベース）と検証ジョブ（手法 × weight × 3 シーン、ArcFace 類似度）を実装。`scripts/face_similarity.py`（pair / calibrate）を作成。insightface 2.0 + antelopev2（実モデル）を CPU でロードし、顔なし画像で検出 0 を確認 |
| G4 バックエンド API | **完了（モック ComfyUI で検証）** | 34 パス。pytest 57 件、ruff、mypy strict、OpenAPI 出力 → `openapi-typescript` で型生成 |
| G5 フロント基盤 + 画面 1・2 | **完了** | Playwright: 初回起動 → 設定、空状態、ウィザード 5 ステップ完走。スクリーンショット 01, 02a〜02e |
| G6 画面 3・4・5 + 共通 UX 10 項目 | **完了** | Playwright: ワークスペース生成 → 結果 → 履歴、locked 警告、OOM 事前警告、ビューア、再生成の snapshot 一致、キャラ切替、ギャラリー（500 枚超の仮想スクロール・比較トレイ・ZIP）、設定（COMPLIANCE）、キーボード。スクリーンショット 03a〜06 |
| G7 総合受け入れ | **API 3〜17 と UI 18〜28 をモックで実施。1・2・15（目視）・生成時間は実機待ち** | 下表 |

## 受け入れ基準 1〜28

| # | 項目 | 結果 | 備考 |
| --- | --- | --- | --- |
| 1 | cu128 / sm_120（`torch.__version__`, `torch.version.cuda`, GPU 名） | **未実施** | GPU 無し。`scripts/verify_env.sh` 項目 2 が同じコマンドを実行して生の出力を表示する |
| 2 | ノードが実際に登録されているか（`/object_info`） | **未実施（実 ComfyUI）** | モックの `/object_info` はワークフローから生成したもので、実在の登録を証明しない。`verify_env.sh` 項目 3 |
| 3 | 疎通（torch/cuda と VRAM） | 実施（モック） | `GET /api/health` → `ok:true, vram_total_mb:12282, torch:"0.0.0+mock", cuda:null`（モック値であることが明示される） |
| 4 | 認証（キー無しで 401） | **合格** | `401` |
| 5 | 成人フラグ必須 | **合格** | `400`「adult_only: true が必須です」 |
| 6 | locked 保護 | **合格** | `400`「この操作はキャラの同一性を変えます（face_weight）…」 |
| 7 | 危険な解像度の拒否 | **合格** | 2048×2048 → `400`「VRAM 見積りが未実測のため拒否しました…」。テスト用テーブルで 1536×1536 → 「OOMの可能性が高いため拒否しました（推定ピーク 13500 MB / VRAM 総量 12282 MB）」（pytest） |
| 8 | 参照顔の品質分析 | **合格** | face_ratio / det_score / sharpness / yaw / pitch / roll / composite、類似度行列、クラスタ、recommended_index、理由文 |
| 9 | 顔が無い画像が弾かれる | **合格** | `face_count:0, usable:false, warnings:["no_face"]`、登録時は `400`「使用不可」 |
| 10 | 種顔のドラフト生成 | **合格** | `202` と `job_id` → done、4 枚 |
| 11 | 合成申告なしで登録不可 | **合格** | `400` |
| 12 | 同一性検証 9 枚 | **合格** | `202` → 9 枚、`by_weight` と `best_weight` |
| 13 | 生成 | **合格** | `202`、position 0 |
| 14 | ジョブの進捗 | **合格** | `status / progress{step,total,current,total_images} / result_image_ids / queue_position` |
| 15 | 別シーンでも同一人物 | **実機待ち（目視）** | API 経路は実施（beach seed 999 → 2 枚、類似度あり）。モックの類似度 1.0 は「同一性」の証明ではない |
| 16 | 類似度が計算されている | **合格** | `similarity:1.0, status:"computed", grade:"good"`。顔検出不可は `null` と理由（pytest） |
| 17 | 監査ログ | **合格** | `wc -l audit.jsonl` = 4 = 生成ジョブ数（draft 1 + verify 1 + generate 2） |
| 18 | 5 画面が表示される | **合格** | スクリーンショット 01 / 02a〜e / 03a〜e / 04a〜c / 05a〜c |
| 19 | ウィザード 5 ステップ | **合格** | Playwright で自動実行（アップロード 4 + 種顔 6 → 品質選定 → 検証 9 枚 → 登録 → 完了 → ワークスペース） |
| 20 | 生成 → 結果 → 履歴 | **合格** | 4 枚が結果と履歴に反映 |
| 21 | NSFW ブラー既定 ON | **合格** | `data-blurred="true"`、03a のスクリーンショット |
| 22 | VRAM メーター | **合格** | ヘッダに常時表示（`data-state="online"`）。値はモック |
| 23 | プルダウン + クイック選択でキャラ切替 | **合格** | 03e |
| 24 | locked 変更で警告 | **合格** | 03b「この操作はキャラクターの同一性を変えます」 |
| 25 | ワンクリック再生成が同一パラメータ | **合格** | `params_snapshot` の prompt / positive / seed / steps / cfg / sampler / 解像度 / 手法 / weight / checkpoint / scene_ids が一致（E2E と pytest） |
| 26 | 500 枚相当でも重くない | **合格（モックで実測）** | 509 枚に対して DOM に描画されたカードは 30 枚（react-window の仮想スクロール）。ホイール 10 回のスクロールで 691 ms。504 枚の生成（モック）に 140 秒（raw/e2e-playwright.txt の注記） |
| 27 | キーボードのみで主要操作 | **合格** | Tab でプロンプト・生成・キャラ選択に到達、← → / S / C / Esc |
| 28 | 設定画面から COMPLIANCE.md | **合格** | 05c |

## 目視の品質基準・生成時間

- 「フォトリアルな日本人として自然か」「2 シーンで同一人物か」は **実モデルでのみ判断できる**。この環境の画像はすべてモックの合成画像（マゼンタの矩形）であり、品質の証拠にならない。
- 生成時間: 実測不可。モックでの API 往復は 1 枚あたり約 0.5 秒（PNG 生成・サムネイル・顔分析・DB を含む）。

## 実機（RTX 5070）で再実行する手順

```bash
cd portrait-studio && cp .env.example .env    # API_KEY, MODELS_DIR, CIVITAI_* を設定
scripts/install_models.sh && docker compose up --build -d
scripts/verify_env.sh | tee docs/acceptance/raw/verify_env.txt                     # 項目 1, 2（生の出力）
cd api && uv run python ../scripts/measure_vram.py --checkpoint <file>              # VRAM 実測テーブル
API_URL=http://127.0.0.1:8000 API_KEY=<key> REF_A=<face1.png> REF_B=<face2.png> LANDSCAPE=<no-face.png> \
  bash ../scripts/acceptance.sh | tee ../docs/acceptance/raw/acceptance-api-real.txt   # 項目 3〜17
cd ../web && pnpm build && E2E_API_PORT=8000 pnpm e2e                                # 項目 18〜28（実 API に対して。start-api.sh は使わず reuseExistingServer）
```

`docs/MANUAL_QA.md` の B（目視）と生成時間を記録して完了。
