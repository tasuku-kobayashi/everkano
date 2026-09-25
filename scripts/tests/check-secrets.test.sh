#!/usr/bin/env bash
# =============================================================================
# scripts/tests/check-secrets.test.sh — scripts/check-secrets.sh の回帰テスト
#
#   一時ディレクトリに使い捨ての git リポジトリを作り、そこへ check-secrets.sh をコピーして実行する。
#   主に --history（履歴の走査）を検証する:
#     - 追加して次のコミットで消したキーは、作業ツリーの検査では見えず、--history では検出される
#     - 範囲指定（<rev>..HEAD）では範囲外のコミットで入ったものを報告しない
#     - 過去にコミットされた .env ファイルを検出する
#     - テストコードの `check-secrets: allow` は履歴でも有効
#     - 出力にシークレットの値を表示しない
#     - shallow clone・解決できないリビジョンは終了コード 2
#
# ダミーのキーは実行時に組み立てる（このファイル自体が check-secrets.sh に検出されないように）。
# 使い方: bash scripts/tests/check-secrets.test.sh
# =============================================================================
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-secrets.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 利用者の git 設定（署名・フック・既定ブランチ等）の影響を受けないようにする
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid

PASS=0
FAIL=0
ok() {
  PASS=$((PASS + 1))
  echo "ok - $1"
}
ng() {
  FAIL=$((FAIL + 1))
  echo "not ok - $1"
  if [[ -n "${2:-}" ]]; then
    printf '%s\n' "$2" | sed 's/^/    | /'
  fi
}

# run <dir> [args...] → OUT / RC にセット
run() {
  local dir="$1"
  shift
  RC=0
  OUT="$(bash "$dir/scripts/check-secrets.sh" "$@" 2>&1)" || RC=$?
}

expect_rc() { # name expected
  if [[ "$RC" -eq "$2" ]]; then ok "$1（終了コード $2）"; else ng "$1（終了コード: 期待 $2 / 実際 $RC）" "$OUT"; fi
}
expect_out() { # name regex
  if printf '%s\n' "$OUT" | grep -q -E -e "$2"; then ok "$1"; else ng "$1（/$2/ が出力に無い）" "$OUT"; fi
}
expect_no_out() { # name regex
  if printf '%s\n' "$OUT" | grep -q -E -e "$2"; then ng "$1（/$2/ が出力にある）" "$OUT"; else ok "$1"; fi
}

# ---------------------------------------------------------------------------
# フィクスチャ: 使い捨てリポジトリ
# ---------------------------------------------------------------------------
REPO="$TMP/repo"
mkdir -p "$REPO/scripts" "$REPO/app" "$REPO/tests"
cp "$SCRIPT" "$REPO/scripts/check-secrets.sh"
git -C "$REPO" init -q -b main

KEY_BODY="$(printf 'Ab3%.0s' {1..12})"
FAKE_KEY="sk-${KEY_BODY}"

commit() { # message
  git -C "$REPO" add -A
  git -C "$REPO" commit -q -m "$1"
  git -C "$REPO" rev-parse --short HEAD
}

echo "# README" >"$REPO/README.md"
BASE="$(commit "init")"

printf 'LLM_KEY = "%s"\n' "$FAKE_KEY" >"$REPO/app/config.py"
C_KEY="$(commit "add key")"

printf 'LLM_KEY = os.environ["LLM_API_KEY"]\n' >"$REPO/app/config.py"
commit "remove key" >/dev/null

printf 'LLM_API_KEY=%s\n' "$FAKE_KEY" >"$REPO/.env"
C_ENV="$(commit "add .env")"
git -C "$REPO" rm -q --cached .env
rm "$REPO/.env"
commit "remove .env" >/dev/null

# テストコードのダミー値（allow マーカー付き）は履歴でも許可される
printf 'TOKEN = "%s"  # check-secrets: allow（テスト用のダミー）\n' "$FAKE_KEY" >"$REPO/tests/test_client.py"
# シークレットの「名前」を指定するキー（Fly.io の [[files]] の secret_name）は代入とみなさない
printf '[[files]]\n  guest_path = "/app/certs/ca.crt"\n  secret_name = "SUPABASE_DB_CA_CERT"\n' >"$REPO/app/fly.toml"
commit "add test" >/dev/null

# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------
run "$REPO"
expect_rc "作業ツリーの検査: 現在のファイルにはシークレットが無い" 0

run "$REPO" --history
expect_rc "--history: 消したキーと .env を検出する" 1
expect_out "--history: キーを追加したコミットとパスを表示する" "app/config\\.py @ ${C_KEY}:1  \\[sk-api-key\\]"
expect_out "--history: 過去にコミットされた .env を表示する" "\\.env @ ${C_ENV}  \\[env-file\\]"
expect_no_out "--history: テストコードの allow マーカーは有効" "tests/test_client\\.py"
expect_no_out "--history: 出力にシークレットの値を含めない" "$KEY_BODY"
expect_out "--history: 走査したコミット数を表示する" "6 コミット"

run "$REPO" --history "${C_KEY}..HEAD"
expect_rc "--history <範囲>: 範囲内の .env は検出する" 1
expect_no_out "--history <範囲>: 範囲外のコミットで入ったキーは報告しない" "app/config\\.py"
expect_out "--history <範囲>: 範囲内の .env を表示する" "\\.env @ ${C_ENV}"

run "$REPO" --history "${BASE}..${BASE}"
expect_rc "--history <空の範囲>: 何も無ければ成功" 0

run "$REPO" --history no-such-revision
expect_rc "--history: 解決できないリビジョンは実行エラー" 2

run "$REPO" --no-such-option
expect_rc "不明な引数は実行エラー" 2

# secret_name の除外で、同じ行でない通常の代入まで見逃さない（未追跡のファイルも作業ツリーの検査対象）
printf 'api_secret = "%s"\n' "$KEY_BODY" >"$REPO/app/settings.py"
run "$REPO"
expect_rc "作業ツリーの検査: シークレット名の変数への文字列代入を検出する" 1
expect_out "作業ツリーの検査: secret-literal として報告する" "app/settings\\.py:1  \\[secret-literal\\]"
expect_no_out "作業ツリーの検査: secret_name の指定は報告しない" "app/fly\\.toml"
rm "$REPO/app/settings.py"

git clone -q --depth 1 "file://$REPO" "$TMP/shallow"
run "$TMP/shallow" --history
expect_rc "--history: shallow clone では実行しない" 2
expect_out "--history: shallow clone の対処を表示する" "fetch-depth: 0"

echo
echo "${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
