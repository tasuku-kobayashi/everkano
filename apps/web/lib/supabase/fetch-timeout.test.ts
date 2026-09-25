import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFetchWithTimeout } from "./fetch-timeout";

/** signal が中断されるまで終わらない fetch（通信が固まった状態） */
function hangingFetch(): typeof fetch {
  return (_input, init) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
    });
}

describe("createFetchWithTimeout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("応答が無ければ指定時間で TimeoutError になる", async () => {
    const fetchWithTimeout = createFetchWithTimeout(15_000, hangingFetch());
    const pending = fetchWithTimeout("https://example.supabase.co/rest/v1/posts");
    const assertion = expect(pending).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(14_999);
    await vi.advanceTimersByTimeAsync(1);
    await assertion;
  });

  it("呼び出し側の signal での中断を引き継ぐ", async () => {
    const fetchWithTimeout = createFetchWithTimeout(15_000, hangingFetch());
    const controller = new AbortController();
    const pending = fetchWithTimeout("https://example.supabase.co/rest/v1/posts", {
      signal: controller.signal,
    });
    const assertion = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await assertion;
  });

  it("応答があればそのまま返し、タイマーを残さない", async () => {
    const ok = new Response("[]", { status: 200 });
    const base = vi.fn<typeof fetch>(async () => ok);
    const fetchWithTimeout = createFetchWithTimeout(15_000, base);
    await expect(fetchWithTimeout("https://example.supabase.co/rest/v1/posts")).resolves.toBe(ok);
    expect(vi.getTimerCount()).toBe(0);
    // 元のリクエストの設定（method / headers）は引き継ぐ
    await fetchWithTimeout("https://x", { method: "POST", headers: { a: "b" } });
    expect(base.mock.calls[1]?.[1]).toMatchObject({ method: "POST", headers: { a: "b" } });
  });
});
