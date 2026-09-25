import { paidPost } from "./support/data";
import { sql, sqlOne } from "./support/db";
import { expect, test } from "./support/fixtures";
import { expectBlurred, expectPaidLockFlow } from "./support/ui";

/**
 * A6: 有料投稿がぼかし＋鍵で表示され、タップで「準備中」モーダルが出る（§5.2 / §5.4 / H3）
 * フィード・投稿詳細・プロフィールのグリッドの 3 か所で確認する。
 * モーダルの「購入する（準備中）」はトースト「課金機能は現在準備中です」を出すだけ。
 * 有料投稿の本体画像（post_private_assets）のURLは画面にも通信にも一切現れない。
 */

async function privateAssetUrl(postId: string): Promise<string | null> {
  const rows = await sql<{ image_url: string }>(
    "select image_url from public.post_private_assets where post_id = $1",
    [postId],
  );
  return rows[0]?.image_url ?? null;
}

test("A6: フィードの有料投稿はぼかし＋鍵＋「有料コンテンツ」で、タップすると準備中モーダル", async ({
  page,
  makeUser,
  login,
}) => {
  const requested: string[] = [];
  page.on("request", (req) => requested.push(req.url()));
  const user = await makeUser();
  await login(page, user);

  const paidCard = page.locator('[data-testid="post-card"][data-paid="true"]').first();
  await expect(async () => {
    if ((await paidCard.count()) === 0) await page.mouse.wheel(0, 3000);
    await expect(paidCard).toBeAttached({ timeout: 500 });
  }).toPass({ timeout: 30_000 });
  await paidCard.scrollIntoViewIfNeeded();
  const postId = (await paidCard.getAttribute("data-post-id")) ?? "";
  const { price_tokens: price } = await sqlOne<{ price_tokens: number }>(
    "select price_tokens from public.posts where id = $1 and is_paid",
    [postId],
  );

  const media = paidCard.getByTestId("post-media");
  await expect(media.getByText("有料コンテンツ")).toBeVisible();
  await expect(media.getByText("タップして詳細を見る")).toBeVisible();
  await expect(media.locator("svg").first()).toBeVisible(); // 鍵アイコン
  await expectBlurred(media.locator("img"));

  await media.click();
  await expectPaidLockFlow(page, price);
  await expect(page).toHaveURL(/\/$/);

  const asset = await privateAssetUrl(postId);
  if (asset) {
    expect(requested, "有料投稿の本体画像は取得されない").not.toContain(asset);
    expect(await page.content()).not.toContain(asset);
  }
});

test("A6: 投稿詳細の有料投稿もぼかし＋鍵で、タップすると準備中モーダル", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await paidPost(0);
  const user = await makeUser();
  await login(page, user, `/posts/${post.id}`);

  const card = page.getByTestId("post-card");
  await expect(card).toHaveAttribute("data-paid", "true");
  const media = card.getByTestId("post-media");
  await expect(media.getByText("有料コンテンツ")).toBeVisible();
  await expectBlurred(media.locator("img"));

  await media.click();
  await expectPaidLockFlow(page, post.price_tokens);
  await expect(page).toHaveURL(new RegExp(`/posts/${post.id}$`));
});

test("A6: プロフィールの有料タブのサムネイルは全面ぼかし＋鍵で、タップすると準備中モーダル", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await paidPost(1);
  const user = await makeUser();
  await login(page, user, `/c/${post.handle}`);

  await page.getByTestId("profile-tab-paid").click();
  const tile = page.locator(`[data-testid="grid-paid-tile"]`).first();
  await expect(tile).toBeVisible();
  await expect(tile).toContainText("tokens");
  await expect(tile.locator("svg").first()).toBeVisible(); // 鍵アイコン
  await expectBlurred(tile.locator("img"));

  const tiles = page.getByTestId("grid-paid-tile");
  const count = await tiles.count();
  // タイルの価格表示と DB の価格が一致する（並び順は新しい順）
  const prices = await sql<{ price_tokens: number }>(
    `select p.price_tokens from public.posts p join public.characters c on c.id = p.character_id
      where c.handle = $1 and p.is_paid and p.published_at <= now()
      order by p.published_at desc, p.id desc`,
    [post.handle],
  );
  expect(count).toBe(prices.length);
  const firstPrice = prices[0]?.price_tokens ?? 0;

  await tile.click();
  await expectPaidLockFlow(page, firstPrice);
  await expect(page).toHaveURL(new RegExp(`/c/${post.handle.replace(".", "\\.")}$`));
});
