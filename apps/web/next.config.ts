import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

/**
 * everkano Web の Next.js 設定。
 *
 * - distDir: `NEXT_DIST_DIR` で出力先を切り替えられる（複数の dev サーバーを並行起動するため。
 *   例: `NEXT_DIST_DIR=.next-browse pnpm --filter @everkano/web exec next dev -p 3001`）。
 * - next/image は使わない（画像は StorageAdapter + CDN 変換パラメータ。仕様 §2）。そのため画像最適化の
 *   エンドポイント（/_next/image）も無効にする（images.unoptimized）。使わない機能の攻撃面（過去に DoS・キャッシュ
 *   混同の CVE がある）と、実行時に読み込まれる sharp / libvips（LGPL-3.0）を本番から外す。
 * - CSP はプレースホルダ画像ホスト等を塞がないよう本 MVP では設定しない（ADR-0015 参照）。
 * - 開発専用のページは `page.dev.tsx` と名付ける。`next dev` のときだけページとして扱い、本番ビルドには
 *   ルートもチャンクも含めない（例: 開発用 UI カタログ app/(main)/dev/ui/page.dev.tsx）。
 * - NEXT_PUBLIC_BUILD_ID: デプロイごとの ID（Service Worker の登録 URL に付ける。public/sw.js）。
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

/**
 * デプロイごとの ID。明示の NEXT_PUBLIC_BUILD_ID → Vercel のデプロイ ID / コミット → GitHub Actions のコミット →
 * ビルド時刻。ビルドのワーカー（子プロセス）でも同じ値になるよう、決めた値を process.env に書き戻す。
 */
function resolveBuildId(): string {
  const candidates = [
    process.env.NEXT_PUBLIC_BUILD_ID,
    process.env.VERCEL_DEPLOYMENT_ID,
    process.env.VERCEL_GIT_COMMIT_SHA,
    process.env.GITHUB_SHA,
  ];
  const raw = candidates.find((value) => value && value.trim()) ?? Date.now().toString(36);
  const id = raw.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || "dev";
  process.env.NEXT_PUBLIC_BUILD_ID = id;
  return id;
}

const buildConfig = (phase: string): NextConfig => ({
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // 開発サーバーでだけ *.dev.tsx をページとして扱う（本番ビルドには含めない）
  pageExtensions:
    phase === PHASE_DEVELOPMENT_SERVER
      ? ["dev.tsx", "tsx", "ts", "jsx", "js"]
      : ["tsx", "ts", "jsx", "js"],
  images: { unoptimized: true },
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
    NEXT_PUBLIC_BUILD_ID: resolveBuildId(),
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
});

export default buildConfig;
