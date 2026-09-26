#!/usr/bin/env bash
# =============================================================================
# scripts/check-scope.sh — スコープ外機能の混入チェック（受け入れ基準 A16 / 開発依頼書 §12・H2・H3）
#
#   apps/ と packages/ のうち、コミットされ得るファイル（git ls-files -co --exclude-standard）を走査し、
#   本 MVP で「絶対に実装しない」機能のコード・依存・画面が見つかれば終了コード 1 で失敗する。
#
#   カテゴリ:
#     payment       決済 SDK / 決済 API / カード入力 / 購入・課金の UI 文言（H3）
#     image-gen     画像生成（Stable Diffusion / SDXL / Flux / DALL·E / ComfyUI ...）
#     tts-voice     音声合成・TTS・音声通話（ElevenLabs / speechSynthesis / getUserMedia ...）
#     user-posting  ユーザーによる投稿作成（ファイル入力、posts への書き込み、投稿作成ルート）（H2）
#     notification  プッシュ通知（Push API / web-push）
#     admin-ui      管理画面ルート
#
#   許可（許可した行はすべて一覧表示される。レビューで理由を確認すること）:
#     - 仕様で定められたプレースホルダ文言「購入する（準備中）」「課金機能は現在準備中です」
#     - 行に `scope-check: allow` と書かれた行（例: スコープ外であることを説明するコメント）
#     - payment の「購入・課金の文言」ルールだけに効く、E1 / E2（課金と好感度・関係の結びつけの禁止）の
#       コンプライアンス上の例外（決済 SDK・決済処理のコード・カード入力のルールには効かない）:
#       a. 許可リスト scripts/check-scope-allowlist.txt に理由付きで載っているファイル
#          （禁止を実装・検査するコード: OutputGuard の検出語、E1 の構造テスト、ガードの回帰テスト など）
#       b. 禁止を述べる行: 購入・課金の語の「後ろ」に同じ行で禁止の言い回し（結びつけない / 勧めない /
#          触れない / 使わない / 禁止 …。PROHIBITION_RE）がある行。apps/api と packages（プロンプトの
#          テンプレート・コメント）だけに適用し、利用者に見せる画面の文言（apps/web）には適用しない
#   除外:
#     - Markdown（ドキュメント）、ロックファイル、モデレーションの禁止語リスト、このスクリプト自身
#
# 使い方:
#   scripts/check-scope.sh
#
# 終了コード: 0 = 検出なし / 1 = 検出あり（許可リストの古い項目を含む） / 2 = 実行できない（許可リストの書式エラー等）
# 回帰テスト: bash scripts/tests/check-scope.test.sh
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCAN_DIRS=(apps packages)

# 走査しないファイル（git のパスに対する正規表現）
EXCLUDE_RE='(\.md$|\.mdx$|(^|/)docs/|(^|/)(uv\.lock|pnpm-lock\.yaml|package-lock\.json|yarn\.lock)$|^apps/api/app/services/moderation\.py$|^scripts/check-scope\.sh$)'

# 判定前に行から取り除く文字列（許可されたプレースホルダと、既知の誤検知）
NEUTRALIZE=(
  "購入する（準備中）"
  "課金機能は現在準備中です"
  "解決済" # 「決済」を部分文字列として含む
)

ALLOW_MARKER='scope-check:[[:space:]]*allow'

# E1 / E2 のコンプライアンス上の例外（payment の「購入・課金の文言」ルールだけ）
ALLOWLIST_FILE="scripts/check-scope-allowlist.txt"
# 禁止を述べる行とみなす言い回し（購入・課金の語より後ろにあること）。
# 「課金しないなら〜」のような条件の「しない」を拾わないよう、単独の「しない」は含めない
PROHIBITION_RE='(結びつけない|結び付けない|むすびつけない|結びつけず|勧めない|すすめない|促さない|誘導しない|求めない|煽らない|言わない|触れない|触れず|使わない|扱わない|持ち込まない|入れない|含めない|含まない|含まれず|含まれない|受け取らない|参照しない|影響しない|影響させない|作らない|実装しない|禁止|使いません|含まれません|触れません|受け取りません)'
# 禁止を述べる行の例外を適用する範囲（サーバーのコード・プロンプト。画面の文言 apps/web は対象外）
PROHIBITION_SCOPE_RE='^(apps/api|packages)/'
PAYMENT_WORDING_RE='決済|課金|購入|投げ銭|チップを送'

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

# ---------------------------------------------------------------------------
# 走査対象
# ---------------------------------------------------------------------------
FILES=()
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  while IFS= read -r -d '' f; do
    [[ -f "$f" && ! -L "$f" ]] || continue
    [[ "$f" =~ $EXCLUDE_RE ]] && continue
    FILES+=("$f")
  done < <(git ls-files -z -co --exclude-standard -- "${SCAN_DIRS[@]}")
else
  # git 管理外（アーカイブ展開等）の場合は生成物を除外して find する
  while IFS= read -r -d '' f; do
    f="${f#./}"
    [[ "$f" =~ $EXCLUDE_RE ]] && continue
    FILES+=("$f")
  done < <(find "${SCAN_DIRS[@]}" \( -name node_modules -o -name .next -o -name '.next-*' -o -name .venv \
    -o -name dist -o -name build -o -name .turbo -o -name __pycache__ -o -name '.*_cache' \) -prune \
    -o -type f -print0 2>/dev/null)
fi

FINDINGS=0
ALLOWED=0
hits_file="$(mktemp)"
trap 'rm -f "$hits_file"' EXIT

# ---------------------------------------------------------------------------
# 許可リスト（scripts/check-scope-allowlist.txt）
#   1 行 1 項目: `<パスのパターン>  # <理由>`。パターンは bash のパターン（* は / も含めて一致）。
#   理由の無い項目は書式エラー（終了コード 2）。どのファイルにも一致しない項目は古い項目として失敗（1）。
# ---------------------------------------------------------------------------
ALLOW_PATTERNS=()
ALLOW_REASONS=()
ALLOW_LINES=()
if [[ -f "$ALLOWLIST_FILE" ]]; then
  lineno=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    # 行頭の空白を除く。空行・コメント行は読み飛ばす
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    pattern="${line%%#*}"
    pattern="${pattern%"${pattern##*[![:space:]]}"}"
    reason=""
    if [[ "$line" == *"#"* ]]; then
      reason="${line#*#}"
      reason="${reason#"${reason%%[![:space:]]*}"}"
    fi
    if [[ -z "$pattern" || -z "$reason" || "$pattern" == *[[:space:]]* ]]; then
      echo "error: ${ALLOWLIST_FILE}:${lineno}: 「<パス>  # <理由>」の形で書いてください（理由は必須）: ${line}" >&2
      exit 2
    fi
    ALLOW_PATTERNS+=("$pattern")
    ALLOW_REASONS+=("$reason")
    ALLOW_LINES+=("$lineno")
  done <"$ALLOWLIST_FILE"
fi
declare -A ALLOW_USED=()

# パスが許可リストに載っていれば、その項目の番号を返す（無ければ 1 で終了）
allowlist_index() {
  local path="$1" i
  for i in "${!ALLOW_PATTERNS[@]}"; do
    # shellcheck disable=SC2053 # パターンとして比較する（引用しない）
    if [[ "$path" == ${ALLOW_PATTERNS[$i]} ]]; then
      echo "$i"
      return 0
    fi
  done
  return 1
}

# 表示用の抜粋（行頭の空白を除き、先頭 $2 バイト。途中で切れたマルチバイト文字は落とす）
excerpt() {
  local text
  text="$(printf '%s' "$1" | sed -E 's/^[[:space:]]+//' | cut -c1-"$2")"
  if command -v iconv >/dev/null 2>&1; then
    # -c は変換できない（途中で切れた）バイトを落とし、終了コード 1 を返す
    printf '%s' "$text" | iconv -f UTF-8 -t UTF-8 -c 2>/dev/null || true
  else
    printf '%s' "$text"
  fi
}

# 禁止を述べる行か（購入・課金の語の後ろに禁止の言い回しがある。apps/api と packages だけ）
is_prohibition_line() {
  local path="$1" content="$2"
  [[ "$path" =~ $PROHIBITION_SCOPE_RE ]] || return 1
  printf '%s\n' "$content" | grep -q -E -e "(${PAYMENT_WORDING_RE}).*${PROHIBITION_RE}"
}

# ---------------------------------------------------------------------------
# 内容ルール
#   $1: カテゴリ / $2: 説明 / $3: 正規表現（ERE, 大文字小文字を無視）
#   $4: "compliance" なら E1 / E2 のコンプライアンス上の例外（許可リスト・禁止を述べる行）を適用する
# ---------------------------------------------------------------------------
scan() {
  local category="$1" desc="$2" re="$3" compliance="${4:-}"
  local hit path rest lineno content stripped s index rc=0
  [[ ${#FILES[@]} -gt 0 ]] || return 0
  printf '%s\0' "${FILES[@]}" | xargs -0 grep -I -n -H -i -E -e "$re" -- >"$hits_file" || rc=$?
  # grep: 1 = マッチ無し / xargs: 123 = いずれかの grep が 1〜125 で終了（2 = エラーは stderr に出る）
  if [[ $rc -ne 0 && $rc -ne 1 && $rc -ne 123 ]]; then
    echo "error: grep が失敗しました（${category}, 終了コード ${rc}）" >&2
    exit 2
  fi
  while IFS= read -r hit; do
    [[ -n "$hit" ]] || continue
    path="${hit%%:*}"
    rest="${hit#*:}"
    lineno="${rest%%:*}"
    content="${rest#*:}"
    stripped="$content"
    for s in "${NEUTRALIZE[@]}"; do
      stripped="${stripped//"$s"/}"
    done
    printf '%s\n' "$stripped" | grep -q -i -E -e "$re" || continue
    if printf '%s\n' "$content" | grep -q -E -e "$ALLOW_MARKER"; then
      ALLOWED=$((ALLOWED + 1))
      echo "${YELLOW}allow${RESET} ${path}:${lineno}  [${category}] ${desc}（scope-check: allow）"
      continue
    fi
    if [[ "$compliance" == "compliance" ]]; then
      if index="$(allowlist_index "$path")"; then
        ALLOWED=$((ALLOWED + 1))
        ALLOW_USED[$index]=1
        echo "${YELLOW}allow${RESET} ${path}:${lineno}  [${category}] 許可リスト（${ALLOWLIST_FILE}:${ALLOW_LINES[$index]}）: ${ALLOW_REASONS[$index]}"
        continue
      fi
      if is_prohibition_line "$path" "$stripped"; then
        ALLOWED=$((ALLOWED + 1))
        echo "${YELLOW}allow${RESET} ${path}:${lineno}  [${category}] 禁止を述べる行: $(excerpt "$content" 80)"
        continue
      fi
    fi
    FINDINGS=$((FINDINGS + 1))
    echo "${RED}✗${RESET} ${path}:${lineno}  [${category}] ${desc}"
    echo "      $(excerpt "$content" 160)"
  done <"$hits_file"
}

# ---------------------------------------------------------------------------
# パスルール（画面ルート・API ルーター）
# ---------------------------------------------------------------------------
scan_paths() {
  local category="$1" desc="$2" re="$3" f
  for f in "${FILES[@]}"; do
    if [[ "$f" =~ $re ]]; then
      FINDINGS=$((FINDINGS + 1))
      echo "${RED}✗${RESET} ${f}  [${category}] ${desc}"
    fi
  done
}

echo "スコープチェック: ${#FILES[@]} ファイルを走査します（${SCAN_DIRS[*]}）"

# ---- 決済（H3: 課金処理・トークン購入・カード入力は一切作らない）
scan payment "決済 SDK / 決済サービス" \
  '(^|[^a-z0-9])(stripe|@stripe/|paypal|braintree|adyen|payjp|pay\.jp|komoju|squareup|square-web-payments|lemonsqueezy|paddle\.com|revenuecat|gmo-?pg|paygent|sbpayment)([^a-z0-9]|$)'
scan payment "決済処理のコード" \
  '(^|[^a-z0-9])(checkout|payment_?intents?|payment-intents?|create_?payment|process_?payment|payment_?method|payment-request|paymentrequest|charge_?card|purchase_?tokens?|buy_?tokens?|token_?purchase)([^a-z0-9]|$)|/v1/charges'
scan payment "カード情報の入力" \
  'cc-(number|exp|exp-month|exp-year|csc|name|type)|credit[ _-]?card|card_?number|(^|[^a-z0-9])(cvc|cvv)([^a-z0-9]|$)|クレジットカード|カード番号|セキュリティコード'
scan payment "購入・課金の文言（許可されたプレースホルダ以外）" "$PAYMENT_WORDING_RE" compliance

# ---- 画像生成
scan image-gen "画像生成" \
  'stable[-_ ]?diffusion|(^|[^a-z0-9])(sdxl|flux|diffusers|comfy[-_ ]?ui|dall[-_ ]?e|midjourney|txt2img|img2img|automatic1111|novelai)([^a-z0-9]|$)|/images/generations|images\.generate|replicate\.(com|run)|fal\.ai|画像生成'

# ---- 音声生成・TTS・音声通話
scan tts-voice "音声合成 / TTS / 音声通話" \
  '(^|[^a-z0-9])(tts|elevenlabs|voicevox|coeiroink|coqui|polly)([^a-z0-9]|$)|text[-_ ]?to[-_ ]?speech|speech[-_ ]?synthesis|SpeechSynthesisUtterance|/audio/speech|audio\.speech|audio[-_ ]?generation|generate[-_]?audio|getUserMedia|MediaRecorder|音声合成|音声生成|音声通話|読み上げ機能'

# ---- ユーザー投稿（H2: 投稿できるのは AI キャラクターのみ）
scan user-posting "ファイル選択・画像アップロード UI" \
  'type=[{"'"'"' ]*file|accept=[{"'"'"' ]*image/|UploadFile|multipart/form-data|\.storage[[:space:]]*\.from\(|supabase\.storage'
scan user-posting "クライアント / API からの posts への書き込み" \
  'from\([[:space:]]*["'"'"'`]posts["'"'"'`][[:space:]]*\)[[:space:]]*\.(insert|upsert|update|delete)|@(router|app)\.(post|put|patch)\([[:space:]]*["'"'"']/posts'
scan_paths user-posting "投稿作成・アップロード用の画面ルート" \
  '^apps/web/(src/)?app/(.*/)?(new|create|upload|uploads|compose|post-new|new-post|create-post)(/|$)'
scan_paths user-posting "投稿作成・決済・生成系の API ルーター" \
  '^apps/api/app/routers/(posts|uploads?|payments?|billing|checkout|purchases?|images?|tts|voice|audio)\.py$'

# ---- 通知（§12）
scan notification "プッシュ通知" \
  'Notification\.requestPermission|pushManager|PushSubscription|web-push|addEventListener\([[:space:]]*["'"'"']push["'"'"']|firebase-messaging|onesignal'

# ---- 管理画面（§12）
scan_paths admin-ui "管理画面ルート" '^apps/web/(src/)?app/(.*/)?admin(/|$)'

# ---- 許可リストの古い項目（どのファイルにも一致しない）は失敗、文言の検出が無い項目は警告
for i in "${!ALLOW_PATTERNS[@]}"; do
  matched=0
  for f in "${FILES[@]}"; do
    # shellcheck disable=SC2053 # パターンとして比較する（引用しない）
    if [[ "$f" == ${ALLOW_PATTERNS[$i]} ]]; then
      matched=1
      break
    fi
  done
  if [[ $matched -eq 0 ]]; then
    FINDINGS=$((FINDINGS + 1))
    echo "${RED}✗${RESET} ${ALLOWLIST_FILE}:${ALLOW_LINES[$i]}  許可リストの項目「${ALLOW_PATTERNS[$i]}」に一致するファイルがありません（古い項目は削除する）"
  elif [[ -z "${ALLOW_USED[$i]:-}" ]]; then
    echo "${YELLOW}warn${RESET} ${ALLOWLIST_FILE}:${ALLOW_LINES[$i]}  許可リストの項目「${ALLOW_PATTERNS[$i]}」は使われていません（不要なら削除する）"
  fi
done

echo
if [[ $ALLOWED -gt 0 ]]; then
  echo "${YELLOW}${ALLOWED} 行を許可しました（scope-check: allow / 許可リスト / 禁止を述べる行。理由をレビューで確認すること）${RESET}"
fi
if [[ $FINDINGS -gt 0 ]]; then
  echo "${RED}${FINDINGS} 件のスコープ外機能の疑いが見つかりました。${RESET}"
  echo "決済・画像生成・TTS・ユーザー投稿・通知・管理画面は本 MVP では実装しません（開発依頼書 §12）。"
  echo "誤検知（説明コメント等）の場合は、その行に 'scope-check: allow' と理由を書いてください"
  echo "（E1 / E2 の禁止を実装・検査するファイルは ${ALLOWLIST_FILE} に理由付きで載せる）。"
  exit 1
fi
echo "${GREEN}OK${RESET}: スコープ外機能は見つかりませんでした"
