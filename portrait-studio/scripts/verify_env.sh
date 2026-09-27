#!/usr/bin/env bash
# =============================================================================
# scripts/verify_env.sh — G0/G1 verification in one command. Prints RAW outputs; exits non-zero on any failure.
#
#   1. GPU visible in WSL2 (nvidia-smi) and inside Docker (--gpus all)
#   2. PyTorch build: torch.version.cuda == 12.8 and no "sm_120 is not compatible" warning
#   3. ComfyUI reachable (GET /system_stats) and the required node classes registered (GET /object_info)
#   4. Workflow files validated against /object_info (titles + class names + required inputs)
#   5. API reachable (GET /api/health) and authentication enforced (401 without key)
#
# Usage: scripts/verify_env.sh [--image <pytorch image>] [--skip-docker] [--comfy http://127.0.0.1:8188] [--api http://127.0.0.1:8000]
# =============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${PYTORCH_IMAGE:-pytorch/pytorch:2.11.0-cuda12.8-cudnn9-devel}"
COMFY="${COMFY_URL:-http://127.0.0.1:8188}"
API="${API_URL:-http://127.0.0.1:8000}"
SKIP_DOCKER=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --comfy) COMFY="$2"; shift 2 ;;
    --api) API="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [[ -f "$HERE/.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$HERE/.env"; set +a
fi
FAIL=0
ok()   { printf '\033[32mOK\033[0m   %s\n' "$*"; }
bad()  { printf '\033[31mFAIL\033[0m %s\n' "$*"; FAIL=1; }
warn() { printf '\033[33mWARN\033[0m %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

section "1. GPU (host)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>&1 || bad "nvidia-smi failed"
else
  bad "nvidia-smi not found (WSL2: update the Windows NVIDIA driver, run 'wsl --update')"
fi

section "2. PyTorch cu128 / sm_120 (docker run --gpus all $IMAGE)"
if [[ $SKIP_DOCKER -eq 1 ]]; then
  warn "skipped (--skip-docker)"
elif ! command -v docker >/dev/null 2>&1; then
  bad "docker not found"
else
  OUT="$(docker run --rm --gpus all "$IMAGE" python -c 'import torch,warnings;print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NO-CUDA"); print("capability", torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None)' 2>&1)"
  RC=$?
  printf '%s\n' "$OUT"
  if [[ $RC -ne 0 ]]; then bad "docker run failed (GPU passthrough? Docker Desktop → Settings → Resources → WSL integration)"; fi
  if grep -q "sm_120 is not compatible" <<<"$OUT"; then bad "PyTorch build lacks sm_120 (needs +cu128, torch >= 2.7)"; fi
  if grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\+cu128 12\.8 True' <<<"$OUT"; then ok "torch +cu128 / CUDA 12.8 / GPU visible"; else bad "expected 'x.y.z+cu128 12.8 True <GPU>'"; fi
  if grep -q "capability (12, 0)" <<<"$OUT"; then ok "compute capability sm_120"; else warn "compute capability is not (12, 0) — is this the RTX 5070?"; fi
fi

section "3. ComfyUI ($COMFY)"
if STATS="$(curl -fsS "$COMFY/system_stats" 2>&1)"; then
  printf '%s\n' "$STATS" | python3 -c 'import json,sys;d=json.load(sys.stdin);s=d["system"];dev=(d.get("devices") or [{}])[0];print("comfyui",s.get("comfyui_version"),"| torch",s.get("pytorch_version"),"| device",dev.get("name"),"| vram_total_mb",int(dev.get("vram_total",0)/1048576))'
  ok "system_stats"
  NODES="$(curl -fsS "$COMFY/object_info" | python3 -c 'import json,sys;d=json.load(sys.stdin);want=["CheckpointLoaderSimple","LoraLoader","KSampler","LatentUpscaleBy","IPAdapterUnifiedLoaderFaceID","IPAdapterFaceID","PulidModelLoader","PulidInsightFaceLoader","PulidEvaClipLoader","ApplyPulid","InstantIDModelLoader","InstantIDFaceAnalysis","ApplyInstantID","ControlNetLoader","FaceDetailer","UltralyticsDetectorProvider"];missing=[k for k in want if k not in d];print("registered:",[k for k in want if k in d]);print("MISSING:",missing);sys.exit(1 if missing else 0)' 2>&1)"
  RC=$?
  printf '%s\n' "$NODES"
  if [[ $RC -eq 0 ]]; then ok "all required node classes registered"; else bad "node classes missing → custom node not installed / requirements failed (docker compose logs comfyui)"; fi
  section "4. Workflow files vs /object_info"
  if (cd "$HERE/api" && uv run python - "$COMFY" <<'PY'
import json, sys, urllib.request
from pathlib import Path
from app.workflow import load_all, validate_against_object_info, workflow_ok
info = json.load(urllib.request.urlopen(sys.argv[1] + "/object_info"))
rc = 0
for method, wf in load_all(Path("../workflows")).items():
    issues = validate_against_object_info(wf, info)
    status = "OK " if workflow_ok(issues) else "ERR"
    print(f"[{status}] {wf.path.name}")
    for i in issues:
        print("       ", i)
    rc |= 0 if workflow_ok(issues) else 1
sys.exit(rc)
PY
  ); then ok "workflows usable"; else bad "workflow validation failed (fix titles / node inputs per docs/WORKFLOW_NOTES.md)"; fi
else
  bad "ComfyUI not reachable at $COMFY ($STATS)"
fi

section "5. API ($API)"
if HEALTH="$(curl -fsS "$API/api/health" 2>&1)"; then
  printf '%s\n' "$HEALTH" | python3 -m json.tool | head -40
  ok "health"
else
  bad "API not reachable / unhealthy: $HEALTH"
fi
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$API/api/characters")"
if [[ "$CODE" == "401" ]]; then ok "authentication enforced (401 without X-API-Key)"; else bad "expected 401 without key, got $CODE"; fi
if [[ -n "${API_KEY:-}" ]]; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $API_KEY" "$API/api/characters")"
  if [[ "$CODE" == "200" ]]; then ok "API_KEY accepted"; else bad "API_KEY rejected ($CODE)"; fi
fi

echo
if [[ $FAIL -eq 0 ]]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED (see above)"; fi
exit $FAIL
