"use client";

import { useEffect } from "react";
import { ErrorState } from "@/components/ui/error-state";

/** ルート直下（ログイン画面など）で発生した予期しないエラー */
export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[app] unexpected error:", error);
  }, [error]);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col justify-center pt-safe">
      <ErrorState
        title="問題が発生しました"
        message="画面を表示できませんでした。時間をおいて再度お試しください。"
        onRetry={reset}
      />
    </main>
  );
}
