import { freePostWithComments } from "./support/data";
import {
  composer,
  isChatStreamResponse,
  messageLog,
  openConversation,
  typingIndicator,
} from "./support/dm";
import { E2E, MISAKI } from "./support/env";
import { expect, test } from "./support/fixtures";
import { tab, toast } from "./support/ui";

/**
 * A11: PWA としてホーム画面に追加でき、スタンドアロン起動する
 * 「ホーム画面に追加」と実際のスタンドアロン起動は実機でしか確認できないため、ここではその前提を検証する:
 * - Web App Manifest（display: standalone・名前・start_url・192/512/maskable アイコン）
 * - iOS 用の meta（apple-mobile-web-app-capable / apple-touch-icon / viewport-fit=cover）
 * - Service Worker が登録され、再読み込み後にページを制御している（本番ビルドで登録される）
 * - オフライン時のページ遷移で /offline（事前キャッシュ済み）が表示される
 * - SW はビルドごとの URL（/sw.js?v=<ビルドID>）で登録され、/_next/static のキャッシュは件数の上限で古い順に消える
 */

/** SW の登録 URL（/sw.js?v=<ビルドID>） */
const SW_SCRIPT_URL = /\/sw\.js\?v=[A-Za-z0-9_-]+$/;

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
  expect(scriptURL).toMatch(SW_SCRIPT_URL);

  // 初回の読み込みは SW の登録前に始まっているため、再読み込み後から SW がページを制御する
  await page.reload();
  const controller = await page.evaluate(
    () => navigator.serviceWorker.controller?.scriptURL ?? null,
  );
  expect(controller).toMatch(SW_SCRIPT_URL);

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

  // オフラインでページを開くと、事前キャッシュしたオフラインページ（URL は開こうとした画面のまま）
  const reload = page.getByRole("button", { name: "再読み込み" });
  await context.setOffline(true);
  try {
    await page.goto("/search").catch(() => undefined);
    await expect(page.getByRole("heading", { name: "オフラインです" })).toBeVisible();
    await expect(page).toHaveURL(/\/search$/);

    // 「再読み込み」は開こうとしていた画面を読み込み直す（まだ圏外ならオフラインページのまま。ホームへ飛ばさない）
    const navigated = page.waitForEvent("framenavigated", (frame) => frame === page.mainFrame());
    await reload.click();
    await navigated;
    await expect(page.getByRole("heading", { name: "オフラインです" })).toBeVisible();
    await expect(page).toHaveURL(/\/search$/);
  } finally {
    await context.setOffline(false);
  }
  // 通信が戻ったら自動で読み込み直す（未ログインなので /search → ログイン画面。戻り先に /search を保持）
  await expect(page).toHaveURL(/\/login\?next=%2Fsearch$/, { timeout: 15_000 });

  // /offline を直接開いた場合だけ、「再読み込み」でホームへ（未ログインなのでログイン画面）
  await page.goto("/offline");
  await expect(page.getByRole("heading", { name: "オフラインです" })).toBeVisible();
  await reload.click();
  await expect(page).toHaveURL(/\/login$/);
});

test("SW: オフライン用キャッシュはビルドごと、/_next/static のキャッシュは上限（200 件）を超えると古い順に消える", async ({
  page,
}) => {
  await page.goto("/login");
  const buildId = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return new URL(registration.active?.scriptURL ?? location.href).searchParams.get("v");
  });
  expect(buildId).toBeTruthy();
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);

  // 旧方式（手動の VERSION）のキャッシュは残っておらず、オフライン用キャッシュの名前にビルド ID が入っている
  const names = await page.evaluate(() => caches.keys());
  expect(names).toContain(`everkano-offline-v2-${buildId}`);
  expect(names.filter((name) => /^everkano-(static|offline)-v1$/.test(name))).toEqual([]);

  // 静的キャッシュを上限まで埋めてから、まだキャッシュに無い /_next/static のファイルを SW 経由で取得する
  const result = await page.evaluate(async () => {
    const cache = await caches.open("everkano-static-v2");
    const script = Array.from(document.querySelectorAll<HTMLScriptElement>("script[src]"))
      .map((el) => new URL(el.src))
      .find((url) => url.pathname.startsWith("/_next/static/"));
    if (!script) throw new Error("no /_next/static script on the page");
    for (const name of await caches.keys()) await (await caches.open(name)).delete(script.href);
    for (let i = 0; i < 205; i += 1) {
      await cache.put(`/_next/static/e2e-filler-${i}.js`, new Response("//"));
    }
    await fetch(script.href);
    // キャッシュへの保存と削減は SW の waitUntil の中で行われる
    for (let i = 0; i < 50; i += 1) {
      const keys = (await cache.keys()).map((req) => new URL(req.url).pathname);
      if (keys.length <= 200 && keys.includes(script.pathname)) {
        return {
          size: keys.length,
          hasFetched: true,
          hasOldest: keys.includes("/_next/static/e2e-filler-0.js"),
        };
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const keys = (await cache.keys()).map((req) => new URL(req.url).pathname);
    return {
      size: keys.length,
      hasFetched: keys.includes(script.pathname),
      hasOldest: keys.includes("/_next/static/e2e-filler-0.js"),
    };
  });
  expect(result).toEqual({ size: 200, hasFetched: true, hasOldest: false });

  // 後片付け（このコンテキストはテストごとに破棄されるが、念のため）
  await page.evaluate(async () => {
    const cache = await caches.open("everkano-static-v2");
    for (const req of await cache.keys()) {
      if (req.url.includes("e2e-filler-")) await cache.delete(req);
    }
  });
});

test("初回表示で使う別オリジン（Supabase）へ事前に接続する（preconnect）", async ({ request }) => {
  const res = await request.get("/login");
  const html = await res.text();
  const head = html.slice(0, html.indexOf("</head>"));
  const supabaseOrigin = new URL(E2E.supabaseURL).origin;
  expect(head).toMatch(
    new RegExp(`<link[^>]+rel="preconnect"[^>]+href="${supabaseOrigin.replace(/[.]/g, "\\.")}/?"`),
  );
  expect(head).toMatch(/<link[^>]+rel="dns-prefetch"/);
});

test.describe("D-3: 通信が切れたとき", () => {
  // ページのキャッシュ（Service Worker）の影響を受けないよう、この中では SW を使わない
  test.use({ serviceWorkers: "block" });

  test("DM: 圏外で送信すると「入力中…」のまま止まらず、通信エラーと再送ボタンを表示する", async ({
    page,
    context,
    makeUser,
    login,
  }) => {
    const user = await makeUser();
    await login(page, user, "/dm");
    await openConversation(page, MISAKI);

    const text = `圏外テスト ${Date.now()}`;
    await context.setOffline(true);
    try {
      await composer(page).fill(text);
      await page.getByRole("button", { name: "送信", exact: true }).click();
      // 原因を端末の電波と決めつけない文言（API の停止・再起動でも同じ失敗になるため）
      await expect(
        toast(page, "通信できませんでした。接続を確認して、しばらくしてから再度お試しください。"),
      ).toBeVisible({ timeout: 10_000 });
      await expect(typingIndicator(page, MISAKI.name)).toBeHidden();
      const retry = messageLog(page, MISAKI.name).getByRole("button", {
        name: `送信できませんでした。タップで再送: ${text}`,
      });
      await expect(retry).toBeVisible();
    } finally {
      await context.setOffline(false);
    }

    // 電波が戻ったら、失敗した吹き出しをタップして再送できる
    const response = page.waitForResponse(isChatStreamResponse, { timeout: 30_000 });
    await messageLog(page, MISAKI.name)
      .getByRole("button", { name: `送信できませんでした。タップで再送: ${text}` })
      .click();
    expect((await response).status()).toBe(200);
  });

  test("読み取り: Supabase に届かない・応答が止まったときも、スケルトンのままにせずエラー表示にする", async ({
    page,
    makeUser,
    login,
  }) => {
    test.setTimeout(90_000);
    const user = await makeUser();
    await login(page, user, "/me");
    await expect(page.getByRole("button", { name: "ログアウト" })).toBeVisible();
    const posts = "**/rest/v1/posts**";

    // 通信失敗（DNS 失敗・キャプティブポータル等。navigator.onLine は true のまま）:
    // postgrest-js の自動リトライ（1/2/4 秒待ち × 3）を重ねず、React Query の 1 回の再試行だけで表示する
    let attempts = 0;
    await page.route(posts, (route) => {
      attempts += 1;
      return route.abort("internetdisconnected");
    });
    let started = Date.now();
    await tab(page, "ホーム").click();
    await expect(page.getByText("読み込めませんでした").first()).toBeVisible({ timeout: 10_000 });
    expect(Date.now() - started, "エラー表示までの時間（ms）").toBeLessThan(6_000);
    expect(attempts, "リクエスト回数（初回 + 再試行 1 回）").toBeLessThanOrEqual(2);
    await page.unroute(posts);

    // 応答が返らない（通信が固まった）: 15 秒のタイムアウトでエラー表示（再試行ボタンが出る）
    await page.goto("/me");
    await expect(page.getByRole("button", { name: "ログアウト" })).toBeVisible();
    await page.route(posts, () => undefined);
    started = Date.now();
    await tab(page, "ホーム").click();
    await expect(page.getByText("読み込めませんでした").first()).toBeVisible({ timeout: 25_000 });
    expect(Date.now() - started).toBeLessThan(20_000);
    await page.unroute(posts);
  });

  test("コメント: 圏外で投稿すると、送信中のまま止まらずに通信エラーを表示する", async ({
    page,
    context,
    makeUser,
    login,
  }) => {
    const user = await makeUser();
    const post = await freePostWithComments(test.info().project.name === "iphone" ? 4 : 5);
    await login(page, user, `/posts/${post.id}`);
    await expect(page.getByTestId("comment-list")).toBeVisible({ timeout: 20_000 });

    await context.setOffline(true);
    try {
      await page.getByTestId("comment-input").fill("圏外からのコメント");
      await page.getByTestId("comment-submit").click();
      await expect(toast(page, /通信できませんでした/)).toBeVisible({ timeout: 10_000 });
      await expect(page.getByRole("status", { name: "投稿中" })).toHaveCount(0);
    } finally {
      await context.setOffline(false);
    }
  });
});
