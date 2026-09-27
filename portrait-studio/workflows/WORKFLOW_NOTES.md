# workflows/ — ComfyUI API 形式のワークフロー

`face_method` ごとに 1 ファイル。API（`api/app/workflow.py`）は **ノード ID を一切使わず**、各ノードの `_meta.title` で差し替え先を特定する。
ComfyUI の UI で再エクスポートしてノード ID が変わっても壊れない。**`_meta.title` の無いノードは起動時に拒否**され、
必須 title が欠けると API はエラーメッセージ（ファイル名と欠けている title）を出して起動しない。

| ファイル | 用途 | 主なカスタムノード |
| --- | --- | --- |
| `portrait_txt2img_api.json` | 種顔の生成（Step 1）・VRAM 実測の基準 | なし（コアのみ） |
| `portrait_pulid_api.json` | PuLID（既定採用候補） | `PulidModelLoader` `PulidEvaClipLoader` `PulidInsightFaceLoader` `ApplyPulid`（cubiq/PuLID_ComfyUI） |
| `portrait_faceid_api.json` | IP-Adapter FaceID Plus v2 | `IPAdapterUnifiedLoaderFaceID` `IPAdapterFaceID`（cubiq/ComfyUI_IPAdapter_plus） |
| `portrait_instantid_api.json` | InstantID（`--lowvram` 前提のオプション） | `InstantIDModelLoader` `InstantIDFaceAnalysis` `ControlNetLoader` `ApplyInstantID`（cubiq/ComfyUI_InstantID） |

顔ワークフロー共通: `FaceDetailer`（ltdrdata/ComfyUI-Impact-Pack）+ `UltralyticsDetectorProvider`（Impact-Subpack, `bbox/face_yolov8m.pt`）。

## 必須 title と API が書き換える入力

| title | クラス（同梱ファイル） | API が設定する `inputs` |
| --- | --- | --- |
| `CHECKPOINT` | `CheckpointLoaderSimple` | `ckpt_name` ← キャラの `locked.checkpoint` |
| `LORA` | `LoraLoader` | `lora_name` / `strength_model` / `strength_clip`。LoRA 未設定なら**バイパス**（CHECKPOINT の model/clip に接続し直してノードを削除） |
| `POSITIVE` | `CLIPTextEncode` | `text` ← `prefix_prompt` + シーン断片 + ユーザープロンプト |
| `NEGATIVE` | `CLIPTextEncode` | `text` ← `negative_prompt` |
| `LATENT` | `EmptyLatentImage` | `width` / `height` / `batch_size`（**常に 1**） |
| `SAMPLER` | `KSampler` | `seed` / `steps` / `cfg` / `sampler_name` / `scheduler`（ファイルの値が既定） |
| `UPSCALE` + `HIRES_SAMPLER`（任意） | `LatentUpscaleBy` + `KSampler` | `scale_by` / hires の `seed` `denoise`。upscale 1.0 なら両方バイパス |
| `DECODE` | `VAEDecode` | なし |
| `REF_IMAGE`（顔） | `LoadImage` | `image` ← `/upload/image` で `input/refs/` に置いた参照顔 |
| `FACE_APPLY`（顔） | `ApplyPulid` / `IPAdapterFaceID` / `ApplyInstantID` | `weight`（FaceID は `weight_faceidv2` も） ← `face_weight` |
| `FACE_DETAILER`（顔） | `FaceDetailer` | `denoise` ← `face_detailer_denoise`（0.5 未満）、`seed`。無効なら DECODE → SAVE に直結してバイパス |
| `SAVE` | `SaveImage` | `filename_prefix` |

その他の title（`FACE_MODEL` `FACE_ANALYSIS` `EVA_CLIP` `FACE_LOADER` `FACE_CONTROLNET` `FACE_DETECTOR`）は API が触らないローダー。
**モデルのファイル名はここに書いてあるとおりに配置する**（`scripts/install_models.sh` が同じ名前で保存する）。違う名前で置いた場合は
JSON の該当ノードの値を書き換える（`GET /api/system/models` で配置済みファイルを確認できる）。

## 検証状況（正直に）

- この 4 ファイルは、この文書を書いた環境（GPU 無し・カスタムノード無し）では **ライブの ComfyUI に対して未検証**。
  ノードのクラス名・入力名は各リポジトリの公開 API に合わせて書いたが、Impact-Pack 等は更新で入力名が変わることがある。
- 検証手順（G1）: `scripts/verify_env.sh` が `GET /object_info` と全ファイルを突き合わせ、
  `error:`（クラス未登録・必須入力の欠落）と `warn:`（未知の入力名・選択肢外の値）を一覧する。API 起動時も同じ検証を行い、
  `GET /api/health` の `workflows[].issues` に出る。`error:` が残る手法は `face_methods` から外れ、その手法での生成は 400 になる。
- 直し方: `GET /object_info/<ClassName>` の `input.required` / `input.optional` と JSON の `inputs` を合わせる。title は変えない。

## 同梱値の根拠

- サンプラー既定: `dpmpp_2m` / `karras` / 28 steps / cfg 5.0（SDXL 写実系の一般的な既定。G2 で実測して調整する）。
- FaceDetailer: `denoise 0.35`（0.3〜0.4。**0.5 以上は別人化するため API が拒否**）、`guide_size 512` `max_size 1024` `bbox_crop_factor 3.0`。
- hires-fix: `LatentUpscaleBy nearest-exact ×1.3` + `denoise 0.45`。倍率はキャラの `hires_max`（既定 1.3）まで。
- InstantID は `ApplyInstantID` が positive / negative を返すため、`SAMPLER` `HIRES_SAMPLER` `FACE_DETAILER` の conditioning はその出力を使う。
