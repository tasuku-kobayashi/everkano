import { describe, expect, it, vi } from "vitest";
import {
  CONFIRM_VERIFY_PATH,
  confirmResponseLocation,
  isSameOriginPath,
  submitConfirm,
} from "./confirm-submit";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

describe("マジックリンクの確認画面の送信（履歴を置き換えて遷移する）", () => {
  it("fetch で Accept: application/json を付けて送り、応答の遷移先を返す", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ location: "/posts/p1" }));
    await expect(
      submitConfirm({ tokenHash: "abcdefgh12", type: "email", next: "/posts/p1" }, fetchImpl),
    ).resolves.toBe("/posts/p1");

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(CONFIRM_VERIFY_PATH);
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("accept")).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
    expect(String(init.body)).toBe("token_hash=abcdefgh12&type=email&next=%2Fposts%2Fp1");
  });

  it("失敗時の遷移先（/login?error=link）もそのまま返す", async () => {
    await expect(
      confirmResponseLocation(jsonResponse({ location: "/login?error=link&next=%2Fme" })),
    ).resolves.toBe("/login?error=link&next=%2Fme");
  });

  it("読めない応答・他オリジンへの遷移先は受け付けない（null）", async () => {
    await expect(
      confirmResponseLocation(jsonResponse({ location: "/" }, { status: 500 })),
    ).resolves.toBeNull();
    await expect(
      confirmResponseLocation(new Response("<html>", { status: 200 })),
    ).resolves.toBeNull();
    for (const location of ["https://evil.example/", "//evil.example", "/\\evil", "", 1, null]) {
      await expect(
        confirmResponseLocation(jsonResponse({ location })),
        String(location),
      ).resolves.toBeNull();
    }
  });

  it("通信の失敗は例外（画面でエラーを表示して再試行できるようにする）", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(
      submitConfirm({ tokenHash: "abcdefgh12", type: "email", next: "/" }, fetchImpl),
    ).rejects.toThrow("Failed to fetch");
  });

  it("同一オリジンの相対パスの判定", () => {
    expect(isSameOriginPath("/")).toBe(true);
    expect(isSameOriginPath("/dm/c1?x=1#y")).toBe(true);
    expect(isSameOriginPath("dm")).toBe(false);
    expect(isSameOriginPath("/\u0000x")).toBe(false);
  });
});
