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
#   許可:
#     - 仕様で定められたプレースホルダ文言「購入する（準備中）」「課金機能は現在準備中です」
#     - 行に `scope-check: allow` と書かれた行（例: スコープ外であることを説明するコメント）。
#       許可した行は一覧表示されるので、レビューで理由を確認すること。
#   除外:
#     - Markdown（ドキュメント）、ロックファイル、モデレーションの禁止語リスト、このスクリプト自身
#
# 使い方:
#   scripts/check-scope.sh
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
# 内容ルール
#   $1: カテゴリ / $2: 説明 / $3: 正規表現（ERE, 大文字小文字を無視）
# ---------------------------------------------------------------------------
scan() {
  local category="$1" desc="$2" re="$3"
  local hit path rest lineno content stripped s rc=0
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
    FINDINGS=$((FINDINGS + 1))
    echo "${RED}✗${RESET} ${path}:${lineno}  [${category}] ${desc}"
    echo "      $(printf '%s' "$content" | sed -E 's/^[[:space:]]+//' | cut -c1-160)"
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
scan payment "購入・課金の文言（許可されたプレースホルダ以外）" \
  '決済|課金|購入|投げ銭|チップを送'

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

echo
if [[ $ALLOWED -gt 0 ]]; then
  echo "${YELLOW}${ALLOWED} 行を scope-check: allow により許可しました（理由をレビューで確認すること）${RESET}"
fi
if [[ $FINDINGS -gt 0 ]]; then
  echo "${RED}${FINDINGS} 件のスコープ外機能の疑いが見つかりました。${RESET}"
  echo "決済・画像生成・TTS・ユーザー投稿・通知・管理画面は本 MVP では実装しません（開発依頼書 §12）。"
  echo "誤検知（説明コメント等）の場合は、その行に 'scope-check: allow' と理由を書いてください。"
  exit 1
fi
echo "${GREEN}OK${RESET}: スコープ外機能は見つかりませんでした"
