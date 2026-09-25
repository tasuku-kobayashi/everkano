import { describe, expect, it } from "vitest";
import { decideAuthGate, isPublicPath } from "./route-gate";

const USER = "11111111-1111-4111-8111-111111111111";

function gate(
  pathname: string,
  userId: string | null,
  search = "",
  loginError: string | null = null,
) {
  return decideAuthGate({ pathname, search, userId, loginError });
}

describe("isPublicPath", () => {
  it("ログイン画面・オフライン・PWA の静的ファイル・認証の着地点だけが公開", () => {
    for (const path of [
      "/login",
      "/offline",
      "/manifest.json",
      "/sw.js",
      "/favicon.ico",
      "/auth/confirm",
      "/auth/confirm/verify",
      "/auth/callback",
      "/icons/icon-192.png",
    ]) {
      expect(isPublicPath(path), path).toBe(true);
    }
  });

  it("アプリの画面・画像プロキシ・似た名前のパスは公開しない", () => {
    for (const path of [
      "/",
      "/dm",
      "/dm/abc",
      "/me",
      "/search",
      "/posts/1",
      "/c/misaki_ol",
      "/media/posts/a.jpg",
      "/login-help",
      "/loginx",
      "/auth",
      "/authx/confirm",
      "/offline/x",
      "/iconsx/a.png",
    ]) {
      expect(isPublicPath(path), path).toBe(false);
    }
  });
});

describe("decideAuthGate: 未ログイン", () => {
  it("保護ページはログイン画面へ。元のパスとクエリを next に入れる（/ は next なし）", () => {
    expect(gate("/", null)).toEqual({ action: "redirect", pathname: "/login", search: "" });
    expect(gate("/dm", null)).toEqual({
      action: "redirect",
      pathname: "/login",
      search: "?next=%2Fdm",
    });
    expect(gate("/posts/p1", null, "?comment=c1&x=1")).toEqual({
      action: "redirect",
      pathname: "/login",
      search: `?next=${encodeURIComponent("/posts/p1?comment=c1&x=1")}`,
    });
  });

  it("画像プロキシ（/media）はリダイレクトせず 401（未ログインに署名 URL を発行しない）", () => {
    expect(gate("/media/posts/a.jpg", null)).toEqual({ action: "unauthorized" });
  });

  it("公開パスはそのまま通す", () => {
    expect(gate("/login", null)).toEqual({ action: "next" });
    expect(gate("/auth/confirm", null, "?token_hash=abc&type=email")).toEqual({ action: "next" });
    expect(gate("/offline", null)).toEqual({ action: "next" });
  });
});

describe("decideAuthGate: ログイン済み", () => {
  it("保護ページ・画像プロキシはそのまま通す", () => {
    expect(gate("/", USER)).toEqual({ action: "next" });
    expect(gate("/dm/abc", USER)).toEqual({ action: "next" });
    expect(gate("/media/posts/a.jpg", USER)).toEqual({ action: "next" });
  });

  it("ログイン画面を開いたら ?next=（無ければホーム）へ（使用済みリンクのエラー表示 link も含む）", () => {
    expect(gate("/login", USER)).toEqual({ action: "redirect", pathname: "/", search: "" });
    expect(gate("/login", USER, "?next=%2Fdm")).toEqual({
      action: "redirect",
      pathname: "/dm",
      search: "",
    });
    expect(gate("/login", USER, `?next=${encodeURIComponent("/posts/p1?comment=c1")}`)).toEqual({
      action: "redirect",
      pathname: "/posts/p1",
      search: "?comment=c1",
    });
    expect(gate("/login", USER, "?error=link", "link")).toEqual({
      action: "redirect",
      pathname: "/",
      search: "",
    });
    expect(gate("/login", USER, "?error=link&next=%2Fdm%2Fc1", "link")).toEqual({
      action: "redirect",
      pathname: "/dm/c1",
      search: "",
    });
  });

  it("?next= が外部・認証系・ログイン画面ならホームへ（オープンリダイレクト・ループ防止）", () => {
    for (const next of [
      "https://evil.example/x",
      "//evil.example",
      "/\\evil.example",
      "/login?next=/dm",
      "/auth/confirm",
      "dm",
    ]) {
      expect(gate("/login", USER, `?next=${encodeURIComponent(next)}`), next).toEqual({
        action: "redirect",
        pathname: "/",
        search: "",
      });
    }
  });

  it("退会済み・セッション無効・利用停止のエラー表示中はログイン画面を表示する（リダイレクトループ防止）", () => {
    for (const reason of ["withdrawn", "session", "banned"]) {
      expect(gate("/login", USER, `?error=${reason}`, reason), reason).toEqual({ action: "next" });
    }
  });

  it("未知の error 値ではループ防止の例外にしない", () => {
    expect(gate("/login", USER, "?error=evil", "evil")).toEqual({
      action: "redirect",
      pathname: "/",
      search: "",
    });
  });
});
