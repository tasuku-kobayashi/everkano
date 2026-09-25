import { randomBytes } from "node:crypto";
import type { Locator, Page } from "@playwright/test";
import { freePostWithComments, paidPost } from "./support/data";
import { sql } from "./support/db";
import { E2E, MISAKI } from "./support/env";
import { messageLog, sendAndWaitReply } from "./support/dm";
import { expect, test } from "./support/fixtures";
import { expectNoHorizontalOverflow, expectNoOrphanLine, tab, tabBar } from "./support/ui";

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

// ---------------------------------------------------------------------------
// ホームフィードの再読み込み（PWA ではブラウザの再読み込みが無いため、アプリ内で更新できること）
// ---------------------------------------------------------------------------

/** テスト用の投稿を「今」公開する（後片付けで削除する） */
async function publishTestPost(label: string): Promise<{ id: string; caption: string }> {
  const caption = `e2e-refresh-${label}-${randomBytes(4).toString("hex")}`;
  const [row] = await sql<{ id: string }>(
    `insert into public.posts (character_id, image_url, caption, published_at)
     values ($1, 'https://picsum.photos/seed/e2e-refresh/1080/1080', $2, now()) returning id`,
    [MISAKI.id, caption],
  );
  if (!row) throw new Error("insert post failed");
  return { id: row.id, caption };
}

/** 画面上部から下へ指で引っ張って離す（CDP のタッチイベント） */
async function pullDown(page: Page, distance: number): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  const x = 200;
  const startY = 260;
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x, y: startY }],
  });
  for (let step = 1; step <= 10; step += 1) {
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x, y: startY + (distance * step) / 10 }],
    });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
}

test("ホーム: 先頭で Home タブ・ロゴを再タップ・下に引っ張ると、新しい投稿を読み込む", async ({
  page,
  makeUser,
  login,
}) => {
  const created: string[] = [];
  try {
    const user = await makeUser();
    await login(page, user);
    await expect(page.getByTestId("post-card").first()).toBeVisible();

    // 表示後に公開された投稿は、そのままでは出てこない
    const first = await publishTestPost("tab");
    created.push(first.id);
    await page.waitForTimeout(500);
    await expect(page.getByText(first.caption)).toHaveCount(0);

    // Home タブの再タップ（先頭にいるとき）→ 再読み込み（同じ URL への遷移はしない）
    await tab(page, "ホーム").click();
    await expect(page.getByText(first.caption).first()).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/$/);

    // ヘッダーのロゴのタップも同じ
    const byLogo = await publishTestPost("logo");
    created.push(byLogo.id);
    await page.getByRole("link", { name: "everkano ホーム" }).click();
    await expect(page.getByText(byLogo.caption).first()).toBeVisible({ timeout: 15_000 });

    // プルリフレッシュ（先頭で下に引っ張って離す）
    const second = await publishTestPost("pull");
    created.push(second.id);
    await pullDown(page, 260);
    await expect(page.getByText(second.caption).first()).toBeVisible({ timeout: 15_000 });

    // スクロールしている状態で Home タブ → まず先頭へ戻る
    await page.evaluate(() => window.scrollTo(0, 1500));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    await tab(page, "ホーム").click();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThanOrEqual(2);
  } finally {
    if (created.length > 0) await sql("delete from public.posts where id = any($1)", [created]);
  }
});

/** 縦スクロール量が y（±2px）になるまで待つ */
async function expectScrollY(page: Page, y: number, message: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const current = await page.evaluate(() => window.scrollY);
        return Math.abs(current - y) <= 2 ? y : Math.round(current);
      },
      { message, timeout: 5_000 },
    )
    .toBe(y);
}

test("ホーム: 別のタブへ移ってから Home タブで戻ると、読んでいた位置から表示する（再タップで先頭へ）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await expect(page.getByTestId("post-card").nth(4)).toBeVisible();

  const reading = 2_000;
  await page.evaluate((y) => window.scrollTo(0, y), reading);
  await expectScrollY(page, reading, "フィードを途中まで読む");

  // 検索タブ → Home タブ（通常の画面遷移。ブラウザの「戻る」ではない）
  await tab(page, "検索").click();
  await expect(page).toHaveURL(/\/search$/);
  await expect(page.getByTestId("explore-grid")).toBeVisible();
  await tab(page, "ホーム").click();
  await expect(page).toHaveURL(/\/$/);
  await expectScrollY(page, reading, "検索から Home タブで戻ったとき");

  // 戻った位置から少し読み進めて、プロフィールタブ → Home タブ
  const further = 2_400;
  await page.evaluate((y) => window.scrollTo(0, y), further);
  await expectScrollY(page, further, "さらに読み進める");
  await tab(page, "プロフィール").click();
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByRole("button", { name: "ログアウト" })).toBeVisible();
  await tab(page, "ホーム").click();
  await expect(page).toHaveURL(/\/$/);
  await expectScrollY(page, further, "プロフィールから Home タブで戻ったとき");

  // ホームで Home タブを再タップ → 先頭へ。その後に別のタブから戻っても先頭のまま
  await tab(page, "ホーム").click();
  await expectScrollY(page, 0, "Home タブの再タップ");
  await tab(page, "検索").click();
  await expect(page).toHaveURL(/\/search$/);
  await tab(page, "ホーム").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("post-card").first()).toBeVisible();
  await page.waitForTimeout(300);
  await expectScrollY(page, 0, "先頭で離れたホームは先頭から");
});

test("ホーム: ファーストビューの画像（LCP）はフェードインせず、2 枚目以降はフェードインする", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  const media = page.getByTestId("post-card").getByTestId("post-media");
  const first = media.first().locator("img").first();
  await expect(first).toBeVisible();
  await expect(first).toHaveAttribute("fetchpriority", "high");
  const firstClass = (await first.getAttribute("class")) ?? "";
  expect(firstClass).not.toContain("opacity-0");
  expect(firstClass).not.toContain("transition-opacity");

  const second = media.nth(1).locator("img").first();
  await expect(second).toHaveAttribute("loading", "lazy");
  await expect(second).toHaveClass(/transition-opacity/);
});

// ---------------------------------------------------------------------------
// 端末の「戻る」（Android の戻るジェスチャー）とシート・モーダル
// ---------------------------------------------------------------------------

test("端末の「戻る」はシート・モーダルだけを閉じ、UI で閉じた後の「戻る」は前の画面へ戻る", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await expect(page.getByTestId("post-card").first()).toBeVisible();
  await tab(page, "プロフィール").click();
  await expect(page).toHaveURL(/\/me$/);

  const sheet = page.getByRole("dialog", { name: "表示名を変更" });
  const withdraw = page.getByRole("alertdialog", { name: "退会しますか？" });

  // ボトムシート: 「戻る」で閉じる（画面は /me のまま）
  await page.getByRole("button", { name: "プロフィールを編集" }).click();
  await expect(sheet).toBeVisible();
  await page.goBack();
  await expect(sheet).toBeHidden();
  await expect(page).toHaveURL(/\/me$/);

  // モーダル: 「戻る」で閉じる
  await page.getByRole("button", { name: "退会する" }).click();
  await expect(withdraw).toBeVisible();
  await page.goBack();
  await expect(withdraw).toBeHidden();
  await expect(page).toHaveURL(/\/me$/);

  // UI（Esc・キャンセル）で閉じた場合は履歴を残さない → 次の「戻る」で前の画面（ホーム）へ
  await page.getByRole("button", { name: "プロフィールを編集" }).click();
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await page.getByRole("button", { name: "退会する" }).click();
  await withdraw.getByRole("button", { name: "キャンセル" }).click();
  await expect(withdraw).toBeHidden();
  await page.waitForTimeout(300);
  await expect(page).toHaveURL(/\/me$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("post-card").first()).toBeVisible();
});

test("シートの操作で画面遷移しても遷移は取り消されず、「戻る」で元の画面へ戻る", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  const card = page.getByTestId("post-card").first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "その他のオプション" }).click();
  const options = page.getByRole("dialog", { name: "投稿のオプション" });
  await expect(options).toBeVisible();
  await options.getByRole("button", { name: "プロフィールを見る" }).click();
  await expect(page).toHaveURL(/\/c\/[^/]+$/, { timeout: 15_000 });
  await expect(page.getByTestId("post-grid")).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page).toHaveURL(/\/c\/[^/]+$/);

  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await expect(options).toBeHidden();
  await expect(page.getByTestId("post-card").first()).toBeVisible();
});

// ---------------------------------------------------------------------------
// その他の基盤
// ---------------------------------------------------------------------------

test("絵文字で始まる表示名でも、アバターの頭文字が文字化けしない（文字数は 1 文字 = 1）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await sql("update public.profiles set display_name = $2 where id = $1", [user.id, "🌸さくら"]);
  await login(page, user, "/me");

  const tabAvatar = tab(page, "プロフィール");
  await expect(tabAvatar).toContainText("🌸");
  const text = (await tabAvatar.innerText()).trim();
  expect(text).toBe("🌸");
  expect(text).not.toMatch(/[\uD800-\uDFFF]/u);
  await expect(page.getByText("🌸", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "プロフィールを編集" }).click();
  const sheet = page.getByRole("dialog", { name: "表示名を変更" });
  await expect(sheet.getByText("4/30")).toBeVisible();
});

test("タブバーを出さない画面では DM 未読バッジを取得しない", async ({ page, makeUser, login }) => {
  const user = await makeUser();
  const post = await freePostWithComments(0);
  let threadCalls = 0;
  page.on("request", (req) => {
    if (req.url().includes("/rest/v1/rpc/list_dm_threads")) threadCalls += 1;
  });
  await login(page, user, `/posts/${post.id}`);
  await expect(page.getByTestId("comment-list")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(2_000);
  expect(threadCalls, "投稿詳細（タブバー非表示）で list_dm_threads を呼ばない").toBe(0);

  // タブバーのある画面に戻ると取得する
  await page.goto("/search");
  await expect(tabBar(page)).toBeVisible();
  await expect.poll(() => threadCalls).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// アクセシビリティの基盤（ランドマーク・見出し・シートを閉じる手段）
// ---------------------------------------------------------------------------

test("ログイン後の画面は本文が 1 つの <main> で、タブバーはその外。主要画面に h1 がある", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(0);
  // 未ログインのログイン画面にも h1（視覚的には非表示）
  await page.goto("/login");
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);

  const user = await makeUser();
  await login(page, user);
  const screens: [string, string, (p: Page) => Locator, string | RegExp | null][] = [
    ["home", "/", (p) => p.getByTestId("post-card").first(), "ホーム"],
    ["me", "/me", (p) => p.getByRole("button", { name: "ログアウト" }), null],
    ["post", `/posts/${post.id}`, (p) => p.getByTestId("comment-list"), null],
    ["profile", `/c/${MISAKI.handle}`, (p) => p.getByTestId("post-grid"), null],
    ["search", "/search", (p) => p.getByTestId("explore-grid"), null],
    ["dm-inbox", "/dm", (p) => p.getByRole("heading", { name: "おすすめ" }), null],
    ["dm-conversation", `/dm/${MISAKI.id}`, (p) => messageLog(p, MISAKI.name), null],
  ];
  for (const [name, path, ready, heading] of screens) {
    await page.goto(path);
    await expect(ready(page)).toBeVisible({ timeout: 20_000 });
    const main = page.getByRole("main");
    await expect(main, `${name}: <main> は 1 つ`).toHaveCount(1);
    // タブバー（ナビゲーション）は <main> の外
    await expect(main.getByRole("navigation", { name: "メインメニュー" })).toHaveCount(0);
    if (["home", "me", "post", "profile"].includes(name)) {
      const h1 = page.getByRole("heading", { level: 1 });
      await expect(h1.first(), `${name}: h1`).toBeAttached();
      if (heading) await expect(h1.first()).toHaveText(heading);
    }
  }
});

test("ボトムシートは「閉じる」ボタンで閉じられる（初期フォーカスは入力欄のまま）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/me");
  await page.getByRole("button", { name: "プロフィールを編集" }).click();
  const sheet = page.getByRole("dialog", { name: "表示名を変更" });
  await expect(sheet).toBeVisible();
  // 開いた直後は入力欄にフォーカス（「閉じる」ではなく）
  await expect(sheet.getByLabel(/表示名/)).toBeFocused();

  // キーボード / スクリーンリーダーでも「閉じる」に到達できる（フォーカストラップ内）
  const close = sheet.getByRole("button", { name: "閉じる" });
  await expect(close).toBeVisible();
  let reached = false;
  for (let i = 0; i < 6 && !reached; i += 1) {
    await page.keyboard.press("Tab");
    reached = await close.evaluate((el) => el === document.activeElement);
  }
  expect(reached, "Tab で「閉じる」にフォーカスできる").toBe(true);

  await close.click();
  await expect(sheet).toBeHidden();
  await expect(page).toHaveURL(/\/me$/);
});

test("起動時のアカウント確認は、Auth（/auth/v1/user）の応答を待たずに profiles の取得を始める", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/me");
  await expect(page.getByRole("button", { name: "ログアウト" })).toBeVisible();

  // Auth への問い合わせを 1.5 秒遅らせ、その間に profiles の取得が始まるかを見る（直列なら 1.5 秒以上後になる）
  const DELAY_MS = 1_500;
  let userRequestedAt = 0;
  let profilesRequestedAt = 0;
  await page.route("**/auth/v1/user", async (route) => {
    userRequestedAt ||= Date.now();
    await new Promise((resolve) => setTimeout(resolve, DELAY_MS));
    await route.continue();
  });
  page.on("request", (request) => {
    if (request.url().includes("/rest/v1/profiles") && profilesRequestedAt === 0) {
      profilesRequestedAt = Date.now();
    }
  });

  await page.reload();
  await expect(page.getByRole("button", { name: "ログアウト" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(user.email).first()).toBeVisible();
  expect(userRequestedAt, "/auth/v1/user を呼ぶ").toBeGreaterThan(0);
  expect(profilesRequestedAt, "profiles を取得する").toBeGreaterThan(0);
  expect(profilesRequestedAt - userRequestedAt, "profiles の取得開始までの待ち（ms）").toBeLessThan(
    DELAY_MS - 500,
  );
});

test("空状態の見出しは、最後の 1 文字だけが次の行に落ちない（有料投稿がまだ無いキャラ）", async ({
  page,
  makeUser,
  login,
}) => {
  // 有料投稿の無いキャラのプロフィール（シードのキャラは全員有料投稿を持つため、一覧の取得だけを空にする）
  await page.route(/\/rest\/v1\/posts\?.*is_paid=eq\.true/, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
      : route.fallback(),
  );
  const user = await makeUser();
  await login(page, user, `/c/${MISAKI.handle}`);
  await page.getByTestId("profile-tab-paid").click();
  const heading = page.getByRole("heading", { name: "有料コンテンツはまだありません" });
  await expect(heading).toBeVisible();
  await expectNoOrphanLine(heading, "有料タブの空状態の見出し");
});
