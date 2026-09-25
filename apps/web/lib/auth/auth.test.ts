import { describe, expect, it } from "vitest";
import { authErrorMessage, isValidEmail } from "./errors";
import { loginPath, parseLoginError, sanitizeNextPath } from "./redirect";

describe("sanitizeNextPath", () => {
  it("同一オリジンの相対パスのみ許可", () => {
    expect(sanitizeNextPath("/dm/abc?x=1")).toBe("/dm/abc?x=1");
    expect(sanitizeNextPath(null)).toBe("/");
    expect(sanitizeNextPath("")).toBe("/");
    expect(sanitizeNextPath("https://evil.example")).toBe("/");
    expect(sanitizeNextPath("//evil.example")).toBe("/");
    expect(sanitizeNextPath("/\\evil.example")).toBe("/");
    expect(sanitizeNextPath("dm")).toBe("/");
    expect(sanitizeNextPath("/login?next=/x")).toBe("/");
    expect(sanitizeNextPath("/auth/callback")).toBe("/");
  });
});

describe("login helpers", () => {
  it("parseLoginError / loginPath", () => {
    expect(parseLoginError("withdrawn")).toBe("withdrawn");
    expect(parseLoginError("other")).toBeNull();
    expect(loginPath()).toBe("/login");
    expect(loginPath({ error: "withdrawn" })).toBe("/login?error=withdrawn");
    expect(loginPath({ next: "/dm" })).toBe("/login?next=%2Fdm");
  });
});

describe("authErrorMessage", () => {
  it("主要なエラーを日本語にする", () => {
    expect(authErrorMessage({ code: "over_email_send_rate_limit", status: 429 })).toContain("上限");
    expect(authErrorMessage({ code: "email_address_invalid", status: 400 })).toContain("形式");
    expect(authErrorMessage({ code: "otp_expired", status: 403 }, "verify")).toContain(
      "確認コード",
    );
    expect(
      authErrorMessage({ name: "AuthRetryableFetchError", message: "Failed to fetch", status: 0 }),
    ).toContain("通信");
    expect(authErrorMessage({ message: "???", status: 500 })).toContain("送信できませんでした");
  });

  it("isValidEmail", () => {
    expect(isValidEmail("a@b.co")).toBe(true);
    expect(isValidEmail(" a@b.co ")).toBe(true);
    expect(isValidEmail("a@b")).toBe(false);
    expect(isValidEmail("ab.co")).toBe(false);
  });
});
