# 検収レビュー 報告書（リポジトリ全体）— 2026-09-27

everkano リポジトリ全体（`apps/web`・`apps/api`・`infra/supabase`・`scripts/`・`.github/`・`portrait-studio/`）に対して、検収業者の立場で
セキュリティリスク・循環参照・低品質コード・レイテンシ・その他の品質観点の検査を行い、必要な補修を行った結果をまとめる。
前回の納品前検査（2026-09-25〜26、[inspection-report.md](inspection-report.md)）の後に追加された `portrait-studio/`（ADR-0050）を含む。

| 項目 | 内容 |
| --- | --- |
| 検査日 | 2026-09-27 |
| 検査の対象 | ブランチ `claude/inspiring-shannon-kd96k5`、コミット `741718d`（portrait-studio 追加）〜 `bb48de4` の時点の全ファイル。修正はこの報告書と同じコミット群 |
| 観点 | セキュリティ（アプリ・サプライチェーン・運用）、循環参照（TypeScript / Python）、コード品質（静的解析・死んだコード・重複）、レイテンシ（計測と N+1 / ブロッキング）、その他（テストの空白・ドキュメントのずれ・DB 設計） |
| 方法 | 自動検査のベースライン（依存の脆弱性・循環参照・lint / 型 / テストの全ゲート）→ 領域別の精読（4 名の検査担当が独立に読み、指摘ごとに再現手順を記録）→ 指摘ごとに「確認 / 反証」→ 確認した指摘を修正し、回帰テストを追加 → 全ゲートを再実行 |
| 環境 | クラウドの Linux コンテナ。GPU 無し・Docker デーモン無し・Postgres サーバー無し（psql のみ）。PyPI / npm / GitHub リリース資産は到達可、Hugging Face / Civitai は遮断。したがって **DB のマイグレーション・pgTAP・E2E（apps/web は Supabase ローカルが必要）・GPU に依存する検証はこの環境では実行できない**（該当項目は明記し、実行手順と SQL を残した） |

## 1. 結論

- 指摘は **55 件**（portrait-studio 23、apps/web 7、apps/api 13、infra / scripts / CI 12）。うち **検収側の検証で新たに見つけた 2 件**
  （同梱 JSON がコミットされておらず新規 clone で API が起動しない [F22]、最終監査で出た React Router の advisory [F23]）を含む。
- 判定: 確認 52 件、一部反証 3 件（Q1・C4・S5。前提の一部が成り立たないが、成り立つ部分は対応した）、全面的な反証 0 件。
- 対応: **コードで修正 48 件**（うち 3 件は一部の修正: portrait-studio F21 のテスト追加、apps/api F4・F11）、**DB のマイグレーションが必要な 7 件は
  適用する SQL と検証手順を文書化**（[db-followups-2026-09-27.md](db-followups-2026-09-27.md)。この環境に Postgres が無いため）。修正せずに残した指摘は無い。
- 重大度: high 1（F22、修正済み）、medium 22（すべて修正または文書化）、low 32。
- 修正後の全ゲート（6 章）は成功: apps/web vitest 439、apps/api pytest 1340（統合 214 件は Postgres が無く skip）、portrait-studio pytest 68 + E2E 8、
  依存の脆弱性 0、循環参照 0、check-secrets / check-scope とその回帰テスト 80 件。
- 修正の途中で検収側が自分で入れた回帰を 1 件検出して直した（生成画像の PNG を毎回再エンコードして 1 枚あたり 300 ms 増えていた。4 章）。
- この環境で実行できなかった検証（5 章）は受け入れ側の実機 / ローカル Supabase で行う必要がある。特に apps/api の SQL を変えた修正は
  `pytest -m integration` と pgTAP で確認してから本番に出すこと。

## 2. 自動検査のベースライン（修正前）

| 検査 | 結果 |
| --- | --- |
| `uvx pip-audit`（apps/api の `uv export`、portrait-studio/api の `uv export`） | 既知の脆弱性なし（どちらも 0 件） |
| `pnpm audit --audit-level high` / `pnpm audit --prod`（ワークスペース）、portrait-studio/web の `pnpm audit` | 既知の脆弱性なし |
| 循環参照（TypeScript: madge 相当の自作グラフ解析 — `@/` エイリアスと相対解決、Tarjan SCC。apps/web 151 ファイル / 473 エッジ、portrait-studio/web 全ファイル） | 0 件（型のみの import を含めても 0） |
| 循環参照（Python: AST で import 時の依存を解析。apps/api、portrait-studio/api） | 0 件 |
| `pnpm lint` / `pnpm typecheck` / `pnpm test`（apps/web: vitest 43 ファイル / 432 件） | 成功 |
| apps/api: `ruff check` / `ruff format --check` / `mypy --strict` / `pytest` | ruff E501 が 1 件（`app/engine/affinity/service.py:14`、CI を落とす）→ 修正して `bb48de4`。pytest 928 成功 / 147 skip（skip は Postgres が必要な統合テスト） |
| portrait-studio/api: `ruff` / `mypy --strict` / `pytest` 57 件、portrait-studio/web: `tsc` / `eslint` / vitest 6 件 / Playwright | 成功 |
| knip（apps/web、未使用のエクスポート） | 指摘なし |

## 3. 指摘と対応

各指摘は「確認（再現した / コードで裏付けた）」「反証（再現しない・仕様どおり・設計判断が記録済み）」に分けた。重大度は検査担当の評価（C / H / M / L）。

### 3.1 portrait-studio（23 件: 確認 23・修正 22・一部 1。うち 2 件は検収側の検証で発見）

| # | 重大度 | 指摘 | 判定 | 対応 |
| --- | --- | --- | --- | --- |
| F1 | M | `psk` Cookie が全ルートで有効（別ポートの同一サイトのページから Cookie で状態を変える API を叩ける） | 確認 | Cookie は **画像 / サムネイル / 参照顔のファイルを返す 3 つの GET だけ**で受け付ける（`security.require_api_key_or_cookie`、`characters.files_router` / `images.files_router`）。他は全てヘッダのみ。OpenAPI から 37 か所の Cookie パラメータが消えた。回帰テスト `test_cookie_is_accepted_only_by_file_routes` |
| F2 | M | アップロードを全部読み込んでから 40 MB を検査（メモリを先に使う） | 確認 | 1 MB ずつ読み、超えた時点で 413。`Content-Length` の事前検査、1 回あたり最大 12 ファイル。テストは上限を小さくして 413 を確認 |
| F3 | L | 画素数の上限が無い（小さな PNG で巨大な画像を展開させられる） | 確認 | `MAX_IMAGE_PIXELS` 24 MP（Pillow の上限にも設定）。超過は 400、ファイルを残さない。30 MP の 1 bit PNG で確認 |
| F4 | M | `install_models.sh`: Civitai が返すファイル名をそのままパスに使う / トークンがコマンドラインに出る / `.env` を `source` する | 確認 | ファイル名は basename + 許可文字のみ、version id は数値のみ。トークンは 0600 のヘッダファイル経由（`curl -H @file`）。`.env` は KEY=VALUE 行だけを読む（展開・実行なし）。`bash -n` で確認 |
| F5 | M | ComfyUI コンテナが root / カスタムノードが HEAD（再現性なし）/ API コンテナに `.env` を丸ごと注入（CIVITAI_TOKEN も入る） | 確認 | `comfy` ユーザー（`COMFY_UID` / `COMFY_GID`、既定 1000）で実行。5 ノードを `git ls-remote` で解決した当日のコミットに固定（docs/MODELS.md に記録。ノード名の実登録は G1 で実機確認）。compose は API が読むキーだけを明示（`API_KEY` は `:?` で必須）。`docker compose config` で確認 |
| F6 | M | 行全体の UPDATE による更新の消失（ジョブが進めたカウンタを PATCH が戻す） | 確認（再現） | `update_character` は編集可能な列だけを書く。`set_current_version` / `update_image_flags` / `set_image_similarity` / `soft_delete_images` に分離。DB 単体と API の回帰テスト |
| F7 | M | `delete_images=true` が論理削除済みの画像を消さない（ディスクに残る） | 確認 | `images_of_character(include_deleted=True)` で全行を消す。テストで PNG / サムネイル / サイドカーが消えることを確認 |
| F8 | M | 実行完了後の `/history` 待ちが無限（ComfyUI 再起動で永久に待つ）でキャンセルも効かない | 確認 | 30 秒の上限（`history_timeout`）とキャンセルイベント。モックに `drop_history` を追加し、ジョブがエラーで終わりキューが生き続けることをテスト |
| F9 | L | 途中失敗で孤児ファイル（参照顔のコピー途中、サムネイル失敗） | 確認 | 参照顔は全件を検証してからコピー（失敗時は巻き戻し）。アップロードの保存 / サムネイル失敗時に削除。テスト |
| F10 | M | キューのループが未処理例外で静かに死ぬ | 確認 | 1 ジョブごとに例外を捕捉して記録・待機して継続、終了時のコールバック、`alive` |
| F11 | L | `regenerate` が `generate` の検証（denoise 上限、count、解像度、hires_max、シーン、下書き）を一部省く | 確認 | `prepare_generate_job` を共通化して同じ検証を通す。テスト |
| F12 | L | ギャラリーの日付フィルタがローカル日付を UTC と比較（JST の 0〜9 時が前日に） | 確認 | ローカル日の開始 / 終了を UTC ISO に変換して送る（vitest）。API 側も境界を保存形式に正規化 |
| F13 | L | VRAM テーブルが checkpoint / LoRA を考慮せず「上限」を返す（fp32 や LoRA で OOM） | 確認 | テーブルに実測時の `checkpoint` / `lora` を記録し、異なる場合は「未実測」として拒否（理由つき）。`measure_vram.py` は別モデルの混在を拒否 |
| F14 | L | PIL / ファイルコピーがイベントループ上で走る | 確認 | アップロードの保存・サムネイル・参照顔のコピー・パージをスレッドへ |
| F15 | L | 進捗フレームごとに fsync を伴う DB 書き込み | 確認 | 250 ms に間引き（完了時は即時） |
| F16 | L | N+1（キャラ一覧で 1 件ごとに 2 クエリ）、監査ログの全走査、ポーリングの重複 | 確認 | `current_versions` で 2 クエリに、監査ログは行数カウンタ。1 秒のジョブポーリングは仕様（進捗表示）のため維持 |
| F17 | L | ギャラリーの S / C ショートカットが常に no-op | 確認 | 選択中の画像に対して動く（S: お気に入り、C: 比較トレイ） |
| F18 | L | 履歴のぼかしがストアを直接読み反応しない | 確認 | `BlurImage` に統一 |
| F19 | L | 永続化した比較トレイの消えた画像を永久に再試行 | 確認 | 404 で外す・再試行しない |
| F20 | L | 死んだコードと `type: ignore` | 確認 | `_order` / `elapsed_ms` / `queue_state` / `isfinite_list` / `Settings.host/port` を削除。`type: ignore` は 0 |
| F21 | L | テストの空白 | 一部 | 追加: 履歴消失・パージ・アップロード上限・更新消失・Cookie の範囲・regenerate の検証（pytest 57 → 68 件）。未追加: WS → ポーリングのフォールバック、`_spa`、`install_models.sh` |
| F22 | **H**（検収側で発見） | **`api/app/data/*.json`（シーンプリセット・スタイル・VRAM テーブル）がコミットされていない**。`portrait-studio/.gitignore` の `data/` がアンカー無しで `api/app/data/` にも効いていた。新規 clone では API が起動時に `FileNotFoundError` で落ちる（修正前のコミットを worktree に展開して基準値を計測しようとして判明） | 確認（再現） | `.gitignore` を `/data/` に修正し 3 ファイルをコミット。`git check-ignore` で非対象を確認 |
| F23 | M（最終監査で発見） | `pnpm audit`（portrait-studio/web）が `react-router` 6.30.6 に moderate 2 件（GHSA-wrjc-x8rr-h8h6 `<Link>` / `useNavigate` のバックスラッシュによるオープンリダイレクト、GHSA-337j-9hxr-rhxg SSR ハイドレーションのコンストラクタ注入。修正版 ≥ 7.18.0）を報告。ベースライン時点では未公開だった | 確認 | `react-router-dom` 7.18.4 へ更新（データルータ API は互換）。tsc / eslint / vitest / build / audit 0 件、E2E 再実行 |

検査担当が「問題なし」と確認した点（記録のため）: SPA のパス走査（`..`・`%2e%2e`・symlink）、SQL のバインド、`compare_digest`、キューの原子性、ワークフローの bypass / prune、VRAM の単調性、日時の tz。

### 3.2 apps/web（7 件: 確認 7・修正 7、すべて low）

| # | 重大度 | 指摘 | 判定 | 対応 |
| --- | --- | --- | --- | --- |
| WEB-01 | L | `sanitizeNextPath` が `/login` `/auth/` を前方一致でしか除外せず、`/x/../login?error=withdrawn` が通る（ログイン直後に偽の「退会済み」でサインアウトさせられる。Node の `URL` で再現） | 確認 | `new URL(next, "http://x")` で正規化してオリジン不変を確認し、正規化後のパスで `/login` `/login/…` `/auth/…` を除外。`%2e` を含む値は拒否。`auth.test.ts` に 15 ケース（`/x/../login`・`/%2e%2e/auth/confirm`・`/dm/../auth/confirm?token_hash=a` → `/`、通常のパス・クエリ・ハッシュは保持） |
| WEB-02 | L | middleware の matcher が拡張子で除外するため `/c/x.png` `/posts/a.txt` `/dm/a.svg` が認証ゲートを通らない（未ログインでもシェルが描画され「有効期限切れ」と誤表示。データは RLS で漏れない） | 確認 | 除外を「ルート直下の静的ファイル」と「画像拡張子の `/media/*`（Route Handler 自身が検証）」に限定。`middleware.test.ts` に 9 ケース |
| WEB-03 | L | 記憶の更新 / 削除の失敗時に一覧全体をスナップショットに戻し、並行する他の記憶の楽観的更新（確定済み含む）まで消す | 確認 | 失敗した 1 件だけを戻す（更新: 残っていれば元の内容に、削除: 無ければ元の位置に）。`memories.test.ts` に並行更新 / 並行削除のケース |
| WEB-04 | L（推定） | ストリーミングの 1 チャンクごとに全バブルを再描画 | 確認（props の参照安定性を検証） | `MessageBubble` を `memo` 化（それ以上の構造変更はしない） |
| WEB-05 | L | `MessageComposer` の `forwardRef` / `useImperativeHandle` に利用者が無い | 確認 | 削除 |
| WEB-06 | L | `isUuid` / `isRecord` の重複定義（5 か所） | 確認 | `lib/guards.ts` に統一（`navigation.ts` の配列を通す版は配列を除外する版に統一）。`guards.test.ts` |
| WEB-07 | L | README が `lib/env.ts` は zod で検証すると書いている（実際は手書き検証。zod はサーバー専用） | 確認 | 記述を修正 |

検査担当が「問題なし」と確認した点: XSS（`dangerouslySetInnerHTML` 無し、RichText はトークン化）、オープンリダイレクト（`//` `/\` 制御文字の拒否、同一オリジン解決）、認証ハンドラ（GET でトークンを消費しない、`Sec-Fetch-Site` 検査、`deleted_at` の再確認）、シークレット（`BUNNY_TOKEN_AUTH_KEY` は `server-only`）、Service Worker のキャッシュ範囲、Supabase の列の限定、Realtime の購読の後始末、ハイドレーション。

### 3.3 apps/api（13 件: 確認 11・一部 2・反証 0、修正 13）

| # | 重大度 | 指摘 | 判定 | 対応 |
| --- | --- | --- | --- | --- |
| F1 | M | スケジューラが 1 回の走査で固定した `now` を全タスクに渡し、自発メッセージの `created_at` が走査中に届いたユーザー発言より古くなる（並び順が崩れる）／「最後のメッセージ」を `<= now` で切っていて直近の発言を見落とす | 確認 | タスクごとに時計を読み直す。自発メッセージの INSERT は `pipeline` と同じ `greatest(now, max(created_at) + 1 ms)`。走査後に会話が動いていたペアには送らない。ユニットテスト（偽の時計） |
| F2 | M | `memory_tombstones` がペアあたり無制限に増える／`DELETE /memories` にレート制限が無い | 確認 | 墓標はペアあたり 200 件（削除のトランザクション内で古いものを消す）。DELETE は `MemoryWriteRateLimitedUser`。ルート走査テスト |
| F3 | M | `/chat` ごとに履歴 60 件を毎回モデレーション（約 95 ms） | 確認 | メッセージ ID + 本文ハッシュをキーにした上限 4096 の LRU（`ModerationVerdictCache`）。呼び出し回数を数える偽モデレータでテスト |
| F4 | M | ジョブワーカー / スケジューラのループが DB・OS 以外の例外で静かに死ぬ | 一部（ハンドラの例外は既に捕捉されていた。ループ層のみ該当） | `except Exception` + `logger.exception` + 待機、終了時のコールバックで記録。ValueError を 1 回投げる偽キューでテスト |
| F5 | M | メモリパネルから作った記憶が注入防止のふるい（`injection_labels`）を通らない（自動抽出だけが対象だった） | 確認 | 作成・更新とも Gate #1 の後に同じふるいを通し、引っかかれば 422（本文は残さず、監査 `memory.injection_skipped`）。テスト |
| F6 | L | 容量の計算が `superseded` 行も数える | 確認 | `active` だけを数え、`superseded` の履歴はペアあたり 100 件に別途制限（監査 `history_trim`） |
| F7 | L | 要約が退避されず増え続ける | 確認 | 新しい要約を入れたら古い自動要約を上限（30 件、設定可）を超えた分 `superseded` に。ユーザーが編集した要約は残す |
| F8 | M | `PATCH /promises`・`PUT /proactive/settings`・`DELETE /memories` にレート制限が無い | 確認 | `settings` バケット（30/分）を追加、promises / memories DELETE は `memories` バケットを共有。429 は既存の `ERROR_RESPONSES`、OpenAPI を再生成。ルート走査テスト |
| F9 | M | `mark_referenced(ids)` が所有者で絞らず ID だけで UPDATE | 確認 | `user_id` / `character_id` を WHERE に追加し呼び出し側から渡す。統合テストに他人の ID を混ぜるケース（DB 必須） |
| F10 | L | 1.5 s のアセンブラ締め切りと 5 s の埋め込みタイムアウトの関係が不明 | 確認（文書） | 設定に「/chat は 1.5 s が勝ち記憶セクションを落として続行、5 s は分析・要約・パネル向け」を明記 |
| F11 | L | バッチのチェックポイントが step 1 成功後に初めて保存され、再試行で別の（大きい）範囲を読む | 一部 | 初回の step 1 の前に範囲を保存 |
| S3 | M | E6（自傷・自殺の打ち明けへの安全対応）のフォールバック文が DM プロンプトに無い／安全対応した会話にも自発メッセージが飛ぶ | 確認 | `dm_system.ja.txt` に「役より相手の安全を優先し、身近な人・相談窓口へ」の 1 行を追加（プロンプトテスト）。`messages.safety_triggered` を見て、最後の返答が安全対応か 24 時間以内に安全対応があればスキップ（`user_blocked_reason = safety_triggered`、テスト） |
| S4 | M | `audit_logs` に保持期間が無い（本文・プロンプトを含む） | 確認 | `AUDIT_LOG_RETENTION_DAYS`（既定 0 = 消さない）で日次 `audit.cleanup` が 1 万件ずつ削除。テスト（偽プール） |

ゲート: `ruff check` / `ruff format --check` / `mypy app evals`（135 ファイル）/ `export_openapi.py --check` / `pytest` **1340 成功・214 skip**（修正前 1326。skip はすべて Postgres が必要な統合テスト）。
**この環境では実行できなかった検証**: F1 の INSERT ガードと `_PAIRS_SQL`、F2 / F6 / F7 のトリム、F9 の所有者で絞る UPDATE、拡張した `test_retrieval.py` は SQL の変更であり、ローカル Supabase で `pytest -m integration` と pgTAP を回して確認すること（5 章）。

### 3.4 infra / scripts / CI（12 件: 確認 9・一部反証 3・反証 0。修正 5、文書化 7）

| # | 重大度 | 指摘 | 判定 | 対応 |
| --- | --- | --- | --- | --- |
| S1 | M | `check-secrets.sh` が Supabase PAT（`sbp_`）・Fly.io（`FlyV1 fm2_`）・Groq（`gsk_`）・Hugging Face（`hf_`）・16 文字未満のパスワード直書きを検出せず、テストのパスを走査しない | 確認 | 5 種の規則を追加。テストのパスも走査し、既知のダミー値（8 個）と `# check-secrets: allow` だけを許可。回帰テスト 18 → 34 件 |
| S2 | M | `check-scope.sh` は `docs/` を含むパスの `.ts` を除外、`.mdx` を走査せず、`infra/` を走査せず、PayPay が無く、行内の `禁止` 1 語で行全体を許可 | 確認 | 除外は `*.md`（および `docs/` 配下の `.mdx`）だけに、`infra` を追加、`paypay(opa)?` を追加、`禁止` は述語の形（〜は禁止 / 〜を禁止する / 禁止（E1））に限定。回帰テスト 46 件。`5be8579` で追加した E6 の文（「〜の話をしない」）が旧スクリプトでは誤検出になるため文末形だけを許可（条件形は検出のまま） |
| Q1 | L | CI の ruff が一部の Python を検査しない／pnpm に公開直後の版を避ける設定が無い | 一部反証（apps/api は `api-db` ジョブが全体を検査済み。未検査は apps/api の外） | `packages/personas/scripts` と `scripts/*.py` を CI の ruff に追加（ローカルで成功を確認）。`.npmrc` に `minimum-release-age=1440`（`--frozen-lockfile` には影響しない） |
| Q2 | L | bash 4 以降の機能に版ガードが無い（macOS は 3.2） | 確認 | `check-secrets` / `check-scope` に 4.4 以上のガード（`set -u` 下の空配列展開と `declare -A` のため）。他のスクリプトは bash 4 専用の構文を使っていない |
| C4 | L | `apps/api/tests/README.md` の表の 10〜13 行と `PUBLIC_MEMORY_COLUMNS` の記述がコードとずれる | 一部反証（`apps/api/tests/README.md` は存在しない。該当は `infra/supabase/tests/README.md`。`PUBLIC_MEMORY_COLUMNS` は文書に登場しない） | pgTAP の README に 10〜13 の 4 行を追加。`PUBLIC_MEMORY_COLUMNS`（`packages/shared`、未使用・列数がずれる）はコードの申し送りとして DB 文書 §5 に記録 |
| S5 | M | RLS が `deleted_at` を見ない | 一部反証（他人の行は読めない。退会・利用停止した本人の PostgREST / Realtime のアクセスが残るのが実際の穴） | `is_active_user()` とポリシーの書き換え、pgTAP 14 を [db-followups](db-followups-2026-09-27.md) に記載 |
| S6 | M | テーブル単位の GRANT が後から足した列を公開する（`conversations.summary_cursor` など 6 テーブル） | 確認 | 列単位の GRANT への置き換え SQL と 00 テストの更新を記載 |
| C1 | L | ユーザー削除のカスケードの pgTAP が無い（12 の FK のうち 3 のみ） | 確認 | カスケードのテストとカタログの検査を記載 |
| C2 | L | SECURITY DEFINER 関数の `search_path` の検査が無い | 確認（5 関数は固定済み。検査だけが無い） | `proconfig` の検査を記載 |
| C3 | L | マイグレーションが要約の `updated_at` を書き換えた | 確認（影響は表示のみ） | 任意の修復 SQL と「バックフィル中はトリガを止める」運用を記載 |
| L1 | L | `memories` が Realtime の publication に入り、参照カウントの UPDATE と埋め込みが WAL に流れる | 確認 | `publish='insert, delete'` への変更と `memory_notices` 表の案を記載 |
| L2 | L | 自発メッセージの走査が `conversations` を全件読む | 確認 | `conversations(last_message_at desc)` のインデックスと事前フィルタを記載 |

ゲート: `check-secrets.sh`（888 ファイル、検出なし）/ `check-scope.sh`（許可 101 行、検出なし）/ 回帰テスト 34 + 46 件成功 / ShellCheck 0.11 成功（検査担当の実行）/ CI の YAML を解析して成功。

## 4. 計測値（portrait-studio、モック ComfyUI）

同じ手順（モック ComfyUI + `FACE_ENGINE=mock`、1 キャラで 8 枚 × 63 ジョブ = 505 枚を生成してから各 API を 20 回計測、
p50 / p95 / max）で修正前後を計測した。GPU の推論時間は含まない（モックは 2 ステップ・5 ms）。

| 項目 | 修正前（`741718d`） | 修正後 |
| --- | --- | --- |
| 505 枚の生成（モック描画 + サムネイル + 顔分析 + サイドカー + DB） | 134.5 s（266 ms / 枚） | 142.2 s（282 ms / 枚）。**途中の版では 589 ms / 枚**（PNG を毎回再エンコードしていた。検証だけ行い PNG はそのまま書く形に直した: 1 枚 314 → 20 ms） |
| `GET /api/images?limit=500`（ギャラリー） | p50 22.8 / p95 35.7 ms | p50 22.5 / p95 32.1 ms |
| `GET /api/images?q=bulk&min_similarity=0.5` | p50 27.7 ms | p50 28.0 ms |
| `GET /api/images?from=…&to=…`（UTC 境界、`Z` 付き） | 計測なし（境界の形式が不一致） | p50 22.5 ms、当日の 500 枚を返す |
| `GET /api/characters`（61 キャラ、N+1 の対象） | p50 65.5 / p95 73.7 ms（1 件ごとに 2 クエリ + 接続。`741718d` を worktree に展開して同条件で計測） | p50 16.8 / p95 19.9 ms（2 クエリ） |
| `GET /api/jobs?limit=30`（1 秒ポーリング） | p50 4.6 ms | p50 4.4 ms |
| `GET /api/images/{id}/thumb`（ヘッダ / Cookie） | p50 3.1 ms / 計測なし | p50 2.8 ms / 2.6 ms |
| `GET /api/audit?limit=100` | p50 3.1 ms（全走査） | p50 3.0 ms（カウンタ。63 行では差が出ない） |
| `GET /api/images/{id}/similarity`（モックエンジン） | p50 49.5 ms | p50 2.1 ms（初回のエンジン読み込みを含まない計測） |
| 61 キャラの作成（analyze + 登録） | — | 6.1 s |
| SQLite / 画像ディレクトリ | 2.4 MB / 603.6 MB | 3.2 MB / 640.3 MB |

生の出力: [raw/portrait-studio-latency-before.txt](raw/portrait-studio-latency-before.txt)、[raw/portrait-studio-latency-after.txt](raw/portrait-studio-latency-after.txt)。

## 5. この環境で実行できなかった検証と、次の一手

| 項目 | 理由 | 次の一手（担当: 受け入れ側の実機 / ローカル Supabase） |
| --- | --- | --- |
| apps/api の SQL を変えた修正（F1 の INSERT ガードと `_PAIRS_SQL`、F2 / F6 / F7 のトリム、F9 の所有者スコープ、S4 の削除）と拡張した統合テスト | Postgres サーバーが無い（`pytest -m integration` 214 件が skip） | `supabase start` → `pnpm --filter @everkano/api test`（統合 214 件）→ `pnpm db:test`（pgTAP） |
| DB 設計の指摘（S5 RLS と `deleted_at`、S6 テーブル単位の GRANT、C1 カスケードの pgTAP、C2 `search_path` の検査、C3 要約の `updated_at`、L1 Realtime の `memories`、L2 自発メッセージ走査のインデックス） | マイグレーションを検証できないため、コードではなく **適用する SQL と検証手順を文書化**した | [db-followups-2026-09-27.md](db-followups-2026-09-27.md) の SQL を次のマイグレーションに入れ、pgTAP を追加して `pnpm db:test` |
| apps/web の E2E（Playwright、Supabase ローカルが必要） | 同上 | `pnpm e2e`（前回 143 成功の構成） |
| portrait-studio の G0〜G3（cu128 / sm_120、`/object_info` のノード登録、VRAM 実測、しきい値の較正）と、カスタムノードのコミット固定が提供するノード名の確認 | GPU・Docker デーモン・Hugging Face / Civitai が無い | `scripts/verify_env.sh`、`scripts/measure_vram.py --checkpoint <file> [--lora <file>]`、`scripts/face_similarity.py calibrate`（[portrait-studio/docs/acceptance/report.md](../../portrait-studio/docs/acceptance/report.md)） |
| portrait-studio の Docker イメージのビルド（非 root の ComfyUI、compose の環境変数） | Docker デーモンが無い（`docker compose config` のみ確認） | `docker compose up --build` → `scripts/verify_env.sh` |
| portrait-studio で未追加のテスト（WS → ポーリングのフォールバック、`_spa`、`install_models.sh`） | 時間の制約 | 次の変更で追加 |

## 6. 再実行したゲート（修正後、2026-09-27）

| ゲート | 結果 |
| --- | --- |
| apps/web: `pnpm --filter @everkano/web lint` / `typecheck`（`next typegen` + tsc）/ `test` | 成功 / 成功 / vitest **44 ファイル / 439 件**（修正前 432） |
| apps/api: `ruff check` / `ruff format --check`（225 ファイル）/ `mypy app evals`（135 ファイル）/ `export_openapi.py --check` / `pytest -q` | 成功 / 成功 / 成功 / 最新 / **1340 成功・214 skip**（修正前 1326・214） |
| portrait-studio/api: `ruff check` / `ruff format --check` / `mypy app`（strict、29 ファイル）/ `pytest -q` | 成功 / 成功 / 成功 / **68 成功**（修正前 57）（[portrait-studio/docs/acceptance/raw/api-quality-gates.txt](../../portrait-studio/docs/acceptance/raw/api-quality-gates.txt)） |
| portrait-studio/web: `tsc` / `eslint` / vitest / `vite build` / Playwright E2E | 成功 / 成功 / 7 件 / 成功 / **8 件成功**（React Router 更新後に再実行、[raw/e2e-playwright.txt](../../portrait-studio/docs/acceptance/raw/e2e-playwright.txt)） |
| `pip-audit`（apps/api、portrait-studio/api）/ `pnpm audit --audit-level high` / `pnpm audit --prod` / `pnpm audit`（portrait-studio/web） | 既知の脆弱性なし / なし / なし / react-router の moderate 2 件 → 7.18.4 に更新して **なし** |
| 循環参照（TypeScript 2 アプリ、Python 2 パッケージ） | 0 件（修正前後とも） |
| `docker compose config`（portrait-studio、`API_KEY` 未設定は拒否） | 成功 / 拒否を確認 |
| `bash scripts/check-secrets.sh` / `bash scripts/check-scope.sh` / `scripts/tests/check-*.test.sh` | 888 ファイル・検出なし / 許可 101 行・検出なし / **34 + 46 件成功**（[raw/repo-checks-2026-09-27.txt](raw/repo-checks-2026-09-27.txt)） |
| CI の新しい ruff ステップ（`packages/personas/scripts`、`scripts`）をローカルで実行 / `ci.yml` の YAML 解析 / Prettier（変更した Markdown） | 成功 / 成功 / 成功 |

## 7. 参照

- 前回の納品前検査: [inspection-report.md](inspection-report.md)（2026-09-25〜26）。今回はその後に追加された portrait-studio と、前回の検査で扱わなかった観点（循環参照の再確認、スクリプトの抜け道、DB の申し送り）を含む
- DB の申し送り（SQL / pgTAP）: [db-followups-2026-09-27.md](db-followups-2026-09-27.md)
- portrait-studio の受け入れ基準と実機で行う検証: [portrait-studio/docs/acceptance/report.md](../../portrait-studio/docs/acceptance/report.md)、生の出力 [portrait-studio/docs/acceptance/raw/](../../portrait-studio/docs/acceptance/raw/)
- 修正のコミット（ブランチ `claude/inspiring-shannon-kd96k5`）: `bb48de4`（ruff）、`06817d7`・`2841d10`・`486980b`・`61b8abc`（portrait-studio）、`e4ecda3`（apps/web）、`5be8579`（apps/api）、`21990dd`（scripts / CI / DB 文書）
