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
#   作業ツリーの検査では次も検証する:
#     - Supabase の personal access token（sbp_）/ Fly.io（FlyV1 fm2_）/ Groq（gsk_）/ Hugging Face（hf_）の既知形式
#     - パスワードの短いリテラル（6〜15 文字）は検出し、プレースホルダ（changeme 等）は報告しない
#     - テストコードも名前ベースのルールで走査する。許可は既知のダミー値（KNOWN_TEST_DUMMY_VALUES）と allow マーカーだけで、
#       既知のダミー値もテスト以外のファイルでは報告する
#     - bash の版の検査（4.4 以上）の判定
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

# 既知形式の追加（Supabase PAT / Fly.io / Groq / Hugging Face）とパスワードの短いリテラル。ダミーの値は実行時に組み立てる。
# 変数名は名前ベースのルール（secret-literal / secret-assignment）に掛からないものにして、形式のルールだけを検証する
HEX40="$(printf '0123456789abcdef%.0s' {1..3})"
HEX40="${HEX40:0:40}"
ALNUM54="$(printf 'Zx9%.0s' {1..18})"
FLY_BODY="$(printf 'lJPECAAAAAAAB%.0s' {1..5})"
printf 'SUPABASE_PAT = "sbp_%s"\n' "$HEX40" >"$REPO/app/supa.py"
printf 'FLY_DEPLOY = "FlyV1 fm2_%s"\n' "$FLY_BODY" >"$REPO/app/fly.py"
printf 'GROQ = "gsk_%s"\n' "$ALNUM54" >"$REPO/app/groq.py"
printf 'HF = "hf_%s"\n' "${ALNUM54:0:34}" >"$REPO/app/hf.py"
# パスワードの値も引数で渡す（このファイル自体が password-literal に検出されないように）
printf 'db_password = "%s"\n' 'hunter2xyz' >"$REPO/app/db.py"
printf 'db_password = "%s"\n' 'changeme123' >"$REPO/app/db_placeholder.py"
run "$REPO"
expect_rc "作業ツリーの検査: 既知形式の追加分とパスワードの短いリテラルを検出する" 1
expect_out "Supabase の personal access token（sbp_）を検出する" "app/supa\\.py:1  \\[supabase-access-token\\]"
expect_out "Fly.io のトークン（FlyV1 fm2_）を検出する" "app/fly\\.py:1  \\[fly-token\\]"
expect_out "Groq の API キー（gsk_）を検出する" "app/groq\\.py:1  \\[groq-api-key\\]"
expect_out "Hugging Face のトークン（hf_）を検出する" "app/hf\\.py:1  \\[huggingface-token\\]"
expect_out "パスワードの短いリテラル（16 文字未満）を検出する" "app/db\\.py:1  \\[password-literal\\]"
expect_no_out "プレースホルダ（changeme）のパスワードは報告しない" "app/db_placeholder\\.py"
expect_no_out "出力にトークンの値を含めない" "$HEX40|$FLY_BODY|$ALNUM54"
rm "$REPO/app/supa.py" "$REPO/app/fly.py" "$REPO/app/groq.py" "$REPO/app/hf.py" "$REPO/app/db.py" "$REPO/app/db_placeholder.py"

# テストコードも名前ベースのルールで走査する。許可は既知のダミー値（KNOWN_TEST_DUMMY_VALUES）と allow マーカーだけ
printf 'api_secret = "%s"\n' "$KEY_BODY" >"$REPO/tests/test_settings.py"
printf 'api_secret = "%s"  # check-secrets: allow（テスト用のダミー）\n' "$KEY_BODY" >"$REPO/tests/test_allowed.py"
printf 'const env = { bunnyTokenAuthKey: "secret-key-123" };\n' >"$REPO/tests/media.test.ts"
run "$REPO"
expect_rc "作業ツリーの検査: テストコードのシークレット名への代入も検出する" 1
expect_out "テストコードの secret-literal を報告する" "tests/test_settings\\.py:1  \\[secret-literal\\]"
expect_no_out "テストコードの allow マーカーの行は許可する" "tests/test_allowed\\.py"
expect_no_out "テストコードの既知のダミー値は許可する" "tests/media\\.test\\.ts"
rm "$REPO/tests/test_settings.py" "$REPO/tests/test_allowed.py" "$REPO/tests/media.test.ts"

# 既知のダミー値も allow マーカーも、テスト以外のファイルでは無効
printf 'const env = { bunnyTokenAuthKey: "secret-key-123" };\n' >"$REPO/app/media.ts"
printf 'api_secret = "%s"  # check-secrets: allow\n' "$KEY_BODY" >"$REPO/app/marker.py"
run "$REPO"
expect_rc "作業ツリーの検査: テスト以外のファイルでは既知のダミー値も報告する" 1
expect_out "テスト以外の既知のダミー値を報告する" "app/media\\.ts:1  \\[bunny-b2-key\\]"
expect_out "テスト以外の allow マーカーは無効" "app/marker\\.py:1  \\[secret-literal\\]"
rm "$REPO/app/media.ts" "$REPO/app/marker.py"

# bash の版の検査（4.4 以上）。判定の関数だけを取り出して検証する（古い bash は用意できないため）
eval "$(sed -n '/^bash_version_ok()/,/^}/p' "$SCRIPT")"
if bash_version_ok 4 4 && bash_version_ok 5 2 && ! bash_version_ok 4 3 && ! bash_version_ok 3 2 && ! bash_version_ok x y; then
  ok "bash の版の判定: 4.4 / 5.2 は可、4.3 / 3.2 / 不正な値は不可"
else
  ng "bash の版の判定"
fi

git clone -q --depth 1 "file://$REPO" "$TMP/shallow"
run "$TMP/shallow" --history
expect_rc "--history: shallow clone では実行しない" 2
expect_out "--history: shallow clone の対処を表示する" "fetch-depth: 0"

echo
echo "${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
