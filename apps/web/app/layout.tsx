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

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // 環境変数の検証（不正ならここで分かりやすいエラーになる = fail fast）
  getPublicEnv();

  return (
    <html lang="ja">
      <body className="bg-ig-bg text-ig-text antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
