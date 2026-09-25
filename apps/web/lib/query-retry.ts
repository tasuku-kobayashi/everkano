import { ApiError, isPostgrestErrorLike, toAppError } from "@/lib/api/errors";

/**
 * 読み取りクエリを 1 回だけ再試行してよいエラーか（lib/query-client.ts の retry 判定）。
 *
 * 再試行しても結果が変わらないエラーはすぐにエラー表示へ進める（無駄な待ち時間とリクエストを減らす）:
 * - ApiError（Python API、および lib/api/errors.ts の toAppError で変換した Supabase のエラー）:
 *   4xx（429 を除く）・中断・タイムアウト
 * - supabase-js Auth（AuthApiError 等）: 4xx
 * - PostgREST（{ code, message, details, hint }。toAppError で変換していないもの）: toAppError で ApiError にそろえて
 *   同じ判定をする。権限不足 42501・型不正 22P02・PGRST116 などの SQLSTATE / PGRST コードは再試行しない。
 *   通信失敗（code ""）と、接続・一時的な資源不足（08*** / 40*** / 53*** / 57P** / PGRST000〜009）だけ再試行する
 * - タイムアウト（既に 15 秒待っている）と中断は再試行しない
 */

export function isRetryableQueryError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return !(error.isClientError || error.code === "aborted" || error.code === "timeout");
  }
  if (typeof error !== "object" || error === null) return true;

  const { name, status } = error as { name?: unknown; status?: unknown };

  // HTTP ステータスを持つエラー（supabase-js の AuthApiError / AccountBannedError 等）
  if (typeof status === "number" && status >= 400 && status < 500 && status !== 429) {
    return false;
  }
  if (typeof name === "string" && name.startsWith("Auth") && name !== "AuthRetryableFetchError") {
    return typeof status === "number" && status >= 500;
  }

  // PostgREST の分類（通信失敗・一時的な障害・その他）は lib/api/errors.ts の toAppError に一本化している
  if (isPostgrestErrorLike(error)) return isRetryableQueryError(toAppError(error));
  return true;
}
