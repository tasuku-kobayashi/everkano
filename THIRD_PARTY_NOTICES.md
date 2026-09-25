# Third-party notices（依存パッケージのライセンス）

everkano が本番で使うサードパーティのパッケージと、そのライセンスの一覧。
`scripts/generate-third-party-notices.py` で生成する（手で編集しない）。依存を更新したら再生成してコミットすること。

## 帰属表示・扱いに注意が必要なもの

| パッケージ | ライセンス | 扱い |
| ---------- | ---------- | ---- |
| `caniuse-lite`（npm。Next.js のビルドが使うブラウザ対応表） | CC-BY-4.0 | データの出典の表示が必要: "Browser support data from caniuse.com (Alexis Deveria), CC BY 4.0" |
| `certifi`（Python。httpx の CA 証明書） | MPL-2.0 | ファイル単位のコピーレフト。改変して配布する場合はそのファイルのソースを公開する（改変していない） |
| `@img/sharp-libvips-*`（npm。Next.js の任意依存 sharp のネイティブライブラリ） | LGPL-3.0-or-later | インストールはされるが、画像最適化を無効（`images.unoptimized: true`）にしているため実行時には読み込まれない。配布物に含める場合は LGPL の条件（差し替え可能な形での提供など）に従う |
| `@playwright/test` / `playwright` / `playwright-core`（npm） | Apache-2.0 | Next.js の任意の peer 依存として本番依存の一覧に出るが、アプリのコードからは使っていない（E2E テスト用） |

上記以外は MIT / BSD / Apache-2.0 / ISC / PSF などの寛容なライセンス。各パッケージの著作権表示とライセンス本文は、
インストールされたパッケージ（`node_modules/<name>/LICENSE`、Python の `*.dist-info/`）に含まれる。

## Web（npm、`pnpm licenses list --prod`、linux-x86_64）

| パッケージ | バージョン | ライセンス |
| ---------- | ---------- | ---------- |
| `tslib` | 2.8.1 | 0BSD |
| `@img/sharp-linux-x64` | 0.35.4 | Apache-2.0 |
| `@playwright/test` | 1.56.1 | Apache-2.0 |
| `@swc/helpers` | 0.5.15 | Apache-2.0 |
| `detect-libc` | 2.1.2 | Apache-2.0 |
| `playwright` | 1.56.1 | Apache-2.0 |
| `playwright-core` | 1.56.1 | Apache-2.0 |
| `sharp` | 0.35.4 | Apache-2.0 |
| `source-map-js` | 1.2.1 | BSD-3-Clause |
| `caniuse-lite` | 1.0.30001812 | CC-BY-4.0 |
| `picocolors` | 1.1.1 | ISC |
| `semver` | 7.8.5 | ISC |
| `@img/sharp-libvips-linux-x64` | 1.3.3 | LGPL-3.0-or-later |
| `@img/colour` | 1.1.0 | MIT |
| `@next/env` | 15.5.26 | MIT |
| `@next/swc-linux-x64-gnu` | 15.5.26 | MIT |
| `@supabase/auth-js` | 2.117.1 | MIT |
| `@supabase/functions-js` | 2.117.1 | MIT |
| `@supabase/phoenix` | 0.4.5 | MIT |
| `@supabase/postgrest-js` | 2.117.1 | MIT |
| `@supabase/realtime-js` | 2.117.1 | MIT |
| `@supabase/ssr` | 0.12.7 | MIT |
| `@supabase/storage-js` | 2.117.1 | MIT |
| `@supabase/supabase-js` | 2.117.1 | MIT |
| `@tanstack/query-core` | 5.103.2 | MIT |
| `@tanstack/react-query` | 5.103.2 | MIT |
| `@types/node` | 22.20.4 | MIT |
| `client-only` | 0.0.1 | MIT |
| `cookie` | 1.1.1 | MIT |
| `iceberg-js` | 0.8.1 | MIT |
| `nanoid` | 3.3.19 | MIT |
| `next` | 15.5.26 | MIT |
| `postcss` | 8.5.28 | MIT |
| `react` | 19.3.0 | MIT |
| `react-dom` | 19.3.0 | MIT |
| `scheduler` | 0.28.0 | MIT |
| `server-only` | 0.0.1 | MIT |
| `styled-jsx` | 5.1.6 | MIT |
| `undici-types` | 6.21.0 | MIT |
| `zod` | 4.6.5 | MIT |

## API（Python、`uv export --no-dev`）

| パッケージ | バージョン | ライセンス | URL |
| ---------- | ---------- | ---------- | --- |
| `annotated-doc` | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| `annotated-types` | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| `anyio` | 4.15.1 | MIT | https://anyio.readthedocs.io/en/latest/ |
| `asyncpg` | 0.31.0 | Apache-2.0 |  |
| `certifi` | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| `cffi` | 2.1.1 | MIT-0 | https://cffi.readthedocs.io/ |
| `click` | 8.5.0 | BSD-3-Clause | https://click.palletsprojects.com/ |
| `cryptography` | 50.0.1 | Apache-2.0 OR BSD-3-Clause | https://cryptography.io/ |
| `fastapi` | 0.141.1 | MIT | https://github.com/fastapi/fastapi |
| `h11` | 0.16.0 | MIT License | https://github.com/python-hyper/h11 |
| `httpcore` | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore |
| `httptools` | 0.8.0 | MIT | https://github.com/MagicStack/httptools |
| `httpx` | 0.28.1 | BSD License | https://www.python-httpx.org |
| `idna` | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| `pycparser` | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| `pydantic` | 2.13.5 | MIT | https://github.com/pydantic/pydantic |
| `pydantic_core` | 2.46.5 | MIT | https://github.com/pydantic/pydantic |
| `pydantic-settings` | 2.15.0 | MIT | https://github.com/pydantic/pydantic-settings |
| `PyJWT` | 2.15.0 | MIT | https://github.com/jpadilla/pyjwt |
| `python-dotenv` | 1.2.3 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| `PyYAML` | 6.0.3 | MIT License | https://pyyaml.org/ |
| `sentry-sdk` | 2.70.0 | MIT | https://github.com/getsentry/sentry-python |
| `starlette` | 1.7.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| `typing_extensions` | 4.16.0 | PSF-2.0 | https://typing-extensions.readthedocs.io/ |
| `typing-inspection` | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| `tzdata` | 2026.4 | Apache-2.0 | https://github.com/python/tzdata |
| `urllib3` | 2.8.0 | MIT | https://urllib3.readthedocs.io |
| `uvicorn` | 0.53.0 | BSD-3-Clause | https://uvicorn.dev/ |
| `uvloop` | 0.22.1 | Apache Software License / MIT License |  |
| `watchfiles` | 1.3.0 | MIT License | https://github.com/samuelcolvin/watchfiles |
| `websockets` | 17.1 | BSD-3-Clause | https://github.com/python-websockets/websockets |
