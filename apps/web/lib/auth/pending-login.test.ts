import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearPendingLogin,
  loadPendingLogin,
  parsePendingLogin,
  parseResendWaitSeconds,
  PENDING_LOGIN_TTL_MS,
  resendCooldownRemaining,
  savePendingLogin,
} from "./pending-login";

const NOW = Date.parse("2026-09-25T12:00:00Z");

describe("parsePendingLogin", () => {
  const raw = (value: unknown) => JSON.stringify(value);

  it("有効期限内のメールアドレスと送信時刻を読む", () => {
    expect(parsePendingLogin(raw({ email: "a@example.com", sentAt: NOW - 30_000 }), NOW)).toEqual({
      email: "a@example.com",
      sentAt: NOW - 30_000,
    });
  });

  it("確認コードの有効期限を過ぎたもの・未来の時刻・壊れた値は null", () => {
    expect(
      parsePendingLogin(raw({ email: "a@example.com", sentAt: NOW - PENDING_LOGIN_TTL_MS }), NOW),
    ).toBeNull();
    expect(
      parsePendingLogin(raw({ email: "a@example.com", sentAt: NOW + 10 * 60_000 }), NOW),
    ).toBeNull();
    expect(parsePendingLogin(raw({ email: "", sentAt: NOW }), NOW)).toBeNull();
    expect(parsePendingLogin(raw({ email: "a@example.com", sentAt: "now" }), NOW)).toBeNull();
    expect(parsePendingLogin(raw(["a@example.com"]), NOW)).toBeNull();
    expect(parsePendingLogin("{not json", NOW)).toBeNull();
    expect(parsePendingLogin(null, NOW)).toBeNull();
  });
});

describe("resendCooldownRemaining", () => {
  it("送信からの経過時間に応じて、再送信までの残り秒数を返す", () => {
    expect(resendCooldownRemaining(NOW, NOW)).toBe(60);
    expect(resendCooldownRemaining(NOW - 15_500, NOW)).toBe(45);
    expect(resendCooldownRemaining(NOW - 60_000, NOW)).toBe(0);
    expect(resendCooldownRemaining(NOW - 10 * 60_000, NOW)).toBe(0);
    // 時計が戻っても上限を超えない
    expect(resendCooldownRemaining(NOW + 30_000, NOW)).toBe(60);
  });
});

describe("parseResendWaitSeconds（Supabase の送信間隔エラー）", () => {
  it("GoTrue の本文から待ち秒数を読む", () => {
    expect(
      parseResendWaitSeconds("For security purposes, you can only request this after 42 seconds."),
    ).toBe(42);
    expect(
      parseResendWaitSeconds("For security purposes, you can only request this after 1 second."),
    ).toBe(1);
  });

  it("プロジェクト全体の上限など、読めないものは null", () => {
    expect(parseResendWaitSeconds("Email rate limit exceeded")).toBeNull();
    expect(parseResendWaitSeconds(undefined)).toBeNull();
  });
});

describe("localStorage への保存", () => {
  let store: Map<string, string>;

  beforeEach(() => {
    store = new Map();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => void store.set(key, value),
        removeItem: (key: string) => void store.delete(key),
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("保存 → 読み取り → 消去", () => {
    savePendingLogin("a@example.com", NOW - 1_000);
    expect(loadPendingLogin(NOW)).toEqual({ email: "a@example.com", sentAt: NOW - 1_000 });
    clearPendingLogin();
    expect(loadPendingLogin(NOW)).toBeNull();
  });

  it("期限切れの値は読み取り時に消す", () => {
    savePendingLogin("a@example.com", NOW - PENDING_LOGIN_TTL_MS - 1);
    expect(loadPendingLogin(NOW)).toBeNull();
    expect(store.size).toBe(0);
  });

  it("localStorage が使えない環境（アクセスで例外）でも例外を投げない", () => {
    vi.stubGlobal("window", {
      get localStorage(): Storage {
        throw new DOMException("denied", "SecurityError");
      },
    });
    expect(() => savePendingLogin("a@example.com")).not.toThrow();
    expect(loadPendingLogin(NOW)).toBeNull();
    expect(() => clearPendingLogin()).not.toThrow();
  });
});
