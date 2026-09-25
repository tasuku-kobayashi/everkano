# 引き継ぎ資料

everkano（Project P MVP）を引き継ぐエンジニア向けの資料。環境構築・起動・デプロイはルートの [README.md](../../README.md)、
設計判断の理由は [docs/adr/](../adr/README.md)、受け入れ基準の検証結果は [docs/acceptance/report.md](../acceptance/report.md)。

## 読む順番

| #   | 資料                                                   | 内容                                                                                          |
| --- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| 0   | [README.md](../../README.md)                           | プロダクトの概要、ローカル環境構築、テスト、デプロイ、環境変数                                 |
| 1   | [01-architecture.md](01-architecture.md)               | 構成図、コンポーネント間の通信と認証、技術スタック、環境                                       |
| 2   | [02-data-flow.md](02-data-flow.md)                     | ログイン・フィード・いいね・コメントと自動返信・DM（`/chat` の 13 ステップ）・メモリ編集・退会のシーケンス図 |
| 3   | [03-data-model.md](03-data-model.md)                   | テーブル、権限マトリクス（ロール別）、トリガー、RPC、Realtime                                  |
| 4   | [04-api.md](04-api.md)                                 | Python API のエンドポイント、エラーコード、入力制限、タイムアウト、レート制限                 |
| 5   | [05-memory-and-moderation.md](05-memory-and-moderation.md) | メモリエンジンの 3 層・パラメーター・プロンプト、Gate #1 の語彙と調整方法                   |
| 6   | [06-operations.md](06-operations.md)                   | ランブック: デプロイ / ロールバック、監視、監査ログの SQL、障害対応、キャラ・投稿の追加、退会の復旧、鍵の交換、バックアップ、スケール、既知の制約 |
| 7   | [07-security.md](07-security.md)                       | 脅威モデル、H4 / H7 の守り方、RLS のテストスイート、個人データ、既知のギャップ                 |
| 8   | [08-dev-guide.md](08-dev-guide.md)                     | 開発の約束、エンドポイント追加の手順、DB 変更、テスト（E2E を含む）、コミット規約、ブランチ保護の設定 |
| —   | [supabase-auth.md](supabase-auth.md)                   | ホスト版 Supabase の Auth 設定チェックリスト（**本番構築時に必須**）                          |
| —   | [docs/adr/](../adr/README.md)                          | ADR-0001〜0021                                                                                |
| —   | 各パッケージの README                                  | [apps/web](../../apps/web/README.md) / [apps/api](../../apps/api/README.md) / [apps/web/e2e](../../apps/web/e2e/README.md) / [packages/personas](../../packages/personas/README.md) / [packages/prompts](../../packages/prompts/README.md) / [infra/supabase/tests](../../infra/supabase/tests/README.md) / [scripts](../../scripts/README.md) |

## このプロダクトの前提（変えてはいけないこと）

仕様書のハードルール（H1〜H8）とスコープ外の機能（§12）。CI（`scripts/check-*.sh`・pgTAP・E2E）とレビューで守っている。

- **H1** スマートフォン専用。PC 向けのレイアウトは作らない（アプリの幅は最大 480px）。
- **H2** 投稿できるのは AI キャラクターだけ。ユーザーの投稿機能・投稿タブは作らない。
- **H3** 決済は実装しない。有料投稿はぼかし + 鍵 + 「購入する（準備中）」のモーダルまで。
- **H4** NSFW 画像を Vercel / Supabase Storage に置かない。画像は StorageAdapter 経由で Bunny.net（オリジン Backblaze B2）。
- **H5** Python API は Vercel で動かさない（Fly.io）。
- **H6** 会話・生成・判定はすべて `audit_logs` と stdout に残す。
- **H7** シークレットはコミットしない。`.env.example` にはキー名とローカルの既定値だけ。
- **H8** Cloudflare を使わない。
- キャラクターは全員 20 歳以上の成人。未成年を想起させる表現は Gate #1 とペルソナ / シードの検証で防いでいる。
- 最も大事な価値は「キャラクターが生きていると感じられること」（仕様書 §18）: フィードが時間とともに更新されること、DM でキャラが覚えていること。

## 引き継ぎ直後にやること

- [ ] ルートの README の手順でローカル環境を作り、画面を一通り触る（ログイン → フィード → プロフィール → DM → メモリパネル）
- [ ] [受け入れ検証レポート](../acceptance/report.md) の未完了項目（A1 / A11 の実機、A15 の第三者による環境構築、実 LLM での A8〜A10）を実施する
- [ ] GitHub の main ブランチ保護（PR 必須・CI 必須）を設定する（[08-dev-guide.md](08-dev-guide.md#main-ブランチの保護github-での設定)）
- [ ] Supabase / Vercel / Fly.io / LLM 提供元 / SMTP / Backblaze B2 / Bunny.net / Sentry のアカウントを引き継ぎ、旧担当者の権限を外す
- [ ] シークレットをすべてローテーションする（[06-operations.md](06-operations.md#シークレットのローテーション)）
- [ ] ホスト版 Supabase の Auth 設定が [supabase-auth.md](supabase-auth.md) と一致しているか確認する
- [ ] 監視・アラートを設定する（[06-operations.md](06-operations.md#監視とログ)）
- [ ] 監査ログの保存期間・個人データの扱い・LLM 提供元のデータ利用条件を事業側と決める（[07-security.md](07-security.md#個人データの扱い)）
- [ ] 既知の制約と次フェーズの項目を確認する（[06-operations.md](06-operations.md#既知の制約と次フェーズ)）
