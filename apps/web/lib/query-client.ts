import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { handleGlobalAuthError } from "@/lib/auth/account";
import { isRetryableQueryError } from "@/lib/query-retry";

/**
 * TanStack Query の既定設定。
 * - staleTime 30 秒、ウィンドウフォーカスでの再取得なし（ホームの再読み込みは lib/feed-refresh.ts）
 * - リトライは 1 回まで。再試行しても変わらないエラー（4xx・権限不足・タイムアウト等）はリトライしない
 *   （判定は lib/query-retry.ts）
 * - ミューテーション（DM 送信・コメント等）は自動リトライしない（二重送信防止）
 * - networkMode: "always" … 既定の "online" だと、端末がオフラインのあいだクエリ・ミューテーションが
 *   「一時停止」されて queryFn / mutationFn が呼ばれず、エラーにもならない（DM の「入力中…」が出たまま・
 *   コメントの送信中表示が消えない・メモリ上に保留された送信はアプリを閉じると消える）。
 *   常に実行して fetch を失敗させ、lib/api の network_error（「通信できませんでした。接続を確認して、
 *   しばらくしてから再度お試しください。」）や各画面のエラー表示・再試行 UI にそのまま流す（仕様 D-3）。
 *   オンライン復帰時の再取得（refetchOnReconnect）は "always" でも有効。
 * - 401 / 403 account_deleted はグローバルに処理してログイン画面へ
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handleGlobalAuthError }),
    mutationCache: new MutationCache({ onError: handleGlobalAuthError }),
    defaultOptions: {
      queries: {
        networkMode: "always",
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => failureCount < 1 && isRetryableQueryError(error),
      },
      mutations: {
        networkMode: "always",
        retry: false,
      },
    },
  });
}
