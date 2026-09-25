import { onlineManager } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";
import { API_ERROR_MESSAGES, ApiError } from "@/lib/api/errors";
import { createQueryClient } from "./query-client";
import { isRetryableQueryError } from "./query-retry";

describe("createQueryClient: オフライン時の挙動（仕様 D-3）", () => {
  afterEach(() => {
    onlineManager.setOnline(true);
  });

  it("オフラインでもクエリを一時停止せずに実行する（エラー表示へ進められる）", async () => {
    onlineManager.setOnline(false);
    const client = createQueryClient();
    let calls = 0;
    const result = await client.fetchQuery({
      queryKey: ["offline-test"],
      queryFn: () => {
        calls += 1;
        return "ok";
      },
    });
    expect(result).toBe("ok");
    expect(calls).toBe(1);
  });

  it("オフラインでもミューテーションを実行し、通信エラーで失敗させる（入力中…のまま止まらない）", async () => {
    onlineManager.setOnline(false);
    const client = createQueryClient();
    const networkError = new ApiError({
      status: 0,
      code: "network_error",
      message: API_ERROR_MESSAGES.network_error,
    });
    const mutation = client.getMutationCache().build(client, {
      mutationFn: async () => {
        throw networkError;
      },
    });
    await expect(mutation.execute(undefined)).rejects.toBe(networkError);
    expect(mutation.state.isPaused).toBe(false);
    expect(mutation.state.status).toBe("error");
  });
});

describe("isRetryableQueryError", () => {
  it("Python API: 4xx と中断は再試行しない。通信失敗・5xx・429 は再試行する", () => {
    const api = (status: number, code: ApiError["code"]) =>
      new ApiError({ status, code, message: "x" });
    expect(isRetryableQueryError(api(404, "not_found"))).toBe(false);
    expect(isRetryableQueryError(api(0, "aborted"))).toBe(false);
    expect(isRetryableQueryError(api(0, "network_error"))).toBe(true);
    expect(isRetryableQueryError(api(503, "llm_unavailable"))).toBe(true);
    expect(isRetryableQueryError(api(429, "rate_limited"))).toBe(true);
  });

  it("PostgREST: 権限不足・型不正・0 件などは再試行しない", () => {
    for (const code of ["42501", "22P02", "PGRST116", "PGRST301", "23505", "P0001", "57014"]) {
      expect(isRetryableQueryError({ code, message: "x", details: "", hint: "" }), code).toBe(
        false,
      );
    }
  });

  it("PostgREST: 通信失敗と一時的な接続エラーは 1 回だけ再試行する", () => {
    expect(
      isRetryableQueryError({ code: "", message: "TypeError: Failed to fetch", details: "" }),
    ).toBe(true);
    for (const code of ["PGRST000", "PGRST002", "PGRST003", "08006", "40001", "53300", "57P01"]) {
      expect(isRetryableQueryError({ code, message: "x" }), code).toBe(true);
    }
  });

  it("タイムアウトと中断は再試行しない（既に待たせている）", () => {
    expect(
      isRetryableQueryError({ code: "", message: "TimeoutError: signal timed out", hint: "" }),
    ).toBe(false);
    expect(
      isRetryableQueryError({ code: "", message: "AbortError: The user aborted a request." }),
    ).toBe(false);
  });

  it("supabase-js Auth: 4xx は再試行しない。通信失敗は再試行する", () => {
    expect(isRetryableQueryError({ name: "AuthApiError", status: 403, code: "user_banned" })).toBe(
      false,
    );
    expect(isRetryableQueryError({ name: "AuthSessionMissingError", status: 400 })).toBe(false);
    expect(isRetryableQueryError({ name: "AuthRetryableFetchError", status: 0 })).toBe(true);
    expect(isRetryableQueryError({ name: "AuthApiError", status: 500 })).toBe(true);
  });

  it("その他の例外（TypeError 等）は 1 回だけ再試行する", () => {
    expect(isRetryableQueryError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isRetryableQueryError("boom")).toBe(true);
  });
});
