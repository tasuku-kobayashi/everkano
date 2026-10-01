#!/usr/bin/env bash
# =============================================================================
# scripts/check_civitai_license.sh — print a Civitai model's real license flags and version list (G2 candidate
# comparison). Never guess a license or a version id from a web page summary: this reads the model's own API
# response, which is the only place the actual `allowCommercialUse` / `allowNoCredit` / `allowDerivatives` /
# `allowDifferentLicense` flags live (they can differ per model even under the same license *name*).
#
# Usage: scripts/check_civitai_license.sh <civitai model id> [civitai model id ...]
#   CIVITAI_TOKEN=<token> scripts/check_civitai_license.sh 139562   # token only needed for gated/login-walled models
#
# The model id is the number in the model's URL, e.g. https://civitai.com/models/139562/realvisxl-v50 -> 139562
# (NOT the modelVersionId query param — this prints every version's id so you can pick the exact one for
# CIVITAI_CHECKPOINT_VERSION_ID in .env).
# =============================================================================
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <civitai model id> [civitai model id ...]" >&2
  exit 2
fi

HEADER_FILE="$(mktemp)"
trap 'rm -f "$HEADER_FILE"' EXIT
chmod 600 "$HEADER_FILE"
if [[ -n "${CIVITAI_TOKEN:-}" ]]; then
  printf 'Authorization: Bearer %s\n' "$CIVITAI_TOKEN" > "$HEADER_FILE"
fi

for id in "$@"; do
  echo "== civitai.com/models/$id =="
  auth_args=()
  [[ -s "$HEADER_FILE" ]] && auth_args=(-H "@$HEADER_FILE")
  code="$(curl -L -sS -o "/tmp/civitai-$id.json" -w '%{http_code}' "${auth_args[@]}" "https://civitai.com/api/v1/models/$id")"
  if [[ "$code" != "200" ]]; then
    echo "  ERROR: HTTP $code (set CIVITAI_TOKEN if this model requires login)" >&2
    rm -f "/tmp/civitai-$id.json"
    continue
  fi
  python3 - "/tmp/civitai-$id.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(f"  name: {data.get('name')!r}  type: {data.get('type')}  nsfw: {data.get('nsfw')}")
print(f"  allowCommercialUse: {data.get('allowCommercialUse')}")
print(f"  allowNoCredit: {data.get('allowNoCredit')}  allowDerivatives: {data.get('allowDerivatives')}  "
      f"allowDifferentLicense: {data.get('allowDifferentLicense')}")
print("  versions (id, name, baseModel, file, size, primary, fp/size, sha256):")
for v in data.get("modelVersions", []):
    files = [f for f in v.get("files", []) if f.get("type") == "Model"] or v.get("files", [])
    for f in files or [{}]:
        size_mb = round(f.get("sizeKB", 0) / 1024, 1) if f.get("sizeKB") else "?"
        sha256 = (f.get("hashes") or {}).get("SHA256", "?")
        meta = f.get("metadata") or {}
        fp_size = f"{meta.get('fp', '?')}/{meta.get('size', '?')}"
        # when a version ships more than one Model file (fp32 "full" + fp16 "pruned" is common), `primary` is the
        # one Civitai itself recommends as the default download — pick that one, never just the first in the list
        print(f"    {v.get('id'):>9}  {v.get('name')!r:30s}  {v.get('baseModel')!r:12s}  "
              f"{f.get('name')}  {size_mb} MB  primary={f.get('primary')}  {fp_size}  sha256={sha256}")
PY
  rm -f "/tmp/civitai-$id.json"
  echo
done

cat <<'NOTE'
allowCommercialUse の見方（値は配列。空 [] または欠落 = 商用不可とみなす）:
  "Image"     生成した画像を商用利用・販売できる
  "Rent"      このモデルを使った有料の生成サービス（ComfyUI/API を含む）を運営できる
  "RentCivit" Civitai 上の有料生成サービスで使える（自前サービスの可否とは別）
  "Sell"      このモデルや派生物自体を販売できる
Project P（商用サービスとして画像を生成）には最低限 "Image" と "Rent" の両方が必要。
NOTE
