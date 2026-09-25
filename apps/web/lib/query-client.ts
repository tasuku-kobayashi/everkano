import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/errors";
import { handleGlobalAuthError } from "@/lib/auth/account";

/**
 * TanStack Query の既定設定。
 * - staleTime 30 秒、ウィンドウフォーカスでの再取得なし
 * - リトライは 1 回まで。再試行しても変わらない 4xx（429 以外）はリトライしない
 * - ミューテーション（DM 送信・コメント等）は自動リトライしない（二重送信防止）
 * - 401 / 403 account_deleted はグローバルに処理してログイン画面へ
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handleGlobalAuthError }),
    mutationCache: new MutationCache({ onError: handleGlobalAuthError }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && (error.isClientError || error.code === "aborted")) {
            return false;
          }
          return failureCount < 1;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
}
