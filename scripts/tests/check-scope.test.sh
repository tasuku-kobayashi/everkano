#!/usr/bin/env bash
# =============================================================================
# scripts/tests/check-scope.test.sh — scripts/check-scope.sh の回帰テスト
#
#   一時ディレクトリに使い捨ての git リポジトリを作り、check-scope.sh と許可リストの書式だけを持ち込んで実行する。
#   主に E1 / E2 のコンプライアンス上の例外（payment の文言ルールだけに効く）を検証する:
#     - 許可リストに載ったファイルの購入・課金の文言は許可される
#     - 許可リストに載ったファイルでも、決済 SDK・決済処理のコード・カード入力は検出される
#     - 禁止を述べる行（「〜と結びつけない」）は apps/api / packages では許可、画面の文言（apps/web）では検出
#     - 条件の「しない」（「課金しないと見られません」）は禁止を述べる行とみなさない
#     - 本物の課金 UI の文言は検出される。仕様のプレースホルダと `scope-check: allow` は従来どおり許可
#     - 理由の無い項目は書式エラー（2）、どのファイルにも一致しない項目は失敗（1）
#
# 使い方: bash scripts/tests/check-scope.test.sh
# =============================================================================
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-scope.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

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

# run → OUT / RC にセット
run() {
  RC=0
  OUT="$(bash "$REPO/scripts/check-scope.sh" 2>&1)" || RC=$?
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
# フィクスチャ: 使い捨てリポジトリ（未追跡のファイルも走査対象）
# ---------------------------------------------------------------------------
REPO="$TMP/repo"
mkdir -p "$REPO/scripts" "$REPO/apps/api/app/safety" "$REPO/apps/api/tests" "$REPO/apps/web/components" \
  "$REPO/packages/prompts/templates"
cp "$SCRIPT" "$REPO/scripts/check-scope.sh"
git -C "$REPO" init -q -b main

write_allowlist() {
  cat >"$REPO/scripts/check-scope-allowlist.txt" <<'EOF'
# テスト用の許可リスト
apps/api/app/safety/guard.py  # E2 の検出語のリスト
apps/api/tests/test_guard*.py  # E2 の回帰テスト（違反文の例）
EOF
}
write_allowlist

# 許可リストに載ったファイル（検出語のリスト・違反文の例）
printf '%s\n' '_COMMERCE = r"課金|購入|投げ銭"  # E2 の検出語' >"$REPO/apps/api/app/safety/guard.py"
printf '%s\n' 'CASES = ["課金してくれたら許してあげる", "課金しないならもう口きかないから"]' \
  >"$REPO/apps/api/tests/test_guard_e2.py"
# 禁止を述べる行（プロンプトのテンプレート・サーバーのコメント）
printf '%s\n' '- 購入・課金を、好意・仲直り・機嫌と結びつけない。何かを買うよう勧めない。' \
  >"$REPO/packages/prompts/templates/dm_system.ja.txt"
printf '%s\n' '# 忙しさは返答の長さにだけ使う（課金の誘導には使わない）' >"$REPO/apps/api/app/state.py"
# 仕様のプレースホルダと、従来の scope-check: allow
printf '%s\n' '<button>購入する（準備中）</button>' '<p>課金機能は現在準備中です</p>' \
  >"$REPO/apps/web/components/paid-lock.tsx"
printf '%s\n' '// 投げ銭は MVP の対象外  scope-check: allow（スコープ外の説明）' >"$REPO/apps/web/components/note.ts"

# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------
run
expect_rc "許可リスト・禁止を述べる行・プレースホルダ・allow マーカーだけなら成功" 0
expect_out "許可リストで許可した行は理由付きで表示する" \
  "allow apps/api/app/safety/guard\\.py:1  \\[payment\\] 許可リスト（scripts/check-scope-allowlist\\.txt:2）: E2 の検出語のリスト"
expect_out "許可リストのパターン（*）で回帰テストを許可する" "allow apps/api/tests/test_guard_e2\\.py:1"
expect_out "テンプレートの禁止を述べる行は許可して表示する" \
  "allow packages/prompts/templates/dm_system\\.ja\\.txt:1  \\[payment\\] 禁止を述べる行"
expect_out "サーバーのコメントの禁止を述べる行は許可する" "allow apps/api/app/state\\.py:1"
expect_no_out "プレースホルダは検出も許可の表示もしない" "paid-lock\\.tsx"
expect_out "scope-check: allow は従来どおり許可する" "allow apps/web/components/note\\.ts:1"

# 許可リストに載ったファイルでも、決済 SDK・決済処理のコードは検出する（文言ルールだけの例外）
printf '%s\n' 'import stripe' 'stripe.PaymentIntent.create(amount=500)  # 課金' >>"$REPO/apps/api/app/safety/guard.py"
run
expect_rc "許可リストのファイルに決済 SDK が入れば失敗" 1
expect_out "決済 SDK を検出する" "✗ apps/api/app/safety/guard\\.py:2  \\[payment\\] 決済 SDK"
expect_out "決済処理のコードを検出する" "✗ apps/api/app/safety/guard\\.py:3  \\[payment\\] 決済処理のコード"
expect_no_out "同じファイルの文言は許可リストで許可されたまま" "✗ apps/api/app/safety/guard\\.py:[0-9]+  \\[payment\\] 購入・課金の文言"
printf '%s\n' '_COMMERCE = r"課金|購入|投げ銭"  # E2 の検出語' >"$REPO/apps/api/app/safety/guard.py"

# 画面の文言（apps/web）の購入・課金は、禁止の言い回しがあっても検出する
printf '%s\n' '<p>未成年の課金は禁止されています</p>' '<button>課金する</button>' \
  >"$REPO/apps/web/components/paywall.tsx"
# 条件の「しない」は禁止を述べる行とみなさない（テンプレートでも検出する）
printf '%s\n' '続きは課金しないと見られません' >"$REPO/packages/prompts/templates/paywall.ja.txt"
# 許可リストに無いサーバーのコードの課金の文言は検出する
printf '%s\n' 'MESSAGE = "トークンを購入してください"' >"$REPO/apps/api/app/tokens.py"
run
expect_rc "本物の課金の文言は検出する" 1
expect_out "画面の文言は禁止の言い回しがあっても検出する" "✗ apps/web/components/paywall\\.tsx:1  \\[payment\\]"
expect_out "画面の課金ボタンを検出する" "✗ apps/web/components/paywall\\.tsx:2  \\[payment\\]"
expect_out "条件の「しない」は禁止を述べる行とみなさない" "✗ packages/prompts/templates/paywall\\.ja\\.txt:1  \\[payment\\]"
expect_out "許可リストに無いファイルの購入の文言を検出する" "✗ apps/api/app/tokens\\.py:1  \\[payment\\]"
rm "$REPO/apps/web/components/paywall.tsx" "$REPO/packages/prompts/templates/paywall.ja.txt" "$REPO/apps/api/app/tokens.py"

# 禁止の言い回しが購入・課金の語より前にあるだけの行は、禁止を述べる行とみなさない
printf '%s\n' '# 禁止: なし。ここで課金する' >"$REPO/apps/api/app/order.py"
run
expect_rc "禁止の言い回しが前にあるだけの行は検出する" 1
expect_out "語順を見て判定する" "✗ apps/api/app/order\\.py:1  \\[payment\\]"
rm "$REPO/apps/api/app/order.py"

# 許可リストの古い項目（どのファイルにも一致しない）は失敗
printf '%s\n' 'apps/api/app/removed.py  # 削除したファイル' >>"$REPO/scripts/check-scope-allowlist.txt"
run
expect_rc "許可リストの古い項目は失敗" 1
expect_out "古い項目の行を表示する" "scripts/check-scope-allowlist\\.txt:4  許可リストの項目「apps/api/app/removed\\.py」に一致するファイルがありません"
write_allowlist

# 理由の無い項目は書式エラー
printf '%s\n' 'apps/api/app/state.py' >>"$REPO/scripts/check-scope-allowlist.txt"
run
expect_rc "理由の無い項目は実行エラー" 2
expect_out "書式エラーの行を表示する" "scripts/check-scope-allowlist\\.txt:4: 「<パス>  # <理由>」"
write_allowlist

# 許可リストが無ければ、検出語のリストも検出する（例外は明示した分だけ）
rm "$REPO/scripts/check-scope-allowlist.txt"
run
expect_rc "許可リストが無ければ検出語のリストも検出する" 1
expect_out "許可リストが無いときの検出" "✗ apps/api/app/safety/guard\\.py:1  \\[payment\\]"
write_allowlist

run
expect_rc "元に戻せば成功" 0

echo
echo "${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
