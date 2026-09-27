import { resolve } from "node:path";
import { expect, type Page, type TestInfo } from "@playwright/test";

export const API_KEY = "e2e-test-api-key-0123456789"; // check-secrets: allow - dummy key for the E2E stack
export const FIXTURES = resolve(import.meta.dirname, ".tmp/fixtures");
export const SHOTS = process.env.E2E_SCREENSHOTS_DIR ?? resolve(import.meta.dirname, "../../docs/screenshots");

export function fixture(name: string): string {
  return resolve(FIXTURES, name);
}

/** Store the key the way the UI does (localStorage + cookie) so pages load authenticated. */
export async function login(page: Page): Promise<void> {
  await page.addInitScript((key) => {
    window.localStorage.setItem("portrait-studio-ui", JSON.stringify({ state: { apiKey: key, blurDefault: true, compareTray: [], recentCharacters: [], similarity: { good: 0.75, acceptable: 0.6 }, vramWarnPercent: 85 }, version: 0 }));
    document.cookie = `psk=${key}; path=/; SameSite=Strict`;
  }, API_KEY);
}

export async function shot(page: Page, name: string): Promise<void> {
  await page.waitForTimeout(300);
  await page.screenshot({ path: resolve(SHOTS, `${name}.png`), fullPage: false });
}

export async function apiCall<T>(page: Page, method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await page.request.fetch(path, { method, headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" }, data: body === undefined ? undefined : JSON.stringify(body) });
  expect(res.ok(), `${method} ${path}: ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function waitJob(page: Page, jobId: string, maxIterations = 600): Promise<{ status: string; result_image_ids: string[]; result: Record<string, unknown> | null; error: string | null }> {
  for (let i = 0; i < maxIterations; i += 1) {
    const job = await apiCall<{ status: string; result_image_ids: string[]; result: Record<string, unknown> | null; error: string | null }>(page, "GET", `/api/jobs/${jobId}`);
    if (["done", "error", "canceled"].includes(job.status)) return job;
    await page.waitForTimeout(100);
  }
  throw new Error(`job ${jobId} did not finish`);
}

export function note(info: TestInfo, text: string): void {
  info.annotations.push({ type: "note", description: text });
}
