import { expect, type Locator, type Page } from "@playwright/test";

/** 画面共通の要素 */

export function tabBar(page: Page): Locator {
  return page.getByRole("navigation", { name: "メインメニュー" });
}

/** 下部タブ（ホーム / 検索 / メッセージ / プロフィール） */
export function tab(page: Page, name: "ホーム" | "検索" | "メッセージ" | "プロフィール"): Locator {
  return tabBar(page).getByRole("link", { name, exact: name !== "メッセージ" });
}

/** トースト（画面下の通知）。Next.js のルートアナウンサー（role=alert）と区別するため aria-live 領域の中を探す */
export function toast(page: Page, text: string | RegExp): Locator {
  return page.locator('[aria-live="polite"]').getByText(text);
}

export function lockModal(page: Page): Locator {
  return page.getByRole("alertdialog", { name: "この投稿は有料コンテンツです" });
}

/**
 * 有料投稿のロックモーダル → 「購入する（準備中）」 → トースト「課金機能は現在準備中です」→ 閉じる。
 * ボタンを押しても何も処理されず画面遷移もしないこと（URL が変わらないこと）も確認する。
 */
export async function expectPaidLockFlow(
  page: Page,
  expectedPriceTokens: number,
  onShown?: (step: "modal" | "toast") => Promise<void>,
): Promise<void> {
  const urlBefore = page.url();
  const modal = lockModal(page);
  await expect(modal).toBeVisible();
  await expect(modal.getByTestId("paid-price")).toHaveText(
    `${expectedPriceTokens.toLocaleString("ja-JP")} tokens`,
  );
  await onShown?.("modal");
  await modal.getByRole("button", { name: "購入する（準備中）" }).click();
  await expect(toast(page, "課金機能は現在準備中です")).toBeVisible();
  await onShown?.("toast");
  expect(page.url(), "「準備中」ボタンで画面遷移しない").toBe(urlBefore);
  await modal.getByRole("button", { name: "閉じる" }).click();
  await expect(modal).toBeHidden();
}

/** 画像が CSS で強くぼかされていること（filter: blur(Npx) の N >= minPx） */
export async function expectBlurred(img: Locator, minPx = 8): Promise<void> {
  const filter = await img.evaluate((el) => getComputedStyle(el).filter);
  const px = Number(/blur\(([\d.]+)px\)/.exec(filter)?.[1] ?? 0);
  expect(px, `CSS filter: ${filter}`).toBeGreaterThanOrEqual(minPx);
}

/** 横スクロール（はみ出し）が発生していないこと（A1 の代替チェック） */
export async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(
    Math.max(metrics.scrollWidth, metrics.bodyScrollWidth),
    `${label}: ページ幅が画面幅（${metrics.innerWidth}px）を超えない`,
  ).toBeLessThanOrEqual(metrics.innerWidth);
}

/**
 * 文字色と、その要素の実際の背景色（透明なら祖先をたどる）とのコントラスト比（WCAG 2.1 の式）。
 * 半透明の背景は下の色と合成する。opacity 等は考慮しない（通常表示の文字の検査用）。
 */
export async function textContrast(
  locator: Locator,
): Promise<{ ratio: number; fg: string; bg: string }> {
  return locator.evaluate((el) => {
    type Rgba = [number, number, number, number];
    const parse = (color: string): Rgba => {
      const m = /rgba?\(([\d.]+),?\s*([\d.]+),?\s*([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\)/.exec(color);
      if (!m) return [0, 0, 0, 0];
      const alphaRaw = m[4];
      const alpha =
        alphaRaw === undefined
          ? 1
          : alphaRaw.endsWith("%")
            ? Number(alphaRaw.slice(0, -1)) / 100
            : Number(alphaRaw);
      return [Number(m[1]), Number(m[2]), Number(m[3]), alpha];
    };
    const over = (top: Rgba, bottom: Rgba): Rgba => {
      const a = top[3] + bottom[3] * (1 - top[3]);
      if (a === 0) return [0, 0, 0, 0];
      const mix = (i: 0 | 1 | 2) => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a;
      return [mix(0), mix(1), mix(2), a];
    };
    const layers: Rgba[] = [];
    for (let node: Element | null = el; node; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0) layers.push(bg);
      if (bg[3] >= 1) break;
    }
    let background: Rgba = [255, 255, 255, 1];
    if (layers.at(-1)?.[3] !== 1) {
      // 最後まで不透明な背景が無ければ html の背景（無ければ白）
      background = parse(getComputedStyle(document.documentElement).backgroundColor);
      if (background[3] < 1) background = [255, 255, 255, 1];
    } else {
      background = layers.pop() as Rgba;
    }
    for (const layer of layers.reverse()) background = over(layer, background);
    const foreground = over(parse(getComputedStyle(el).color), background);
    const luminance = ([r, g, b]: Rgba) => {
      const f = (c: number) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const [hi, lo] = [luminance(foreground), luminance(background)].sort((a, b) => b - a) as [
      number,
      number,
    ];
    const css = (c: Rgba) => `rgb(${c.slice(0, 3).map(Math.round).join(", ")})`;
    return { ratio: (hi + 0.05) / (lo + 0.05), fg: css(foreground), bg: css(background) };
  });
}

/** 読ませる文字が WCAG 2.1 AA（4.5:1）以上のコントラストであること */
export async function expectReadableText(locator: Locator, label: string): Promise<void> {
  const { ratio, fg, bg } = await textContrast(locator);
  expect(ratio, `${label}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
}

/** 要素の文字列を、画面に表示された行ごとに分けて返す（折り返し位置の検査用。空白は除く） */
export async function renderedLines(locator: Locator): Promise<string[]> {
  return locator.evaluate((el) => {
    const lines: { top: number; text: string }[] = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent ?? "";
      let offset = 0;
      for (const char of Array.from(text)) {
        const range = document.createRange();
        range.setStart(node, offset);
        range.setEnd(node, offset + char.length);
        offset += char.length;
        if (!char.trim()) continue;
        const rect = range.getBoundingClientRect();
        if (rect.height === 0) continue;
        const line = lines.find((l) => Math.abs(l.top - rect.top) < rect.height / 2);
        if (line) line.text += char;
        else lines.push({ top: rect.top, text: char });
      }
    }
    return lines.sort((a, b) => a.top - b.top).map((line) => line.text);
  });
}

/** 見出しなどの最後の行が 1 文字だけになっていない（「…ありませ / ん」のような折り返しをしない） */
export async function expectNoOrphanLine(locator: Locator, label: string): Promise<void> {
  const lines = await renderedLines(locator);
  expect(lines.length, `${label}: 表示されている`).toBeGreaterThan(0);
  for (const line of lines) {
    expect(Array.from(line).length, `${label}: 行 ${JSON.stringify(lines)}`).toBeGreaterThan(1);
  }
}
