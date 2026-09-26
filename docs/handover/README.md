# 引き継ぎ資料

everkano（Project P MVP）を引き継ぐエンジニア向けの資料。環境構築・起動・デプロイはルートの [README.md](../../README.md)、
設計判断の理由は [docs/adr/](../adr/README.md)、受け入れ基準の検証結果は [docs/acceptance/report.md](../acceptance/report.md)。
MVP の後に追加したキャラクターエンジン v1.0（記憶 × カレンダー × 好感度 × 自発メッセージ）は、各資料の該当箇所と [ADR-0035](../adr/0035-character-engine-architecture.md)〜
[ADR-0049](../adr/0049-prompt-order-and-prefix-cache.md)、評価ハーネスとその結果は [docs/eval/](../eval/README.md)。

## 読む順番

| #   | 資料                                                   | 内容                                                                                          |
| --- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| 0   | [README.md](../../README.md)                           | プロダクトの概要、ローカル環境構築、テスト、デプロイ、環境変数                                 |
| 1   | [01-architecture.md](01-architecture.md)               | 構成図、コンポーネント間の通信と認証、キャラクターエンジンの構成（`app` / `worker` のプロセスグループ・ジョブ・スケジューラ）、技術スタック、環境 |
| 2   | [02-data-flow.md](02-data-flow.md)                     | ログイン・フィード・いいね・コメントと自動返信・DM（`/chat/stream`）・返答の後のジョブ・メモリパネルと約束・予定と状態・自発メッセージ・日次の処理・退会のシーケンス図 |
| 3   | [03-data-model.md](03-data-model.md)                   | テーブル（エンジンのテーブルを含む）、権限マトリクス（ロール別）、トリガー、RPC、Realtime      |
| 4   | [04-api.md](04-api.md)                                 | Python API のエンドポイント（`/chat/stream` の SSE・約束・自発メッセージの設定・相談窓口を含む）、エラーコード、入力制限、タイムアウト、レート制限 |
| 5   | [05-memory-and-moderation.md](05-memory-and-moderation.md) | 記憶エンジン v2 の層・パラメーター・プロンプト、Gate #1・E6 の安全対応・OutputGuard（E2 / E3）の語彙と調整方法 |
| 6   | [06-operations.md](06-operations.md)                   | ランブック: デプロイ / ロールバック（worker を含む）、監視、監査ログ・エンジンの状態の SQL、障害対応、キャラ・投稿の追加、相談窓口の確認、退会の復旧、鍵の交換、バックアップ、スケール、既知の制約 |
| 7   | [07-security.md](07-security.md)                       | 脅威モデル、H4 / H7・エンジンの E1〜E9 の守り方、RLS のテストスイート、個人データ、既知のギャップ |
| 8   | [08-dev-guide.md](08-dev-guide.md)                     | 開発の約束、エンドポイント追加の手順、DB 変更、テスト（E2E を含む）、キャラクターエンジンの開発（worker・評価ハーネス・ペルソナ）、コミット規約、ブランチ保護の設定 |
| —   | [supabase-auth.md](supabase-auth.md)                   | ホスト版 Supabase の Auth 設定チェックリスト（**本番構築時に必須**）                          |
| —   | [docs/adr/](../adr/README.md)                          | ADR-0001〜0049（0035 以降がキャラクターエンジン）                                              |
| —   | [docs/eval/](../eval/README.md)                        | 評価ハーネスの使い方・指標・判定のプロンプト・結果の推移（エンジン仕様書 §9）                  |
| —   | [character-engine-report.md](../character-engine-report.md) | キャラクターエンジン v1.0 の最終報告（全指標の結果・コスト・レイテンシ・残っている確認事項）      |
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
- キャラクターエンジンのハードルール（エンジン仕様書 E1〜E9。守り方は [07-security.md](07-security.md#エンジン仕様書のハードルールe1e9の守り方)）:
  **E1** 課金を好感度に影響させない（構造テスト）/ **E2** 購入と関係を結びつけない / **E3** 実在の人間だと主張しない・「AIキャラクター」バッジ /
  **E4** 自発メッセージの上限・送らない時間帯・停止 / **E5** ユーザーが編集・削除した記憶を上書き・復活させない / **E6** 自傷・希死念慮には安全対応を優先 /
  **E7** 1 ユーザー月 ¥100 前後 / **E8** 最初の文字まで中央値 2.5 秒 / **E9** 状態の変化をすべて監査ログに。好感度はユーザーに見せない（A11）。
- 最も大事な価値は「キャラクターが生きていると感じられること」（仕様書 §18）: フィードが時間とともに更新されること、DM でキャラが覚えていること。

## 引き継ぎ直後にやること

- [ ] ルートの README の手順でローカル環境を作り、画面を一通り触る（ログイン → フィード → プロフィール → DM → メモリパネル。
      「覚えました」の通知は会話が止まって約 3 分後（最大 18 分）に出る。すぐ見るなら `ENGINE_POST_TURN_DELAY_SECONDS=1`）
- [ ] [受け入れ検証レポート](../acceptance/report.md) の未完了項目（A1 / A11 の実機、A15 の第三者による環境構築、実 LLM での A8〜A10）を実施する
- [ ] GitHub の main ブランチ保護（PR 必須・CI 必須）を設定する（[08-dev-guide.md](08-dev-guide.md#main-ブランチの保護github-での設定)）
- [ ] Supabase / Vercel / Fly.io / LLM 提供元 / SMTP / Backblaze B2 / Bunny.net / Sentry のアカウントを引き継ぎ、旧担当者の権限を外す
- [ ] シークレットをすべてローテーションする（[06-operations.md](06-operations.md#シークレットのローテーション)）
- [ ] ホスト版 Supabase の Auth 設定が [supabase-auth.md](supabase-auth.md) と一致しているか確認する
- [ ] 監視・アラートを設定する（[06-operations.md](06-operations.md#監視とログ)）
- [ ] 監査ログの保存期間・個人データの扱い・LLM 提供元のデータ利用条件を事業側と決める（[07-security.md](07-security.md#個人データの扱い)）
- [ ] 既知の制約と次フェーズの項目を確認する（[06-operations.md](06-operations.md#既知の制約と次フェーズ)）
- [ ] 納品前の検査の未対応の項目と推奨する対応を確認する（[inspection-report.md](../acceptance/inspection-report.md)。特に CAPTCHA・DB の証明書の検証・`LICENSE` の権利者）
- [ ] Fly.io で `app` と `worker` の両方のプロセスグループが動いていることを確かめる（`fly scale count app=1 worker=1 --config apps/api/fly.toml`。[06-operations.md](06-operations.md#デプロイとロールバック)）
- [ ] **公開前に** E6 の相談窓口の番号・受付時間を公式サイトで確かめる（[06-operations.md](06-operations.md#相談窓口の番号の確認公開前定期)）
- [ ] `LLM_MODEL` と価格表（`ENGINE_PRICE_TABLE_JSON`）を確かめ、API キーで評価ハーネスを live で実行して E7 / E8 と言語の質を確かめる（ここまでの結果は mock。
      [06-operations.md](06-operations.md#live-の-llm-に切り替える前モデルや価格が変わったとき)）
- [ ] 予定からの投稿の画像プール（`post_image_pool`）を本番の画像に差し替える（[06-operations.md](06-operations.md#画像を本番の-cdn-に移す)）
