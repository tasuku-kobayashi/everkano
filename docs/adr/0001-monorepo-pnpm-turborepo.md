# ADR-0001: モノレポ構成（pnpm + Turborepo）

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §2「リポジトリ」・§3 / 実装: `package.json`, `pnpm-workspace.yaml`, `turbo.json`, `apps/api/package.json`

## コンテキスト

- 仕様書 §2 は「pnpm workspaces + Turborepo のモノレポ」、§3 はリポジトリのディレクトリ構成を指定している。
- TypeScript（Next.js の Web）と Python（FastAPI の API）が同じリポジトリに入る。ペルソナ YAML とプロンプトテンプレートは
  API が実行時に読み込み、型の契約（`packages/shared`）は Web と API の両方に関わる。
- 事業売却を前提にしているため、引き継ぐ側が 1 つのコマンド体系（`pnpm lint` / `typecheck` / `test`）で全体を検証できる必要がある。

## 決定

- **pnpm workspaces**（`apps/*`, `packages/*`）と **Turborepo** でタスクを束ねる。ルートの `pnpm dev` / `lint` / `typecheck` /
  `test` / `build` は `turbo run <task>`。
- パッケージ名は `@everkano/web`・`@everkano/api`・`@everkano/shared`・`@everkano/personas`・`@everkano/prompts`。
- バージョンを固定する: `packageManager: pnpm@10.33.0`、`engines`（Node >= 22, pnpm >= 10）+ `.npmrc` の `engine-strict=true`、
  `.nvmrc` = 22、Python は `apps/api/.python-version` = 3.12。
- Python API（`apps/api`）は **uv** で管理する（`pyproject.toml` + `uv.lock`）。turbo から呼べるよう、`apps/api/package.json` を
  「uv のコマンドを呼ぶだけのラッパー」として置く（`pnpm --filter @everkano/api test` = `uv run pytest`）。
- `packages/personas`（YAML）と `packages/prompts`（テンプレート）は **データのパッケージ**。API が `PERSONAS_DIR` / `PROMPTS_DIR`
  （未設定ならリポジトリ内の `packages/` を探す）から読み込み、Docker イメージには `/srv/everkano/{personas,prompts}` としてコピーする
  （そのため Docker のビルドコンテキストはリポジトリのルート）。
- `packages/shared` は Web / API の型契約。`database.types.ts` は `supabase gen types` の生成物、`api.ts` は API の入出力型。
- 整形は Prettier（ルート）。Web は ESLint、Python は ruff（lint / format）と mypy（strict）。ルートの `pnpm lint` は
  各パッケージの lint に加えて `prettier --check .` を実行する（`*.md` / `*.sql` / `*.yaml` / `apps/api` は `.prettierignore` で対象外）。

## 結果・トレードオフ

- `@everkano/api#test` は turbo のキャッシュを使わない（`turbo.json`）。テストが `packages/personas`・`packages/prompts`・DB の状態に
  依存し、`apps/api` 配下のファイルだけではキャッシュキーにならないため。`TEST_DATABASE_URL` は `passThroughEnv` で pytest に渡す
  （turbo 2 の strict env モード対策）。
- turbo の `build` は `NEXT_PUBLIC_*` / `BUNNY_*` / `SENTRY_*` だけを環境変数として受け取る。それ以外の変数を `pnpm build`
  （turbo 経由）で渡しても Next.js には届かない（例: `NEXT_DIST_DIR` は `cd apps/web && pnpm build` で渡す）。
- Node と Python の 2 つのツールチェーン（pnpm と uv）を入れる必要がある。README の前提ツールに明記した。
- ペルソナ YAML と `infra/supabase/seed.sql` の整合は `pnpm personas:validate`（CI の checks ジョブ）で検査する（[ADR-0020](0020-content-seed-generation.md)）。

## 代替案

- **Python を別リポジトリにする**: 型契約（`api.ts` ↔ Pydantic）とペルソナ YAML の同期が崩れやすく、仕様書 §3 の構成にも反する。不採用。
- **Nx**: 仕様で Turborepo が指定されている。不採用。
- **Poetry / pip-tools**: uv の方がロックと仮想環境の作成が速く、CI（`astral-sh/setup-uv`）と Docker（`uv sync --frozen`）で同じ手順を再現できる。
