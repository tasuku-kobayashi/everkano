# scripts/

リポジトリ全体で使う補助スクリプト。リポジトリのどこから実行しても動く
（スクリプト自身の位置からリポジトリルートを解決する）。CI（`.github/workflows/ci.yml`）でも同じものを実行する。

| スクリプト                 | 目的                                                         | 対応する基準    | CI  |
| -------------------------- | ------------------------------------------------------------ | --------------- | --- |
| `setup-env.sh`             | `supabase status` から `apps/web/.env.local` / `apps/api/.env` を生成 | A15（環境構築） | —   |
| `test-db.sh`               | RLS・権限・トリガーの pgTAP テストを psql で実行             | A13             | ✓   |
| `check-db-types.sh`        | `packages/shared/src/database.types.ts` とスキーマのずれ検出 | 型契約          | ✓   |
| `check-secrets.sh`         | シークレット混入チェック（作業ツリー / `--history` でコミット履歴） | A14 / H7        | ✓   |
| `check-scope.sh`           | スコープ外機能（決済・画像生成・TTS・ユーザー投稿 等）の混入チェック（E1 / E2 の例外は `check-scope-allowlist.txt`） | A16 / §12       | ✓   |
| `generate-third-party-notices.py` | `THIRD_PARTY_NOTICES.md`（本番依存のライセンス一覧）を生成。依存を更新したら実行してコミット: `uv run --frozen --project apps/api python scripts/generate-third-party-notices.py` | 提出物（DD） | —   |

終了コードは共通で `0` = 成功、`1` = チェック失敗（検出あり）、`2` = 実行できない（接続不可・ツール不足など）。

`scripts/tests/` はスクリプト自体の回帰テスト（CI の checks ジョブで実行。`bash scripts/tests/<名前>.test.sh`:
`check-secrets.test.sh` / `check-scope.test.sh`）。

---

## setup-env.sh — ローカル env ファイルの生成

```bash
pnpm db:start            # supabase start --workdir infra
scripts/setup-env.sh     # apps/web/.env.local と apps/api/.env を生成
```

- `.env.example` をテンプレートに、次の値だけを `supabase status --workdir infra -o env` から埋める。
  - `NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_URL` ← `API_URL`
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY` ← `ANON_KEY`（無ければ `PUBLISHABLE_KEY`）
  - `DATABASE_URL` ← `DB_URL`
- Web 用には `NEXT_PUBLIC_*` / `BUNNY_*` だけ、API 用にはそれ以外を書き出す（各変数の説明コメントも残る）。
- service_role key・secret key・JWT secret は書き出さない（ローカルの API は JWKS で ES256 トークンを検証する）。
- 既存ファイルがあると **何も書かずに終了コード 1**。`--force` で上書きし、元のファイルは
  `*.bak.<日時>`（`.gitignore` 済み）に退避する。LLM の API キー等を手で書き足している場合は退避先から戻すこと。
- 生成ファイルのパーミッションは `600`。
- オプション: `--status-file <path>`（`supabase status -o env` の出力を保存したファイルを使う）、
  環境変数 `OUT_DIR`（出力先ルート。テスト用）、`SUPABASE_BIN`（CLI のパス）。

## test-db.sh — RLS / 権限テスト（pgTAP）

```bash
scripts/test-db.sh                  # infra/supabase/tests/database/*.test.sql をすべて実行
scripts/test-db.sh -v               # TAP 出力をすべて表示
scripts/test-db.sh infra/supabase/tests/database/05_dm_isolation.test.sql
```

- `DATABASE_URL`（既定: `postgresql://postgres:postgres@127.0.0.1:54322/postgres`）に psql で接続して各ファイルを実行する。
  **Docker 不要**（`supabase test db` は pg_prove 用の追加イメージを pull するため、こちらを正とする）。
- 各テストファイルは `begin; ... rollback;` で完結し、pgTAP 拡張の作成も含めて DB に何も残さない。
  `db reset` も DROP もしないので、開発中の DB に対して何度実行してもよい。
- 失敗判定: psql のエラー / `not ok` 行（`# TODO` 付きは除く）/ `plan(n)` と実行数の不一致。
- 誤って本番に向けないよう、ホストが `127.0.0.1` / `localhost` 以外なら拒否する（`TEST_DB_ALLOW_REMOTE=1` で解除）。
- `supabase test db --workdir infra` でも同じファイルが実行できる
  （Docker Hub に届かない環境では `SUPABASE_INTERNAL_IMAGE_REGISTRY=mirror.gcr.io` を付ける）。

テストの書き方は [`infra/supabase/tests/README.md`](../infra/supabase/tests/README.md) を参照。

## check-db-types.sh — DB 型のずれ検出

```bash
pnpm db:start && scripts/check-db-types.sh
```

`supabase gen types typescript --local` の結果とコミット済みの `packages/shared/src/database.types.ts` を
（Prettier があれば両方を整形してから）比較する。マイグレーションを変えたら `pnpm db:types` で再生成すること。

## check-secrets.sh — シークレット混入チェック（A14）

```bash
scripts/check-secrets.sh                              # 作業ツリー
scripts/check-secrets.sh --history                    # HEAD までの全コミットの全ファイル版
scripts/check-secrets.sh --history origin/main..HEAD  # このブランチのコミットだけ（push 前の確認に）
```

- 対象: `git ls-files -co --exclude-standard`（コミット済み + 未追跡で `.gitignore` されていないファイル）。
- `--history [<git rev-list の引数>]`: 作業ツリーの検査では「あるコミットで追加し、次のコミットで消した」
  シークレットがすり抜けるため、範囲内のコミットに含まれた全ファイル版（blob）を同じルールで走査する。
  検出箇所は `パス @ そのファイル版を最初に含んだコミット:行` で表示する。shallow clone では実行しない（終了コード 2）。
  CI は PR ならその PR のコミット（`base..head`）、main への push なら push されたコミットを走査する。
  履歴に入った本物の鍵は、ファイルを消しても残るので必ず無効化（ローテーション）すること。
- 検出: 秘密鍵ブロック、`sk-` 形式の API キー、Supabase の `sb_secret_` キーと JWT（service_role / anon。role を表示）、
  AWS / GitHub / Slack / Google / Stripe の既知形式、Sentry DSN、Bunny.net / Backblaze B2 のキーへの代入、
  `SECRET` / `PASSWORD` / `API_KEY` 等の名前への文字列リテラル代入、ローカル以外を指すパスワード付き DB 接続文字列、
  `.env*` ファイル（`.env.example` 以外）や `*.pem` / `*.key` 等のファイルそのもの。
- `.env.example` は「キー・シークレット系の変数が空」「URL の認証情報はローカル既定値のみ」であることを検査する。
- 出力にはシークレットの値を表示しない（CI ログへの二次漏洩防止）。
- 誤検知の抑制:
  - 名前ベースのヒューリスティックなルールは、テストコード（`tests/`, `__tests__/`, `fixtures/`, `*.test.*`,
    `*.spec.*`, `test_*.py`, `conftest.py`）と、`example` / `dummy` / `test` / `env(...)` / `process.env` 等を含む行を対象外にする。
  - 既知形式のキー（`sk-`・JWT・秘密鍵など）はテストコードでも検出する。テスト用のダミー値であれば、
    テストファイルの該当行に `check-secrets: allow` と理由を書く（テスト以外のファイルでは無効）。
- 回帰テスト: `bash scripts/tests/check-secrets.test.sh`（使い捨ての git リポジトリで `--history` 等を検証する）。

## check-scope.sh — スコープ外機能の混入チェック（A16）

```bash
scripts/check-scope.sh
```

- 対象: `apps/` と `packages/` のコミットされ得るファイル。Markdown・ロックファイル・
  モデレーションの禁止語リスト（`apps/api/app/services/moderation.py`）は除外。
- カテゴリ: `payment`（決済 SDK / 決済 API / カード入力 / 購入・課金の文言）、`image-gen`、`tts-voice`、
  `user-posting`（ファイル入力・posts への書き込み・投稿作成ルート / API ルーター）、`notification`（Push API）、`admin-ui`。
- 仕様で定められたプレースホルダ文言 **「購入する（準備中）」「課金機能は現在準備中です」** は許可。
- スコープ外であることを説明するコメント等の誤検知は、その行に `scope-check: allow` と理由を書くと許可される
  （許可した行は一覧に表示されるのでレビューで確認する）。
- 終了コード: `0` = 検出なし / `1` = 検出あり（許可リストの古い項目を含む）/ `2` = 実行できない（許可リストの書式エラー等）。
- 回帰テスト: `bash scripts/tests/check-scope.test.sh`（使い捨ての git リポジトリで下の例外と検出を検証する。CI の checks ジョブで実行）。

### E1 / E2 のコンプライアンス上の例外（payment の「購入・課金の文言」ルールだけ）

キャラクターエンジン v1.0 のハードルール E1（課金・購入は好感度に影響させない）/ E2（キャラが購入と関係の
継続・破綻を結びつけない）は、**禁止する言い回しを知らないと守れない**。出力の検査（OutputGuard の検出語）・
好感度の構造テスト・ガードの回帰テスト・プロンプトの禁止の指示（「購入・課金を好意と結びつけない」）は
どれも「課金」「購入」を含むため、文言ルールがそのままではこれらを決済機能と誤検知する。
そこで、文言ルール（`決済|課金|購入|投げ銭|チップを送`）に限って次の 2 つを例外にする。

1. **許可リスト** [`scripts/check-scope-allowlist.txt`](check-scope-allowlist.txt): 禁止を実装・検査するファイルを、
   1 行 1 項目 `<パスのパターン>  # <理由>` で載せる（理由は必須。無ければ終了コード 2）。パターンは bash の
   パターン（`*` は `/` にも一致）。どのファイルにも一致しない項目は失敗（ファイルを消したら項目も消す）、
   文言の検出が無くなった項目は警告を出す。載せるのは最小限（現在: OutputGuard、ペルソナの E2 文言検査、
   E1 の構造テスト、ガード・指針・自発メッセージ・安全対応の回帰テスト、評価ハーネス `apps/api/evals/` と
   そのテスト（本番のイメージに含まれない。操作しようとするユーザーの課金の誘い文・E2 の判定の例を持つため
   ディレクトリ単位））。
2. **禁止を述べる行**: 購入・課金の語の **後ろ** に、同じ行で禁止の言い回し（`結びつけない` `勧めない` `触れない`
   `使わない` `含まれず` `受け取らない` `実装しない` `禁止` … スクリプトの `PROHIBITION_RE`）がある行。
   適用するのは `apps/api/` と `packages/`（プロンプトのテンプレート・コメント・docstring）だけで、
   利用者に見せる画面の文言（`apps/web/`）には適用しない（画面の課金の文言は必ず人が確認する）。
   「課金しないと見られません」のような条件の「しない」を拾わないよう、単独の「しない」は禁止の言い回しに含めない。
   コメントを書くときは、禁止の言い回しを購入・課金の語と同じ行に置く（改行で分けると検出される）。

どちらの例外も **決済 SDK・決済処理のコード（`checkout` / `payment_intent` …）・カード入力のルールには効かない**。
許可リストに載ったファイルに本物の決済コードが入れば、これまでどおり失敗する（回帰テストで確認している）。
許可した行はすべて `allow <パス>:<行>  [payment] 許可リスト（…）: <理由>` / `… 禁止を述べる行: <抜粋>` として
一覧に出るので、PR のレビューで確認する。

---

## package.json から呼ぶ場合

ルートの `package.json` に次のエイリアスがある（`.env.example` と README の手順は `pnpm setup:env` を案内している）:

```json
{
  "setup:env": "bash scripts/setup-env.sh",
  "db:test": "bash scripts/test-db.sh",
  "db:types:check": "bash scripts/check-db-types.sh",
  "check:secrets": "bash scripts/check-secrets.sh",
  "check:scope": "bash scripts/check-scope.sh"
}
```

## 動作環境

bash 4.4 以上・GNU grep を想定（Linux / GitHub Actions の ubuntu ランナー）。macOS では `brew install bash grep` を推奨。
`test-db.sh` には psql（PostgreSQL クライアント）が必要。
