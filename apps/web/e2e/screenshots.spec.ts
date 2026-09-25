import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { CreateCommentResponse } from "@everkano/shared";
import type { Page } from "@playwright/test";
import { accessTokenFor, uniqueEmail, waitForLoginMail } from "./support/auth";
import { addMemory, apiCall, isApiError } from "./support/api";
import { freePostWithComments, paidPost } from "./support/data";
import { deleteUsersByEmail, sql } from "./support/db";
import { MISAKI } from "./support/env";
import {
  composer,
  messageLog,
  openConversation,
  sendAndWaitReply,
  typingIndicator,
} from "./support/dm";
import { expect, test } from "./support/fixtures";
import { lockModal, toast } from "./support/ui";

/**
 * 受け入れ確認用のスクリーンショット（A1 の実機撮影の代わりの参考資料。iPhone 相当 390×844 @3x）。
 * 全画面をライト / ダークの両方で撮る。
 *
 * 保存先: E2E_SCREENSHOTS_DIR（apps/web からの相対パス可）。未指定なら test-results/screenshots。
 * docs/acceptance/screenshots を更新するには:
 *   E2E_SCREENSHOTS_DIR=../../docs/acceptance/screenshots pnpm e2e --project=iphone e2e/screenshots.spec.ts
 */

const OUT_DIR = resolve(process.env.E2E_SCREENSHOTS_DIR ?? "test-results/screenshots");

type Scheme = "light" | "dark";

async function settle(page: Page): Promise<void> {
  // 画面内に見えている画像の読み込み完了を待つ（画面外の loading="lazy" は読み込まれないので対象外）
  await page
    .waitForFunction(
      () =>
        Array.from(document.images)
          .filter((img) => {
            const rect = img.getBoundingClientRect();
            return rect.bottom > 0 && rect.top < innerHeight && rect.width > 0;
          })
          .every((img) => img.complete),
      undefined,
      { timeout: 5_000 },
    )
    .catch(() => undefined);
  await page.waitForTimeout(450); // 画像のフェードイン・シートのアニメーション
}

async function shot(page: Page, index: number, name: string, scheme: Scheme): Promise<void> {
  await settle(page);
  const file = `${String(index).padStart(2, "0")}-${name}-${scheme}.png`;
  await page.screenshot({ path: resolve(OUT_DIR, file), animations: "disabled", caret: "hide" });
}

test("スクリーンショット（全画面 × ライト / ダーク）", async ({
  page,
  makeUser,
  login,
  newDeviceContext,
}) => {
  test.skip(test.info().project.name !== "iphone", "iPhone 相当（390×844 @3x）のみで撮影する");
  test.setTimeout(300_000);
  mkdirSync(OUT_DIR, { recursive: true });

  // ------------------------------------------------------------------ 表示用のデータを用意
  const user = await makeUser("screens");
  await sql("update public.profiles set display_name = $1 where id = $2", ["ゆうと", user.id]);
  const token = await accessTokenFor(user);
  const post = await freePostWithComments(4);
  const paid = await paidPost(0);

  await login(page, user);
  await openConversation(page, MISAKI);
  await sendAndWaitReply(page, MISAKI, "こんばんは！今日もおつかれさま");
  await sendAndWaitReply(page, MISAKI, "来週、大阪に出張するんだ");
  await sendAndWaitReply(page, MISAKI, "ぼくは料理が好きなんだ");
  await addMemory(token, MISAKI.id, "ぼくの誕生日は3月3日");
  const comment = await apiCall<CreateCommentResponse>(token, "POST", "/comments", {
    post_id: post.id,
    body: "素敵な写真！どこで撮ったの？",
  });
  expect(comment.status).toBe(201);
  const commentId = isApiError(comment.body) ? "" : (comment.body?.comment.id ?? "");
  // キャラの返信（バックグラウンドで生成）を待つ
  await expect(async () => {
    const rows = await sql(
      "select 1 from public.comments where parent_comment_id = $1 and author_type = 'character'",
      [commentId],
    );
    expect(rows.length).toBeGreaterThan(0);
  }).toPass({ timeout: 30_000 });

  const storageState = await page.context().storageState();
  const loginEmails: string[] = [];

  try {
    for (const scheme of ["light", "dark"] as const) {
      // ---------------------------------------------------------------- ログイン画面（未ログイン）
      const anonContext = await newDeviceContext({ colorScheme: scheme });
      const anon = await anonContext.newPage();
      await anon.goto("/login");
      await expect(anon.getByPlaceholder("メールアドレス")).toBeVisible();
      await shot(anon, 1, "login", scheme);
      const email = uniqueEmail(`screens-login-${scheme}`);
      loginEmails.push(email);
      await anon.getByPlaceholder("メールアドレス").fill(email);
      const since = Date.now();
      await anon.getByRole("button", { name: "ログインリンクを送信" }).click();
      await expect(anon.getByRole("heading", { name: "メールを確認してください" })).toBeVisible();
      await waitForLoginMail(email, since);
      await shot(anon, 2, "login-code", scheme);
      await anonContext.close();

      // ---------------------------------------------------------------- ログイン後
      const context = await newDeviceContext({ colorScheme: scheme, storageState });
      const p = await context.newPage();

      await p.goto("/");
      await expect(p.getByTestId("post-card").first()).toBeVisible();
      await shot(p, 3, "home", scheme);

      const paidCard = p.locator('[data-testid="post-card"][data-paid="true"]').first();
      await expect(async () => {
        if ((await paidCard.count()) === 0) await p.mouse.wheel(0, 3000);
        await expect(paidCard).toBeAttached({ timeout: 500 });
      }).toPass({ timeout: 30_000 });
      // 有料投稿のカードをヘッダー（44px）の直下に合わせる
      await paidCard.evaluate((el) =>
        window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 44),
      );
      await shot(p, 4, "home-paid-post", scheme);
      await paidCard.getByTestId("post-media").click();
      await expect(lockModal(p)).toBeVisible();
      await shot(p, 5, "paid-lock-modal", scheme);
      await lockModal(p).getByRole("button", { name: "購入する（準備中）" }).click();
      await expect(toast(p, "課金機能は現在準備中です")).toBeVisible();
      await shot(p, 6, "paid-lock-toast", scheme);
      await lockModal(p).getByRole("button", { name: "閉じる" }).click();

      await p.goto(`/posts/${post.id}`);
      await expect(p.getByTestId("comment-list")).toBeVisible();
      await shot(p, 7, "post-detail", scheme);
      await p.getByTestId("comment-list").getByTestId("comment").last().scrollIntoViewIfNeeded();
      await shot(p, 8, "post-comments", scheme);

      await p.goto(`/posts/${paid.id}`);
      await expect(p.getByText("有料コンテンツ")).toBeVisible();
      await shot(p, 9, "post-detail-paid", scheme);

      await p.goto(`/c/${MISAKI.handle}`);
      await expect(p.getByTestId("grid-free-tile").first()).toBeVisible();
      await shot(p, 10, "profile-free", scheme);
      await p.getByTestId("profile-tab-paid").click();
      await expect(p.getByTestId("grid-paid-tile").first()).toBeVisible();
      await shot(p, 11, "profile-paid", scheme);

      await p.goto("/search");
      await expect(p.getByTestId("explore-grid")).toBeVisible();
      await shot(p, 12, "search", scheme);
      await p.getByTestId("search-input").fill("ひなた");
      await expect(p.getByTestId("search-result").first()).toBeVisible();
      await p.getByTestId("search-input").blur();
      await shot(p, 13, "search-results", scheme);

      await p.goto("/dm");
      await expect(p.getByRole("link", { name: new RegExp(`^${MISAKI.name}、`) })).toBeVisible();
      await shot(p, 14, "dm-inbox", scheme);

      await openConversation(p, MISAKI);
      await expect(messageLog(p, MISAKI.name).getByText("ぼくは料理が好きなんだ")).toBeVisible();
      await shot(p, 15, "dm-conversation", scheme);

      // 入力中インジケーター
      await composer(p).fill(
        scheme === "light" ? "週末は映画を見に行く予定なんだ" : "おやすみ前にちょっとだけ話そう",
      );
      await p.getByRole("button", { name: "送信", exact: true }).click();
      await expect(typingIndicator(p, MISAKI.name)).toBeVisible();
      await p.screenshot({ path: resolve(OUT_DIR, `16-dm-typing-${scheme}.png`), caret: "hide" });
      await expect(typingIndicator(p, MISAKI.name)).toBeHidden({ timeout: 20_000 });

      // メモリパネル
      await p.getByRole("button", { name: `${MISAKI.name}が覚えていること` }).click();
      const panel = p.getByRole("dialog", { name: `${MISAKI.name}が覚えていること` });
      await expect(panel.getByRole("listitem").first()).toBeVisible();
      await shot(p, 17, "memory-panel", scheme);
      await panel.getByRole("button", { name: "覚えてほしいことを追加" }).click();
      await panel
        .getByPlaceholder("例: 10月2日（金）に大事なプレゼンがある")
        .fill("週末は実家に帰る予定");
      await shot(p, 18, "memory-add", scheme);
      await panel.getByRole("button", { name: "キャンセル" }).click();
      await panel
        .getByRole("listitem")
        .first()
        .getByRole("button", { name: "この記憶を削除" })
        .click();
      await expect(p.getByRole("alertdialog", { name: "この記憶を削除しますか？" })).toBeVisible();
      await shot(p, 19, "memory-delete-confirm", scheme);
      await p.getByRole("alertdialog").getByRole("button", { name: "キャンセル" }).click();

      await p.goto("/me");
      await expect(p.getByRole("button", { name: "ログアウト" })).toBeVisible();
      await shot(p, 20, "me", scheme);

      await p.goto("/offline");
      await expect(p.getByRole("heading", { name: "オフラインです" })).toBeVisible();
      await shot(p, 21, "offline", scheme);

      await p.goto("/posts/00000000-0000-4000-8001-999999999999");
      await expect(p.getByText("このページはご利用いただけません")).toBeVisible();
      await shot(p, 22, "not-found", scheme);
      await context.close();
    }
  } finally {
    await deleteUsersByEmail(loginEmails);
  }
});
