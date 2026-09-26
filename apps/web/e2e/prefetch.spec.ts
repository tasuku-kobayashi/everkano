import type { Page, Request } from "@playwright/test";
import { messageLog, suggestionLink } from "./support/dm";
import { MISAKI } from "./support/env";
import { expect, test } from "./support/fixtures";

/**
 * 遷移先のデータの先読み（lib/queries/prefetch.ts・prefetchDmConversation）。
 *
 * リンクに触れた時点（pointerdown）で、遷移（RSC の往復・ページの JS の読み込み）を待たずに遷移先の画面の
 * データ（Supabase の SELECT）を取りに行く。遷移先の画面は先読みしたキャッシュをそのまま使い、同じデータを
 * 取り直さない。ここでは「触れただけ（遷移していない）の時点でリクエストが出ていること」と
 * 「遷移後に同じリクエストが増えないこと」を確かめる（キーの一致は lib/queries/prefetch.test.ts）。
 */

/** Supabase REST（/rest/v1/<table>?<column>=eq.<value>）への GET を数える */
function trackRest(page: Page, table: string, column: string, value: string) {
  const matches = (request: Request) => {
    if (request.method() !== "GET") return false;
    const url = new URL(request.url());
    return url.pathname === `/rest/v1/${table}` && url.searchParams.get(column) === `eq.${value}`;
  };
  let count = 0;
  page.on("request", (request) => {
    if (matches(request)) count += 1;
  });
  return {
    count: () => count,
    /** 次の一致するリクエストを待つ（先に待ち始めてから操作する） */
    next: () => page.waitForRequest(matches, { timeout: 5_000 }),
  };
}

const DM_PATH = new RegExp(`/dm/${MISAKI.id}$`);
const PROFILE_PATH = new RegExp(`/c/${MISAKI.handle}$`);

test("DM 会話のヘッダー → プロフィール: 触れた時点でプロフィールのデータを取りに行き、遷移後は取り直さない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const profile = trackRest(page, "characters", "handle", MISAKI.handle);
  await login(page, user, `/dm/${MISAKI.id}`);
  await expect(messageLog(page, MISAKI.name)).toBeVisible({ timeout: 20_000 });
  expect(profile.count(), "DM 画面自体はキャラを handle では取得しない").toBe(0);

  // DM ヘッダーのリンク（名前は「美咲のプロフィール（AIキャラクター・<今の状況>）」）
  const link = page.getByRole("link", {
    name: new RegExp(`^${MISAKI.name}のプロフィール（AIキャラクター・`),
  });
  const requested = profile.next();
  await link.dispatchEvent("pointerdown");
  await requested;
  await expect(page, "触れただけでは遷移しない").toHaveURL(DM_PATH);

  await link.click();
  await expect(page).toHaveURL(PROFILE_PATH);
  await expect(page.getByTestId("profile-header")).toContainText(MISAKI.name);
  expect(profile.count(), "プロフィール画面は先読みしたキャラ本体を使う").toBe(1);
});

test("DM 会話の先頭の「プロフィールを見る」: 触れた時点でプロフィールのデータを取りに行く", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const profile = trackRest(page, "characters", "handle", MISAKI.handle);
  await login(page, user, `/dm/${MISAKI.id}`);
  // 新しい会話（挨拶だけ）なので、会話の先頭のプロフィールカードが表示されている
  const link = page.getByRole("link", { name: "プロフィールを見る" });
  await expect(link).toBeVisible({ timeout: 20_000 });
  expect(profile.count()).toBe(0);

  const requested = profile.next();
  await link.dispatchEvent("pointerdown");
  await requested;
  await expect(page).toHaveURL(DM_PATH);

  await link.click();
  await expect(page).toHaveURL(PROFILE_PATH);
  await expect(page.getByTestId("profile-header")).toContainText(MISAKI.name);
  expect(profile.count()).toBe(1);
});

test("DM 一覧の「おすすめ」（会話のまだ無いキャラ）: 触れた時点で会話画面のキャラ情報を取りに行き、会話は作らない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const dmCharacter = trackRest(page, "characters", "id", MISAKI.id);
  const createConversation: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/conversations") {
      createConversation.push(request.url());
    }
  });

  await login(page, user, "/dm");
  await expect(page.getByText("メッセージはまだありません")).toBeVisible();
  const row = suggestionLink(page, MISAKI);
  await expect(row).toBeVisible();
  expect(dmCharacter.count()).toBe(0);

  const requested = dmCharacter.next();
  await row.dispatchEvent("pointerdown");
  await requested;
  await expect(page).toHaveURL(/\/dm$/);
  expect(createConversation, "会話の作成（副作用）は先読みしない").toEqual([]);

  await row.click();
  await expect(page).toHaveURL(DM_PATH);
  await expect(messageLog(page, MISAKI.name)).toBeVisible({ timeout: 20_000 });
  expect(dmCharacter.count(), "会話画面は先読みしたキャラ情報を使う").toBe(1);
});

test("フィード → 投稿詳細・プロフィール: 触れた時点で遷移先のデータを取りに行く", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/");
  const card = page.getByTestId("post-card").first();
  await expect(card).toBeVisible();
  const postId = await card.getAttribute("data-post-id");
  expect(postId).toBeTruthy();
  const profileLink = card.getByRole("link", { name: /のプロフィール$/ }).first();
  const handle = decodeURIComponent(
    (await profileLink.getAttribute("href"))?.replace(/^\/c\//, "") ?? "",
  );
  expect(handle).toMatch(/^[a-z0-9_.]{2,30}$/);

  // 投稿詳細（コメントアイコン）: 投稿本体とコメント一覧
  const post = trackRest(page, "posts", "id", postId ?? "");
  const comments = trackRest(page, "comments", "post_id", postId ?? "");
  const postRequested = post.next();
  const commentsRequested = comments.next();
  await card.getByRole("link", { name: "コメントを見る" }).dispatchEvent("pointerdown");
  await Promise.all([postRequested, commentsRequested]);
  await expect(page).toHaveURL(/\/$/);

  // プロフィール（投稿者のアバター）: キャラ本体
  const profile = trackRest(page, "characters", "handle", handle);
  const profileRequested = profile.next();
  await profileLink.dispatchEvent("pointerdown");
  await profileRequested;
  await expect(page).toHaveURL(/\/$/);
});
