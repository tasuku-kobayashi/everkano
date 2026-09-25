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
