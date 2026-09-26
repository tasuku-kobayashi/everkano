# ADR（Architecture Decision Records）

仕様書（開発依頼書 Project P MVP v0.1）から一歩踏み込んだ設計判断と、仕様との差分を理由とあわせて記録する。
ADR-0035 以降はキャラクターエンジン v1.0（追加仕様「実装指示書 Project P キャラクターエンジン v1.0（記憶 × カレンダー × 好感度）」。ADR では「エンジン仕様書」）の判断。
コード・マイグレーション・テストのコメントにある「ADR-000N」はこのディレクトリのファイルを指す（**番号は変えない**）。

| #                                                             | タイトル                                                                                      | ステータス           |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------- |
| [ADR-0001](0001-monorepo-pnpm-turborepo.md)                   | モノレポ構成（pnpm + Turborepo）                                                              | 採用                 |
| [ADR-0002](0002-data-access-split.md)                         | データアクセス分離（読み取りは Supabase RLS 直結・ユーザー由来テキストの書き込みは API 経由） | 採用（0030 で追補）  |
| [ADR-0003](0003-api-db-connection-asyncpg.md)                 | Python API の DB 接続（asyncpg + postgres ロール + 明示的な所有者チェック）                   | 採用（0025 で追補）  |
| [ADR-0004](0004-schema-changes-from-spec.md)                  | 仕様スキーマからの追加・変更点（comments の制約矛盾・コメント可視性・退会の一方向化を含む）   | 採用                 |
| [ADR-0005](0005-vector-index-and-exact-memory-search.md)      | ベクトル索引 HNSW と user×character 内の厳密検索                                              | 採用（0024 で追補）  |
| [ADR-0006](0006-paid-post-private-assets.md)                  | 有料投稿アセットの分離（post_private_assets）                                                 | 採用                 |
| [ADR-0007](0007-jwt-verification-jwks-and-hs256.md)           | JWT 検証（JWKS 非対称鍵 + 旧 HS256）                                                          | 採用（0025・0028 で追補） |
| [ADR-0008](0008-llm-embedding-providers-and-mock.md)          | LLM / Embedding プロバイダ抽象化とモックモード                                                | 採用（0022・0035・0037 で追補） |
| [ADR-0009](0009-memory-engine.md)                             | メモリエンジン設計（ターン定義・抽出の並行実行・重複排除・ユーザー編集の保護・中期要約）      | 採用（抽出の並行実行・再注入・要約の実行方法は 0038 により置き換え。0022・0024・0028・0039 で追補） |
| [ADR-0010](0010-gate1-moderation.md)                          | Gate #1 モデレーション設計                                                                    | 採用（0023・0027・0043 で追補） |
| [ADR-0011](0011-storage-adapter-and-bunny-token-auth.md)      | StorageAdapter と Bunny.net トークン認証                                                      | 採用                 |
| [ADR-0012](0012-pwa-and-login-magic-link-otp.md)              | PWA とログイン方式（マジックリンク + 6 桁コード）                                             | 採用（リンクの扱いは 0026 により置き換え。0032・0033 で追補） |
| [ADR-0013](0013-audit-log.md)                                 | 監査ログ設計（イベント種別と payload）                                                        | 採用（0022・0024・0027・0028・0035 で追補） |
| [ADR-0014](0014-comments-via-api-and-auto-reply.md)           | コメント投稿の API 経由化とキャラ自動返信                                                     | 採用（0027 で追補）  |
| [ADR-0015](0015-no-csp-in-mvp.md)                             | MVP では Content-Security-Policy を設定しない                                                 | 採用（公開前に再検討） |
| [ADR-0016](0016-realtime-anon-primary-key-grant.md)           | Realtime 対象テーブルの anon への主キー列 grant                                               | 採用                 |
| [ADR-0017](0017-discard-unverified-password.md)               | メール確認前パスワードの破棄と Confirm email 必須（事前乗っ取り対策）                          | 採用（0033 で追補）  |
| [ADR-0018](0018-in-process-rate-limit.md)                     | レート制限はプロセス内のスライディングウィンドウ（1 マシン 1 プロセス）                       | 採用（スケール時に再検討。0024 で追補） |
| [ADR-0019](0019-chat-deadline.md)                             | `/chat` の締め切り（CHAT_DEADLINE_SECONDS）と「何も保存しない」失敗                           | 採用（抽出の待ち方は 0038、ストリーミングしないことは 0037 により置き換え。0022・0029・0037 で追補） |
| [ADR-0020](0020-content-seed-generation.md)                   | コンテンツ管理（ペルソナ YAML → seed.sql の生成・予約投稿・成人のみの検証）                   | 採用（0035・0040 で追補） |
| [ADR-0021](0021-web-ui-implementation.md)                     | Web の UI 実装方針（スマホ専用シェル・Instagram 準拠のトークン・自作 SVG アイコン）            | 採用（0031・0047 で追補。`/dev/ui` の扱いは 0031 により置き換え） |
| [ADR-0022](0022-embedding-failures-and-audit-additions.md)    | 埋め込みの障害時の扱い（検索の省略・専用のタイムアウト）と監査ログの追加項目                  | 採用                 |
| [ADR-0023](0023-gate1-latin-and-romaji-terms.md)              | Gate #1 のラテン文字・ローマ字の照合と残存リスク                                              | 採用                 |
| [ADR-0024](0024-memory-capacity-per-pair.md)                  | ユーザー × キャラあたりの記憶の上限と自動記憶の入れ替え                                       | 採用（0038 で追補） |
| [ADR-0025](0025-db-tls-and-api-entry-failures.md)             | API → DB の TLS 必須化と、API の入口での失敗の分類（本文サイズ上限・認証サーバー障害）         | 採用                 |
| [ADR-0026](0026-magic-link-confirm-page.md)                   | マジックリンクは確認画面を表示し、POST で初めてログインする                                   | 採用（0048 で追補） |
| [ADR-0027](0027-comment-reply-generation-limits.md)            | `POST /comments/generate` の制限（自分のコメントだけ・コメント 1 件につき返信 1 件）と公開返信のリンクの差し止め | 採用 |
| [ADR-0028](0028-llm-input-budgets-and-summary-retries.md)     | LLM 呼び出しの上限（DM 履歴の文字数・中期要約のチャンク化と失敗時のバックオフ・外部 HTTP の接続数） | 採用（DM 履歴の上限の値は 0035 により置き換え。0038 で追補） |
| [ADR-0029](0029-web-network-failure-policy.md)                | Web の通信失敗の扱い（タイムアウト・再試行・オフライン・「ログイン切れ」の判定・エラー文言）   | 採用（0047 で追補） |
| [ADR-0030](0030-web-data-fetching-dm-and-prefetch.md)         | Web のデータ取得（既存の DM 会話の直接の読み取り・差分での回収・遷移先の先読み・購読の絞り込み） | 採用（0047 で追補） |
| [ADR-0031](0031-web-ui-accessibility-and-dev-only-pages.md)   | Web の UI の追補（読ませる文字の色・アクセシビリティ・端末の「戻る」・ホームの再読み込み・開発専用ページ） | 採用（0048 で追補） |
| [ADR-0032](0032-service-worker-versioning.md)                 | Service Worker のデプロイごとの更新とキャッシュの上限・オフラインページの復帰                  | 採用 |
| [ADR-0033](0033-auth-hardening-password-otp-captcha.md)       | 認証の追加の守り（パスワード設定の無効化・コードの有効期限 15 分・CAPTCHA は Web の対応後・利用停止） | 採用（CAPTCHA は未導入） |
| [ADR-0034](0034-supply-chain-and-telemetry-minimization.md)   | 依存・ビルドの固定と、外部へ送る情報の最小化（Sentry のスクラブ・画像最適化の無効化・権利表示） | 採用 |
| [ADR-0035](0035-character-engine-architecture.md)            | キャラクターエンジンの全体構成（モジュールと契約・Context Assembler・用途別のモデル・構造化出力・時計・機能フラグ・監査ログ）と着手前の前提 | 採用 |
| [ADR-0036](0036-engine-job-queue-and-scheduler.md)            | 非同期ジョブと定期実行の基盤（Postgres のキュー `engine_jobs`・スケジューラ・worker プロセスグループ） | 採用 |
| [ADR-0037](0037-chat-streaming-sse.md)                        | DM の返答のストリーミング（`POST /chat/stream`・SSE・文単位のフラッシュと出力の検査）          | 採用 |
| [ADR-0038](0038-memory-engine-v2.md)                          | 記憶エンジン v2（種類・返答の後の 1 回の分析・矛盾の置き換え・約束・ランキング・キャラ側の記憶） | 採用 |
| [ADR-0039](0039-user-edited-memory-protection.md)             | ユーザーが編集・削除した記憶の保護（E5）と記憶経由の注入の防止                                | 採用 |
| [ADR-0040](0040-character-calendar.md)                        | キャラクターカレンダー（ルールだけの決定的な生成・1 件 1 行の予定・一貫性・祝日と季節・状態の反映・フィード投稿） | 採用 |
| [ADR-0041](0041-affinity-engine.md)                           | 好感度（6 つの軸・隔離した評価・操作の検知・上限・段階とヒステリシス・ユーザーに見せない・E1 の構造テスト） | 採用 |
| [ADR-0042](0042-proactive-messenger.md)                       | 自発メッセージ（きっかけと優先度・E4 の上限・送らない時間帯・連投しない・冪等・設定 API・A/B テストの設計） | 採用（A/B テストは未実装） |
| [ADR-0043](0043-safety-e6-and-output-guard.md)                | 安全対応（E6 を Gate #1 より前に・相談窓口）と出力の追加の検査（E2 / E3）                     | 採用（相談窓口の番号は公開前に要確認） |
| [ADR-0044](0044-check-scope-compliance-allowlist.md)          | スコープ外機能の検査（check-scope）の E1 / E2 の例外（許可リストと「禁止を述べる行」）         | 採用 |
| [ADR-0045](0045-evaluation-harness.md)                        | 評価ハーネス（プロセス内の早送り・シミュレーションユーザー・mock / live・費用の計測・素の LLM との比較） | 採用 |
| [ADR-0046](0046-engine-cost-and-latency.md)                   | コストとレイテンシの方針（E7 ¥100 前後・E8 中央値 2.5 秒。デバウンス 180 秒・計測値）         | 採用（live の計測は未実施） |
| [ADR-0047](0047-web-engine-ui.md)                             | Web のキャラクターエンジンの画面（AI バッジ・ストリーミング・相談窓口のカード・自発メッセージの設定・メモリパネル・状況の表示） | 採用 |
| [ADR-0048](0048-web-confirm-history-hydration-fixes.md)       | Web の補修（確認画面の fetch 送信・「戻る」の履歴の書き込みの保留・スケルトンの高さ・`RouteContent` による #418 の解消） | 採用 |
| [ADR-0049](0049-prompt-order-and-prefix-cache.md)             | DM のプロンプトの順序と履歴の窓（DeepSeek のプレフィックスキャッシュに合わせる）              | 採用 |

## 書き方

- ファイル名は `NNNN-kebab-case.md`。番号は欠番にせず連番で増やす（次は 0050）。
- 一度「採用」した ADR の本文は書き換えない。判断を変えるときは新しい ADR を書き、古い方のステータスを「ADR-00XX により置き換え」に変える
  （誤字や、実装の場所を示すリンクの修正は可）。判断は変えずに項目を足すだけなら、新しい ADR に書き、古い方のステータスに「ADR-00XX で追補」と添える。
- コード側には「ADR-000N」と番号で参照を残す。
- 下のテンプレートを使う（見出しの順番もそろえる）。

```markdown
# ADR-NNNN: タイトル

- ステータス: 提案 / 採用 / 置き換え（ADR-XXXX）/ 廃止
- 日付: YYYY-MM-DD
- 関連: 仕様書の該当箇所 / 関連 ADR / 実装の場所

## コンテキスト

何が問題で、どんな制約があるか（仕様書の該当箇所も）。

## 決定

何をするか。設定値・実装の場所まで具体的に。

## 結果・トレードオフ

この決定で得たもの・失ったもの。運用上の注意、将来の見直し条件。

## 代替案

採らなかった案と、その理由。
```
