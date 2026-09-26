# ADR-0021: Web の UI 実装方針（スマホ専用シェル・Instagram 準拠のトークン・自作 SVG アイコン）

- ステータス: 採用（読ませる文字の色・アクセシビリティの約束・端末の「戻る」・ホームの再読み込みを [ADR-0031](0031-web-ui-accessibility-and-dev-only-pages.md) で追補。`/dev/ui` の扱いは ADR-0031 により置き換え。「AIキャラクター」バッジなどキャラクターエンジンの画面を [ADR-0047](0047-web-engine-ui.md) で追補）
- 日付: 2026-09-25
- 関連: 仕様書 §4・H1・H2・§2（`next/image` 不使用） / 実装: `apps/web/app/globals.css`, `apps/web/components/ui/`（`main-shell.tsx`, `tab-bar.tsx`, `icons.tsx`, `wordmark.tsx`, `image.tsx`）, `apps/web/README.md`

## コンテキスト

- H1: スマートフォン専用。PC レイアウトは作らない。仕様書 §4: Instagram のモバイルアプリの UI/UX を忠実に再現し、ダークモードは端末設定に追従する。
- H2: 投稿できるのは AI キャラクターだけ。投稿タブは存在しない（下部タブは ホーム / 検索 / DM / プロフィール の 4 つ）。
- 表示速度（スマホ回線）と、引き継ぎ後の保守のしやすさを両立したい。

## 決定

- **アプリシェルは最大幅 480px の 1 カラム**（`max-w-[480px] mx-auto`）。デスクトップ向けのブレークポイントは作らない。
- **デザイントークンを CSS 変数 `--ig-*`**（背景 白 / 黒、文字 `#000` / `#f5f5f5`、補助 `#737373` / `#a8a8a8`、区切り `#dbdbdb` / `#262626`、
  アクセント `#0095f6`、いいね `#ff3040` など）として `globals.css` に定義し、`prefers-color-scheme: dark` で値を切り替える。Tailwind CSS v4 は
  `@theme inline` でこの変数を参照する。部品は
  `bg-ig-bg` / `text-ig-text` などのトークンだけを使う（ダークモードが自動で効く）。
- フォントはシステムフォント（`-apple-system, "Hiragino Sans", "Noto Sans JP", Roboto, sans-serif`）。**Web フォントを読み込まない**。
  ロゴ（ワードマーク）は Grand Hotel（SIL OFL 1.1）のグリフを輪郭化したインライン SVG。
- **アイコンは手作りのインライン SVG**（ホーム・検索・紙飛行機・ハート・吹き出し・シェア・ブックマーク・…・鍵・戻る・i・グリッド）。アイコンライブラリは使わない。
- 下部タブバーは 4 タブ（投稿タブなし）。`/posts/[postId]` と `/dm/[characterId]` ではタブバーを隠し、固定の入力フッターを出す。
- iOS 対策: 入力欄は 16px 以上（フォーカス時の自動ズーム防止。ピンチズームは制限しない）、固定要素は `env(safe-area-inset-*)` を考慮、`viewport-fit=cover`。
- 画像は `next/image` を使わず `<img loading="lazy" decoding="async">` + StorageAdapter（[ADR-0011](0011-storage-adapter-and-bunny-token-auth.md)）。
- サーバー状態は TanStack Query v5。フィードはカーソル方式（`published_at DESC, id DESC`。予約投稿が公開されて先頭に増えても重複・欠落しない）、
  DM は 30 件ずつ遡る。書き込みは楽観的更新 + 失敗時ロールバック。UI 文言は日本語、識別子は英語。
- 開発用の UI 部品カタログ `/dev/ui`（本番ビルドでは 404）。

## 結果・トレードオフ

- 依存が少なく（UI ライブラリ・アイコンライブラリ・Web フォント無し）、初回表示が軽い。見た目を Instagram に寄せやすい。
- アイコンや部品を自前で保守する必要がある。新しいアイコンは `components/ui/icons.tsx` に同じ線幅・サイズの規則で追加する。
- PC で開いても 480px 幅で中央に表示されるだけ（H1 どおり）。
- E2E（`layout.spec.ts` / `dark-mode.spec.ts`）で、390px / 412px 幅での横はみ出し無し・入力欄 16px 以上・タブが 4 つ・ダークモードの配色を検査している。
  実機（iOS Safari / Android Chrome）の確認は別途必要（[acceptance report](../acceptance/report.md)）。

## 代替案

- **UI コンポーネントライブラリ（MUI / shadcn/ui 等）**: Instagram の見た目から離れ、上書きのコストが大きい。
- **アイコンライブラリ（lucide 等）**: 線の太さ・形が Instagram と異なる。必要な数も少ない。
- **レスポンシブ（PC 対応）**: H1 で禁止。
