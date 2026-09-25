#!/usr/bin/env bash
# =============================================================================
# scripts/check-secrets.sh — シークレット混入チェック（受け入れ基準 A14 / ハードルール H7）
#
#   git 管理下のファイル + 未追跡かつ .gitignore されていないファイル
#   （= `git ls-files -co --exclude-standard`、つまりコミットされ得るもの全部）を走査し、
#   シークレットらしき文字列・ファイルが見つかれば終了コード 1 で失敗する。
#
# 検出するもの:
#   - 秘密鍵ブロック（-----BEGIN ... PRIVATE KEY-----）
#   - sk- 形式の API キー（OpenAI / OpenRouter / DeepSeek 等）
#   - Supabase の secret key（sb_secret_...）と JWT（service_role / anon を問わず。role を表示）
#   - AWS / GitHub / Slack / Google / Stripe の既知形式のキー、Sentry DSN
#   - Bunny.net / Backblaze B2 のキー・トークンへの非空の代入
#   - SECRET / PASSWORD / API_KEY 等の名前への文字列リテラル代入（プレースホルダは除外）
#   - ローカル以外のホストを指すパスワード付き DB 接続文字列
#   - .env / .env.local 等のファイル自体（.env.example 以外）、秘密鍵・証明書ファイル
#   - .env.example: キー・シークレット系の変数に値が入っていないこと、URL の認証情報がローカル既定値のみであること
#
# 許可（誤検知の抑制）:
#   テストコード（tests/ __tests__/ fixtures/ *.test.* *.spec.* test_*.py 等）の行末に
#   `check-secrets: allow` と書いた行は、テスト用のダミー値として許可する（理由も併記すること）。
#   テスト以外のファイルではこのマーカーは無視される。
#
# 使い方:
#   scripts/check-secrets.sh                          # 作業ツリー（コミットされ得るファイル全部）
#   scripts/check-secrets.sh --history               # HEAD までの全コミットに含まれた全ファイル版
#   scripts/check-secrets.sh --history origin/main..HEAD   # 指定範囲のコミットで追加されたファイル版だけ
#   出力にはシークレットの値そのものは表示しない（CI ログへの二次漏洩防止）。
#
# --history: 作業ツリーだけを見ると「あるコミットで追加し、次のコミットで消した」シークレットが
#   すり抜ける（履歴には残る）。このモードでは `git rev-list --objects <範囲>` の全ファイル版（blob）を
#   取り出して同じルールで走査し、見つかった場合は「パス @ そのファイル版を最初に含んだコミット」を表示する。
#   <範囲> は git rev-list の引数（既定: HEAD）。shallow clone では不完全になるため実行しない（終了コード 2）。
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SELF="scripts/check-secrets.sh"

if [[ -t 1 ]]; then
  RED=$'\033[31m'
  GREEN=$'\033[32m'
  RESET=$'\033[0m'
else
  RED=""
  GREEN=""
  RESET=""
fi

usage() {
  sed -n '3,/^# =====/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'
}

MODE=tree
REVS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --history)
      MODE=history
      shift
      # 残りの引数はすべて git rev-list に渡すリビジョン（範囲）として扱う
      REVS=("$@")
      break
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "error: 不明な引数: $1（--help を参照）" >&2
      exit 2
      ;;
  esac
done

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "error: git リポジトリ内で実行してください" >&2
  exit 2
}

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
hits_file="$TMP_ROOT/hits"

# ---------------------------------------------------------------------------
# 走査対象
#   tree    : FILES = 作業ツリーの相対パス / ALL_PATHS = コミットされ得る全パス
#   history : FILES = "$TMP_ROOT/blobs" 配下に書き出した "<blob の SHA>/<元のパス>"（cwd をそこへ移す）
#             ALL_PATHS = 範囲内のコミットに現れた全パス（ファイル名ルール用）
# ---------------------------------------------------------------------------
FILES=()
ALL_PATHS=()
if [[ "$MODE" == tree ]]; then
  while IFS= read -r -d '' f; do
    ALL_PATHS+=("$f")
    [[ -f "$f" && ! -L "$f" ]] || continue
    [[ "$f" == "$SELF" ]] && continue
    FILES+=("$f")
  done < <(git ls-files -z -co --exclude-standard)
else
  [[ ${#REVS[@]} -gt 0 ]] || REVS=(HEAD)
  if [[ "$(git rev-parse --is-shallow-repository)" == "true" ]]; then
    echo "error: shallow clone では履歴を走査できません（git fetch --unshallow / actions/checkout の fetch-depth: 0）" >&2
    exit 2
  fi
  if ! COMMIT_COUNT="$(git rev-list --count "${REVS[@]}" 2>/dev/null)"; then
    echo "error: リビジョンを解決できません: ${REVS[*]}" >&2
    exit 2
  fi
  BLOB_DIR="$TMP_ROOT/blobs"
  mkdir -p "$BLOB_DIR"
  git rev-list --objects "${REVS[@]}" >"$TMP_ROOT/objects"
  while IFS=' ' read -r type sha path; do
    [[ "$type" == blob && -n "$path" ]] || continue
    [[ "$path" == "$SELF" ]] && continue
    dest="$BLOB_DIR/$sha/$path"
    mkdir -p "${dest%/*}"
    git cat-file blob "$sha" >"$dest"
    FILES+=("$sha/$path")
  done < <(git cat-file --batch-check='%(objecttype) %(objectname) %(rest)' <"$TMP_ROOT/objects")
  while IFS= read -r -d '' f; do
    [[ -n "$f" ]] && ALL_PATHS+=("$f")
  done < <(git log -z --format= --name-only --no-renames -m --diff-filter=d "${REVS[@]}" | sort -z -u)
  cd "$BLOB_DIR"
fi

# history モードの表示名: "<blob>/<path>" → "<path> @ <そのファイル版を最初に含んだコミット>"
display_path() {
  local blob rel commit
  if [[ "$MODE" == history && "$1" =~ ^([0-9a-f]{40}|[0-9a-f]{64})/(.+)$ ]]; then
    blob="${BASH_REMATCH[1]}"
    rel="${BASH_REMATCH[2]}"
    commit="$(git -C "$ROOT_DIR" log --format=%h --find-object="$blob" "${REVS[@]}" -- 2>/dev/null | tail -n 1 || true)"
    printf '%s @ %s' "$rel" "${commit:-blob ${blob:0:12}}"
  else
    printf '%s' "$1"
  fi
}

FINDINGS=0
report() { # path line rule message
  FINDINGS=$((FINDINGS + 1))
  local where
  where="$(display_path "$1")"
  if [[ -n "$2" ]]; then
    echo "${RED}✗${RESET} ${where}:$2  [$3] $4"
  else
    echo "${RED}✗${RESET} ${where}  [$3] $4"
  fi
}

# テストコードかどうか（allow マーカーを受け付けるパス）
is_test_path() {
  [[ "$1" =~ (^|/)(tests?|__tests__|__mocks__|fixtures?|e2e)/ ]] ||
    [[ "$1" =~ \.(test|spec)\.[A-Za-z0-9]+$ ]] ||
    [[ "$1" =~ (^|/)test_[^/]*\.py$ ]] ||
    [[ "$1" =~ _test\.py$ ]] ||
    [[ "$1" =~ (^|/)conftest\.py$ ]]
}

ALLOW_MARKER='check-secrets:[[:space:]]*allow'

# プレースホルダ・参照とみなす値（汎用ルールのみに適用）
PLACEHOLDER_RE='dummy|example|placeholder|changeme|change-me|your[-_]|xxxxxx|\*\*\*\*|<[A-Za-z_ -]+>|redacted|not-a-real|fake|test|sample|env\(|process\.env|os\.environ|os\.getenv|import\.meta\.env|settings\.|secrets\.'

# ローカル既定値とみなす DB ホスト
LOCAL_DB_HOST_RE='@(127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0|host\.docker\.internal|db|postgres|supabase_db_[A-Za-z0-9_-]*)([:/"'"'"'[:space:]]|$)'

# grep で候補行を $hits_file に書き出す。形式: path:line:content
grep_candidates() { # [-i] regex
  local flags=(-I -n -H -E) rc=0
  if [[ "$1" == "-i" ]]; then
    flags+=(-i)
    shift
  fi
  : >"$hits_file"
  [[ ${#FILES[@]} -gt 0 ]] || return 0
  printf '%s\0' "${FILES[@]}" | xargs -0 grep "${flags[@]}" -e "$1" -- >"$hits_file" || rc=$?
  # grep: 1 = マッチ無し / xargs: 123 = いずれかの grep が 1〜125 で終了（2 = エラーは stderr に出る）
  if [[ $rc -ne 0 && $rc -ne 1 && $rc -ne 123 ]]; then
    echo "error: grep が失敗しました（終了コード ${rc}）" >&2
    exit 2
  fi
}

# 1 ルールを適用する
#   $1: rule 名 / $2: メッセージ / $3: 正規表現 / $4: 除外フィルタ（行に対する正規表現, 空可）
#   $5: -i（大文字小文字を無視）or "" / $6: skip-tests（テストコードを対象外にする。ヒューリスティックなルール用）
scan() {
  local rule="$1" msg="$2" re="$3" ignore="${4:-}" icase="${5:-}" skip_tests="${6:-}"
  local hit path rest lineno content
  grep_candidates ${icase:+"$icase"} "$re"
  while IFS= read -r hit; do
    [[ -n "$hit" ]] || continue
    path="${hit%%:*}"
    rest="${hit#*:}"
    lineno="${rest%%:*}"
    content="${rest#*:}"
    if [[ -n "$skip_tests" ]] && is_test_path "$path"; then
      continue
    fi
    if [[ -n "$ignore" ]] && printf '%s\n' "$content" | grep -q -i -E -e "$ignore"; then
      continue
    fi
    if is_test_path "$path" && printf '%s\n' "$content" | grep -q -E -e "$ALLOW_MARKER"; then
      continue
    fi
    report "$path" "$lineno" "$rule" "$msg"
  done <"$hits_file"
}

# JWT: payload の role を表示して報告する
scan_jwt() {
  local re='eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'
  local hit path rest lineno content token payload role pad
  grep_candidates "$re"
  while IFS= read -r hit; do
    [[ -n "$hit" ]] || continue
    path="${hit%%:*}"
    rest="${hit#*:}"
    lineno="${rest%%:*}"
    content="${rest#*:}"
    if is_test_path "$path" && printf '%s\n' "$content" | grep -q -E -e "$ALLOW_MARKER"; then
      continue
    fi
    while IFS= read -r token; do
      payload="$(printf '%s' "$token" | cut -d. -f2 | tr '_-' '/+')"
      pad=$(((4 - ${#payload} % 4) % 4))
      payload="${payload}$(printf '%*s' "$pad" '' | tr ' ' '=')"
      role="$(printf '%s' "$payload" | { base64 --decode 2>/dev/null || true; } |
        grep -o -E '"role"[[:space:]]*:[[:space:]]*"[A-Za-z_]+"' | head -n1 | sed -E 's/.*"([A-Za-z_]+)"$/\1/' || true)"
      case "$role" in
        service_role | supabase_admin)
          report "$path" "$lineno" "supabase-service-role-jwt" "service_role の JWT（RLS をバイパスする鍵）" ;;
        "")
          report "$path" "$lineno" "jwt" "JWT（アクセストークン/API キー）がハードコードされています" ;;
        *)
          report "$path" "$lineno" "jwt" "JWT（role=${role}）がハードコードされています。env から読み込むこと" ;;
      esac
    done < <(printf '%s\n' "$content" | grep -o -E -e "$re")
  done <"$hits_file"
}

if [[ "$MODE" == history ]]; then
  echo "シークレットチェック（履歴: ${REVS[*]}）: ${COMMIT_COUNT} コミット / ${#FILES[@]} ファイル版を走査します"
else
  echo "シークレットチェック: ${#FILES[@]} ファイルを走査します"
fi

# history モード: そのパスを最初に追加したコミット付きの表示名
history_path_label() {
  local commit
  commit="$(git -C "$ROOT_DIR" log --format=%h --diff-filter=A "${REVS[@]}" -- "$1" 2>/dev/null | tail -n 1 || true)"
  printf '%s @ %s' "$1" "${commit:-?}"
}

# ---------------------------------------------------------------------------
# 1. コミットされ得るファイル名のチェック
# ---------------------------------------------------------------------------
for f in "${ALL_PATHS[@]}"; do
  base="${f##*/}"
  if [[ "$base" =~ ^\.env($|\.) ]] && [[ ! "$base" =~ ^\.env\.example$ ]]; then
    if [[ "$MODE" == history ]]; then
      report "$(history_path_label "$f")" "" "env-file" ".env ファイルが過去のコミットに含まれています（.env.example 以外は禁止）"
    elif git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      report "$f" "" "env-file" ".env ファイルが git 管理されています（.env.example 以外は禁止）"
    else
      report "$f" "" "env-file" ".env ファイルが .gitignore されていません"
    fi
  fi
  if [[ "$base" =~ \.(pem|key|p12|pfx|jks|keystore|ppk)$ ]] ||
    [[ "$base" =~ ^id_(rsa|dsa|ecdsa|ed25519)$ ]] ||
    [[ "$base" =~ ^(credentials|service[-_]?account.*)\.json$ ]]; then
    if [[ "$MODE" == history ]]; then
      report "$(history_path_label "$f")" "" "key-file" "秘密鍵・証明書・認証情報ファイルが過去のコミットに含まれています"
    else
      report "$f" "" "key-file" "秘密鍵・証明書・認証情報ファイルはコミットしないこと"
    fi
  fi
done

# ---------------------------------------------------------------------------
# 2. 内容のチェック（既知の形式）
# ---------------------------------------------------------------------------
scan "private-key" "秘密鍵ブロック" \
  '-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY( BLOCK)?-----'
scan "sk-api-key" "sk- 形式の API キー（OpenAI / OpenRouter / DeepSeek 等）" \
  '(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}'
scan "supabase-secret-key" "Supabase の secret key（sb_secret_）" \
  'sb_secret_[A-Za-z0-9_-]{8,}'
scan_jwt
scan "aws-access-key" "AWS アクセスキー ID" \
  '(^|[^A-Z0-9])(AKIA|ASIA)[0-9A-Z]{16}([^A-Z0-9]|$)'
scan "aws-secret-key" "AWS シークレットアクセスキー" \
  'aws_?secret_?access_?key["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9/+=]{40}' "" -i
scan "github-token" "GitHub トークン" \
  '(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}'
scan "slack-token" "Slack トークン" \
  'xox[abprs]-[A-Za-z0-9-]{10,}'
scan "google-api-key" "Google API キー" \
  'AIza[0-9A-Za-z_-]{35}'
scan "stripe-key" "Stripe のキー" \
  '(sk|rk)_(live|test)_[A-Za-z0-9]{16,}'
scan "sentry-dsn" "Sentry DSN（env で設定すること）" \
  'https://[0-9a-f]{32}(:[0-9a-f]{32})?@[A-Za-z0-9.-]*sentry\.io'

# ---------------------------------------------------------------------------
# 3. 内容のチェック（代入・接続文字列）
# ---------------------------------------------------------------------------
scan "bunny-b2-key" "Bunny.net / Backblaze B2 のキー・トークンに文字列リテラルが代入されています" \
  '(bunny|b2|backblaze)[A-Za-z0-9_]*(key|token|secret|password|pass)[A-Za-z0-9_]*["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'[:space:]]{8,}["'"'"']' \
  "$PLACEHOLDER_RE" -i skip-tests
scan "bunny-b2-key" "Bunny.net / Backblaze B2 のキー・トークンに値が設定されています（env / YAML / shell）" \
  '^[[:space:]]*(export[[:space:]]+|-[[:space:]]+)?(BUNNY|B2|BACKBLAZE)[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9/+_=-]{8,}(["'"'"'[:space:]]|$)' \
  "$PLACEHOLDER_RE" "" skip-tests
# secret_name（Fly.io の [[files]] 等）はシークレットの「名前」を指定するキーなので対象外
scan "secret-literal" "シークレット名の変数に文字列リテラルが代入されています" \
  '(secret|password|passwd|private_?key|api_?key|auth_?key|access_?key|service_?role_?key|access_?token|auth_?token)[A-Za-z0-9_]*["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'[:space:]]{16,}["'"'"']' \
  "${PLACEHOLDER_RE}|(^|[^A-Za-z0-9_])secret_?name[\"']?[[:space:]]*[:=]" -i skip-tests
scan "secret-assignment" "シークレット名の環境変数に値が設定されています（env / YAML / shell）" \
  '^[[:space:]]*(export[[:space:]]+|-[[:space:]]+)?[A-Z0-9_]*(SECRET|PASSWORD|PRIVATE_KEY|API_KEY|AUTH_KEY|ACCESS_KEY|SERVICE_ROLE_KEY|ACCESS_TOKEN|AUTH_TOKEN|_DSN)[A-Z0-9_]*[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9/+_=.@:-]{8,}(["'"'"'[:space:]]|$)' \
  "$PLACEHOLDER_RE" "" skip-tests
scan "db-url-credentials" "ローカル以外を指すパスワード付き接続文字列" \
  '(postgres(ql)?|mysql|mongodb(\+srv)?|redis|rediss|amqp)://[^:/@[:space:]"'"'"']+:[^@/[:space:]"'"'"']+@' \
  "${LOCAL_DB_HOST_RE}|\\[YOUR-PASSWORD\\]|:password@|:pass@|<password>|\\\$\\{|\\\$[A-Z_]+@"

# ---------------------------------------------------------------------------
# 4. .env.example（ローカル既定値以外の値が入っていないこと）
# ---------------------------------------------------------------------------
for f in "${FILES[@]}"; do
  [[ "${f##*/}" == ".env.example" ]] || continue
  lineno=0
  # shellcheck disable=SC2094 # report() はファイル名を表示するだけで $f に書き込まない
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    name="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"
    value="${value%%[[:space:]]#*}"
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    if [[ "$name" =~ (KEY|SECRET|PASSWORD|TOKEN|DSN|CREDENTIAL) ]] && [[ -n "$value" ]] &&
      [[ ! "$value" =~ ^[0-9]+$ ]]; then
      report "$f" "$lineno" "env-example-value" "${name} に値が入っています（.env.example にはキー名だけを書く）"
    fi
    if [[ "$value" =~ ://[^/@[:space:]]+:[^/@[:space:]]*@([^/:?[:space:]]+) ]]; then
      host="${BASH_REMATCH[1]}"
      case "$host" in
        127.0.0.1 | localhost | "[::1]") ;;
        *) report "$f" "$lineno" "env-example-value" "${name} の URL にローカル以外（${host}）の認証情報があります" ;;
      esac
    fi
  done <"$f"
done

echo
if [[ $FINDINGS -gt 0 ]]; then
  echo "${RED}${FINDINGS} 件の問題が見つかりました。${RESET}"
  echo "シークレットは環境変数（.env.local / apps/api/.env / Vercel / ホストの設定）で渡してください（H7）。"
  echo "テスト用のダミー値であれば、テストファイルの該当行に 'check-secrets: allow'（理由付き）を付けてください。"
  if [[ "$MODE" == history ]]; then
    echo "履歴のシークレットは後のコミットで消しても残ります。本物の鍵なら無効化（ローテーション）し、"
    echo "未マージのブランチならコミットを作り直して（rebase / squash）履歴から取り除いてください。"
  fi
  exit 1
fi
echo "${GREEN}OK${RESET}: シークレットは見つかりませんでした"
