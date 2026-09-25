import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { getPublicEnv } from "@/lib/env";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "everkano",
    template: "%s • everkano",
  },
  description: "AIキャラクターたちが毎日を投稿するSNS。気になるあの子と、DMで話そう。",
  applicationName: "everkano",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "everkano",
  },
  formatDetection: { telephone: false, email: false, address: false },
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "48x48" },
      { url: "/icons/favicon-32.png", type: "image/png", sizes: "32x32" },
      { url: "/icons/icon-192.png", type: "image/png", sizes: "192x192" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180" }],
  },
  // 社内 MVP のため検索エンジンにはインデックスさせない
  robots: { index: false, follow: false },
};

// ピンチズームは制限しない（maximumScale / userScalable を指定しない。WCAG 1.4.4）。
// iOS の入力時の自動ズームは globals.css で入力欄を 16px にして防いでいる
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // 環境変数の検証（不正ならここで分かりやすいエラーになる = fail fast）
  const env = getPublicEnv();

  return (
    <html lang="ja">
      <head>
        {/*
          初回表示のデータ・画像はすべて別オリジン（Supabase / CDN / Python API）から、JS の読み込み後に取得する。
          HTML の <head> で DNS・TCP・TLS の接続確立を先に始め、JS のダウンロードと並行させる
          （モバイルの冷えた起動で数百 ms 短縮）。react-dom の preconnect() は RSC のヒントとして送られ、
          HTML には出力されない（ハイドレーション後に挿入されて間に合わない）ため、<link> を直接書いている。
          - Supabase: supabase-js の fetch は資格情報なし（CORS）なので crossOrigin="anonymous" の接続を用意する
          - CDN: <img> は資格情報ありの接続を使うため crossOrigin を付けない
          - Python API: 初回表示では使わない画面が多いため DNS の先引きだけ
        */}
        <link rel="preconnect" href={env.supabaseUrl} crossOrigin="anonymous" />
        {env.cdnBaseUrl ? <link rel="preconnect" href={env.cdnBaseUrl} /> : null}
        <link rel="dns-prefetch" href={env.apiBaseUrl} />
      </head>
      <body className="bg-ig-bg text-ig-text antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
