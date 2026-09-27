# portrait-studio — 同一の架空キャラクター（実写調・日本人・成人）で画像を生成・管理するローカル Web アプリ

ComfyUI（RTX 5070 / 12 GB, CUDA 12.8）を推論エンジンに、**1 体の架空キャラクターを「作成」し、その同一性を保ったまま何百回でも生成・管理**するためのブラウザ UI 付きアプリ。
everkano モノレポの中にあるが MVP アプリ（`apps/` `packages/`）とは独立（[ADR-0050](../docs/adr/0050-portrait-studio-local-image-tool.md)）。**ローカル専用。外部公開しない。**

| 機能 | 画面 | 本質 |
| --- | --- | --- |
| 1. 新規キャラクターの作成 | 作成ウィザード（5 ステップ） | 種顔を作る → 品質を測って選ぶ → 同一性を検証する → 固定設定と共に登録（探索・1 体につき 1 回） |
| 2. 既存キャラクターでの生成 | ワークスペース（3 カラム） | 保存済みキャラ + プロンプト → 同一人物の画像。全生成物に `params_snapshot` と参照顔との類似度（反復） |

## この実装の状態（正直な現状）

| 領域 | 状態 | 検証 |
| --- | --- | --- |
| API（FastAPI, `api/`） | 仕様の全エンドポイント実装（34 パス） | pytest 57 件（モック ComfyUI + モック顔エンジン）、ruff、mypy strict |
| Web UI（React + Vite + TS, `web/`） | 5 画面 + 共通 UX 10 項目 | tsc、ESLint、vitest、Playwright 8 シナリオ（`docs/screenshots/`） |
| ワークフロー（`workflows/`） | 4 ファイル（title ベース） | 構造・差し替え・バイパスはテスト済み。**ライブの ComfyUI（カスタムノード）に対しては未検証** |
| 顔分析（insightface 2.0 + antelopev2, CPU） | 実装済み | 実モデルの取得・ロード・顔なし画像の検出 0 を確認。**実写顔での精度・しきい値は未較正** |
| GPU / CUDA 12.8 / sm_120（G0） | `scripts/verify_env.sh` | **この実装環境に GPU が無いため未実施**。RTX 5070 機で実行する |
| VRAM 実測テーブル（G2） | `scripts/measure_vram.py` | **未実測**（同梱テーブルは全行 null → 該当組み合わせの生成は拒否される） |
| チェックポイント・手法の選定（G2 / G3） | `docs/MODELS.md` に手順と記録欄 | **未選定**（Civitai / Hugging Face に到達できない環境で作成） |
| Docker（`docker/`, `api/Dockerfile`, `docker-compose.yml`） | 作成済み。`docker compose config` は通る | **イメージのビルドは未実施**（Docker デーモン無し） |

GPU 実機での残作業は [docs/SETUP_WSL2.md](docs/SETUP_WSL2.md) の「7. 次にやること」と [docs/MANUAL_QA.md](docs/MANUAL_QA.md)。

## 構成

```
portrait-studio/
├─ docker/Dockerfile            # ComfyUI（ARG PYTORCH_IMAGE=pytorch/pytorch:2.11.0-cuda12.8-cudnn9-devel）+ カスタムノード
├─ docker-compose.yml           # comfyui(:8188) + api(:8000)。どちらも 127.0.0.1 バインド、shm 8gb
├─ .env.example                 # API_KEY（必須）/ CIVITAI_TOKEN / MODELS_DIR / しきい値
├─ api/                         # FastAPI（Python 3.12, uv）。web/dist を / で静的配信、API は /api 配下
│  ├─ app/  main.py config.py security.py db.py models.py schemas.py comfy_client.py workflow.py queue.py jobs.py
│  │        face.py vram.py audit.py presets.py storage.py serialize.py container.py routers/ data/
│  ├─ tools/mock_comfy.py       # GPU 無しで UI/テストを動かすモック ComfyUI（HTTP + WebSocket）
│  ├─ tests/                    # 57 件
│  └─ scripts/export_openapi.py # docs/api/openapi.json → web の型生成
├─ web/                         # React 18 + Vite + TS + React Router + TanStack Query + Zustand + Tailwind + react-window
│  ├─ src/pages/  CharactersPage WizardPage WorkspacePage GalleryPage SettingsPage
│  ├─ src/api/types.ts          # openapi-typescript で生成（契約ズレを構造的に防ぐ）
│  └─ e2e/                      # Playwright（実 API + モック ComfyUI）
├─ workflows/                   # portrait_{txt2img,pulid,faceid,instantid}_api.json + WORKFLOW_NOTES.md
├─ scripts/                     # install_models.sh verify_env.sh measure_vram.py face_similarity.py
└─ docs/                        # SETUP_WSL2 MODELS TROUBLESHOOTING MANUAL_QA COMPLIANCE screenshots/ api/openapi.json
```

## 起動手順（RTX 5070 の WSL2 機）

詳細は [docs/SETUP_WSL2.md](docs/SETUP_WSL2.md)。

```bash
cd portrait-studio
cp .env.example .env           # API_KEY を設定（必須。無いと API は起動しない）。MODELS_DIR / DATA_DIR は ext4 側
scripts/install_models.sh      # antelopev2（検証済み URL）+ 顔一貫性の重み + Civitai チェックポイント
docker compose up --build      # ComfyUI + API/UI
scripts/verify_env.sh          # G0/G1: cu128 / sm_120 / ノード登録 / ワークフロー / 認証
# ブラウザ: http://127.0.0.1:8000 → 設定画面で API_KEY を入力
```

GPU の無いマシンで UI を触る（開発・レビュー用）:

```bash
cd api && uv sync && cp ../.env.example ../.env    # API_KEY を設定
uv run python -m tools.mock_comfy --port 8188                                    # ターミナル A
FACE_ENGINE=mock COMFY_URL=http://127.0.0.1:8188 uv run uvicorn app.main:app --port 8000  # ターミナル B
cd ../web && pnpm install && pnpm dev                                            # ターミナル C → http://127.0.0.1:5173
```

## 使い方

1. **キャラクター一覧**（ホーム）: カードで一覧。「＋ 新規キャラクター作成」でウィザードへ。カードのメニューから生成 / 版の管理 / 編集 / 削除。
2. **作成ウィザード**: Step 1 種顔（テキスト生成 or アップロード）→ Step 2 品質スコア・クラスタ・推奨で参照顔を選ぶ（顔が無い画像は選べない）→
   Step 3 手法 × weight × 3 シーン = 9 枚を生成し ArcFace 類似度で検証、「この手法・この weight で固定」→ Step 4 名前 + 2 つの必須申告（実在人物ではない / 成人）→ Step 5 完了。
3. **ワークスペース**: 左＝キャラカードと固定設定（読み取り専用。「設定を変更」は警告付きでこの生成限り。「新版として保存」も選べる）、
   中央＝プロンプト・シーンプリセット・枚数・生成（Enter で生成、Shift+Enter 改行。▸ 詳細 / ▸ 上級 で段階開示。OOM 事前警告、失敗時の 1 クリック復旧）、
   右＝このキャラの履歴（seed・類似度・版・再生成）。
4. **ギャラリー**: 全生成物の横断閲覧（キャラ / 期間 / seed / 手法 / 類似度の下限 / お気に入り / タグ / テキスト）。キャラ別グループ表示、比較トレイ（参照顔を左端に固定）、
   一括お気に入り / タグ / 削除 / パラメータコピー / ZIP（`params_snapshot` 同梱）。仮想スクロール（500 枚超を想定）。
5. **設定**: API キー、接続先（GPU・VRAM・torch・CUDA）、しきい値、VRAM 実測テーブル、シーンプリセット編集、監査ログ、参照顔の保持と削除、法務・禁止事項。

キーボード: `Enter` 生成 / `← →` 画像移動 / `S` 保存（お気に入り）/ `C` 比較トレイ / `Esc` 閉じる。NSFW ぼかしは既定 ON（ホバー / クリックで個別解除、設定で既定を変更）。

## セキュリティ・法務（要点）

- 認証は `X-API-Key`（`GET /api/health` 以外すべて）。`API_KEY` 未設定なら起動を拒否。`<img>` 用に同じ値を Cookie `psk` でも受け付ける。
- UI は API キーを **localStorage と Cookie に保存**する（ローカル専用のため許容）。**外部公開する構成に変える場合はこの方式を必ず変更**し、
  ComfyUI（無認証の `/prompt`）を絶対に露出しないこと。compose は両サービスを `127.0.0.1` にのみ公開する。
- `POST /api/generate` は `adult_only: true` 必須、登録は `is_synthetic: true` と `adult_confirmed: true` 必須（違反は 400）。
- 全生成を `data/logs/audit.jsonl` に追記し、画像と同じ場所にサイドカー JSON を保存する。
- **参照顔の保持と削除**: 参照顔と埋め込みは `data/refs/<キャラ id>/v<版>/` に、キャラクターを削除するまで保持（自動削除なし）。
  `DELETE /api/characters/{id}?delete_images=true|false`（UI: カードメニュー / 設定画面）で全版の参照顔を即時削除。`true` なら生成画像とサイドカーも削除。
- 詳細は [docs/COMPLIANCE.md](docs/COMPLIANCE.md)（設定画面からも読める）。

## API

OpenAPI: [docs/api/openapi.json](docs/api/openapi.json)（`cd api && uv run python scripts/export_openapi.py`）。起動後は `http://127.0.0.1:8000/docs`。
主なもの: `GET /api/health`（無認証・ComfyUI 未接続で 503）、`/api/characters`（一覧 / 登録 / 詳細 / PATCH / 削除 / versions / rollback / analyze / draft / verify / register）、
`POST /api/generate`（+ `/preview-vram`）、`/api/jobs`（直列キュー。1 秒ポーリング・cancel は `/interrupt`）、`/api/images`（一覧 / file / thumb / PATCH / 削除 / regenerate / similarity / bulk / zip）、
`/api/presets`、`/api/system`（vram / free / models / vram-table / compliance / workflows）、`GET /api/audit`。

## 開発

```bash
# API
cd api && uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run python scripts/export_openapi.py            # スキーマを変えたら
# Web
cd web && pnpm install && pnpm api:types && pnpm typecheck && pnpm lint && pnpm test && pnpm build
pnpm e2e                                            # 実 API + モック ComfyUI を自動起動し docs/screenshots/ を更新
```

- ワークフローは **ノード ID を使わず `_meta.title` で参照**（[workflows/WORKFLOW_NOTES.md](workflows/WORKFLOW_NOTES.md)）。`batch_size` は常に 1。
- VRAM 見積りは `api/app/data/vram_table.json` の **実測値のみ**。未実測の組み合わせは拒否（`VRAM_ALLOW_UNMEASURED=true` で緩和可）。
- 品質スコア（`api/app/face.py`）: face_ratio / det_score / sharpness（Laplacian 分散）/ yaw・pitch・roll / composite（重みは定数、しきい値は `.env`）。
- 類似度: ArcFace コサイン。生成画像から顔が検出できないときは `null`（UI は「顔検出不可」）。
