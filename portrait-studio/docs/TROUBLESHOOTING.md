# トラブルシューティング

| 症状 | 原因 / 対処 |
| --- | --- |
| `UserWarning: ... sm_120 is not compatible with the current PyTorch installation` | cu124 以前の PyTorch。`.env` の `PYTORCH_IMAGE` を cuda12.8 タグ（torch ≥ 2.7）にして `docker compose build comfyui`。`scripts/verify_env.sh` の項目 2 で検出 |
| `Torch not compiled with CUDA` / `torch.cuda.is_available()` が False | Windows 側の NVIDIA ドライバが古い、`wsl --update` 未実施、Docker Desktop の WSL integration が無効、`docker run --gpus all` が通らない |
| `docker compose up` で `could not select device driver "nvidia"` | Docker Desktop の GPU サポート（WSL2 バックエンド）が無効。Docker Desktop を更新し、WSL integration を確認 |
| `docker compose build comfyui` で `error: externally-managed-environment`（`pip install` が拒否される） | ベースイメージの Debian が Python を PEP 668 で保護している。`docker/Dockerfile` に `PIP_BREAK_SYSTEM_PACKAGES=1` を設定済み（2026-09-30 に実機で発生・修正）。古いコミットの場合は `git pull` してから再ビルド |
| `error while interpolating services.api.environment.API_KEY` | `.env` の `API_KEY` が空のまま。`openssl rand -hex 24` の出力を実際に `.env` の `API_KEY=` に書き込む（出力を見るだけでは設定されない） |
| `docker compose build comfyui` で `groupadd: GID '1000' already exists` | ベースイメージに既に uid/gid 1000 のアカウントがある。`docker/setup_comfy_user.sh` がそれを comfy へ改名する方式に修正済み（2026-09-30 に実機で発生・修正）。`git pull` してから再ビルド |
| API が起動しない: `api_key Field required` | `.env` に `API_KEY` が無い。設定漏れで無防備に公開される事故を防ぐため、意図的に起動を拒否する |
| API が起動しない: `必須の title がありません` | ワークフロー JSON のノードに `_meta.title` が無い / 名前が違う。`workflows/WORKFLOW_NOTES.md` の表に合わせる |
| `GET /api/health` が 503 | ComfyUI に接続できない。`docker compose logs comfyui`、`COMFY_URL`（コンテナ内は `http://comfyui:8188`） |
| `face_methods` が空 / 生成が 400「ワークフローが使えません」 | `GET /object_info` にノードクラスが無い（カスタムノードの requirements 導入失敗が多い）。`docker compose logs comfyui` で `import failed` を探す。`scripts/verify_env.sh` の項目 3〜4 |
| `Failed building wheel for insightface` | `build-essential` 不足。`docker/Dockerfile` は導入済み。手元の venv なら `apt install build-essential cmake` |
| `ModuleNotFoundError: cv2` / `libGL.so.1` | GUI 版 opencv が入っている。API は `opencv-python-headless` のみ（`pyproject.toml` の override）。ComfyUI 側は `libgl1` を導入済み |
| `CUDA out of memory` | batch > 1 / 解像度過大 / VRAM 断片化。batch は常に 1（API が強制）。832×1216 に戻す、upscale 1.0、face_detailer OFF、`COMFY_EXTRA_ARGS=--lowvram`。設定画面の「VRAM を解放」（`/free`）。ワークスペースの失敗時ボタンで 1 クリック再試行 |
| InstantID だけ OOM | 12 GB でぎりぎり。`--lowvram` を試し、ダメなら PuLID（目安 9 GB）に切り替えるのが最短 |
| 生成が 400「未実測のため拒否」 | `api/app/data/vram_table.json` にその組み合わせの実測が無い。`scripts/measure_vram.py` で実測して追加（推測値は書かない）。一時的に許すなら `VRAM_ALLOW_UNMEASURED=true`（自己責任） |
| 顔が似ない（類似度が低い） | antelopev2 未配置 / 参照画像が低解像度・横顔・ぼけ / provider の設定ミス。**実写の正面・高解像度・素の表情** の参照を使う。ウィザード Step 2 の品質スコアと警告を確認 |
| `similarity: null`（顔検出不可） | 生成画像から顔が検出できない（全身の遠景、顔が小さい、後ろ向き）。クローズアップで検証する。値は「別人」ではなく「測れない」の意味 |
| FaceDetailer がエラー | Impact-Pack の更新で入力名が変わった。`GET /object_info/FaceDetailer` の `input.required` と JSON を合わせる（`GET /api/health` の `workflows[].issues` に `warn:`/`error:` が出る） |
| 生成が遅い | モデルが `/mnt/c`（NTFS）にある。`MODELS_DIR` を ext4 側へ（`scripts/install_models.sh` は `/mnt/*` を拒否する） |
| 再エクスポート後に API が壊れる | ノード ID が変わっても title で参照するので壊れない。壊れたなら title が消えている。`_meta.title` を付け直す |
| フロントの型がバックとずれる | `cd api && uv run python scripts/export_openapi.py && cd ../web && pnpm api:types` を忘れている |
| `shm` 不足で insightface が落ちる | `docker-compose.yml` の `shm_size: "8gb"` を確認 |
| 画像一覧が重い | 一覧はサムネイル（`/thumb`, WebP 384px）+ 仮想スクロール。原寸（`/file`）を並べない |
| ブラウザで画像が 401 | Cookie `psk` が無い。設定画面で API キーを保存し直す（保存時に Cookie も更新される） |
| 前回のジョブが `error: サーバーの再起動により中断されました` | 直列キューは再起動で復元しない（意図どおり）。再生成ボタンで同一パラメータで作り直す |
