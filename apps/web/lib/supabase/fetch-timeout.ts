/** Supabase（REST / Auth）へのリクエストのタイムアウト。lib/api の既定値（15 秒）と揃える */
export const SUPABASE_FETCH_TIMEOUT_MS = 15_000;

/**
 * タイムアウト付きの fetch を作る（supabase-js の global.fetch に渡す）。
 *
 * 通信が途中で止まった（TCP が固まった・キャプティブポータル等）場合、fetch はいつまでも終わらず
 * スケルトンが出たままになるため、一定時間で中断して TimeoutError（DOMException）で失敗させる。
 * 呼び出し側の signal（React Query のキャンセル・postgrest の abortSignal()）も引き継ぐ。
 * AbortSignal.any / AbortSignal.timeout が無い古い iOS Safari でも動くよう、AbortController で合成している。
 */
export function createFetchWithTimeout(
  timeoutMs: number = SUPABASE_FETCH_TIMEOUT_MS,
  baseFetch: typeof fetch = (input, init) => fetch(input, init),
): typeof fetch {
  return (input, init) => {
    const external = init?.signal ?? null;
    if (external?.aborted) return baseFetch(input, init);

    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort(timeoutError(timeoutMs));
    }, timeoutMs);
    const onExternalAbort = () => controller.abort(external?.reason);
    external?.addEventListener("abort", onExternalAbort, { once: true });

    return baseFetch(input, { ...init, signal: controller.signal }).finally(() => {
      clearTimeout(timer);
      external?.removeEventListener("abort", onExternalAbort);
    });
  };
}

function timeoutError(timeoutMs: number): Error {
  const message = `Supabase request timed out after ${timeoutMs}ms`;
  // DOMException(name) が使えない環境（古いブラウザ・一部のテスト環境）では Error で代用する
  try {
    return new DOMException(message, "TimeoutError");
  } catch {
    const error = new Error(message);
    error.name = "TimeoutError";
    return error;
  }
}
