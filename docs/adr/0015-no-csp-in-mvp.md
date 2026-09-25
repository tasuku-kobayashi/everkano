# ADR-0015: MVP では Content-Security-Policy を設定しない

- ステータス: 採用（一般公開・売却前に再検討する）
- 日付: 2026-09-25
- 関連: [07-security.md](../handover/07-security.md) / 実装: `apps/web/next.config.ts`（`securityHeaders`）

## コンテキスト

- Web が接続・読み込みする先は環境ごとに変わる: Supabase（`https://<ref>.supabase.co` と Realtime の `wss://`）、Python API（Fly.io）、
  画像（開発・staging はプレースホルダ画像ホスト、本番は Bunny.net CDN）。
- Next.js（App Router）はインラインスクリプトを出力するため、`script-src` を厳しくするには nonce を発行する middleware と動的レンダリングが必要。
- 誤った CSP は「画像が出ない」「Realtime がつながらない」「Service Worker が登録できない」といった、スマホ実機でしか気付きにくい障害になる。

## 決定

- MVP では CSP ヘッダーを設定しない。
- 代わりに次のセキュリティヘッダーを全パスに付ける: `X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`、
  `X-Frame-Options: DENY`、`Permissions-Policy`（camera / microphone / geolocation / payment / usb / browsing-topics を無効化）。`X-Powered-By` は出さない。
- XSS の主な対策は React のエスケープに頼る（`dangerouslySetInnerHTML` を使わない）。ユーザー由来のテキストは API 側でも長さと制御文字を検証する。
- 検索エンジンにはインデックスさせない（`robots: { index: false }`）。

## 結果・トレードオフ

- XSS が起きた場合の被害軽減（外部へのデータ送信の遮断など）が無い。セッションは Supabase の Cookie / ストレージにあるため、XSS はアカウント乗っ取りにつながり得る。
- 一般公開・事業売却前に、次の方針で CSP を導入する（新しい ADR を書く）:
  1. `middleware.ts` でリクエストごとに nonce を発行し、`script-src 'self' 'nonce-...' 'strict-dynamic'`。
  2. `connect-src` は `NEXT_PUBLIC_SUPABASE_URL`（https と wss）と `NEXT_PUBLIC_API_BASE_URL`、`img-src` は `NEXT_PUBLIC_CDN_BASE_URL` から組み立てる。
  3. `frame-ancestors 'none'`、`object-src 'none'`、`base-uri 'self'`。
  4. まず `Content-Security-Policy-Report-Only` で staging に出し、実機（iOS Safari / Android Chrome / ホーム画面の PWA）で違反が出ないことを
     確認してから本番に適用する。

## 代替案

- **固定の CSP を `next.config.ts` に書く**: 環境ごとの接続先を列挙しきれず、プレースホルダ画像ホストや CDN の変更で壊れる。
- **Report-Only で導入**: 収集先（レポートのエンドポイント）が必要で、MVP の範囲を超える。公開前の導入手順として上に残した。
