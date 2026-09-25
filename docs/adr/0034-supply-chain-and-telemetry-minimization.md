# ADR-0034: 依存・ビルドの固定と、外部へ送る情報の最小化（Sentry のスクラブ・画像最適化の無効化・権利表示）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: 仕様書 §2「監視・ログ」・§15・H7 / [ADR-0013](0013-audit-log.md)・[ADR-0015](0015-no-csp-in-mvp.md)・[ADR-0021](0021-web-ui-implementation.md) /
  実装: `apps/api/app/core/observability.py`, `apps/api/app/core/config.py`（`hide_input_in_errors`）, `apps/api/Dockerfile`, `apps/api/fly.toml`（`[build.args]`）,
  `docker-compose.yml`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `package.json`（`pnpm.overrides`）, `scripts/check-secrets.sh`（`--history`）,
  `apps/web/next.config.ts`（`images.unoptimized`）, `LICENSE`, `THIRD_PARTY_NOTICES.md`（`scripts/generate-third-party-notices.py`）, `SECURITY.md`

## コンテキスト

納品後の売却（買い手のデューデリジェンス）を前提に、納品前の検査で次の指摘を受けた。

- **Sentry に機微な情報が送られる**: 06-operations が推奨していた Sentry を有効にすると、`send_default_pii=False` のままでも、スタックフレームの
  ローカル変数（ASGI の scope に `authorization: Bearer <JWT>` がそのまま入る）、リクエスト本文（DM・記憶の内容）、ログのパンくず（監査ログの複製 =
  発言・返答・プロンプト全文）が送られていた。起動時の設定エラーのメッセージにも、入力値（API キー・DB のパスワードを含み得る）が出ていた。
- **ビルドが再現できない**: API のベースイメージがタグだけ（同じコミットから別のイメージになる）、uv をハッシュ検証なしで pip から入れていた、
  実行イメージに pip が残っていた。GitHub Actions のサードパーティのアクションを可変のタグで参照していた。シークレットの検査は作業ツリーだけで、履歴を見ていなかった。
- `pnpm audit` が失敗していた（postcss 8.4.31（next 経由）と vitest 3.2.7。どちらもこの構成では悪用できないが、DD の機械的な確認で指摘される）。
- 開発用の `docker-compose.yml` が API を全インターフェース（`0.0.0.0:8000`）に公開していた。
- 使っていない Next.js の画像最適化（`/_next/image`）が有効で、LGPL-3.0 のネイティブライブラリ（sharp / libvips）が実行時に読み込まれ得た。
  リポジトリに権利表示（LICENSE）・依存のライセンス一覧・脆弱性の報告窓口が無かった。

## 決定

- **Sentry（任意）**: `app/core/observability.py` の `init_sentry()` で、`send_default_pii=False` に加えて `include_local_variables=False`・
  `max_request_body_size="never"`・ログをパンくずにしない（`LoggingIntegration(level=None, event_level=ERROR)`）・監査ロガー `everkano.audit` を除外
  （`ignore_logger`）にする。`before_send` で、リクエストの本文・Cookie・環境変数を捨て、ヘッダーは許可リスト（User-Agent・Content-Type・Content-Length・
  X-Request-ID・Origin・Accept）だけを残し、フレームの変数を消し、`extra` / `contexts` の本文系のキー（payload / text / body / message / reply / content /
  prompt_messages / token / error など）を伏せ、ユーザー情報を消す。送るのは例外の型・メッセージ・スタックトレース（変数なし）・メソッドと URL だけ。
  詳細は stdout のログ（`request_id` で突合）と監査ログで見る。トレース（性能計測）は送らない。
- **設定の検証エラーに入力値を含めない**（pydantic-settings の `hide_input_in_errors`）。
- **API のイメージ**: ベースイメージ（`PYTHON_IMAGE`）と uv（`UV_IMAGE`）を digest（sha256）まで固定する（`Dockerfile` の `ARG`・`fly.toml` の `[build.args]`・
  `docker-compose.yml` の既定を同じ値に）。uv は digest 固定の公式イメージからバイナリだけを取る。実行イメージには pip を入れず、ペルソナは `*.yaml` だけを
  コピーする。非 root（uid 10001）で動かす。digest の更新は Dependabot が扱えないため、毎月と Python / Debian のセキュリティ修正の公開時に手で行う
  （[06-operations.md](../handover/06-operations.md#api-のベースイメージと-uv-の更新毎月)）。
- **CI**: サードパーティのアクションはコミット SHA で固定する（コメントに版）。uv の版も固定。`pnpm audit --audit-level high` を web ジョブと毎晩の定期実行で回す。
  シークレットの検査は PR・push の範囲のコミット履歴も走査する（`check-secrets.sh --history`）。Dependabot（`.github/dependabot.yml`）で GitHub Actions・npm・uv を
  毎週更新する（公開から 7 日経った版だけ。`cooldown`）。
- **npm の脆弱性**: `pnpm.overrides` で `postcss@<8.5.23` を `^8.5.28` に上げ、vitest を 4 系に上げる（`pnpm audit --prod` / `--audit-level high` とも既知の脆弱性なし）。
- **docker-compose** の API は `127.0.0.1:8000` だけで待ち受ける（CI の checks ジョブで検査）。
- **Web の画像最適化を無効にする**（`images: { unoptimized: true }`）。`next/image` は元々使っていない（ADR-0021）。`/_next/image` の攻撃面と、実行時の
  sharp / libvips（LGPL-3.0）の読み込みを本番から外す。
- **権利表示**: `LICENSE`（非公開の著作物。権利者は「開発委託契約に定める者」とし、**依頼者が名義を確認して書き換える**）、`THIRD_PARTY_NOTICES.md`
  （本番依存のライセンス一覧。`scripts/generate-third-party-notices.py` で生成し、依存を更新したら再生成する）、`SECURITY.md`（脆弱性の報告窓口と対応の目安）を置く。

## 結果・トレードオフ

- Sentry を有効にしても、アクセストークン・DM の本文・プロンプトは送られない（再検査: 実際のトークンで強制的に 500 を起こし、送信内容にトークン・署名・
  リクエスト本文・パンくずが無いことを確認。`tests/test_observability.py`）。代わりに Sentry の画面だけでは原因の文脈が分からないことがあり、`request_id` で
  stdout のログを引く運用になる。
- 同じコミットから同じ API イメージを作れる。digest の更新は手作業になる（更新を忘れると OS のセキュリティ修正が入らない）。
- Python 依存の脆弱性の検査（`pip-audit` 等）は CI に入れていない（`uvx` での実行はロックされていない PyPI からの取得になるため。入れる場合は
  `apps/api` のロック済みの開発依存に追加する）。GitHub の Dependabot alerts / security updates はリポジトリ管理者が有効にする必要がある。
- 機械可読な SBOM（CycloneDX 等）は生成していない（`THIRD_PARTY_NOTICES.md` が人が読む一覧）。DD で求められたら CI に生成の手順を追加する。

## 代替案

- **Sentry を使わない**: 例外の検知が遅れる。スクラブした上で使えるようにした（`SENTRY_DSN` を設定しなければ何も送らない）。
- **Sentry の `send_default_pii=False` と `before_send` のヘッダー削除だけにする**: ローカル変数・パンくずからトークンや本文が漏れる（検査で再現）。
- **Renovate で digest も自動更新する**: 設定と運用の対象が増える。MVP では月 1 回の手作業とし、更新の頻度が問題になったら導入する。
- **gitleaks のジョブを追加する**: サードパーティのアクション（組織では有償ライセンスが必要）か、固定されていないバイナリを CI に追加することになる。
  既存の `check-secrets.sh` に履歴の走査を足した。
