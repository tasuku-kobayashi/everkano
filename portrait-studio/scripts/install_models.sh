#!/usr/bin/env bash
# =============================================================================
# scripts/install_models.sh — download the models into ${MODELS_DIR} (ComfyUI models folder layout).
#
# Nothing here is downloaded silently from a guessed location:
#   - antelopev2 (insightface): GitHub release asset — URL + sha256 verified (docs/MODELS.md)
#   - SDXL checkpoint: Civitai, by the *version id* you choose (CIVITAI_CHECKPOINT_VERSION_ID) after the G2 comparison
#   - face-identity weights (PuLID / IP-Adapter FaceID / InstantID / face_yolov8m): the script prints the
#     canonical Hugging Face locations it is about to fetch and stops on any non-200 response. These URLs were
#     NOT fetched from the environment that wrote this script (blocked network) — verify them on first run.
# Usage: scripts/install_models.sh [--all | --face | --checkpoint | --antelopev2 | --list]
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# .env is data, not a shell script: read KEY=VALUE lines only (no expansion, no command execution), env wins over file
load_env_file() {
  local file="$1" line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"; value="${BASH_REMATCH[3]}"
    value="${value%%[[:space:]]#*}"                      # trailing comment
    value="${value%"${value##*[![:space:]]}"}"           # trailing whitespace
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
    if [[ -z "${!key+x}" ]]; then export "$key=$value"; fi
  done < "$file"
}
if [[ -f "$HERE/.env" ]]; then load_env_file "$HERE/.env"; fi
MODELS_DIR="${MODELS_DIR:-$HERE/data/models}"
CIVITAI_TOKEN="${CIVITAI_TOKEN:-}"
CIVITAI_CHECKPOINT_VERSION_ID="${CIVITAI_CHECKPOINT_VERSION_ID:-}"
WHAT="${1:---all}"

case "$MODELS_DIR" in
  /mnt/[a-z]/*) echo "ERROR: MODELS_DIR=$MODELS_DIR is on NTFS (/mnt/*). Use the WSL2 ext4 side (e.g. /home/<you>/models)." >&2; exit 2 ;;
esac
mkdir -p "$MODELS_DIR"/{checkpoints,loras,controlnet/instantid,pulid,ipadapter,instantid,clip_vision,insightface/models,ultralytics/bbox}

# Tokens never appear on a command line (visible in `ps` / shell history): curl reads them from a 0600 header file.
HEADER_FILE="$(mktemp)"
trap 'rm -f "$HEADER_FILE"' EXIT
chmod 600 "$HEADER_FILE"
write_bearer_header() { # write_bearer_header <token>
  printf 'Authorization: Bearer %s\n' "$1" > "$HEADER_FILE"
}

fetch() { # fetch <url> <dest> [--auth]
  local url="$1" dest="$2" auth="${3:-}"
  if [[ -s "$dest" ]]; then echo "  exists: $dest"; return 0; fi
  echo "  GET $url"
  local code
  if [[ -n "$auth" ]]; then
    code="$(curl -L -sS -o "$dest.part" -w '%{http_code}' -H "@$HEADER_FILE" "$url")"
  else
    code="$(curl -L -sS -o "$dest.part" -w '%{http_code}' "$url")"
  fi
  if [[ "$code" != "200" ]]; then
    rm -f "$dest.part"
    echo "  ERROR: HTTP $code for $url" >&2
    return 1
  fi
  mv "$dest.part" "$dest"
  echo "  saved: $dest ($(du -h "$dest" | cut -f1))"
}

sha256_check() { # sha256_check <file> <expected>
  local actual
  actual="$(sha256sum "$1" | cut -d' ' -f1)"
  if [[ "$actual" != "$2" ]]; then echo "  ERROR: sha256 mismatch for $1: $actual != $2" >&2; return 1; fi
  echo "  sha256 OK"
}

install_antelopev2() {
  echo "== insightface antelopev2 (detector scrfd_10g_bnkps + ArcFace glintr100 + 3D landmarks for pose)"
  local zip="$MODELS_DIR/insightface/models/antelopev2.zip"
  # Verified 2026-09-27 from the environment that wrote this script: HTTP 200, 360,662,982 bytes.
  fetch "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip" "$zip"
  sha256_check "$zip" "8e182f14fc6e80b3bfa375b33eb6cff7ee05d8ef7633e738d1c89021dcf0c5c5"
  if [[ ! -f "$MODELS_DIR/insightface/models/antelopev2/glintr100.onnx" ]]; then
    (cd "$MODELS_DIR/insightface/models" && unzip -o -q antelopev2.zip)
    # the zip may contain a nested antelopev2/antelopev2/ on some versions
    if [[ -d "$MODELS_DIR/insightface/models/antelopev2/antelopev2" ]]; then
      mv "$MODELS_DIR/insightface/models/antelopev2/antelopev2/"*.onnx "$MODELS_DIR/insightface/models/antelopev2/"
      rmdir "$MODELS_DIR/insightface/models/antelopev2/antelopev2"
    fi
  fi
  ls -la "$MODELS_DIR/insightface/models/antelopev2/"
}

install_face() {
  echo "== face identity weights (verify each URL on first run; see docs/MODELS.md)"
  local hf="https://huggingface.co"
  # PuLID for SDXL — file name referenced by workflows/portrait_pulid_api.json (PulidModelLoader.pulid_file)
  fetch "$hf/huchenlei/ipadapter_pulid/resolve/main/ip-adapter_pulid_sdxl_fp16.safetensors" "$MODELS_DIR/pulid/ip-adapter_pulid_sdxl_fp16.safetensors"
  # IP-Adapter FaceID Plus v2 (SDXL) + its LoRA — resolved by IPAdapterUnifiedLoaderFaceID preset "FACEID PLUS V2"
  fetch "$hf/h94/IP-Adapter-FaceID/resolve/main/ip-adapter-faceid-plusv2_sdxl.bin" "$MODELS_DIR/ipadapter/ip-adapter-faceid-plusv2_sdxl.bin"
  fetch "$hf/h94/IP-Adapter-FaceID/resolve/main/ip-adapter-faceid-plusv2_sdxl_lora.safetensors" "$MODELS_DIR/loras/ip-adapter-faceid-plusv2_sdxl_lora.safetensors"
  # CLIP vision used by IP-Adapter FaceID Plus (ViT-H)
  fetch "$hf/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors" "$MODELS_DIR/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
  # InstantID — file names referenced by workflows/portrait_instantid_api.json
  fetch "$hf/InstantX/InstantID/resolve/main/ip-adapter.bin" "$MODELS_DIR/instantid/ip-adapter.bin"
  fetch "$hf/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors" "$MODELS_DIR/controlnet/instantid/diffusion_pytorch_model.safetensors"
  fetch "$hf/InstantX/InstantID/resolve/main/ControlNetModel/config.json" "$MODELS_DIR/controlnet/instantid/config.json"
  # FaceDetailer bbox detector (Impact-Subpack, UltralyticsDetectorProvider model_name bbox/face_yolov8m.pt)
  fetch "$hf/Bingsu/adetailer/resolve/main/face_yolov8m.pt" "$MODELS_DIR/ultralytics/bbox/face_yolov8m.pt"
}

install_checkpoint() {
  echo "== SDXL checkpoint from Civitai"
  if [[ -z "$CIVITAI_CHECKPOINT_VERSION_ID" ]]; then
    echo "  CIVITAI_CHECKPOINT_VERSION_ID is empty. scripts/check_civitai_license.sh <civitai model id> tells you which"
    echo "  candidates actually allow running your own paid generation service (allowCommercialUse must include \"Rent\","
    echo "  not just \"RentCivit\"/\"Image\") — verified so far: RealVisXL V5.0 (139562) and CyberRealistic Pony (443821)"
    echo "  qualify; Juggernaut XL (133005) and Pony Realism (372465) do not (see docs/MODELS.md). Put the chosen"
    echo "  *version id* in .env and re-run."
    return 0
  fi
  if [[ ! "$CIVITAI_CHECKPOINT_VERSION_ID" =~ ^[0-9]+$ ]]; then
    echo "  ERROR: CIVITAI_CHECKPOINT_VERSION_ID must be numeric (got '$CIVITAI_CHECKPOINT_VERSION_ID')" >&2; return 1
  fi
  write_bearer_header "$CIVITAI_TOKEN"
  local meta name query
  meta="$(curl -fsS -H "@$HEADER_FILE" "https://civitai.com/api/v1/model-versions/$CIVITAI_CHECKPOINT_VERSION_ID")"
  # a version can ship more than one Model file (e.g. fp32 "full" + fp16 "pruned" — confirmed on RealVisXL V5.0,
  # where fp32 happens to be files[0]): always take the one Civitai marks `primary`, never just the first entry.
  # Falls back to the first Model file when none is marked (older uploads sometimes omit the flag).
  name="$(python3 -c '
import json, sys
d = json.load(sys.stdin)
models = [x for x in d["files"] if x.get("type") == "Model"]
f = next((x for x in models if x.get("primary")), models[0])
print(f["name"])
' <<<"$meta")"
  # the file name comes from a remote API: keep the basename only and allow a conservative character set
  name="$(basename -- "$name")"
  if [[ ! "$name" =~ ^[A-Za-z0-9._-]+\.safetensors$ || "$name" == .* ]]; then
    echo "  ERROR: refusing unexpected checkpoint file name from Civitai: '$name'" >&2; return 1
  fi
  python3 -c '
import json, sys
d = json.load(sys.stdin)
models = [x for x in d["files"] if x.get("type") == "Model"]
f = next((x for x in models if x.get("primary")), models[0])
print("  model:", d["model"]["name"], "| version:", d["name"], "| file:", f["name"],
      "| size_kb:", f.get("sizeKB"), "| sha256:", f.get("hashes", {}).get("SHA256"))
' <<<"$meta"
  # pin the download to the exact file just picked (its own fp/size metadata) instead of a bare type=Model&format=
  # SafeTensor request, whose server-side default is unverified and may not match the file picked above
  query="$(python3 -c '
import json, sys
d = json.load(sys.stdin)
models = [x for x in d["files"] if x.get("type") == "Model"]
f = next((x for x in models if x.get("primary")), models[0])
m = f.get("metadata") or {}
parts = ["type=Model", "format=SafeTensor"]
if m.get("fp"):
    parts.append("fp=" + str(m["fp"]))
if m.get("size"):
    parts.append("size=" + str(m["size"]))
print("&".join(parts))
' <<<"$meta")"
  fetch "https://civitai.com/api/download/models/$CIVITAI_CHECKPOINT_VERSION_ID?$query" "$MODELS_DIR/checkpoints/$name" --auth
  local expected
  expected="$(python3 -c '
import json, sys
d = json.load(sys.stdin)
models = [x for x in d["files"] if x.get("type") == "Model"]
f = next((x for x in models if x.get("primary")), models[0])
print((f.get("hashes", {}).get("SHA256") or "").lower())
' <<<"$meta")"
  if [[ -n "$expected" ]]; then sha256_check "$MODELS_DIR/checkpoints/$name" "$expected"; fi
  echo "  → record model / version / file / license in docs/MODELS.md and set DEFAULT_CHECKPOINT=$name in .env"
}

case "$WHAT" in
  --list) find "$MODELS_DIR" -type f | sort ;;
  --antelopev2) install_antelopev2 ;;
  --face) install_face ;;
  --checkpoint) install_checkpoint ;;
  --all) install_antelopev2; install_face; install_checkpoint ;;
  *) echo "usage: $0 [--all | --face | --checkpoint | --antelopev2 | --list]" >&2; exit 2 ;;
esac
echo "done. models under $MODELS_DIR"
