#!/usr/bin/env bash
# Starts the FastAPI backend for E2E: mock face engine, mock ComfyUI, temp data dir, built web UI served from /.
set -euo pipefail
API_PORT="${1:-18000}"
COMFY_PORT="${2:-18188}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$HERE/.." && pwd)"
API_DIR="$(cd "$WEB_DIR/../api" && pwd)"
TMP="$HERE/.tmp"
rm -rf "$TMP/data"
mkdir -p "$TMP/data"
export API_KEY="e2e-test-api-key-0123456789"  # check-secrets: allow - dummy key for the E2E stack
export COMFY_URL="http://127.0.0.1:${COMFY_PORT}"
export DATA_DIR="$TMP/data"
export FACE_ENGINE="mock"
export WEB_DIST_DIR="$WEB_DIR/dist"
export VRAM_TABLE_PATH="$API_DIR/tests/fixtures/vram_table.test.json"
export COMFY_POLL_INTERVAL_SECONDS="0.05"
export LOG_LEVEL="WARNING"
cd "$API_DIR"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT"
