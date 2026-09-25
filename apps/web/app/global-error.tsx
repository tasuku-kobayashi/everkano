"use client";

import { useEffect } from "react";

/**
 * 端末のダークモード設定に追従する配色（仕様 §4.1）。値は globals.css の --ig-* と同じ。
 * globals.css が読み込まれなくても効くよう、ここにインラインで持つ。
 */
const COLOR_STYLES = `
body { background: #ffffff; color: #000000; }
.ge-sub { color: #737373; }
@media (prefers-color-scheme: dark) {
  body { background: #000000; color: #f5f5f5; }
  .ge-sub { color: #a8a8a8; }
}
`;

/**
 * ルートレイアウト自体が落ちた場合の最終エラー画面。
 * globals.css が読み込まれない可能性があるため、スタイルはインラインで最小限にする。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[app] fatal error:", error);
  }, [error]);

  return (
    <html lang="ja" style={{ colorScheme: "light dark" }}>
      <head>
        <meta name="color-scheme" content="light dark" />
        <meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff" />
        <meta name="theme-color" media="(prefers-color-scheme: dark)" content="#000000" />
        <style>{COLOR_STYLES}</style>
      </head>
      <body
        style={{
          margin: 0,
          minHeight: "100dvh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: 32,
          textAlign: "center",
          fontFamily: '-apple-system, "Hiragino Sans", "Noto Sans JP", Roboto, sans-serif',
        }}
      >
        <h1 style={{ fontSize: 20, margin: 0 }}>問題が発生しました</h1>
        <p className="ge-sub" style={{ fontSize: 14, marginTop: 8 }}>
          しばらくしてから再度お試しください。
        </p>
        <button
          type="button"
          onClick={reset}
          style={{
            marginTop: 20,
            padding: "10px 20px",
            border: 0,
            borderRadius: 8,
            background: "#0095f6",
            color: "#fff",
            fontWeight: 600,
            fontSize: 14,
          }}
        >
          再読み込み
        </button>
      </body>
    </html>
  );
}
