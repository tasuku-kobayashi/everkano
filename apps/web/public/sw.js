/*
 * everkano Service Worker（手書き・ビルド不要）
 *
 * 方針:
 * - ページ遷移（navigate）: ネットワーク優先。オフラインなら /offline を返す（HTML はキャッシュしない。
 *   ログイン中ユーザーのページを端末に残さないため）
 * - /_next/static/*: キャッシュ優先（ファイル名にハッシュが入る不変アセット）。件数に上限を設け、古いものから消す
 * - /icons/*・/manifest.json: キャッシュを返しつつ裏で取り直す（stale-while-revalidate。アイコンの差し替えが届く）
 * - Supabase・Python API・CDN など他オリジンへのリクエスト、/auth/*・/media/* は一切扱わない（キャッシュしない）
 *
 * バージョン:
 * - ビルドごとの ID を登録 URL のクエリ（/sw.js?v=<ビルドID>。components/pwa/service-worker-register.tsx）で受け取る。
 *   デプロイのたびに登録 URL が変わるので新しい SW がインストールされ、/offline（とその CSS/JS）を取り直し、
 *   前のビルドのオフライン用キャッシュは activate 時に消える。
 * - CACHE_POLICY はキャッシュの構成（名前・中身の方針）を変えたときだけ上げる（全キャッシュを作り直す）。
 * - /_next/static のキャッシュはビルドをまたいで使い回す（デプロイ直後もまだ開いている古い画面が、
 *   遅延読み込みする古いチャンクを取れるように）。代わりに STATIC_MAX_ENTRIES 件を超えたら古い順に消す。
 */

const CACHE_POLICY = "v2";
const BUILD_ID = new URL(self.location.href).searchParams.get("v") || "dev";
const STATIC_CACHE = `everkano-static-${CACHE_POLICY}`;
const OFFLINE_CACHE = `everkano-offline-${CACHE_POLICY}-${BUILD_ID}`;
const OFFLINE_URL = "/offline";
const PRECACHE_URLS = [
  OFFLINE_URL,
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
];
/**
 * /_next/static のキャッシュの上限（件数）。1 ビルドで 1 セッションに使うチャンクは 40 件前後。
 * 上限を超えたら追加した順に古いものから消す（iOS の PWA はストレージの上限が小さく、溢れると
 * オリジンのデータがまとめて消されることがあるため、デプロイのたびに増え続けないようにする）。
 */
const STATIC_MAX_ENTRIES = 200;

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(OFFLINE_CACHE);
      await cache.addAll(PRECACHE_URLS.map((url) => new Request(url, { cache: "reload" })));

      // オフラインページが参照する CSS / JS も同じキャッシュに取り込んでおく（無いとスタイルなしで表示される）。
      // STATIC_CACHE に入れると件数の上限で消されることがあるため、ビルドごとのオフライン用キャッシュに入れる
      try {
        const response = await cache.match(OFFLINE_URL);
        const html = response ? await response.text() : "";
        const assets = Array.from(new Set(html.match(/\/_next\/static\/[^"'\s)\\]+/g) || []));
        await Promise.all(
          assets.map((url) =>
            cache.add(url).catch((error) => console.warn("[sw] precache failed:", url, error)),
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
      await trimCache(STATIC_CACHE, STATIC_MAX_ENTRIES);
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable();
      }
      await self.clients.claim();
    })(),
  );
});

/**
 * キャッシュを maxEntries 件以下にする（cache.keys() は追加順なので、先頭 = 古いものから消す）。
 * 複数のリクエストから同時に呼ばれても、消しすぎ・例外にはならない（delete は冪等）。
 */
async function trimCache(cacheName, maxEntries) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    const excess = keys.length - maxEntries;
    if (excess <= 0) return;
    await Promise.all(keys.slice(0, excess).map((request) => cache.delete(request)));
  } catch (error) {
    console.warn("[sw] failed to trim cache:", cacheName, error);
  }
}

function isImmutableAsset(url) {
  return url.pathname.startsWith("/_next/static/");
}

function isRevalidatedAsset(url) {
  return url.pathname.startsWith("/icons/") || url.pathname === "/manifest.json";
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

async function cacheFirst(event) {
  const { request } = event;
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    const copy = response.clone();
    event.waitUntil(
      (async () => {
        try {
          const cache = await caches.open(STATIC_CACHE);
          await cache.put(request, copy);
          await trimCache(STATIC_CACHE, STATIC_MAX_ENTRIES);
        } catch (error) {
          console.warn("[sw] cache put failed:", error);
        }
      })(),
    );
  }
  return response;
}

/** キャッシュがあればすぐ返し、裏でネットワークから取り直して次回に備える（無ければネットワーク） */
async function staleWhileRevalidate(event) {
  const { request } = event;
  const cache = await caches.open(OFFLINE_CACHE);
  const cached = await cache.match(request);
  const refresh = fetch(request).then(async (response) => {
    if (response.ok && response.type === "basic") {
      // 保存に失敗（容量超過など）しても、取得できたレスポンスは返す
      await cache
        .put(request, response.clone())
        .catch((error) => console.warn("[sw] cache put failed:", error));
    }
    return response;
  });
  if (!cached) return refresh;
  event.waitUntil(
    refresh.catch((error) => console.warn("[sw] revalidate failed:", request.url, error)),
  );
  return cached;
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

  if (isImmutableAsset(url)) {
    event.respondWith(cacheFirst(event));
    return;
  }

  if (isRevalidatedAsset(url)) {
    event.respondWith(staleWhileRevalidate(event));
  }
});
