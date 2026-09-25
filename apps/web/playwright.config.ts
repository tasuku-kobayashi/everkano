import { defineConfig } from "@playwright/test";
import { ANDROID, IPHONE } from "./e2e/support/devices";

/**
 * 受け入れ基準（仕様書 §13）の E2E テスト。
 *
 * サーバーは起動しない（本番ビルドの Web・API・ローカル Supabase を先に起動しておく）。
 * 手順は e2e/README.md。例:
 *   pnpm --filter @everkano/web e2e                      # 全テスト（iphone + android）
 *   pnpm --filter @everkano/web e2e --project=iphone     # iPhone 相当のみ
 *
 * 環境変数:
 *   E2E_BASE_URL（既定 http://localhost:3000） / E2E_API_URL（既定 http://localhost:8000）
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE（Chromium の実行ファイルを明示する場合）
 *   E2E_WORKERS（並列数。既定 3） / E2E_KEEP_USERS=1（テストユーザーを削除しない）
 */

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts$/,
  outputDir: "test-results",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: Number(process.env.E2E_WORKERS ?? 3),
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["json", { outputFile: "test-results/results.json" }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Service Worker はキャッシュでテストの前提を変えないよう通常はブロックし、
    // PWA のテスト（e2e/pwa.spec.ts）だけ許可する
    serviceWorkers: "block",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [
    { name: "iphone", use: { ...IPHONE, browserName: "chromium" } },
    { name: "android", use: { ...ANDROID, browserName: "chromium" } },
  ],
});
