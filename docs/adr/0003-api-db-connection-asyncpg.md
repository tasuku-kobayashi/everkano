# ADR-0003: Python API の DB 接続（asyncpg + postgres ロール + 明示的な所有者チェック）

- ステータス: 採用（DB 接続の TLS 必須化を [ADR-0025](0025-db-tls-and-api-entry-failures.md) で追補）
- 日付: 2026-09-25
- 関連: [ADR-0002](0002-data-access-split.md) / 実装: `apps/api/app/core/db.py`, `apps/api/app/services/*.py`, `apps/api/tests/integration/`

## コンテキスト

- API は会話の作成・メッセージの保存・記憶の抽出 / 要約 / 検索（pgvector）・監査ログの書き込みを行う。クライアントには書き込み権限が無い
  テーブル（[ADR-0002](0002-data-access-split.md)）に書くため、`authenticated` ロールでは実行できない。
- `/chat` は 1 リクエストで複数のクエリ（所有者確認・短期履歴・ベクトル検索・2 件の INSERT・記憶の保存）を発行する。遅延を抑えたい。
- ベクトル（`extensions.vector(1536)`）と jsonb を扱う。

## 決定

- **asyncpg** で `DATABASE_URL`（`postgres` ロール）に直接接続する。ORM は使わず、SQL はサービス層に定数として書く。
- **RLS はバイパスされる前提で、すべてのクエリを検証済みの `user_id`（JWT の `sub`）でスコープする**:
  - 会話: `where c.id = $1 and c.user_id = $2 and c.character_id = $3 and ch.is_active`（`services/chat.py`）
  - 記憶: `where id = $1 and user_id = $2`（`services/user_memories.py`、要約・重複排除も同様）
  - コメント対象の投稿: クライアントの RLS と同じ条件（`published_at <= now()` かつ有効キャラ）を SQL で再現する（`services/comments.py`）
- 他人のリソースは **404 `not_found`**（403 ではなく存在自体を教えない）。
- 接続設定（`app/core/db.py`）:
  - プール `DATABASE_POOL_MIN_SIZE` / `DATABASE_POOL_MAX_SIZE`（既定 1 / 10）、`command_timeout=30`、`application_name=everkano-api`。
  - `DATABASE_STATEMENT_CACHE_SIZE`（既定 100）。Supavisor の transaction mode（:6543）経由では `0` にする（プリペアドステートメント非対応のため）。
  - jsonb / json は Python の dict と相互変換するコーデックを登録。
  - ベクトルはテキストリテラル `'[0.1,...]'` で渡し、SQL 側で `$n::text::extensions.vector` にキャストする。演算子は
    `operator(extensions.<=>)` と完全修飾する（search_path に依存しない）。
- 監査ログはチャットのトランザクションとは別の接続で書く（[ADR-0013](0013-audit-log.md)）。

## 結果・トレードオフ

- `DATABASE_URL` は RLS をバイパスできる強い資格情報。Fly.io の secrets にだけ置く（H7）。漏えい時は DB パスワードを再設定する
  （[06-operations.md](../handover/06-operations.md#シークレットのローテーション)）。
- **スコープ漏れ = 他人のデータの漏えい** になる。対策:
  - 統合テスト（`tests/integration/test_chat_api.py::test_chat_ownership`、`test_memory_api.py::test_memories_crud_and_ownership` など）で
    他人の会話・記憶が 404 になることを検証。E2E（`apps/web/e2e/rls.spec.ts`）でも 2 アカウントで API の 404 を確認している。
  - PR テンプレートのチェックリストに「API の DB アクセスは `user_id` で所有者チェックしている」を入れている。
- ORM が無いぶん、スキーマ変更時は SQL 文字列の修正漏れに注意（DB 型の生成物 `database.types.ts` は Web 用で、Python 側には効かない）。
  統合テストが実 DB に対して動くので、列名の誤りはテストで検出される。

## 代替案

- **ユーザーの JWT で接続して RLS を効かせる**（`set local role authenticated; set local request.jwt.claims = ...`）: 二重の防御になるが、
  クライアントに与えていない書き込み（messages / memories など）のために別のポリシーやロールが必要になり、記憶の要約・再埋め込みなど
  ユーザー文脈の無い処理と経路が分かれる。MVP では所有者チェック + テストで担保する。
- **supabase-py（PostgREST 経由）**: pgvector の距離計算を含む SQL を書けず、トランザクション（ユーザー発言とキャラ返答の同時保存）も扱えない。
- **SQLAlchemy / ORM**: 依存と抽象が増える割に、クエリは少数で固定的。asyncpg の直接利用の方が速く読みやすい。
- **専用の DB ロール（必要なテーブルだけ grant）**: 最小権限としては望ましい。次の改善候補（[06-operations.md](../handover/06-operations.md#既知の制約と次フェーズ)）。
