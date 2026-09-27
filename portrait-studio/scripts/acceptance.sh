#!/usr/bin/env bash
# =============================================================================
# scripts/acceptance.sh — runs the API-level acceptance commands (items 3–17 of the prompt) against a running API and
# prints every command with its RAW output. Works against the real stack (GPU) or the mock stack (tools/mock_comfy.py).
#
#   API_URL=http://127.0.0.1:8000 API_KEY=... REF_A=a.png REF_B=b.png LANDSCAPE=l.png scripts/acceptance.sh
#
# REF_A / REF_B: two images of the SAME synthetic face (front / another shot). LANDSCAPE: an image without a face.
# Items 1–2 (torch inside the ComfyUI container, /object_info) are printed by scripts/verify_env.sh.
# =============================================================================
set -uo pipefail
API="${API_URL:-http://127.0.0.1:8000}"
KEY="${API_KEY:?API_KEY is required}"
H=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")
LOG_PATH="${AUDIT_LOG:-}"
FAIL=0
step() { printf '\n### %s\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1; echo; }
expect_code() { # expect_code <expected> <actual>
  if [[ "$1" == "$2" ]]; then echo "→ OK (HTTP $2)"; else echo "→ FAIL (expected HTTP $1, got $2)"; FAIL=1; fi
}
wait_job() { # wait_job <job_id>
  local id="$1" status="" i
  for i in $(seq 1 600); do
    status="$(curl -s "${H[@]}" "$API/api/jobs/$id" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')"
    case "$status" in done|error|canceled) echo "$status"; return 0 ;; esac
    sleep 0.5
  done
  echo "timeout"; return 1
}

step "3. 疎通（torch/cuda バージョンと VRAM が返る）"
run curl -s "$API/api/health"

step "4. 認証が効いている（期待 401）"
code="$(curl -s -o /dev/null -w '%{http_code}' "$API/api/characters")"; echo "$code"; expect_code 401 "$code"

step "8. 参照顔の品質分析"
analyze="$(curl -s "${H[@]:0:2}" -X POST "$API/api/characters/analyze" -F "files=@${REF_A:?REF_A is required}" -F "files=@${REF_B:?REF_B is required}")"
printf '%s\n' "$analyze" | python3 -m json.tool
IMG_A="$(printf '%s' "$analyze" | python3 -c 'import json,sys;print(json.load(sys.stdin)["items"][0]["image_id"])')"

step "9. 顔が無い画像が弾かれること（期待 face_count=0 と使用不可）"
curl -s "${H[@]:0:2}" -X POST "$API/api/characters/analyze" -F "files=@${LANDSCAPE:?LANDSCAPE is required}" | python3 -m json.tool

step "10. 種顔のドラフト生成（期待 202 と job_id）"
draft="$(curl -s -w '\nHTTP %{http_code}' "${H[@]}" -X POST "$API/api/characters/draft" -d '{"prompt":"japanese woman, portrait, natural light","count":4}')"
printf '%s\n' "$draft"
DRAFT_JOB="$(printf '%s' "$draft" | head -1 | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')"
echo "draft job → $(wait_job "$DRAFT_JOB")"

step "11. 合成申告なしでは登録できないこと（期待 400）"
code="$(curl -s -X POST "$API/api/characters" "${H[@]}" -d '{"name":"X","is_synthetic":false,"adult_confirmed":true,"reference_image_ids":["i_1"],"locked":{}}' -o /dev/null -w '%{http_code}')"; echo "$code"; expect_code 400 "$code"

step "登録（受け入れ 12〜16 の前提。参照顔 = 8 の画像）"
created="$(curl -s "${H[@]}" -X POST "$API/api/characters" -d "{\"name\":\"受け入れ花子\",\"is_synthetic\":true,\"adult_confirmed\":true,\"reference_image_ids\":[\"$IMG_A\"],\"primary_image_id\":\"$IMG_A\",\"locked\":{\"face_method\":\"pulid\"}}")"
printf '%s\n' "$created" | python3 -m json.tool | head -40
CID="$(printf '%s' "$created" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
echo "character_id = $CID"

step "5. 成人フラグ必須（期待 400）"
code="$(curl -s -X POST "$API/api/generate" "${H[@]}" -d "{\"character_id\":\"$CID\",\"prompt\":\"test\",\"count\":1}" -o /dev/null -w '%{http_code}')"; echo "$code"; expect_code 400 "$code"
curl -s -X POST "$API/api/generate" "${H[@]}" -d "{\"character_id\":\"$CID\",\"prompt\":\"test\",\"count\":1}"; echo

step "6. locked 保護（allow_locked_override なしで overrides。期待 400 と「この操作はキャラの同一性を変えます」）"
resp="$(curl -s -w '\nHTTP %{http_code}' -X POST "$API/api/generate" "${H[@]}" -d "{\"character_id\":\"$CID\",\"prompt\":\"x\",\"count\":1,\"adult_only\":true,\"overrides\":{\"face_weight\":0.1}}")"
printf '%s\n' "$resp"; expect_code 400 "$(printf '%s' "$resp" | tail -1 | cut -d' ' -f2)"

step "7. 危険な解像度の拒否（12GB 保護。期待 400 と推定 VRAM / 未実測の説明）"
resp="$(curl -s -w '\nHTTP %{http_code}' -X POST "$API/api/generate" "${H[@]}" -d "{\"character_id\":\"$CID\",\"prompt\":\"x\",\"count\":1,\"adult_only\":true,\"width\":2048,\"height\":2048}")"
printf '%s\n' "$resp"; expect_code 400 "$(printf '%s' "$resp" | tail -1 | cut -d' ' -f2)"

step "12. 同一性検証（9 枚。期待 202 と job_id）"
verify="$(curl -s -w '\nHTTP %{http_code}' "${H[@]}" -X POST "$API/api/characters/$CID/verify" -d '{"face_method":"pulid","face_weights":[0.6,0.8,1.0],"scenes":["portrait_closeup","upper_body_cafe","full_body_street"]}')"
printf '%s\n' "$verify"
VJOB="$(printf '%s' "$verify" | head -1 | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')"
echo "verify job → $(wait_job "$VJOB")"
curl -s "${H[@]}" "$API/api/jobs/$VJOB" | python3 -c 'import json,sys;j=json.load(sys.stdin);print(json.dumps({"status":j["status"],"images":len(j["result_image_ids"]),"by_weight":j["result"].get("by_weight"),"best_weight":j["result"].get("best_weight")},ensure_ascii=False))'

step "13. 生成（期待 202 と job_id）"
gen="$(curl -s -w '\nHTTP %{http_code}' -X POST "$API/api/generate" "${H[@]}" -d "{\"character_id\":\"$CID\",\"prompt\":\"standing in a cafe, casual outfit\",\"count\":4,\"adult_only\":true,\"seed\":12345}")"
printf '%s\n' "$gen"
GJOB="$(printf '%s' "$gen" | head -1 | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')"

step "14. ジョブの進捗が取れること（status / progress / result_image_ids）"
curl -s "${H[@]}" "$API/api/jobs/$GJOB" | python3 -m json.tool | head -30
echo "generate job → $(wait_job "$GJOB")"
curl -s "${H[@]}" "$API/api/jobs/$GJOB" | python3 -m json.tool | head -30
IMG1="$(curl -s "${H[@]}" "$API/api/jobs/$GJOB" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result_image_ids"][0])')"

step "15. 別シーンでも同一人物であること（beach, seed 999）→ 類似度で確認"
gen2="$(curl -s "${H[@]}" -X POST "$API/api/generate" -d "{\"character_id\":\"$CID\",\"prompt\":\"at the beach, swimwear\",\"count\":2,\"adult_only\":true,\"seed\":999}")"
printf '%s\n' "$gen2"
G2JOB="$(printf '%s' "$gen2" | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')"
echo "generate job → $(wait_job "$G2JOB")"
curl -s "${H[@]}" "$API/api/images?character_id=$CID&limit=10" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(json.dumps([{"id":i["id"],"seed":i["seed"],"prompt":i["prompt"],"similarity":i["similarity"],"status":i["similarity_status"]} for i in d["items"]],ensure_ascii=False,indent=1))'

step "16. 類似度が計算されていること"
run curl -s "${H[@]}" "$API/api/images/$IMG1/similarity"

step "17. 監査ログ（生成ジョブ数 = draft 1 + verify 1 + generate 2 = 4 行を期待）"
audit="$(curl -s "${H[@]}" "$API/api/audit?limit=5")"
printf '%s' "$audit" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("total_lines:",d["total_lines"],"path:",d["path"]);[print(json.dumps({k:e.get(k) for k in ("endpoint","status","count","face_method","seed","duration_ms")},ensure_ascii=False)) for e in d["items"]]'
if [[ -n "$LOG_PATH" && -f "$LOG_PATH" ]]; then run wc -l "$LOG_PATH"; fi

echo
if [[ $FAIL -eq 0 ]]; then echo "ACCEPTANCE (API 3–17): all expectations met"; else echo "ACCEPTANCE: some expectations FAILED"; fi
exit $FAIL
