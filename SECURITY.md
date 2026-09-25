# セキュリティポリシー

## 脆弱性の報告

脆弱性を見つけた場合は、**公開の Issue・PR・SNS に書かずに**、リポジトリの管理者へ非公開で報告してください。

- GitHub の「Security」タブ → 「Report a vulnerability」（Private vulnerability reporting。リポジトリ管理者が Settings → Code security で有効化する）
- 上記が使えない場合は、リポジトリの管理者（運用担当）へ直接連絡する

報告には、再現手順・影響範囲（どのデータ・どの操作に影響するか）・確認した環境（staging / 本番、端末・ブラウザ）を含めてください。
本番のユーザーデータ（DM・記憶・メールアドレス）へのアクセスを伴う検証は行わないでください。

## 対応の目安

| 深刻度 | 例 | 初動 | 修正の目安 |
| ------ | -- | ---- | ---------- |
| 緊急 | 他ユーザーの DM・記憶の閲覧（RLS・所有者チェックの不備）、シークレットの漏えい | 当日 | 24 時間以内（シークレットは即時ローテーション） |
| 高 | 認証の回避、モデレーション（Gate #1）の広範な回避 | 2 営業日以内 | 1 週間以内 |
| 中・低 | 上記以外 | 1 週間以内 | 次のリリース |

シークレットが漏えいした場合の手順は [docs/handover/06-operations.md](docs/handover/06-operations.md#シークレットのローテーション)。

## 対象

- このリポジトリのコード（`apps/web`・`apps/api`・`infra/supabase`・`packages/*`・`scripts/`）と、その設定でデプロイした環境
- 外部サービス（Supabase・Vercel・Fly.io・LLM / 埋め込みの提供元・Bunny.net・Backblaze B2）自体の脆弱性は、各サービスの窓口へ報告してください

## このリポジトリでの対策の概要

脅威モデルと対策の一覧は [docs/handover/07-security.md](docs/handover/07-security.md)。主なもの:

- RLS（全テーブル）と API の所有者チェック（pgTAP・統合テスト・E2E で検証）
- シークレットの混入チェック（`scripts/check-secrets.sh`。CI でコミット履歴も走査）
- 依存パッケージの脆弱性チェック（CI の `pnpm audit`）と更新（Dependabot）
- 既知のギャップ（CSP 未設定・レート制限がマシン単位 など）も同じ文書に記載している
