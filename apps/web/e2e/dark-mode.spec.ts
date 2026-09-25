import type { Page } from "@playwright/test";
import { freePostWithComments, paidPost } from "./support/data";
import { MISAKI } from "./support/env";
import { messageLog } from "./support/dm";
import type { TestUser } from "./support/auth";
import { expect, test } from "./support/fixtures";
import { expectReadableText } from "./support/ui";

/**
 * ダークモード（§4.1「ダークモード必須（端末設定に追従）」）の表示確認。
 * 端末設定がダーク（prefers-color-scheme: dark）のとき、主要画面の背景が黒・文字が明るい色になり、
 * 白い大きな面（ダーク対応漏れ）が残っていないことを確認する。
 */

test.use({ colorScheme: "dark" });

/** 画面内の「明るい大きな面」（背景色の輝度が高く、面積が大きい要素）を列挙する */
async function brightSurfaces(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const luminance = (color: string): number | null => {
      const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(color);
      if (!m) return null;
      const alpha = m[4] === undefined ? 1 : Number(m[4]);
      if (alpha < 0.5) return null;
      const [r, g, b] = [m[1], m[2], m[3]].map((v) => Number(v) / 255) as [number, number, number];
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const found: string[] = [];
    for (const el of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
      if (el.closest("img, svg, picture, video")) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width * rect.height < 2_000 || rect.bottom < 0 || rect.top > innerHeight) continue;
      const style = getComputedStyle(el);
      if (style.visibility === "hidden" || Number(style.opacity) === 0) continue;
      const lum = luminance(style.backgroundColor);
      if (lum !== null && lum > 0.85) {
        found.push(
          `${el.tagName.toLowerCase()}.${el.className.toString().slice(0, 80)} ${style.backgroundColor}`,
        );
      }
    }
    return found;
  });
}

async function expectDarkScreen(page: Page, name: string): Promise<void> {
  const colors = await page.evaluate(() => ({
    dark: matchMedia("(prefers-color-scheme: dark)").matches,
    background: getComputedStyle(document.body).backgroundColor,
    text: getComputedStyle(document.body).color,
  }));
  expect(colors.dark).toBe(true);
  expect(colors.background, `${name}: 背景は黒`).toBe("rgb(0, 0, 0)");
  expect(colors.text, `${name}: 文字は明るい色`).toBe("rgb(245, 245, 245)");
  expect(await brightSurfaces(page), `${name}: ダーク未対応の白い面が無い`).toEqual([]);
}

test("ダークモード: 主要画面が端末設定に追従して暗い配色で表示される", async ({
  page,
  makeUser,
  login,
}) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  await expect(page.getByPlaceholder("メールアドレス")).toBeVisible();
  await expectDarkScreen(page, "login");

  const free = await freePostWithComments(0);
  const paid = await paidPost(0);
  const user = await makeUser();
  await login(page, user);

  const screens: [string, string, (p: Page) => ReturnType<Page["getByTestId"]>][] = [
    ["home", "/", (p) => p.getByTestId("post-card").first()],
    ["search", "/search", (p) => p.getByTestId("explore-grid")],
    ["dm-inbox", "/dm", (p) => p.getByRole("heading", { name: "おすすめ" })],
    ["dm-conversation", `/dm/${MISAKI.id}`, (p) => messageLog(p, MISAKI.name)],
    ["post", `/posts/${free.id}`, (p) => p.getByTestId("comment-list")],
    ["post-paid", `/posts/${paid.id}`, (p) => p.getByText("有料コンテンツ")],
    ["profile", `/c/${MISAKI.handle}`, (p) => p.getByTestId("post-grid")],
    ["me", "/me", (p) => p.getByRole("button", { name: "ログアウト" })],
  ];
  for (const [name, path, ready] of screens) {
    await page.goto(path);
    await expect(ready(page)).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(400); // 画像のフェードイン
    await expectDarkScreen(page, name);
  }

  // シート・モーダルもダーク
  await page.goto(`/dm/${MISAKI.id}`);
  await page.getByRole("button", { name: `${MISAKI.name}が覚えていること` }).click();
  const sheet = page.getByRole("dialog", { name: `${MISAKI.name}が覚えていること` });
  await expect(sheet).toBeVisible();
  await expect(sheet).toHaveCSS("background-color", "rgb(38, 38, 38)");
  await page.waitForTimeout(300);
  expect(await brightSurfaces(page)).toEqual([]);

  // スクリーンリーダー（Esc・背景タップが無い）でも閉じられる「閉じる」ボタンがシート内にある
  await sheet.getByRole("button", { name: "閉じる" }).click();
  await expect(sheet).toBeHidden();
});

/**
 * 読ませる文字（エラー文言・テキストボタン・破壊的操作のラベル）が WCAG 2.1 AA（4.5:1）を満たす。
 * Instagram の #0095f6 / #ff3040 は塗り専用で、文字には --ig-blue-text / --ig-red-text を使う（globals.css）。
 */
async function expectReadableTexts(
  page: Page,
  scheme: string,
  user: TestUser,
  login: (page: Page, user: TestUser, next?: string) => Promise<void>,
): Promise<void> {
  await page.goto("/login");
  await page.getByPlaceholder("メールアドレス").fill("not-an-email");
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  await expectReadableText(page.locator("#login-error"), `${scheme}: ログインのエラー文言`);
  await expectReadableText(
    page.getByRole("button", { name: "確認コードをお持ちの場合" }),
    `${scheme}: テキストボタン（青）`,
  );

  await login(page, user, "/me");
  await expectReadableText(
    page.getByRole("button", { name: "ログアウト" }),
    `${scheme}: ログアウト`,
  );
  await expectReadableText(page.getByRole("button", { name: "退会する" }), `${scheme}: 退会する`);
  await page.getByRole("button", { name: "退会する" }).click();
  const dialog = page.getByRole("alertdialog", { name: "退会しますか？" });
  await expect(dialog).toBeVisible();
  await page.waitForTimeout(300); // 表示アニメーション
  await expectReadableText(
    dialog.getByRole("button", { name: "退会する" }),
    `${scheme}: ダイアログの破壊的操作`,
  );
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(dialog).toBeHidden();
}

test("文字のコントラスト: エラー文言・テキストボタン・破壊的操作がライト / ダークとも 4.5:1 以上", async ({
  page,
  makeUser,
  login,
  newDeviceContext,
}) => {
  test.setTimeout(120_000);
  await expectReadableTexts(page, "dark", await makeUser("contrast-dark"), login);

  const context = await newDeviceContext({ colorScheme: "light" });
  await expectReadableTexts(
    await context.newPage(),
    "light",
    await makeUser("contrast-light"),
    login,
  );
});

test("ライトモード: 同じ画面が白背景・黒文字で表示される（端末設定に追従）", async ({
  newDeviceContext,
}) => {
  const context = await newDeviceContext({ colorScheme: "light" });
  const page = await context.newPage();
  await page.goto("/login");
  const colors = await page.evaluate(() => ({
    background: getComputedStyle(document.body).backgroundColor,
    text: getComputedStyle(document.body).color,
  }));
  expect(colors).toEqual({ background: "rgb(255, 255, 255)", text: "rgb(0, 0, 0)" });
  // 検査関数自体のポジティブコントロール（ライトでは白い面が検出される）
  expect((await brightSurfaces(page)).length).toBeGreaterThan(0);
});
