#!/usr/bin/env bash
# =============================================================================
# scripts/test-db.sh — RLS / 権限 / トリガーの pgTAP テストを psql で直接実行する
#
#   `supabase test db` は pg_prove 用の追加 Docker イメージを必要とするため、
#   このスクリプトは psql だけで同じテスト（infra/supabase/tests/database/*.test.sql）を実行し、
#   TAP 出力を検証する。Docker もネットワークも不要。
#
# 使い方:
#   scripts/test-db.sh                         # 全テスト
#   scripts/test-db.sh path/to/05_*.test.sql   # 指定ファイルのみ
#   scripts/test-db.sh -v                      # 成功時も TAP 出力を全部表示
#
# 環境変数:
#   DATABASE_URL   接続先（既定: ローカル Supabase postgresql://postgres:postgres@127.0.0.1:54322/postgres）
#   TEST_DB_ALLOW_REMOTE=1  ローカル以外のホストへの実行を許可する（既定は拒否）
#
# 失敗判定:
#   psql がエラー終了した / "not ok" 行がある（# TODO 付きは除く）/ 計画数と実行数が一致しない
#
# 各テストファイルは begin; ... rollback; で完結しており、pgTAP 拡張の作成を含め
# DB に何も残さない。db reset や DROP は行わない。
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="${ROOT_DIR}/infra/supabase/tests/database"
DEFAULT_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"
DATABASE_URL="${DATABASE_URL:-$DEFAULT_DATABASE_URL}"

VERBOSE=0
FILES=()

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -v | --verbose) VERBOSE=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      echo "未知のオプション: $1" >&2
      usage >&2
      exit 2
      ;;
    *) FILES+=("$1") ;;
  esac
  shift
done

if [[ -t 1 ]]; then
  RED=$'\033[31m'
  GREEN=$'\033[32m'
  YELLOW=$'\033[33m'
  RESET=$'\033[0m'
else
  RED=""
  GREEN=""
  YELLOW=""
  RESET=""
fi

die() {
  echo "${RED}error:${RESET} $*" >&2
  exit 2
}

command -v psql >/dev/null 2>&1 || die "psql が見つかりません（例: sudo apt-get install -y postgresql-client / brew install libpq）"

# ---------------------------------------------------------------------------
# 接続先の安全確認（テストは rollback するが、誤って本番に向けないため）
# ---------------------------------------------------------------------------
db_host="$(printf '%s' "$DATABASE_URL" | sed -E 's#^[a-z]+://([^@/]*@)?(\[[^]]*\]|[^:/?]*).*#\2#')"
case "$db_host" in
  127.0.0.1 | localhost | "[::1]" | "" | host.docker.internal | supabase_db_*) ;;
  *)
    if [[ "${TEST_DB_ALLOW_REMOTE:-0}" != "1" ]]; then
      die "DATABASE_URL のホストがローカルではありません（${db_host}）。意図的なら TEST_DB_ALLOW_REMOTE=1 を指定してください"
    fi
    ;;
esac

export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"
# NOTICE（"extension already exists" 等）を抑制し、警告以上のみ表示
export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"

if ! psql "$DATABASE_URL" -X -q -A -t -c "select 1" >/dev/null 2>&1; then
  die "DB に接続できません（${db_host}）。ローカルなら 'pnpm db:start' で Supabase を起動してください"
fi

pgtap_available="$(psql "$DATABASE_URL" -X -q -A -t -c \
  "select count(*) from pg_available_extensions where name = 'pgtap'")"
[[ "$pgtap_available" == "1" ]] || die "この Postgres には pgtap 拡張がありません（Supabase の Postgres イメージには同梱されています）"

# ---------------------------------------------------------------------------
# テストファイルの収集
# ---------------------------------------------------------------------------
if [[ ${#FILES[@]} -eq 0 ]]; then
  while IFS= read -r f; do
    FILES+=("$f")
  done < <(find "$TEST_DIR" -type f -name '*.test.sql' | LC_ALL=C sort)
fi
[[ ${#FILES[@]} -gt 0 ]] || die "テストファイルが見つかりません: ${TEST_DIR}/*.test.sql"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

total_files=0
failed_files=0
total_tests=0
todo_unexpected=0

for file in "${FILES[@]}"; do
  [[ -f "$file" ]] || die "ファイルがありません: $file"
  total_files=$((total_files + 1))
  name="${file#"$ROOT_DIR"/}"
  out="${tmp_dir}/out.txt"
  err="${tmp_dir}/err.txt"

  rc=0
  psql "$DATABASE_URL" -X -q -A -t -v ON_ERROR_STOP=1 -f "$file" >"$out" 2>"$err" || rc=$?

  planned="$(grep -E '^1\.\.[0-9]+$' "$out" | head -n1 | cut -d. -f3 || true)"
  ran="$(grep -c -E '^(not )?ok [0-9]+' "$out" || true)"
  # TODO 指定のテストは失敗しても許容する（TAP の仕様どおり）
  failures="$(grep -E '^not ok [0-9]+' "$out" | grep -v -i -E '#[[:space:]]*TODO' || true)"
  todo_passed="$(grep -E '^ok [0-9]+.*#[[:space:]]*TODO' "$out" || true)"

  problems=()
  [[ $rc -eq 0 ]] || problems+=("psql が終了コード ${rc} で終了")
  [[ -s "$err" ]] && problems+=("psql が stderr に出力（エラー/警告）")
  [[ -n "$planned" ]] || problems+=("plan 行（1..N）がありません")
  if [[ -n "$planned" && "$planned" != "$ran" ]]; then
    problems+=("計画 ${planned} 件に対して実行 ${ran:-0} 件")
  fi
  [[ -z "$failures" ]] || problems+=("失敗したテストがあります")

  total_tests=$((total_tests + ${ran:-0}))

  if [[ ${#problems[@]} -eq 0 ]]; then
    echo "${GREEN}ok${RESET}    ${name} (${ran} tests)"
    if [[ -n "$todo_passed" ]]; then
      todo_unexpected=1
      echo "${YELLOW}note${RESET}  TODO のテストが成功しています。todo_start/todo_end を外してください:"
      printf '        %s\n' "$todo_passed"
    fi
    [[ $VERBOSE -eq 1 ]] && sed 's/^/        /' "$out"
  else
    failed_files=$((failed_files + 1))
    echo "${RED}FAIL${RESET}  ${name}"
    printf '        - %s\n' "${problems[@]}"
    echo "        ---- stdout ----"
    sed 's/^/        /' "$out"
    if [[ -s "$err" ]]; then
      echo "        ---- stderr ----"
      sed 's/^/        /' "$err"
    fi
  fi
done

echo
if [[ $failed_files -gt 0 ]]; then
  echo "${RED}${failed_files}/${total_files} ファイルが失敗しました${RESET}（${total_tests} tests）"
  exit 1
fi
echo "${GREEN}すべて成功${RESET}: ${total_files} ファイル / ${total_tests} tests"
if [[ $todo_unexpected -eq 1 ]]; then
  echo "${YELLOW}（TODO テストが成功しています。上の note を確認してください）${RESET}"
fi
