"use client";

import { useEffect } from "react";

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
    <html lang="ja">
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
        <p style={{ color: "#737373", fontSize: 14, marginTop: 8 }}>
          時間をおいて再度お試しください。
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
