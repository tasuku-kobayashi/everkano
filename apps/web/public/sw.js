/*
 * everkano Service Worker（手書き・ビルド不要）
 *
 * 方針:
 * - ページ遷移（navigate）: ネットワーク優先。オフラインなら /offline を返す（HTML はキャッシュしない。
 *   ログイン中ユーザーのページを端末に残さないため）
 * - /_next/static/*・/icons/*: キャッシュ優先（ファイル名にハッシュが入る不変アセット）
 * - Supabase・Python API・CDN など他オリジンへのリクエスト、/auth/*・/media/* は一切扱わない（キャッシュしない）
 *
 * キャッシュ構成を変えたら VERSION を上げること（古いキャッシュは activate 時に削除される）。
 */

const VERSION = "v1";
const STATIC_CACHE = `everkano-static-${VERSION}`;
const OFFLINE_CACHE = `everkano-offline-${VERSION}`;
const OFFLINE_URL = "/offline";
const PRECACHE_URLS = [
  OFFLINE_URL,
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(OFFLINE_CACHE);
      await cache.addAll(PRECACHE_URLS.map((url) => new Request(url, { cache: "reload" })));

      // オフラインページが参照する CSS / JS も取り込んでおく（無いとスタイルなしで表示される）
      try {
        const response = await cache.match(OFFLINE_URL);
        const html = response ? await response.text() : "";
        const assets = Array.from(new Set(html.match(/\/_next\/static\/[^"'\s)\\]+/g) || []));
        const staticCache = await caches.open(STATIC_CACHE);
        await Promise.all(
          assets.map((url) =>
            staticCache
              .add(url)
              .catch((error) => console.warn("[sw] precache failed:", url, error)),
          ),
        );
      } catch (error) {
        console.warn("[sw] failed to precache offline assets:", error);
      }

      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([STATIC_CACHE, OFFLINE_CACHE]);
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith("everkano-") && !keep.has(key))
          .map((key) => caches.delete(key)),
      );
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable();
      }
      await self.clients.claim();
    })(),
  );
});

function isCacheFirstAsset(url) {
  return url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/");
}

async function networkFirstNavigation(event) {
  try {
    const preloaded = await event.preloadResponse;
    if (preloaded) return preloaded;
    return await fetch(event.request);
  } catch (error) {
    console.warn("[sw] navigation failed, serving offline page:", error);
    const cache = await caches.open(OFFLINE_CACHE);
    const offline = await cache.match(OFFLINE_URL);
    return (
      offline ||
      new Response("オフラインです", {
        status: 503,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      })
    );
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    const cache = await caches.open(STATIC_CACHE);
    cache
      .put(request, response.clone())
      .catch((error) => console.warn("[sw] cache put failed:", error));
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // 他オリジン（Supabase / Python API / CDN）は素通し。絶対にキャッシュしない
  if (url.origin !== self.location.origin) return;
  // 認証・署名付き画像・API 的なルートは扱わない
  if (
    url.pathname.startsWith("/auth/") ||
    url.pathname.startsWith("/media/") ||
    url.pathname.startsWith("/api/")
  ) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(event));
    return;
  }

  if (isCacheFirstAsset(url)) {
    event.respondWith(cacheFirst(request));
  }
});
