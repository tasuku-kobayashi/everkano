import { describe, expect, it } from "vitest";
import { isRetryableQueryError } from "@/lib/query-retry";
import {
  API_ERROR_MESSAGES,
  ApiError,
  getErrorMessage,
  isPostgrestErrorLike,
  toAppError,
} from "./errors";

/** postgrest-js が返すエラー（throwOnError なしではプレーンなオブジェクト） */
function postgrest(code: string, message = "x") {
  return { code, message, details: "", hint: "" };
}

describe("toAppError: PostgREST（supabase-js）のエラー", () => {
  it("通信失敗（code ''）は network_error。中断・タイムアウトは aborted / timeout", () => {
    expect(toAppError(postgrest("", "TypeError: Failed to fetch"))).toMatchObject({
      status: 0,
      code: "network_error",
      message: API_ERROR_MESSAGES.network_error,
    });
    expect(
      toAppError(postgrest("", "TimeoutError: Supabase request timed out after 15000ms")),
    ).toMatchObject({ status: 0, code: "timeout" });
    expect(toAppError(postgrest("", "AbortError: The user aborted a request."))).toMatchObject({
      status: 0,
      code: "aborted",
    });
  });

  it("権限不足・JWT 不正は forbidden、0 件は not_found、一時的な障害は 5xx、その他は 4xx", () => {
    expect(toAppError(postgrest("42501"))).toMatchObject({ status: 403, code: "forbidden" });
    expect(toAppError(postgrest("PGRST301"))).toMatchObject({ status: 403, code: "forbidden" });
    expect(toAppError(postgrest("PGRST116"))).toMatchObject({ status: 404, code: "not_found" });
    for (const code of ["PGRST000", "PGRST003", "08006", "40001", "53300", "57P01"]) {
      expect(toAppError(postgrest(code)), code).toMatchObject({ status: 503 });
    }
    for (const code of ["22P02", "23505", "P0001", "57014", "PGRST204"]) {
      expect(toAppError(postgrest(code)), code).toMatchObject({ status: 400, code: "unknown" });
    }
  });

  it("元のエラーを cause に残す（ログの調査用）", () => {
    const original = postgrest("42501", "permission denied for table characters");
    expect(toAppError(original).cause).toBe(original);
  });

  it("再試行の判定が変換前（プレーンなオブジェクト）と同じになる", () => {
    for (const [code, message] of [
      ["", "TypeError: Failed to fetch"],
      ["", "TimeoutError: signal timed out"],
      ["", "AbortError: aborted"],
      ["42501", "x"],
      ["22P02", "x"],
      ["PGRST116", "x"],
      ["57014", "x"],
      ["PGRST000", "x"],
      ["08006", "x"],
    ] as const) {
      const raw = postgrest(code, message);
      expect(isRetryableQueryError(toAppError(raw)), `${code} ${message}`).toBe(
        isRetryableQueryError(raw),
      );
    }
  });
});

describe("toAppError: その他の例外", () => {
  it("ApiError はそのまま返す", () => {
    const error = new ApiError({ status: 422, code: "moderation_blocked", message: "だめ" });
    expect(toAppError(error)).toBe(error);
  });

  it("supabase-js Auth: 通信失敗は network_error、5xx は internal_error、4xx は unauthorized にしない", () => {
    expect(
      toAppError({ name: "AuthRetryableFetchError", status: 0, message: "Failed to fetch" }),
    ).toMatchObject({ status: 0, code: "network_error" });
    expect(toAppError({ name: "AuthApiError", status: 500, message: "x" })).toMatchObject({
      status: 500,
      code: "internal_error",
    });
    expect(toAppError({ name: "AuthApiError", status: 401, message: "x" })).toMatchObject({
      status: 401,
      code: "unknown",
    });
    // 応答を解釈できなかった（HTML のエラーページ等。status なし）
    expect(toAppError({ name: "AuthUnknownError", message: "x" })).toMatchObject({
      status: 0,
      code: "unknown",
    });
  });

  it("fetch の TypeError・DOMException の中断/タイムアウト・その他", () => {
    expect(toAppError(new TypeError("Failed to fetch"))).toMatchObject({ code: "network_error" });
    expect(toAppError(new DOMException("t", "TimeoutError"))).toMatchObject({ code: "timeout" });
    expect(toAppError(new DOMException("a", "AbortError"))).toMatchObject({ code: "aborted" });
    expect(toAppError(new Error("boom"))).toMatchObject({ status: 0, code: "unknown" });
    expect(toAppError("boom")).toMatchObject({ code: "unknown" });
  });
});

describe("isPostgrestErrorLike", () => {
  it("code と message を持つプレーンなオブジェクトだけ", () => {
    expect(isPostgrestErrorLike(postgrest(""))).toBe(true);
    expect(isPostgrestErrorLike(new Error("x"))).toBe(false);
    expect(isPostgrestErrorLike({ message: "x" })).toBe(false);
    expect(isPostgrestErrorLike(null)).toBe(false);
  });
});

describe("getErrorMessage", () => {
  it("Supabase の通信失敗（プレーンなオブジェクト）でも通信エラーの案内を出す", () => {
    // 例: DM の履歴の読み込み失敗（lib/queries が postgrest のエラーをそのまま投げた場合）
    expect(getErrorMessage(postgrest("", "TypeError: Failed to fetch"))).toBe(
      API_ERROR_MESSAGES.network_error,
    );
    expect(getErrorMessage(postgrest("", "TimeoutError: timed out"))).toBe(
      API_ERROR_MESSAGES.timeout,
    );
  });

  it("ApiError はその message、分類できないものは fallback", () => {
    expect(
      getErrorMessage(new ApiError({ status: 429, code: "rate_limited", message: "待って" })),
    ).toBe("待って");
    expect(getErrorMessage(new TypeError("Failed to fetch"))).toBe(
      API_ERROR_MESSAGES.network_error,
    );
    expect(getErrorMessage(new Error("boom"), "保存できませんでした")).toBe("保存できませんでした");
    expect(getErrorMessage(postgrest("22P02"), "読み込めませんでした")).toBe(
      "読み込めませんでした",
    );
    expect(getErrorMessage(postgrest("42501"))).toBe(API_ERROR_MESSAGES.forbidden);
  });
});

describe("エラーメッセージの文言の決まり（Python API の DEFAULT_MESSAGES と揃える）", () => {
  const messages = Object.entries(API_ERROR_MESSAGES);

  it("通信失敗・タイムアウトは原因を端末の電波と決めつけない（API の停止・再起動でも出る）", () => {
    for (const [code, message] of messages) {
      expect(message, code).not.toMatch(/電波/);
    }
    expect(API_ERROR_MESSAGES.network_error).toMatch(/^通信できませんでした。/);
  });

  it("文末は句点。時間をおいた再試行の案内は「しばらくしてから再度お試しください。」に統一", () => {
    for (const [code, message] of messages) {
      expect(message, code).toMatch(/。$/);
      expect(message, code).not.toMatch(/時間をおいて|もう一度お試し/);
    }
    for (const code of ["timeout", "unknown", "rate_limited", "llm_unavailable"] as const) {
      expect(API_ERROR_MESSAGES[code], code).toMatch(/しばらくしてから再度お試しください。$/);
    }
  });

  it("ログイン画面の表示と同じ文は同じ表記（セッション切れ・退会済み）", async () => {
    const { LOGIN_ERROR_MESSAGES } = await import("@/lib/auth/redirect");
    expect(API_ERROR_MESSAGES.unauthorized).toBe(LOGIN_ERROR_MESSAGES.session);
    expect(API_ERROR_MESSAGES.account_deleted).toBe(LOGIN_ERROR_MESSAGES.withdrawn);
    for (const [reason, message] of Object.entries(LOGIN_ERROR_MESSAGES)) {
      expect(message, reason).toMatch(/。$/);
    }
  });

  it("ログイン（Supabase Auth）のエラーも同じ決まり", async () => {
    const { authErrorMessage } = await import("@/lib/auth/errors");
    const cases = [
      authErrorMessage({ name: "AuthRetryableFetchError", message: "Failed to fetch", status: 0 }),
      authErrorMessage({ code: "over_email_send_rate_limit", status: 429 }),
      authErrorMessage({ code: "email_address_invalid", status: 400 }),
      authErrorMessage({ code: "signup_disabled", status: 422 }),
      authErrorMessage({ code: "otp_expired", status: 403 }, "verify"),
      authErrorMessage({ code: "user_banned", status: 403 }, "verify"),
      authErrorMessage({ message: "???", status: 500 }, "verify"),
      authErrorMessage({ message: "???", status: 500 }),
    ];
    expect(cases[0]).toBe(API_ERROR_MESSAGES.network_error);
    for (const message of cases) {
      expect(message).toMatch(/。$/);
      expect(message).not.toMatch(/電波|時間をおいて|もう一度お試し/);
    }
  });
});
