# docs/

everkano（Project P MVP）のドキュメント一覧。環境構築・テスト・デプロイ・環境変数はリポジトリのルートの [README.md](../README.md)。

| ディレクトリ / ファイル                         | 内容                                                                                                   | 更新のタイミング                                    |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| [handover/](handover/README.md)                 | 引き継ぎ資料（読む順番・前提・引き継ぎ直後のチェックリスト）                                           | 構成・運用・手順が変わったとき                      |
| [handover/01-architecture.md](handover/01-architecture.md) | 構成図、コンポーネント間の通信と認証、技術スタック、環境                                   |                                                     |
| [handover/02-data-flow.md](handover/02-data-flow.md) | シーケンス図（ログイン・フィード・いいね・コメントと自動返信・DM・メモリ編集・退会）              |                                                     |
| [handover/03-data-model.md](handover/03-data-model.md) | テーブル・権限マトリクス・トリガー・RPC・Realtime                                               | マイグレーションを足したとき                        |
| [handover/04-api.md](handover/04-api.md)        | Python API のエンドポイント・エラーコード・制限                                                        | エンドポイントを変えたとき                          |
| [handover/05-memory-and-moderation.md](handover/05-memory-and-moderation.md) | メモリエンジンと Gate #1                                                  |                                                     |
| [handover/06-operations.md](handover/06-operations.md) | ランブック（デプロイ・監視・監査ログの SQL・障害対応・定常作業・既知の制約）                   |                                                     |
| [handover/07-security.md](handover/07-security.md) | 脅威モデル・H4 / H7・RLS のテスト                                                                    |                                                     |
| [handover/08-dev-guide.md](handover/08-dev-guide.md) | 開発の約束・エンドポイント追加・DB 変更・テスト・コミット / PR・ブランチ保護                       |                                                     |
| [handover/supabase-auth.md](handover/supabase-auth.md) | ホスト版 Supabase の Auth 設定チェックリスト                                                   | `infra/supabase/config.toml` の Auth 設定を変えたとき |
| [adr/](adr/README.md)                           | 設計判断の記録（ADR-0001〜0021）。コードのコメントから番号で参照される                                  | 設計判断をしたとき（新しい番号で追加）              |
| [api/openapi.json](api/openapi.json)            | Python API の OpenAPI（生成物。`pnpm --filter @everkano/api openapi`）                                   | API のモデルを変えたとき（CI が差分を検出）         |
| [acceptance/report.md](acceptance/report.md)    | 受け入れ基準 A1〜A16 の検証結果、実機・第三者の確認手順、提出物（§16）の状況                            | 受け入れ確認をしたとき                              |
| [acceptance/e2e-results.md](acceptance/e2e-results.md) | E2E（Playwright）の実行結果の詳細                                                               | E2E を実行したとき                                  |
| [acceptance/raw/](acceptance/raw/)              | 各テストの生の出力（Playwright・pgTAP・pytest・監査ログのサンプル・会話ログ）                           |                                                     |
| [acceptance/screenshots/](acceptance/screenshots/) | 全 22 画面 × ライト / ダークのスクリーンショット（エミュレーション。画像はダミー）                 | `apps/web/e2e/screenshots.spec.ts` で再生成          |

各パッケージの README: [apps/web](../apps/web/README.md) / [apps/web/e2e](../apps/web/e2e/README.md) / [apps/api](../apps/api/README.md) /
[packages/personas](../packages/personas/README.md) / [packages/prompts](../packages/prompts/README.md) /
[infra/supabase/tests](../infra/supabase/tests/README.md) / [scripts](../scripts/README.md)。

Markdown は Prettier の対象外（`.prettierignore`）。図は Mermaid（GitHub でそのまま表示される）。
