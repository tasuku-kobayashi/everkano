"use client";

import { useEffect } from "react";
import { AppHeader } from "@/components/ui/app-header";
import { ErrorState } from "@/components/ui/error-state";

/** (main) 配下の画面で発生した予期しないエラー（タブバーは表示したまま） */
export default function MainError({
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
    <>
      <AppHeader variant="logo" />
      <ErrorState
        title="問題が発生しました"
        message="画面を表示できませんでした。時間をおいて再度お試しください。"
        onRetry={reset}
      />
    </>
  );
}
