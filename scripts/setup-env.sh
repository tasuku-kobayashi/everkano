#!/usr/bin/env bash
# =============================================================================
# scripts/setup-env.sh — ローカル開発用の env ファイルを生成する
#
#   `supabase status --workdir infra -o env` の値で .env.example を埋め、次を生成する:
#     apps/web/.env.local : Web が参照する変数（NEXT_PUBLIC_* / BUNNY_*）
#     apps/api/.env       : API が参照する変数（それ以外）
#
#   埋める値（それ以外は .env.example の既定値のまま）:
#     NEXT_PUBLIC_SUPABASE_URL      ← API_URL
#     NEXT_PUBLIC_SUPABASE_ANON_KEY ← ANON_KEY（無ければ PUBLISHABLE_KEY）
#     SUPABASE_URL                  ← API_URL
#     DATABASE_URL                  ← DB_URL
#   service_role key / secret key / JWT secret は書き出さない（ローカルでも不要）。
#
# 使い方:
#   pnpm db:start && scripts/setup-env.sh
#   scripts/setup-env.sh --force          # 既存ファイルを上書き（元ファイルは *.bak.<日時> に退避）
#
# オプション / 環境変数:
#   --force                 既存ファイルがあっても上書きする（既定は何も書かずに終了コード 1）
#   --status-file <path>    supabase を実行せず、`supabase status -o env` の出力を保存したファイルを読む
#   OUT_DIR=<dir>           出力先のルート（既定: リポジトリルート。テスト用）
#   SUPABASE_BIN=<cmd>      supabase CLI のパス（既定: supabase）
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE_FILE="${ROOT_DIR}/.env.example"
OUT_DIR="${OUT_DIR:-$ROOT_DIR}"
SUPABASE_BIN="${SUPABASE_BIN:-supabase}"

FORCE=0
STATUS_FILE=""

usage() {
  sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() {
  echo "error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f | --force) FORCE=1 ;;
    --status-file)
      [[ $# -ge 2 ]] || die "--status-file にはパスを指定してください"
      STATUS_FILE="$2"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "未知の引数: $1"
      ;;
  esac
  shift
done

[[ -f "$EXAMPLE_FILE" ]] || die ".env.example が見つかりません: $EXAMPLE_FILE"

WEB_ENV="${OUT_DIR}/apps/web/.env.local"
API_ENV="${OUT_DIR}/apps/api/.env"

# ---------------------------------------------------------------------------
# 上書き防止（何か書く前にすべての出力先を確認する）
# ---------------------------------------------------------------------------
if [[ $FORCE -eq 0 ]]; then
  existing=()
  for f in "$WEB_ENV" "$API_ENV"; do
    [[ -e "$f" ]] && existing+=("$f")
  done
  if [[ ${#existing[@]} -gt 0 ]]; then
    printf 'error: 既に存在するため何も書き込みませんでした:\n' >&2
    printf '  %s\n' "${existing[@]}" >&2
    echo "上書きする場合は --force を指定してください（既存ファイルは *.bak.<日時> に退避されます）" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# supabase status の取得
# ---------------------------------------------------------------------------
if [[ -n "$STATUS_FILE" ]]; then
  [[ -f "$STATUS_FILE" ]] || die "--status-file が見つかりません: $STATUS_FILE"
  status_output="$(cat "$STATUS_FILE")"
else
  command -v "$SUPABASE_BIN" >/dev/null 2>&1 ||
    die "supabase CLI が見つかりません（https://supabase.com/docs/guides/local-development/cli/getting-started）"
  if ! status_output="$("$SUPABASE_BIN" status --workdir "${ROOT_DIR}/infra" -o env 2>/dev/null)"; then
    die "supabase status の取得に失敗しました。先に 'pnpm db:start'（supabase start --workdir infra）を実行してください"
  fi
fi

# KEY="value" / KEY=value 形式の行から値を取り出す
status_value() {
  local key="$1" line value
  line="$(printf '%s\n' "$status_output" | grep -E "^${key}=" | head -n1 || true)"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  value="${value%$'\r'}"
  value="${value#\"}"
  value="${value%\"}"
  printf '%s' "$value"
}

API_URL="$(status_value API_URL)"
DB_URL="$(status_value DB_URL)"
ANON_KEY="$(status_value ANON_KEY)"
[[ -n "$ANON_KEY" ]] || ANON_KEY="$(status_value PUBLISHABLE_KEY)"

[[ -n "$API_URL" ]] || die "supabase status に API_URL がありません（Supabase は起動していますか？）"
[[ -n "$DB_URL" ]] || die "supabase status に DB_URL がありません"
[[ -n "$ANON_KEY" ]] || die "supabase status に ANON_KEY / PUBLISHABLE_KEY がありません"

# ---------------------------------------------------------------------------
# .env.example を変換して書き出す
#   $1: 出力先 / $2: 対象 (web|api)
#   各変数の直前のコメントブロックも一緒に出力する（空行でリセット）
# ---------------------------------------------------------------------------
render_env() {
  local target="$1"
  awk -v target="$target" \
    -v api_url="$API_URL" -v db_url="$DB_URL" -v anon_key="$ANON_KEY" '
    function is_web(key) { return key ~ /^NEXT_PUBLIC_/ || key ~ /^BUNNY_/ }
    BEGIN { buf = ""; blank = 0; printed = 0 }
    /^[[:space:]]*$/ { buf = ""; blank = 1; next }
    /^[[:space:]]*#/ { buf = buf $0 "\n"; next }
    /^[A-Za-z_][A-Za-z0-9_]*=/ {
      key = substr($0, 1, index($0, "=") - 1)
      value = substr($0, index($0, "=") + 1)
      selected = (target == "web") ? is_web(key) : !is_web(key)
      if (selected) {
        if (key == "NEXT_PUBLIC_SUPABASE_URL" || key == "SUPABASE_URL") value = api_url
        else if (key == "NEXT_PUBLIC_SUPABASE_ANON_KEY") value = anon_key
        else if (key == "DATABASE_URL") value = db_url
        if (blank && printed) printf "\n"
        printf "%s%s=%s\n", buf, key, value
        printed = 1
        blank = 0
      }
      buf = ""
      next
    }
    { buf = "" }
  ' "$EXAMPLE_FILE"
}

write_env() {
  local dest="$1" target="$2" tmp backup
  mkdir -p "$(dirname "$dest")"
  tmp="$(mktemp "${dest}.tmp.XXXXXX")"
  {
    echo "# ============================================================================="
    echo "# 自動生成: scripts/setup-env.sh（$(date '+%Y-%m-%d %H:%M:%S')）"
    echo "# 元: .env.example + supabase status（ローカル開発用の値のみ）。コミットしないこと（H7）"
    echo "# LLM / Embedding を live にする場合などは、このファイルを直接編集してください。"
    echo "# ============================================================================="
    echo
    render_env "$target"
  } >"$tmp"
  chmod 600 "$tmp"
  if [[ -e "$dest" ]]; then
    backup="${dest}.bak.$(date '+%Y%m%d%H%M%S')"
    mv "$dest" "$backup"
    echo "既存ファイルを退避しました: ${backup}"
  fi
  mv "$tmp" "$dest"
  echo "生成しました: ${dest}"
}

write_env "$WEB_ENV" web
write_env "$API_ENV" api

cat <<EOF

完了しました。
  Supabase API : ${API_URL}
  Postgres     : ${DB_URL}
  LLM_MODE / EMBEDDING_MODE は .env.example の既定（mock / hash）です。
EOF
