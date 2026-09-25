import { describe, expect, it } from "vitest";
import { isAccountSwitched } from "./account";
import { authErrorMessage, isValidEmail } from "./errors";
import {
  emailRedirectUrl,
  loginPath,
  nextPathFromRedirectTo,
  parseLoginError,
  sanitizeNextPath,
} from "./redirect";

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

describe("emailRedirectUrl（signInWithOtp の emailRedirectTo）", () => {
  it("ログイン後の遷移先を /auth/callback?next= で運ぶ（/ は付けない）", () => {
    expect(emailRedirectUrl("https://app.example", "/")).toBe("https://app.example/auth/callback");
    expect(emailRedirectUrl("https://app.example/", "/dm")).toBe(
      "https://app.example/auth/callback?next=%2Fdm",
    );
    expect(emailRedirectUrl("http://localhost:3000", "/posts/p1?comment=c1&x=1")).toBe(
      `http://localhost:3000/auth/callback?next=${encodeURIComponent("/posts/p1?comment=c1&x=1")}`,
    );
  });

  it("不正な遷移先は付けない", () => {
    for (const next of ["https://evil.example", "//evil.example", "/login?next=/dm", ""]) {
      expect(emailRedirectUrl("https://app.example", next), next).toBe(
        "https://app.example/auth/callback",
      );
    }
  });

  it("nextPathFromRedirectTo と往復する（メールのリンクの redirect_to から同じパスを取り出せる）", () => {
    for (const next of [
      "/dm/c1",
      "/posts/p1?comment=c1&x=1",
      "/c/misaki_ol",
      "/search?q=%E7%8C%AB",
    ]) {
      expect(nextPathFromRedirectTo(emailRedirectUrl("https://app.example", next)), next).toBe(
        next,
      );
    }
  });
});

describe("nextPathFromRedirectTo（メールのリンクの redirect_to）", () => {
  it("/auth/callback?next= の next を検証して返す", () => {
    expect(nextPathFromRedirectTo("https://app.example/auth/callback?next=%2Fdm%2Fc1")).toBe(
      "/dm/c1",
    );
    expect(nextPathFromRedirectTo("http://localhost:3000/auth/callback/?next=/me")).toBe("/me");
  });

  it("next が無い・外部・認証系のパス、形の違う redirect_to（Site URL への置き換え等）は null", () => {
    for (const value of [
      null,
      undefined,
      "",
      "not a url",
      "/auth/callback?next=%2Fdm",
      "https://app.example",
      "https://app.example/",
      "https://app.example/auth/callback",
      "https://app.example/auth/callback?next=",
      "https://app.example/?next=%2Fdm",
      "https://app.example/other?next=%2Fdm",
      "https://app.example/auth/callback?next=https%3A%2F%2Fevil.example",
      "https://app.example/auth/callback?next=%2F%2Fevil.example",
      "https://app.example/auth/callback?next=%2F%5Cevil.example",
      "https://app.example/auth/callback?next=%2Flogin",
      "https://app.example/auth/callback?next=%2Fauth%2Fconfirm",
      "javascript:alert(1)//auth/callback?next=/dm",
      `https://app.example/auth/callback?next=/${"a".repeat(2100)}`,
    ]) {
      expect(nextPathFromRedirectTo(value), String(value)).toBeNull();
    }
  });

  it("redirect_to のオリジンは使わない（別オリジンでもパスだけを自分のオリジンの相対パスとして返す）", () => {
    expect(nextPathFromRedirectTo("https://evil.example/auth/callback?next=%2Fdm")).toBe("/dm");
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

describe("isAccountSwitched", () => {
  it("キャッシュ済みのアカウントと別のユーザーでサインインしたときだけ true", () => {
    const cached = { userId: "user-a" };
    expect(isAccountSwitched(cached, "user-b")).toBe(true);
    expect(isAccountSwitched(cached, "user-a")).toBe(false);
    // キャッシュが無い / 未ログインとしてキャッシュされている / セッションが無い
    expect(isAccountSwitched(undefined, "user-b")).toBe(false);
    expect(isAccountSwitched(null, "user-b")).toBe(false);
    expect(isAccountSwitched(cached, undefined)).toBe(false);
  });
});

describe("セッション終了のログインエラー", () => {
  it("banned を解釈し、withdrawn / session / banned はセッション終了扱い（link は違う）", async () => {
    const { isSessionEndingLoginError, LOGIN_ERROR_MESSAGES } = await import("./redirect");
    expect(parseLoginError("banned")).toBe("banned");
    expect(parseLoginError("session")).toBe("session");
    expect(parseLoginError("constructor")).toBeNull();
    expect(LOGIN_ERROR_MESSAGES.banned).toContain("利用停止");
    expect(isSessionEndingLoginError("withdrawn")).toBe(true);
    expect(isSessionEndingLoginError("session")).toBe(true);
    expect(isSessionEndingLoginError("banned")).toBe(true);
    expect(isSessionEndingLoginError("link")).toBe(false);
    expect(isSessionEndingLoginError(null)).toBe(false);
  });
});

describe("isSameOriginRequest（マジックリンクの POST のログイン CSRF 対策）", () => {
  it("Sec-Fetch-Site があれば same-origin だけ許可", async () => {
    const { isSameOriginRequest } = await import("./csrf");
    const origin = "https://everkano.example";
    const headers = (init: Record<string, string>) => new Headers(init);
    expect(isSameOriginRequest(headers({ "sec-fetch-site": "same-origin" }), origin)).toBe(true);
    expect(isSameOriginRequest(headers({ "sec-fetch-site": "cross-site", origin }), origin)).toBe(
      false,
    );
    expect(isSameOriginRequest(headers({ "sec-fetch-site": "same-site" }), origin)).toBe(false);
    expect(isSameOriginRequest(headers({ "sec-fetch-site": "none" }), origin)).toBe(false);
  });

  it("Sec-Fetch-Site が無ければ Origin を比較し、どちらも無ければ拒否", async () => {
    const { isSameOriginRequest } = await import("./csrf");
    const origin = "https://everkano.example";
    expect(isSameOriginRequest(new Headers({ origin }), origin)).toBe(true);
    expect(isSameOriginRequest(new Headers({ origin: "https://evil.example" }), origin)).toBe(
      false,
    );
    expect(isSameOriginRequest(new Headers({ origin: "null" }), origin)).toBe(false);
    expect(isSameOriginRequest(new Headers(), origin)).toBe(false);
  });
});

describe("parseConfirmParams", () => {
  it("token_hash / type を検証し、next はオープンリダイレクトにならない値だけ", async () => {
    const { parseConfirmParams } = await import("./confirm-params");
    const from = (values: Record<string, unknown>) => (name: string) => values[name];
    const token = "a".repeat(56);
    expect(parseConfirmParams(from({ token_hash: token, type: "email", next: "/dm" }))).toEqual({
      tokenHash: token,
      type: "email",
      next: "/dm",
    });
    expect(parseConfirmParams(from({ token_hash: `pkce_${token}`, type: "magiclink" }))?.next).toBe(
      "/",
    );
    expect(
      parseConfirmParams(from({ token_hash: token, type: "email", next: "https://evil.example" }))
        ?.next,
    ).toBe("/");
    expect(parseConfirmParams(from({ token_hash: token, type: "sms" }))).toBeNull();
    expect(parseConfirmParams(from({ type: "email" }))).toBeNull();
    expect(parseConfirmParams(from({ token_hash: "<script>", type: "email" }))).toBeNull();
    expect(parseConfirmParams(from({ token_hash: token, type: ["email"] }))).toBeNull();
  });

  it("next はメールのリンクの redirect_to（emailRedirectTo）から取り出す。無ければ直接の next", async () => {
    const { parseConfirmParams } = await import("./confirm-params");
    const from = (values: Record<string, unknown>) => (name: string) => values[name];
    const token = `pkce_${"b".repeat(56)}`;
    const redirectTo = (next: string) =>
      `http://localhost:3000/auth/callback?next=${encodeURIComponent(next)}`;

    expect(
      parseConfirmParams(
        from({ token_hash: token, type: "email", redirect_to: redirectTo("/posts/p1") }),
      )?.next,
    ).toBe("/posts/p1");
    // テンプレートを変える前に送ったメール（&next=/）・確認画面のフォームの POST
    expect(parseConfirmParams(from({ token_hash: token, type: "email", next: "/dm" }))?.next).toBe(
      "/dm",
    );
    // redirect_to が Site URL に置き換えられた（許可リストに無い）・next が無い → 直接の next、無ければ /
    expect(
      parseConfirmParams(
        from({ token_hash: token, type: "email", redirect_to: "http://localhost:3000" }),
      )?.next,
    ).toBe("/");
    expect(
      parseConfirmParams(
        from({
          token_hash: token,
          type: "email",
          redirect_to: "http://localhost:3000/auth/callback",
          next: "/me",
        }),
      )?.next,
    ).toBe("/me");
    // 外部へのリダイレクトにはならない
    expect(
      parseConfirmParams(
        from({ token_hash: token, type: "email", redirect_to: redirectTo("//evil.example") }),
      )?.next,
    ).toBe("/");
  });

  it("parseConfirmNext: トークンが不正でも遷移先だけは取り出せる（/login?error=link&next= に引き継ぐ）", async () => {
    const { parseConfirmNext } = await import("./confirm-params");
    const from = (values: Record<string, unknown>) => (name: string) => values[name];
    expect(
      parseConfirmNext(
        from({ token_hash: "bad", redirect_to: "https://app.example/auth/callback?next=%2Fdm" }),
      ),
    ).toBe("/dm");
    expect(parseConfirmNext(from({}))).toBe("/");
    expect(parseConfirmNext(from({ redirect_to: 1, next: ["/dm"] }))).toBe("/");
  });
});
