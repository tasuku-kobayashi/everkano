import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";

/**
 * E2E against the real API + mock ComfyUI + mock face engine (no GPU). The built UI is served by the API on one
 * port (production path). `pnpm build` first, then `pnpm e2e`. Screenshots go to E2E_SCREENSHOTS_DIR
 * (default ../docs/screenshots).
 */
const API_PORT = Number(process.env.E2E_API_PORT ?? 18000);
const COMFY_PORT = Number(process.env.E2E_COMFY_PORT ?? 18188);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
const API_DIR = resolve(import.meta.dirname, "../api");
const COMFY_ROOT = resolve(import.meta.dirname, "e2e/.tmp/comfy");

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts$/,
  outputDir: "test-results",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    baseURL: `http://127.0.0.1:${API_PORT}`,
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    launchOptions: executablePath ? { executablePath } : {},
  },
  webServer: [
    {
      command: `uv run --directory ${API_DIR} python -m tools.mock_comfy --port ${COMFY_PORT} --root ${COMFY_ROOT} --step-delay 0.01 --steps 3`,
      url: `http://127.0.0.1:${COMFY_PORT}/system_stats`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `bash e2e/start-api.sh ${API_PORT} ${COMFY_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
