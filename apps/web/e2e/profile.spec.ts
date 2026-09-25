import { freePostWithComments, profileCounts } from "./support/data";
import { sql, sqlOne } from "./support/db";
import { HINATA } from "./support/env";
import { messageLog } from "./support/dm";
import { expect, test } from "./support/fixtures";

/**
 * A5: プロフィールで「無料」「有料」タブが切り替わる（§5.4）
 * A7: キャラプロフィールから「DMする」で DM 画面に遷移する
 *
 * 投稿数・タイル数を DB と突き合わせるテストは、他のテストが投稿を追加しないキャラ（ひなた）で行う
 * （layout.spec.ts のホーム再読み込みのテストが美咲の投稿を一時的に追加するため、並列実行で件数がずれる）。
 */

/** 投稿数を突き合わせるテストの対象（テスト中に投稿が増減しないキャラ） */
const STABLE = HINATA;

test("A5: キャラプロフィールの「無料」「有料」タブが切り替わる", async ({
  page,
  makeUser,
  login,
}) => {
  const counts = await profileCounts(STABLE.handle);
  expect(counts.free).toBeGreaterThan(1);
  expect(counts.paid).toBeGreaterThan(0);
  const user = await makeUser();
  await login(page, user, `/c/${STABLE.handle}`);

  // ヘッダー: アバター / ハンドル / 投稿数 / フォロワー数 / 自己紹介 / DMする
  const header = page.getByTestId("profile-header");
  await expect(header).toBeVisible();
  // 画面の見出し（AppHeader の h1）= ハンドル
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(STABLE.handle);
  await expect(header.getByTestId("stat-posts").locator("dd")).toHaveText(String(counts.total));
  await expect(header).toContainText("フォロワー");
  await expect(header).toContainText(STABLE.name);
  await expect(page.getByTestId("dm-button")).toHaveText("DMする");

  const freeTab = page.getByTestId("profile-tab-free");
  const paidTab = page.getByTestId("profile-tab-paid");
  const freeTiles = page.getByTestId("grid-free-tile");
  const paidTiles = page.getByTestId("grid-paid-tile");

  // 初期表示は「無料」: is_paid = false の投稿だけの 3 列グリッド
  await expect(freeTab).toHaveAttribute("aria-selected", "true");
  await expect(paidTab).toHaveAttribute("aria-selected", "false");
  await expect(freeTiles).toHaveCount(counts.free);
  await expect(paidTiles).toHaveCount(0);
  const columns = await page
    .getByTestId("post-grid")
    .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
  expect(columns, "3 列グリッド").toBe(3);

  // 「有料」タブ: is_paid = true の投稿だけ（全面ぼかし + 鍵）
  await paidTab.click();
  await expect(paidTab).toHaveAttribute("aria-selected", "true");
  await expect(freeTab).toHaveAttribute("aria-selected", "false");
  await expect(paidTiles).toHaveCount(counts.paid);
  await expect(freeTiles).toHaveCount(0);

  // 「無料」に戻す
  await freeTab.click();
  await expect(freeTab).toHaveAttribute("aria-selected", "true");
  await expect(freeTiles).toHaveCount(counts.free);
  await expect(paidTiles).toHaveCount(0);

  // スクリーンリーダー: タイルごとに区別できる名前（キャラ名 + キャプションの冒頭）
  const labels = await freeTiles.evaluateAll((els) =>
    els.map((el) => el.getAttribute("aria-label") ?? ""),
  );
  expect(new Set(labels).size, "無料タイルの名前がすべて異なる").toBe(labels.length);
  for (const label of labels) expect(label).toMatch(new RegExp(`^${STABLE.name}の投稿を開く`));
  const firstId = ((await freeTiles.first().getAttribute("href")) ?? "").split("/").pop() ?? "";
  const { caption } = await sqlOne<{ caption: string }>(
    "select caption from public.posts where id = $1",
    [firstId],
  );
  const excerpt = caption.replace(/\s+/g, " ").trim().slice(0, 10);
  await expect(freeTiles.first()).toHaveAccessibleName(
    new RegExp(`^${STABLE.name}の投稿を開く: ${excerpt.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`),
  );

  // 無料のタイルは投稿詳細へ
  const firstFree = freeTiles.first();
  await firstFree.click();
  await expect(page).toHaveURL(/\/posts\/[0-9a-f-]{36}$/);
});

test("A5: 投稿のアバターをタップするとキャラプロフィールへ移動する", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(3);
  const user = await makeUser();
  await login(page, user, `/posts/${post.id}`);
  await page
    .getByTestId("post-card")
    .getByRole("link", { name: `${post.name}のプロフィール` })
    .click();
  await expect(page).toHaveURL(new RegExp(`/c/${post.handle.replace(".", "\\.")}$`));
  await expect(page.getByTestId("profile-header")).toContainText(post.name);
});

test("A7: キャラプロフィールの「DMする」で DM 画面に遷移し、挨拶が届く", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, `/c/${HINATA.handle}`);
  await page.getByTestId("dm-button").click();
  await expect(page).toHaveURL(new RegExp(`/dm/${HINATA.id}$`));

  // ヘッダーにキャラの名前、会話ログに挨拶（初回は POST /conversations で会話が作られる）
  await expect(page.locator("header").first()).toContainText(HINATA.name);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(HINATA.name);
  const log = messageLog(page, HINATA.name);
  await expect(log).toBeVisible({ timeout: 20_000 });

  const rows = await sql<{ conversation_id: string; body: string; sender_type: string }>(
    `select m.conversation_id, m.body, m.sender_type
       from public.messages m join public.conversations c on c.id = m.conversation_id
      where c.user_id = $1 and c.character_id = $2
      order by m.created_at`,
    [user.id, HINATA.id],
  );
  expect(rows, "挨拶メッセージが 1 件保存されている").toHaveLength(1);
  expect(rows[0]?.sender_type).toBe("character");
  await expect(log.getByText(rows[0]?.body ?? "", { exact: true })).toBeVisible();
  await expect(page.getByLabel("メッセージ", { exact: true })).toBeVisible();

  // DM 一覧にも会話が出る
  await page.getByRole("button", { name: "戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`/c/${HINATA.handle}$`));
  await page.goto("/dm");
  await expect(page.getByRole("link", { name: new RegExp(`^${HINATA.name}、`) })).toBeVisible();
});

test("A5: プロフィールを直接開くと、投稿数・グリッドの取得はキャラ本体の取得を待たずに始まる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();

  // キャラ本体（handle で取得）の応答を 1 秒遅らせ、その間に投稿数・グリッドの取得が始まっていることを確かめる
  let characterReleasedAt = 0;
  const postRequests: { at: number; method: string }[] = [];
  await page.route(/\/rest\/v1\/characters\?/, async (route) => {
    if (!route.request().url().includes(`handle=eq.${STABLE.handle}`)) return route.fallback();
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    characterReleasedAt = Date.now();
    return route.fallback();
  });
  page.on("request", (request) => {
    const url = decodeURIComponent(request.url());
    if (/\/rest\/v1\/posts\?/.test(url) && url.includes(`character.handle=eq.${STABLE.handle}`)) {
      postRequests.push({ at: Date.now(), method: request.method() });
    }
  });

  await login(page, user, `/c/${STABLE.handle}`);
  const counts = await profileCounts(STABLE.handle);
  await expect(page.getByTestId("grid-free-tile")).toHaveCount(counts.free);
  await expect(
    page.getByTestId("profile-header").getByTestId("stat-posts").locator("dd"),
  ).toHaveText(String(counts.total));
  expect(characterReleasedAt).toBeGreaterThan(0);
  // 投稿数（HEAD）とグリッド（GET）の両方が、キャラ本体の応答より先に始まっている
  const early = postRequests.filter((request) => request.at < characterReleasedAt);
  expect(early.map((request) => request.method).sort()).toEqual(["GET", "HEAD"]);
});
