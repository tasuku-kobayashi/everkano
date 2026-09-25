import type { Locator, Page } from "@playwright/test";
import { freePostWithComments, paidPost } from "./support/data";
import { E2E, MISAKI } from "./support/env";
import { messageLog, sendAndWaitReply } from "./support/dm";
import { expect, test } from "./support/fixtures";
import { expectNoHorizontalOverflow, tabBar } from "./support/ui";

/**
 * A1（代替）: スマホ幅でレイアウトが崩れない — 実機確認の代わりに、エミュレートした端末幅（390px / 412px）で
 *   全画面の横はみ出し（横スクロール）が無いこと・入力欄が 16px 以上（iOS の自動ズーム防止）であることを確認する。
 * H2: 投稿できるのは AI キャラクターのみ — ユーザー向けの投稿 UI（投稿タブ・ファイル選択・作成画面）が無い。
 */

const FILE_INPUT = 'input[type="file"]'; // scope-check: allow（H2: ファイル選択が「無い」ことを確かめるセレクタ）

interface Screen {
  name: string;
  path: string;
  ready: (page: Page) => Locator;
}

async function screens(): Promise<Screen[]> {
  const free = await freePostWithComments(0);
  const paid = await paidPost(0);
  return [
    { name: "home", path: "/", ready: (p) => p.getByTestId("post-card").first() },
    { name: "search", path: "/search", ready: (p) => p.getByTestId("explore-grid") },
    {
      name: "search-results",
      path: "/search?q=%E7%BE%8E%E5%92%B2",
      ready: (p) => p.getByTestId("search-result").first(),
    },
    { name: "dm-inbox", path: "/dm", ready: (p) => p.getByRole("heading", { name: "おすすめ" }) },
    { name: "dm-conversation", path: `/dm/${MISAKI.id}`, ready: (p) => messageLog(p, MISAKI.name) },
    { name: "post-free", path: `/posts/${free.id}`, ready: (p) => p.getByTestId("comment-list") },
    { name: "post-paid", path: `/posts/${paid.id}`, ready: (p) => p.getByText("有料コンテンツ") },
    { name: "profile", path: `/c/${MISAKI.handle}`, ready: (p) => p.getByTestId("post-grid") },
    { name: "me", path: "/me", ready: (p) => p.getByRole("button", { name: "ログアウト" }) },
  ];
}

test("A1: 全画面で横方向のはみ出しがなく、入力欄は 16px 以上", async ({
  page,
  makeUser,
  login,
}) => {
  test.setTimeout(120_000);

  // ログイン画面（未ログイン）
  await page.goto("/login");
  await expect(page.getByPlaceholder("メールアドレス")).toBeVisible();
  await expectNoHorizontalOverflow(page, "login");

  const user = await makeUser();
  await login(page, user);
  for (const screen of await screens()) {
    await page.goto(screen.path);
    await expect(screen.ready(page)).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(300);
    await expectNoHorizontalOverflow(page, screen.name);

    const smallInputs = await page
      .locator("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea")
      .evaluateAll((els) =>
        els
          .filter((el) => (el as HTMLElement).offsetParent !== null)
          .map((el) => ({
            label: el.getAttribute("aria-label") ?? el.getAttribute("placeholder") ?? el.id,
            size: parseFloat(getComputedStyle(el).fontSize),
          }))
          .filter((item) => item.size < 16),
      );
    expect(smallInputs, `${screen.name}: 16px 未満の入力欄（iOS でズームされる）`).toEqual([]);

    // 固定のタブバー / 入力欄が画面幅に収まる
    const nav = tabBar(page);
    if ((await nav.count()) > 0) {
      const box = await nav.boundingBox();
      expect(box?.width ?? 0).toBeLessThanOrEqual(page.viewportSize()?.width ?? 0);
    }
  }

  // プロフィールの有料タブ
  await page.goto(`/c/${MISAKI.handle}`);
  await page.getByTestId("profile-tab-paid").click();
  await expect(page.getByTestId("grid-paid-tile").first()).toBeVisible();
  await expectNoHorizontalOverflow(page, "profile-paid");

  // 長い単語（URL・連続した英数字）を含む DM でも吹き出しが折り返され、はみ出さない
  await page.goto(`/dm/${MISAKI.id}`);
  await expect(messageLog(page, MISAKI.name)).toBeVisible();
  const longWord = `https://example.com/${"a".repeat(120)}`;
  await sendAndWaitReply(page, MISAKI, longWord);
  await expectNoHorizontalOverflow(page, "dm-long-message");
});

test("H2: ユーザーが投稿する UI が存在しない（投稿タブ・ファイル選択・作成画面なし）", async ({
  page,
  makeUser,
  login,
  request,
}) => {
  const user = await makeUser();
  await login(page, user);

  // 下部タブは 4 つだけ（ホーム / 検索 / メッセージ / プロフィール）。投稿タブは存在しない
  const links = tabBar(page).getByRole("link");
  await expect(links).toHaveCount(4);
  const labels = await links.evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
  expect(labels).toEqual(["ホーム", "検索", "メッセージ", "プロフィール"]);

  // コメントの「投稿する」ボタンは対象外（コメントはユーザーも書ける）
  const postingUi = /新規投稿|新しい投稿|投稿を作成|写真を追加|アップロード|^作成$/;
  for (const screen of await screens()) {
    await page.goto(screen.path);
    await expect(screen.ready(page)).toBeVisible({ timeout: 20_000 });
    // ファイル選択（画像アップロード）の input が無い
    await expect(page.locator(FILE_INPUT), screen.name).toHaveCount(0);
    await expect(page.getByRole("button", { name: postingUi }), screen.name).toHaveCount(0);
    await expect(page.getByRole("link", { name: postingUi }), screen.name).toHaveCount(0);
  }

  // 投稿作成っぽい URL は存在しない（404 / 「このページはご利用いただけません」）
  for (const path of ["/new", "/create", "/upload", "/posts/new", "/compose"]) {
    const res = await page.goto(path);
    if (res?.status() !== 404) {
      // /posts/[postId] に当たるパスは「投稿が見つからない」表示（クライアントで判定）
      await expect(page.getByText("このページはご利用いただけません"), path).toBeVisible();
    }
    await expect(page.locator(FILE_INPUT)).toHaveCount(0);
  }

  // Python API にも投稿作成のエンドポイントは無い
  const apiPost = await request.post(`${E2E.apiURL}/posts`, { data: { caption: "x" } });
  expect([404, 405]).toContain(apiPost.status());
});
