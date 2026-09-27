#!/usr/bin/env bash
# Clone each custom node at the pinned commit and install its requirements (without touching torch / onnxruntime-gpu).
set -euo pipefail
DEST="$1"
LOCK="$2"
mkdir -p "$DEST"
while read -r url commit; do
  [[ -z "${url:-}" || "$url" == \#* ]] && continue
  name="$(basename "$url" .git)"
  echo "==> $name @ $commit"
  git clone --filter=blob:none "$url" "$DEST/$name"
  if [[ "$commit" != "HEAD" ]]; then
    git -C "$DEST/$name" checkout --quiet "$commit"
  fi
  if [[ -f "$DEST/$name/requirements.txt" ]]; then
    # never let a node pin torch or install the GPU onnxruntime
    grep -v -i -E '^(torch|torchvision|torchaudio|onnxruntime-gpu)' "$DEST/$name/requirements.txt" > "/tmp/req-$name.txt" || true
    pip install -r "/tmp/req-$name.txt"
  fi
  if [[ -f "$DEST/$name/install.py" ]]; then
    (cd "$DEST/$name" && python install.py) || echo "warn: $name install.py failed (check GET /object_info after start)"
  fi
done < "$LOCK"
