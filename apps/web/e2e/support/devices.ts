import type { BrowserContextOptions } from "@playwright/test";

/**
 * エミュレートする端末（Chromium で実行する。実機の iOS Safari / Android Chrome ではない）。
 * playwright.config.ts のプロジェクトと、テスト内で追加のコンテキスト（ダークモード・2 人目のユーザー）を
 * 作るときの両方で使う。
 */

const COMMON: BrowserContextOptions = {
  isMobile: true,
  hasTouch: true,
  locale: "ja-JP",
  timezoneId: "Asia/Tokyo",
  colorScheme: "light",
};

/** iPhone 15 / 14 相当（390 × 844, DPR 3） */
export const IPHONE: BrowserContextOptions = {
  ...COMMON,
  viewport: { width: 390, height: 844 },
  screen: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  userAgent:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
};

/** Pixel 7 相当（412 × 915, DPR 2.625） */
export const ANDROID: BrowserContextOptions = {
  ...COMMON,
  viewport: { width: 412, height: 915 },
  screen: { width: 412, height: 915 },
  deviceScaleFactor: 2.625,
  userAgent:
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
};

export type ProjectName = "iphone" | "android";

export const DEVICES: Record<ProjectName, BrowserContextOptions> = {
  iphone: IPHONE,
  android: ANDROID,
};

export function deviceFor(projectName: string): BrowserContextOptions {
  return projectName === "android" ? ANDROID : IPHONE;
}
