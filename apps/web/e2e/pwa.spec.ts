import { expect, test } from "./support/fixtures";

/**
 * A11: PWA としてホーム画面に追加でき、スタンドアロン起動する
 * 「ホーム画面に追加」と実際のスタンドアロン起動は実機でしか確認できないため、ここではその前提を検証する:
 * - Web App Manifest（display: standalone・名前・start_url・192/512/maskable アイコン）
 * - iOS 用の meta（apple-mobile-web-app-capable / apple-touch-icon / viewport-fit=cover）
 * - Service Worker が登録され、再読み込み後にページを制御している（本番ビルドで登録される）
 * - オフライン時のページ遷移で /offline（事前キャッシュ済み）が表示される
 */

test.use({ serviceWorkers: "allow" });

interface ManifestIcon {
  src: string;
  sizes: string;
  type: string;
  purpose?: string;
}

interface Manifest {
  name: string;
  short_name: string;
  start_url: string;
  scope: string;
  display: string;
  background_color: string;
  theme_color: string;
  lang?: string;
  icons: ManifestIcon[];
}

/** PNG の IHDR から幅と高さを読む */
function pngSize(buffer: Buffer): { width: number; height: number } {
  expect(buffer.subarray(1, 4).toString("ascii")).toBe("PNG");
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

test("A11: Web App Manifest が standalone で、アイコンが取得できる", async ({ request }) => {
  const res = await request.get("/manifest.json");
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toContain("application/manifest+json");
  const manifest = (await res.json()) as Manifest;

  expect(manifest.display).toBe("standalone");
  expect(manifest.name).toBe("everkano");
  expect(manifest.short_name).toBeTruthy();
  expect(manifest.start_url).toBe("/");
  expect(manifest.scope).toBe("/");
  expect(manifest.lang).toBe("ja");
  expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);

  const sizes = manifest.icons.map((icon) => `${icon.sizes}:${icon.purpose ?? "any"}`);
  expect(sizes).toEqual(expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable"]));

  for (const icon of manifest.icons) {
    const iconRes = await request.get(icon.src);
    expect(iconRes.status(), icon.src).toBe(200);
    expect(iconRes.headers()["content-type"]).toBe(icon.type);
    const { width, height } = pngSize(await iconRes.body());
    expect(`${width}x${height}`, icon.src).toBe(icon.sizes);
  }
});

test("A11: マニフェストと iOS 用の meta・アイコンが <head> にあり、ブラウザが検出できる", async ({
  page,
  context,
  request,
}) => {
  // 動的ページ（ログイン画面・ホーム）でも <head> に出力される（<body> 側だとブラウザが検出しない）
  await page.goto("/login");
  const cdp = await context.newCDPSession(page);
  const detected = (await cdp.send("Page.getAppManifest")) as { url: string; errors: unknown[] };
  expect(detected.url, "Chromium がマニフェストを検出する").toMatch(/\/manifest\.json$/);
  expect(detected.errors).toEqual([]);

  const head = page.locator("head");
  await expect(head.locator('link[rel="manifest"]')).toHaveAttribute("href", "/manifest.json");
  await expect(
    head.locator('meta[name="apple-mobile-web-app-capable"], meta[name="mobile-web-app-capable"]'),
  ).not.toHaveCount(0);
  await expect(head.locator('meta[name="apple-mobile-web-app-title"]')).toHaveAttribute(
    "content",
    "everkano",
  );
  await expect(head.locator('meta[name="theme-color"]').first()).toHaveAttribute("content", /#/);
  const viewport = await head.locator('meta[name="viewport"]').getAttribute("content");
  expect(viewport).toContain("viewport-fit=cover");

  const touchIcon = await head.locator('link[rel="apple-touch-icon"]').first().getAttribute("href");
  expect(touchIcon).toBeTruthy();
  const iconRes = await request.get(touchIcon ?? "");
  expect(iconRes.status()).toBe(200);
  expect(pngSize(await iconRes.body())).toEqual({ width: 180, height: 180 });
});

test("A11: Service Worker が登録されてページを制御し、オフラインでは /offline を表示する", async ({
  page,
  context,
}) => {
  await page.goto("/login");
  const scriptURL = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return registration.active?.scriptURL ?? null;
  });
  expect(scriptURL).toMatch(/\/sw\.js$/);

  // 初回の読み込みは SW の登録前に始まっているため、再読み込み後から SW がページを制御する
  await page.reload();
  const controller = await page.evaluate(
    () => navigator.serviceWorker.controller?.scriptURL ?? null,
  );
  expect(controller).toMatch(/\/sw\.js$/);

  // Supabase / API へのリクエストはキャッシュしない（sw.js の方針）
  const cacheKeys = await page.evaluate(async () => {
    const urls: string[] = [];
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      urls.push(...(await cache.keys()).map((req) => req.url));
    }
    return urls;
  });
  expect(cacheKeys.some((url) => url.endsWith("/offline"))).toBe(true);
  expect(cacheKeys.filter((url) => !url.startsWith(new URL(page.url()).origin))).toEqual([]);

  // オフラインでページを開くと、事前キャッシュしたオフラインページ
  await context.setOffline(true);
  try {
    await page.goto("/search").catch(() => undefined);
    await expect(page.getByRole("heading", { name: "オフラインです" })).toBeVisible();
  } finally {
    await context.setOffline(false);
  }
});
