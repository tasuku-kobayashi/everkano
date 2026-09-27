# モデル一覧・選定の記録

**原則**: 実際にダウンロードしてロード成功を確認したものだけを「確認済み」と書く。推測した URL・モデル名は書かない。
この文書を作成した環境（GPU 無し、Hugging Face / Civitai へ到達不可）で確認できたのは insightface antelopev2 だけです。
それ以外は **利用者の RTX 5070 環境で G1〜G3 を実施し、この表を埋める** 必要があります。

## 確認済み

| 項目 | 内容 |
| --- | --- |
| insightface | PyPI `insightface==2.0`（2026-09-08 公開、pure-python wheel）。`FaceAnalysis(name="antelopev2", providers=["CPUExecutionProvider"])` → `prepare(ctx_id=-1, det_size=(640,640))` → `get(img)` の API が維持されていることをソースで確認 |
| antelopev2 | `https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip`（HTTP 200、360,662,982 bytes、sha256 `8e182f14fc6e80b3bfa375b33eb6cff7ee05d8ef7633e738d1c89021dcf0c5c5`）。展開物: `scrfd_10g_bnkps.onnx`（検出）`glintr100.onnx`（ArcFace 512 次元）`1k3d68.onnx`（3D ランドマーク → pose）`2d106det.onnx` `genderage.onnx`。CPU で 6.6 秒でロード、顔の無い画像で検出 0 を確認 |
| onnxruntime | PyPI `onnxruntime==1.30.0`（CPU 版、cp312 manylinux wheel）。`onnxruntime-gpu` は使わない |
| opencv | `opencv-python-headless` のみ。insightface が要求する GUI 版 `opencv-python` は `api/pyproject.toml` の `[tool.uv] override-dependencies` で解決から除外（libGL 不要） |
| ライセンス | insightface のモデル（antelopev2 を含む）は配布元の表示では **非商用・研究目的**。商用利用は権利者に確認 |

## 未確認（利用者の環境で実施）

### ベースチェックポイント（G2）

要件: フォトリアル / 成人向け表現が可能 / アジア人・日本人の顔が自然に出る SDXL 系。候補系統（名前を鵜呑みにせず生成して目視で選ぶ）:
RealVisXL 系 / Juggernaut XL 系 / CyberRealistic XL 系 / アジア人女性の写実に特化した SDXL 系（例: XXMix_9realisticSDXL）/ Pony 系の写実寄り派生。

手順: Civitai でモデルの **version id** を決め、`.env` の `CIVITAI_CHECKPOINT_VERSION_ID` と `CIVITAI_TOKEN` を設定 → `scripts/install_models.sh --checkpoint`
（メタデータの名前・サイズ・sha256 を表示し、ダウンロード後に sha256 を照合）→ 同一 seed・同一プロンプト（固定プレフィックス + `portrait_closeup`）で
候補ごとに 4 枚生成し、目視比較 → 採用したものを `.env` の `DEFAULT_CHECKPOINT` に設定。

| 候補 | Civitai モデル ID / バージョン ID | 実ファイルサイズ | ライセンス / 商用可否 | 比較結果（何と比較して何が良かったか） | 採用 |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

### 顔一貫性の重み（G1）

`scripts/install_models.sh --face` は以下の **配布元の標準的な場所** から取得を試み、200 以外で停止します。この環境からは到達できなかったため未検証です。
取得できたら、ファイル名（ワークフローが参照する名前）・サイズ・ライセンスをここに記録してください。

| 手法 | ファイル（配置先） | 配布元（要確認） | ライセンス（配布元の表示） | 状態 |
| --- | --- | --- | --- | --- |
| PuLID (SDXL) | `models/pulid/ip-adapter_pulid_sdxl_fp16.safetensors` | huchenlei/ipadapter_pulid（HF） | Apache-2.0 | 未取得 |
| PuLID EVA-CLIP | `models/clip/EVA02_CLIP_L_336_psz14_s6B.pt`（ノードが自動取得） | PuLID_ComfyUI が取得 | MIT（EVA） | 未取得 |
| IP-Adapter FaceID Plus v2 | `models/ipadapter/ip-adapter-faceid-plusv2_sdxl.bin` + `models/loras/ip-adapter-faceid-plusv2_sdxl_lora.safetensors` | h94/IP-Adapter-FaceID（HF） | 非商用 | 未取得 |
| CLIP ViT-H（FaceID Plus 用） | `models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | h94/IP-Adapter（HF） | MIT（LAION） | 未取得 |
| InstantID | `models/instantid/ip-adapter.bin` + `models/controlnet/instantid/diffusion_pytorch_model.safetensors` | InstantX/InstantID（HF） | Apache-2.0 | 未取得 |
| FaceDetailer 検出器 | `models/ultralytics/bbox/face_yolov8m.pt` | Bingsu/adetailer（HF） | AGPL-3.0 | 未取得 |
| insightface（ComfyUI 側） | `models/insightface/models/antelopev2/*.onnx`（PuLID / InstantID）、buffalo_l（FaceID のノードが自動取得） | 上記 | 非商用 | antelopev2 は確認済み |

カスタムノード（`docker/custom_nodes.lock`）: cubiq/PuLID_ComfyUI, cubiq/ComfyUI_IPAdapter_plus, cubiq/ComfyUI_InstantID, ltdrdata/ComfyUI-Impact-Pack, ltdrdata/ComfyUI-Impact-Subpack。
G1 で `GET /object_info` に登録されていることを確認したら、そのときのコミットを lock に固定してここに記録する。

### 手法の選定と類似度（G3）

同一の参照顔・同一プロンプト・同一 seed で 3 手法を生成し、`scripts/face_similarity.py pair` / ウィザード Step 3 の類似度で比較する。
公開ベンチマークの平均顔類似度は **FaceID Plus v2 ≈ 0.62 / PuLID ≈ 0.69 / InstantID ≈ 0.78**。実測がこの帯から大きく外れたら実装を疑う。

| 手法 | weight | portrait_closeup | upper_body_cafe | full_body_street | 平均 | VRAM 実測 | 目視 | 採用 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pulid | 0.6 / 0.8 / 1.0 | | | | | | | |
| faceid | 0.6 / 0.8 / 1.0 | | | | | | | |
| instantid | 0.6 / 0.8 / 1.0 | | | | | | | |

### しきい値の較正

`scripts/face_similarity.py calibrate <charA/> <charB/> <charC/>` で同一キャラ同士 / 別キャラ同士の分布・EER しきい値・推奨値を出し、`.env` の
`SIMILARITY_GOOD` / `SIMILARITY_ACCEPTABLE` に反映する（UI の色分けは設定画面でも調整できる）。初期値 0.75 / 0.60 は仕様の目安であり実測ではない。

| 日付 | 同一キャラ n / 平均 / p5 | 別キャラ n / 平均 / p95 | EER しきい値 | 採用 GOOD / ACCEPTABLE |
| --- | --- | --- | --- | --- |
| | | | | |

### VRAM 実測テーブル

`cd api && uv run python ../scripts/measure_vram.py --checkpoint <file>` が `api/app/data/vram_table.json` を生成する。同梱のファイルは
**全行 `peak_mb: null` のテンプレート**（未実測 → API は該当組み合わせの生成を拒否する）。実測後に設定画面の VRAM テーブルに表示される。
