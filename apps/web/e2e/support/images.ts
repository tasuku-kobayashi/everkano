import type { BrowserContext, Route } from "@playwright/test";

/**
 * 外部のプレースホルダー画像ホスト（シードの picsum.photos / api.dicebear.com / placehold.co）は
 * サンドボックスや CI から到達できないことがあるため、URL から決定的に色を決めた SVG を返す。
 * スクリーンショットが「画像の読み込み失敗」だらけにならないようにするためだけのスタブで、
 * 画像の内容そのものは検証しない。
 */

const EXTERNAL_IMAGE_HOSTS =
  /^https?:\/\/(picsum\.photos|fastly\.picsum\.photos|api\.dicebear\.com|placehold\.co)\//;

function hash(value: string): number {
  let h = 2166136261;
  for (const ch of value) h = Math.imul(h ^ (ch.codePointAt(0) ?? 0), 16777619) >>> 0;
  return h;
}

/** 投稿写真風（空のグラデーション + 太陽 + 丘） */
export function photoSvg(seed: string): string {
  const h = hash(seed) % 360;
  const h2 = (h + 50) % 360;
  const h3 = (h + 190) % 360;
  const sunX = 200 + (hash(`${seed}x`) % 680);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080">
<defs><linearGradient id="g" x1="0" y1="0" x2="0.4" y2="1"><stop offset="0" stop-color="hsl(${h},75%,78%)"/><stop offset="1" stop-color="hsl(${h2},60%,52%)"/></linearGradient></defs>
<rect width="1080" height="1080" fill="url(#g)"/>
<circle cx="${sunX}" cy="310" r="150" fill="hsl(${h3},85%,88%)" opacity="0.85"/>
<path d="M0 760 Q 260 600 520 720 T 1080 660 V1080 H0Z" fill="hsl(${h2},45%,32%)" opacity="0.6"/>
<path d="M0 880 Q 300 780 600 860 T 1080 840 V1080 H0Z" fill="hsl(${h},40%,22%)" opacity="0.7"/>
</svg>`;
}

/** アバター風（背景色 + 顔） */
export function avatarSvg(seed: string, background: string | null): string {
  const h = hash(seed) % 360;
  const bg =
    background && /^[0-9a-f]{6}$/i.test(background) ? `#${background}` : `hsl(${h},60%,85%)`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect width="256" height="256" fill="${bg}"/>
<circle cx="128" cy="138" r="62" fill="#f6dccb"/>
<path d="M62 132 Q 64 58 128 56 Q 192 58 194 132 Q 176 92 128 90 Q 84 92 62 132Z" fill="hsl(${h},35%,28%)"/>
<circle cx="106" cy="142" r="6" fill="#222"/><circle cx="150" cy="142" r="6" fill="#222"/>
<path d="M112 170 Q 128 180 144 170" stroke="#c0605a" stroke-width="5" fill="none" stroke-linecap="round"/>
<path d="M40 256 Q 128 190 216 256Z" fill="hsl(${(h + 180) % 360},45%,60%)"/>
</svg>`;
}

function fulfillImage(route: Route): Promise<void> {
  const url = new URL(route.request().url());
  const body = url.hostname.endsWith("dicebear.com")
    ? avatarSvg(
        url.searchParams.get("seed") ?? url.pathname,
        url.searchParams.get("backgroundColor"),
      )
    : photoSvg(url.pathname.split("/").filter(Boolean).slice(0, 2).join("/") || url.href);
  return route.fulfill({
    status: 200,
    contentType: "image/svg+xml",
    headers: { "Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*" },
    body,
  });
}

export async function stubExternalImages(context: BrowserContext): Promise<void> {
  await context.route(EXTERNAL_IMAGE_HOSTS, fulfillImage);
}
