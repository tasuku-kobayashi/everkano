import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { apiCall, fixture, login, shot, waitJob } from "./support";

/**
 * Acceptance items 18–28 (portrait-studio prompt) against the real API + mock ComfyUI + mock face engine.
 * Runs serially: later tests reuse the character registered by the wizard test.
 */
test.describe.configure({ mode: "serial" });

let characterId = "";

test("18/28 first run redirects to settings and accepts the API key", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByTestId("first-run")).toBeVisible();
  await shot(page, "05a-settings-first-run");
  await page.getByTestId("api-key-input").fill("e2e-test-api-key-0123456789");
  await page.getByTestId("api-key-save").click();
  await expect(page.getByTestId("toast-success")).toBeVisible();
  await expect(page.getByTestId("health-状態")).toContainText("接続中");
});

test("18 characters screen: empty state with a creation call to action", async ({ page }) => {
  await login(page);
  await page.goto("/");
  await expect(page.getByTestId("empty-state")).toContainText("まず 1 体作りましょう");
  await expect(page.getByTestId("vram-meter")).toHaveAttribute("data-state", "online"); // 22
  await shot(page, "01-characters-empty");
});

test("19 wizard: seed → select → verify → register → done", async ({ page }) => {
  await login(page);
  await page.goto("/create");
  await expect(page.getByTestId("synthetic-notice")).toContainText("実在の人物の写真は使用しないでください");

  // (b) upload: 3 faces + 1 landscape
  await page.getByTestId("file-input").setInputFiles([fixture("face_a_front.png"), fixture("face_a_side.png"), fixture("face_b_front.png"), fixture("landscape.png")]);
  await expect(page.getByTestId("candidate-count")).toContainText("候補 4 枚");
  // (a) text: 4 drafts through the mock ComfyUI
  await page.getByTestId("seed-generate").click();
  await expect(page.getByTestId("draft-result")).toHaveCount(6, { timeout: 60_000 });
  await page.getByRole("button", { name: "すべて追加" }).click();
  await expect(page.getByTestId("candidate-count")).toContainText("候補 10 枚");
  await shot(page, "02a-wizard-step1-seed");

  await page.getByTestId("to-step-2").click();
  await expect(page.getByTestId("recommendation")).toContainText("推奨", { timeout: 30_000 });
  const cards = page.getByTestId("quality-card");
  await expect(cards).toHaveCount(10);
  await expect(page.locator('[data-testid="quality-card"][data-usable="false"]')).toHaveCount(1); // landscape is unusable
  await expect(page.locator('[data-testid="quality-card"][data-recommended="true"]')).toHaveCount(1);
  await expect(page.getByText("横顔・傾き").first()).toBeVisible();
  await shot(page, "02b-wizard-step2-select");

  await page.getByTestId("confirm-reference").click();
  await expect(page.getByTestId("verify-reference")).toBeVisible();
  await page.getByTestId("run-verify").click();
  await expect(page.getByTestId("verify-grid")).toBeVisible({ timeout: 90_000 });
  await expect(page.locator('[data-testid="verify-grid"] [data-grade]')).toHaveCount(9, { timeout: 90_000 });
  await expect(page.locator('[data-testid="verify-grid"] [data-grade="warning"]').first()).toBeVisible(); // weight 0.6 -> other person in the mock
  await shot(page, "02c-wizard-step3-verify");

  await page.getByTestId("lock-weight-1").click();
  const submit = page.getByTestId("register-submit");
  await expect(submit).toBeDisabled();
  await page.getByTestId("register-name").fill("E2E 花子");
  await expect(submit).toBeDisabled();
  await page.getByTestId("check-synthetic").check();
  await expect(submit).toBeDisabled();
  await page.getByTestId("check-adult").check();
  await expect(submit).toBeEnabled();
  await shot(page, "02d-wizard-step4-register");
  await submit.click();
  await expect(page.getByTestId("wizard-done")).toContainText("E2E 花子 を登録しました");
  await shot(page, "02e-wizard-step5-done");
  await page.getByTestId("go-workspace").click();
  await expect(page).toHaveURL(/\/workspace\//);
  characterId = page.url().split("/workspace/")[1]!;
  expect(characterId).toBeTruthy();
});

test("20/21/24/25 workspace: generate → results → history, blur on by default, locked warning, regenerate", async ({ page }) => {
  await login(page);
  await page.goto(`/workspace/${characterId}`);
  await expect(page.getByTestId("locked-panel")).toBeVisible();
  await page.getByTestId("prompt").fill("standing in a cafe, casual outfit");
  await page.getByRole("button", { name: "カフェ", exact: true }).click();
  await page.getByTestId("prompt").press("Enter"); // Enter generates
  await expect(page.getByTestId("job-card")).toBeVisible();
  await expect(page.locator('[data-testid="results"] [data-testid="image-card"]')).toHaveCount(4, { timeout: 90_000 });
  await expect(page.locator('[data-testid="workspace-history"] [data-testid="history-item"]')).toHaveCount(4);
  await expect(page.locator('[data-testid="results"] [data-blurred="true"]').first()).toBeVisible(); // 21 NSFW blur default ON
  await shot(page, "03a-workspace-results");

  // 24: locked change shows the warning
  await page.getByTestId("toggle-override").click();
  await expect(page.getByTestId("override-warning")).toContainText("同一性を変えます");
  await shot(page, "03b-workspace-locked-warning");
  await page.getByTestId("toggle-override").click();

  // OOM pre-warning appears the moment a dangerous resolution is chosen
  await page.getByTestId("toggle-details").click();
  await page.getByTestId("width").fill("2048");
  await page.getByTestId("height").fill("2048");
  await expect(page.getByTestId("oom-warning")).toBeVisible();
  await expect(page.getByTestId("oom-warning")).toHaveAttribute("data-risk", /unknown|high/);
  await shot(page, "03c-workspace-oom-warning");
  await page.getByTestId("width").fill("832");
  await page.getByTestId("height").fill("1216");
  await expect(page.getByTestId("oom-warning")).toHaveCount(0);

  // 25: one-click regenerate reproduces the params_snapshot
  const listBefore = await apiCall<{ items: Array<{ id: string; seed: number }> }>(page, "GET", `/api/images?character_id=${characterId}&limit=10`);
  const first = listBefore.items[0]!;
  await page.locator('[data-testid="results"] [data-testid="image-card"]').first().getByRole("button", { name: "画像を開く" }).click();
  await expect(page.getByTestId("image-viewer")).toBeVisible();
  await shot(page, "03d-image-viewer");
  await page.getByTestId("regenerate-same").click();
  await expect(page.getByTestId("toast-success").filter({ hasText: "再生成" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("image-viewer")).toHaveCount(0);
  const jobs = await apiCall<{ items: Array<{ id: string; type: string }> }>(page, "GET", "/api/jobs?limit=5");
  const regenJob = await waitJob(page, jobs.items[0]!.id);
  expect(regenJob.status).toBe("done");
  const original = await apiCall<{ params_snapshot: Record<string, unknown> }>(page, "GET", `/api/images/${first.id}`);
  const regen = await apiCall<{ params_snapshot: Record<string, unknown> }>(page, "GET", `/api/images/${regenJob.result_image_ids[0]}`);
  for (const key of ["prompt", "positive", "seed", "steps", "cfg", "sampler_name", "width", "height", "face_method", "face_weight", "checkpoint", "scene_ids"]) {
    expect(regen.params_snapshot[key], key).toEqual(original.params_snapshot[key]);
  }
  await expect(page.locator('[data-testid="workspace-history"] [data-testid="history-item"]')).toHaveCount(5);
});

test("23 character switching: dropdown and quick chips", async ({ page }) => {
  // second character through the API (same flow as the wizard, fewer steps)
  const analysis = await page.request.post("/api/characters/analyze", { headers: { "X-API-Key": "e2e-test-api-key-0123456789" }, multipart: { files: { name: "b.png", mimeType: "image/png", buffer: readFileSync(fixture("face_b_front.png")) } } });
  const imageId = (await analysis.json()).items[0].image_id as string;
  const other = await apiCall<{ id: string }>(page, "POST", "/api/characters", { name: "E2E 太郎", is_synthetic: true, adult_confirmed: true, reference_image_ids: [imageId], locked: { face_method: "faceid" } });
  await login(page);
  await page.goto(`/workspace/${characterId}`);
  await page.getByTestId("character-select").selectOption(other.id);
  await expect(page).toHaveURL(new RegExp(`/workspace/${other.id}$`));
  await expect(page.getByTestId("workspace-left")).toContainText("E2E 太郎");
  await expect(page.getByTestId("recent-characters")).toBeVisible();
  await page.getByTestId("recent-characters").getByRole("button", { name: "E2E 花子" }).click();
  await expect(page).toHaveURL(new RegExp(`/workspace/${characterId}$`));
  await shot(page, "03e-workspace-switch");
});

test("26 gallery: filters, compare tray, bulk actions, ZIP and virtual scrolling with 500+ images", async ({ page }) => {
  test.setTimeout(600_000);
  // bulk-create images through the API (serial queue; mock ComfyUI at 3 fake steps)
  const jobIds: string[] = [];
  for (let i = 0; i < 63; i += 1) {
    const r = await apiCall<{ job_id: string }>(page, "POST", "/api/generate", { character_id: characterId, prompt: `bulk ${i}`, count: 8, adult_only: true, seed: 1000 + i * 8 });
    jobIds.push(r.job_id);
  }
  const t0bulk = Date.now();
  const last = await waitJob(page, jobIds[jobIds.length - 1]!, 5000);
  expect(last.status).toBe("done");
  test.info().annotations.push({ type: "bulk-generation", description: `504 mock images in ${Math.round((Date.now() - t0bulk) / 1000)}s` });
  const listing = await apiCall<{ total: number }>(page, "GET", `/api/images?character_id=${characterId}&limit=1`);
  expect(listing.total).toBeGreaterThanOrEqual(500);

  await login(page);
  await page.goto("/gallery");
  await expect(page.getByTestId("gallery-total")).toContainText("枚");
  const rendered = await page.locator('[data-testid="image-grid"] [data-testid="image-card"]').count();
  const total = Number((await page.getByTestId("gallery-total").innerText()).replace(/[^0-9]/g, ""));
  expect(total).toBeGreaterThanOrEqual(500);
  expect(rendered).toBeLessThan(80); // virtualized: only visible rows are mounted
  test.info().annotations.push({ type: "virtual-scroll", description: `total=${total} renderedCards=${rendered}` });
  const t0 = Date.now();
  for (let i = 0; i < 10; i += 1) {
    await page.mouse.wheel(0, 2000);
    await page.waitForTimeout(50);
  }
  test.info().annotations.push({ type: "scroll-ms", description: `${Date.now() - t0}ms for 10 wheel steps` });
  await shot(page, "04c-gallery-500");

  await page.getByTestId("min-similarity").fill("0.5");
  await page.getByTestId("filter-character").selectOption(characterId);
  const cards = page.locator('[data-testid="image-grid"] [data-testid="image-card"]');
  await cards.nth(0).getByLabel("選択").check();
  await cards.nth(1).getByLabel("選択").check();
  await expect(page.getByTestId("bulk-bar")).toContainText("2 枚を選択中");
  await cards.nth(0).hover();
  await cards.nth(0).getByRole("button", { name: "比較" }).click();
  await cards.nth(1).hover();
  await cards.nth(1).getByRole("button", { name: "比較" }).click();
  await expect(page.getByTestId("compare-tray")).toContainText("参照顔（固定）");
  await shot(page, "04a-gallery-compare");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("zip-download").click()]);
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  await page.getByRole("checkbox", { name: "キャラ別にグループ表示" }).check();
  await expect(page.getByRole("heading", { name: /E2E 花子/ })).toBeVisible();
  await shot(page, "04b-gallery-grouped");
});

test("28 settings: compliance, VRAM table, audit log, presets", async ({ page }) => {
  await login(page);
  await page.goto("/settings");
  await expect(page.getByTestId("compliance-body")).toContainText("実在");
  await expect(page.getByTestId("vram-section")).toContainText("txt2img");
  await expect(page.getByTestId("audit-section").locator("tbody tr").first()).toBeVisible();
  await expect(page.getByTestId("presets-section")).toContainText("カフェ");
  await shot(page, "05b-settings");
  await page.getByTestId("compliance-section").scrollIntoViewIfNeeded();
  await shot(page, "05c-settings-compliance");
});

test("27 keyboard: Tab reaches the main controls, Esc closes dialogs, arrows move in the viewer", async ({ page }) => {
  await login(page);
  await page.goto(`/workspace/${characterId}`);
  await page.getByTestId("prompt").waitFor();
  const visited = new Set<string>();
  for (let i = 0; i < 60; i += 1) {
    await page.keyboard.press("Tab");
    const id = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el ? `${el.tagName}:${el.getAttribute("data-testid") ?? el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 20) ?? ""}` : "";
    });
    if (id) visited.add(id);
  }
  expect([...visited].some((v) => v.includes("prompt"))).toBeTruthy();
  expect([...visited].some((v) => v.includes("generate"))).toBeTruthy();
  expect([...visited].some((v) => v.includes("character-select"))).toBeTruthy();
  test.info().annotations.push({ type: "tab-reachable", description: [...visited].join(" | ") });

  await page.locator('[data-testid="workspace-history"] [data-testid="history-item"]').first().getByRole("button", { name: "画像を開く" }).click();
  await expect(page.getByTestId("image-viewer")).toContainText("画像 1 /");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("image-viewer")).toContainText("画像 2 /");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("image-viewer")).toContainText("画像 1 /");
  await page.keyboard.press("s");
  await expect(page.getByTestId("image-viewer").getByRole("button", { name: /お気に入り解除/ })).toBeVisible();
  await page.keyboard.press("c");
  await expect(page.getByTestId("compare-tray")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("image-viewer")).toHaveCount(0);
  await shot(page, "06-keyboard-compare-tray");
});
