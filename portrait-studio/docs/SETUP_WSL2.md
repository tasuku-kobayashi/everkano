# セットアップ（Windows 11 + WSL2 + Docker Desktop + RTX 5070）

前提: Windows 11 Pro / WSL2（Ubuntu 22.04 or 24.04）/ Docker Desktop / NVIDIA GeForce RTX 5070（Blackwell, `sm_120`, 12 GB）。
**ComfyUI と API は `127.0.0.1` にしか公開しません。LAN や外部に公開しないでください**（ComfyUI の `/prompt` は無認証）。

## 1. Windows 側

1. NVIDIA ドライバを最新にする（Blackwell 対応の Game Ready / Studio ドライバ）。WSL2 にドライバは入れない（Windows 側の 1 つを共有する）。
2. `wsl --update` を実行し、`wsl --version` でカーネルを確認する。
3. Docker Desktop → Settings → Resources → WSL integration で使用するディストリビューションを有効にする。

## 2. WSL2（Ubuntu）側

```bash
nvidia-smi                         # GPU が見える（ドライバは Windows 側）
docker run --rm --gpus all pytorch/pytorch:2.11.0-cuda12.8-cudnn9-devel \
  python -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))"
# 期待: 2.11.0+cu128 12.8 NVIDIA GeForce RTX 5070   ← `sm_120 is not compatible` が出たら PyTorch のタグを見直す
```

`sm_120 is not compatible` が出る場合は cu124 以前の PyTorch です。`.env` の `PYTORCH_IMAGE` を **cuda12.8 タグ（torch 2.7 以上）** に変えてください。

## 3. リポジトリと .env

```bash
git clone <このリポジトリ> && cd everkano/portrait-studio
cp .env.example .env
# 必須: API_KEY（十分に長いランダム文字列）。MODELS_DIR / DATA_DIR は ext4 側（例 /home/<you>/portrait-models）。/mnt/c は使わない
openssl rand -hex 24               # API_KEY の生成例
```

## 4. モデルの配置

```bash
scripts/install_models.sh --antelopev2   # insightface（URL と sha256 検証済み）
scripts/install_models.sh --face         # PuLID / FaceID / InstantID / face_yolov8m（初回は URL を確認しながら）
# チェックポイント: Civitai でモデルの「version id」を決めて .env の CIVITAI_CHECKPOINT_VERSION_ID と CIVITAI_TOKEN を設定してから
scripts/install_models.sh --checkpoint
scripts/install_models.sh --list
```

配置先は ComfyUI の `models/` と同じレイアウト（`checkpoints/` `loras/` `pulid/` `ipadapter/` `instantid/` `controlnet/instantid/` `clip_vision/`
`insightface/models/antelopev2/` `ultralytics/bbox/`）。ファイル名は `workflows/WORKFLOW_NOTES.md` の表と一致させてください。

## 5. 起動

```bash
docker compose up --build            # 初回は 10〜30 分（ComfyUI + カスタムノード + web の build）
scripts/verify_env.sh                # cu128 / sm_120 / ノード登録 / ワークフロー検証 / API 認証
```

- ComfyUI: http://127.0.0.1:8188（動作確認用。UI で 1 枚生成できることを確認）
- アプリ: http://127.0.0.1:8000 （API が `web/dist` を同じポートで配信。`/docs` に Swagger UI）
- 初回はブラウザの設定画面で `.env` の `API_KEY` を入力する。

## 6. 開発モード（コンテナを使わない）

```bash
# API（Python 3.12 / uv）
cd api && uv sync && cp ../.env.example ../.env   # API_KEY を設定
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# Web（Vite dev server。/api は :8000 にプロキシ）
cd ../web && pnpm install && pnpm dev            # http://127.0.0.1:5173
# GPU が無いマシンで UI を触る: モック ComfyUI + モック顔エンジン
cd ../api && uv run python -m tools.mock_comfy --port 8188        # 別ターミナル
FACE_ENGINE=mock COMFY_URL=http://127.0.0.1:8188 uv run uvicorn app.main:app --port 8000
```

## 7. 次にやること（GPU 実機でのゲート）

1. `scripts/verify_env.sh` の生の出力を保存（G0 / G1）
2. `cd api && uv run python ../scripts/measure_vram.py --checkpoint <file>` で VRAM 実測テーブルを作る（G2）
3. 候補チェックポイントの比較・採用、`scripts/face_similarity.py calibrate` でしきい値を較正し docs/MODELS.md に記録（G2 / G3）
4. docs/MANUAL_QA.md の残りを実機で確認（G7）
