import type { NextConfig } from "next";

/**
 * everkano Web の Next.js 設定。
 *
 * - distDir: `NEXT_DIST_DIR` で出力先を切り替えられる（複数の dev サーバーを並行起動するため。
 *   例: `NEXT_DIST_DIR=.next-browse pnpm --filter @everkano/web exec next dev -p 3001`）。
 * - next/image は使わない（画像は StorageAdapter + CDN 変換パラメータ。仕様 §2）。
 * - CSP はプレースホルダ画像ホスト等を塞がないよう本 MVP では設定しない（ADR-0015 参照）。
 */

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
  },
];

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,
  poweredByHeader: false,
  transpilePackages: ["@everkano/shared"],
  // メタデータのストリーミングを無効化し、すべての UA で <head> に出力する。
  // Next.js 15.2+ は動的ページ（/login・/・/posts/* など）のメタデータを <body> 側にストリーミングするが、
  // <link rel="manifest"> や apple-touch-icon が <head> に無いと Chrome はマニフェストを検出せず
  // （ホーム画面に追加してもスタンドアロンで起動しない）、iOS もアイコンを拾わない（受け入れ基準 A11）。
  // 本アプリのメタデータは静的か軽いクエリのみなので、ブロッキングにしても表示速度への影響は小さい。
  htmlLimitedBots: /.*/,
  // dev のフローティングインジケーターがタブバーに重なるため非表示（エラーオーバーレイは表示される）
  devIndicators: false,
  env: {
    // BUNNY_TOKEN_AUTH_KEY（サーバー専用）が設定されているかどうか「だけ」をクライアントに渡す。
    // "1" のとき StorageAdapter(bunny) は /media/[...key] 経由の署名URLを使う。キー自体は渡さない。
    NEXT_PUBLIC_MEDIA_SIGNED: process.env.BUNNY_TOKEN_AUTH_KEY ? "1" : "0",
  },
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
      {
        source: "/manifest.json",
        headers: [
          { key: "Content-Type", value: "application/manifest+json; charset=utf-8" },
          { key: "Cache-Control", value: "public, max-age=3600" },
        ],
      },
    ];
  },
};

export default nextConfig;
