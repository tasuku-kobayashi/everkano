# ADR-0050: portrait-studio（同一キャラクター画像生成のローカルツール）をモノレポ内の独立ディレクトリに置く

- ステータス: 採用
- 日付: 2026-09-27
- 関連: 仕様書 §12（MVP のスコープ外: 画像生成）・A16 / [ADR-0044](0044-check-scope-compliance-allowlist.md) / [ADR-0040](0040-character-calendar.md)（画像プールは事前に用意した画像） /
  実装: `portrait-studio/`（`api/` `web/` `workflows/` `scripts/` `docker/` `docs/`）

## コンテキスト

- MVP（`apps/` `packages/`）は「画像生成を実装しない」ことが受け入れ基準（A16）で、`scripts/check-scope.sh` が `apps/` と `packages/` を走査して
  Stable Diffusion / SDXL / ComfyUI 等の混入を検出する。フィードの画像は事前に用意した画像プールから選ぶ（ADR-0040）。
- 一方、キャラクター 10 体分の「同一人物のフォトリアル画像」を作る手段が無い。次フェーズの要件として、自前 GPU（RTX 5070 / 12 GB）上で
  ComfyUI を推論エンジンにした **ローカル専用の画像生成・管理ツール**（実装指示プロンプト「portrait-studio」）を作ることになった。
- このツールは Web アプリ（Vercel / Fly.io）に組み込むものではなく、開発者のマシンで動かして画像を作り、出来上がった画像だけを
  画像プール（Bunny.net）へ手で移す前提。MVP の本番イメージ・デプロイ・DB には一切触れない。

## 決定

- `portrait-studio/` をリポジトリ直下の **独立したディレクトリ**として置く。`apps/` `packages/` の外なので `check-scope.sh` の走査対象に入らず、
  MVP の A16 は従来どおり守られる（MVP のコードに画像生成が混入しないことを検査するという目的は変わらない）。
- `portrait-studio/` は **pnpm ワークスペースに含めない**（`pnpm-workspace.yaml` は `apps/*` `packages/*` のまま）。`web/` は `.npmrc` の
  `ignore-workspace=true` で独立に `pnpm install` する。`api/` は独自の `pyproject.toml` / `uv.lock`（Python 3.12、`apps/api` と同じ ruff / mypy strict の設定）。
- ルートの Prettier は `portrait-studio/` を対象外にする（`.prettierignore`）。整形は `portrait-studio/web` 側の設定で行う。
- ルート CI（`.github/workflows/ci.yml`）は変更しない。portrait-studio のテスト（pytest 57 件・vitest・Playwright）は GPU 無しでも動く
  （モック ComfyUI・モック顔エンジン）が、CI ジョブへの追加は次フェーズで判断する。
- 参照顔・生成画像・監査ログは `portrait-studio/data/`（gitignore 済み）に置き、リポジトリにはコミットしない。実在人物の顔・未成年を思わせる
  表現の禁止は `portrait-studio/docs/COMPLIANCE.md` に明記し、API は `is_synthetic` / `adult_confirmed` / `adult_only` を必須にして拒否する。

## 結果・トレードオフ

- MVP と画像生成ツールが 1 つのリポジトリで管理でき、ペルソナ（`packages/personas`）とキャラ画像の対応を追いやすい。
- 「画像生成のコードがリポジトリに存在する」状態になる。A16 の趣旨（MVP アプリに実装しない）とは矛盾しないが、DD（デューデリジェンス）で
  問い合わせがあれば本 ADR を示す。完全に分けたい場合は `git subtree split -P portrait-studio` で別リポジトリに切り出せる（依存は無い）。
- ルート CI では検査されないため、portrait-studio 側の品質ゲート（`uv run ruff check . && uv run mypy app && uv run pytest`、`pnpm typecheck && pnpm lint && pnpm test && pnpm build`）は
  変更時に手で回す（`portrait-studio/README.md`）。

## 代替案

- **別リポジトリにする**: 分離は明確だが、ペルソナと画像の対応・ライセンス表記・ADR の一貫性が二重管理になる。必要になれば subtree で切り出す。
- **`apps/portrait-studio` に置く**: `check-scope.sh` が検出して CI が失敗する。A16 の除外を増やすと MVP アプリの保護が弱まる。
- **check-scope の除外リストに追加する**: 上と同じ理由で採らない（ADR-0044 の例外は決済の文言ルールだけに限定している）。
