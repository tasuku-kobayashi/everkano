#!/usr/bin/env bash
# =============================================================================
# scripts/check-db-types.sh — DB 型（packages/shared/src/database.types.ts）がスキーマと一致しているか
#
#   ローカル Supabase（マイグレーション適用済み）から `supabase gen types` で型を生成し、
#   コミット済みの database.types.ts と比較する。マイグレーションを変更したのに
#   `pnpm db:types` を実行し忘れた場合に失敗する（Web / API の型契約のずれ防止）。
#
#   Prettier（node_modules/.bin/prettier）があれば両方を整形してから比較するため、
#   整形の有無による差分は無視される。無ければ生成物そのものと比較する。
#
# 使い方:
#   pnpm db:start && scripts/check-db-types.sh
#   SUPABASE_BIN=<cmd>  supabase CLI のパス（既定: supabase）
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prettier は一時ディレクトリのファイルを整形するため、.prettierrc.json の相対パス
# （tailwindStylesheet: ./apps/web/app/globals.css）とプラグインをカレントディレクトリから解決する。
# どこから実行しても同じ結果になるよう、リポジトリルートに移動しておく。
cd "$ROOT_DIR"
SUPABASE_BIN="${SUPABASE_BIN:-supabase}"
TYPES_FILE="${ROOT_DIR}/packages/shared/src/database.types.ts"
PRETTIER="${ROOT_DIR}/node_modules/.bin/prettier"

command -v "$SUPABASE_BIN" >/dev/null 2>&1 || {
  echo "error: supabase CLI が見つかりません" >&2
  exit 2
}
[[ -f "$TYPES_FILE" ]] || {
  echo "error: ${TYPES_FILE} がありません" >&2
  exit 2
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

if ! "$SUPABASE_BIN" gen types typescript --local --workdir "${ROOT_DIR}/infra" --schema public \
  >"${tmp_dir}/generated.ts" 2>"${tmp_dir}/gen.log"; then
  cat "${tmp_dir}/gen.log" >&2
  echo "error: 型の生成に失敗しました。'pnpm db:start' で Supabase を起動してください" >&2
  exit 2
fi
cp "$TYPES_FILE" "${tmp_dir}/committed.ts"

if [[ -x "$PRETTIER" ]]; then
  "$PRETTIER" --config "${ROOT_DIR}/.prettierrc.json" --log-level warn --write \
    "${tmp_dir}/generated.ts" "${tmp_dir}/committed.ts"
  mode="Prettier 整形後"
else
  mode="生成物そのまま（Prettier 未インストール）"
fi

if diff -u --label committed/database.types.ts --label generated/database.types.ts \
  "${tmp_dir}/committed.ts" "${tmp_dir}/generated.ts"; then
  echo "OK: database.types.ts はスキーマと一致しています（${mode}で比較）"
else
  echo
  echo "error: packages/shared/src/database.types.ts がスキーマと一致しません（${mode}で比較）。" >&2
  echo "       'pnpm db:types' を実行して再生成し、コミットしてください。" >&2
  exit 1
fi
