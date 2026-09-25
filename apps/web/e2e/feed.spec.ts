import { visiblePostIds } from "./support/data";
import { sqlOne } from "./support/db";
import { expect, test } from "./support/fixtures";

/**
 * A3: ホームフィードが無限スクロールで表示される（§5.2）
 * - 1 ページ目（10 件）→ 下へスクロールで 2 ページ目以降が読み込まれる
 * - 並び順は published_at DESC（同時刻は id DESC）で、ページをまたいで重複・欠落がない
 * - 最後まで読むと「すべて確認済みです」
 */

const FEED_PAGE_SIZE = 10;

test("A3: ホームフィードが無限スクロールで表示される", async ({ page, makeUser, login }) => {
  const user = await makeUser();
  const before = new Date();
  await login(page, user);

  const cards = page.getByTestId("post-card");
  await expect(cards.first()).toBeVisible();
  await expect(page.getByTestId("stories-row")).toBeVisible();
  await expect(page.getByTestId("story").first()).toBeVisible();
  await expect(cards).toHaveCount(FEED_PAGE_SIZE);
  const after = new Date();

  // フィードの 1 枚目は画面幅いっぱいの正方形の画像
  const media = cards.first().getByTestId("post-media");
  const box = await media.boundingBox();
  const viewport = page.viewportSize();
  expect(box && viewport).toBeTruthy();
  if (box && viewport) {
    expect(Math.round(box.width)).toBe(viewport.width);
    expect(Math.abs(box.width - box.height)).toBeLessThanOrEqual(1);
  }

  // 下へスクロール → 2 ページ目
  await expect(async () => {
    await page.mouse.wheel(0, 4000);
    expect(await cards.count()).toBeGreaterThan(FEED_PAGE_SIZE);
  }).toPass({ timeout: 20_000, intervals: [300] });
  expect(await cards.count()).toBeGreaterThanOrEqual(FEED_PAGE_SIZE * 2);

  // 最後まで
  const end = page.getByTestId("feed-end");
  await expect(async () => {
    await page.mouse.wheel(0, 6000);
    await expect(end).toBeVisible({ timeout: 500 });
  }).toPass({ timeout: 60_000, intervals: [200] });
  await expect(end).toContainText("すべて確認済みです");

  const ids = await cards.evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-post-id") ?? ""),
  );
  expect(new Set(ids).size, "ページをまたいで重複がない").toBe(ids.length);

  // DB の公開済み投稿（published_at DESC, id DESC）と完全に一致する（欠落なし・順序どおり）。
  // 予約投稿がテスト中に公開される場合に備え、1 ページ目の取得前後の 2 時点のどちらかと一致すればよい
  const expectedBefore = await visiblePostIds(before);
  const expectedAfter = await visiblePostIds(after);
  const matchesBefore = JSON.stringify(expectedBefore) === JSON.stringify(ids);
  if (!matchesBefore) expect(ids, "published_at DESC, id DESC で DB と一致").toEqual(expectedAfter);
  expect(ids.length).toBeGreaterThan(FEED_PAGE_SIZE * 2);
});

test("A3: フィードの投稿をタップすると投稿詳細が開き、戻ると位置が保たれる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  const free = page.locator('[data-testid="post-card"][data-paid="false"]');
  await expect(free.first()).toBeVisible();

  // 2 枚目の無料投稿までスクロールしてからタップ
  const target = free.nth(1);
  const postId = await target.getAttribute("data-post-id");
  await target.getByTestId("post-media").scrollIntoViewIfNeeded();
  const scrollBefore = await page.evaluate(() => window.scrollY);
  await target.getByTestId("post-media").click();
  await expect(page).toHaveURL(new RegExp(`/posts/${postId}$`));
  await expect(page.getByRole("region", { name: "コメント" })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(`[data-post-id="${postId}"]`)).toBeVisible();
  const scrollAfter = await page.evaluate(() => window.scrollY);
  expect(Math.abs(scrollAfter - scrollBefore), "戻ったときにスクロール位置が保たれる").toBeLessThan(
    200,
  );
});

test("A3: 長いキャプションは 2 行 +「… 続きを読む」で省略され、タップで全文を表示する", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  const moreButton = page.getByRole("button", { name: "キャプションの続きを読む" });
  await expect(moreButton.first()).toBeVisible();
  const postId =
    (await page
      .getByTestId("post-card")
      .filter({ has: moreButton })
      .first()
      .getAttribute("data-post-id")) ?? "";
  const card = page.locator(`[data-testid="post-card"][data-post-id="${postId}"]`);
  const { caption, handle } = await sqlOne<{ caption: string; handle: string }>(
    `select p.caption, c.handle from public.posts p join public.characters c on c.id = p.character_id
      where p.id = $1`,
    [postId],
  );
  const captionParagraph = card
    .locator("p")
    .filter({ has: page.getByRole("link", { name: handle, exact: true }) });

  // 省略表示: 2 行に収まり、「… 続きを読む」が 3 行目に押し出されて隠れていない。
  // 表示している本文はキャプションの先頭部分で、絵文字を途中で切っていない
  const metrics = await captionParagraph.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const button = el.querySelector("button")?.getBoundingClientRect();
    return {
      height: box.height,
      bottom: box.bottom,
      lineHeight: Number.parseFloat(getComputedStyle(el).lineHeight),
      buttonBottom: button?.bottom ?? Number.NaN,
      text: el.textContent ?? "",
    };
  });
  expect(metrics.height).toBeLessThanOrEqual(metrics.lineHeight * 2 + 1);
  expect(metrics.buttonBottom).toBeLessThanOrEqual(metrics.bottom + 1);
  expect(metrics.text.startsWith(handle)).toBe(true);
  expect(metrics.text.endsWith("… 続きを読む")).toBe(true);
  const shown = metrics.text.slice(handle.length, -"… 続きを読む".length);
  expect(shown.length).toBeGreaterThan(0);
  expect(caption.startsWith(shown), `「${shown}」はキャプションの先頭部分`).toBe(true);
  expect(shown).not.toContain("\uFFFD");

  // タップで全文
  await card.getByRole("button", { name: "キャプションの続きを読む" }).click();
  await expect(card.getByRole("button", { name: "キャプションの続きを読む" })).toHaveCount(0);
  await expect
    .poll(() => captionParagraph.evaluate((el) => el.textContent))
    .toBe(`${handle}${caption}`);
});
