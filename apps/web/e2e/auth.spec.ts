import type { Page } from "@playwright/test";
import { uniqueEmail, waitForLoginMail } from "./support/auth";
import { deleteUsersByEmail, sql } from "./support/db";
import { expect, test } from "./support/fixtures";
import { tab } from "./support/ui";

/**
 * A2: マジックリンクでログインできる（§5.1 / ADR-0012）
 * 実際にログイン画面からメールを送り、Mailpit に届いたメールの
 *   (1) 「ログインする」リンク（/auth/confirm?token_hash=...）
 *   (2) 6 桁の確認コード
 * のそれぞれでログインする。どちらも初めてのメールアドレス（= 新規登録）で行う。
 */

const created: string[] = [];

test.afterAll(async () => {
  await deleteUsersByEmail(created);
});

async function requestLoginMail(page: Page, email: string) {
  await page.getByPlaceholder("メールアドレス").fill(email);
  const since = Date.now();
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  await expect(page.getByRole("heading", { name: "メールを確認してください" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
  return waitForLoginMail(email, since);
}

async function expectProfileCreated(email: string) {
  const rows = await sql<{ id: string; deleted_at: string | null }>(
    "select p.id, p.deleted_at from public.profiles p join auth.users u on u.id = p.id where lower(u.email) = lower($1)",
    [email],
  );
  expect(rows, "初回ログインで profiles が自動作成される").toHaveLength(1);
  expect(rows[0]?.deleted_at).toBeNull();
}

test("未ログインで開くとログイン画面へリダイレクトされる", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "ログインリンクを送信" })).toBeVisible();

  // ディープリンクはログイン後の戻り先として保持される
  await page.goto("/dm");
  await expect(page).toHaveURL(/\/login\?next=%2Fdm$/);
});

test("A2: メールのマジックリンク（/auth/confirm）でログインできる", async ({ page }) => {
  const email = uniqueEmail(`${test.info().project.name}-magic`);
  created.push(email);

  await page.goto("/login");
  const mail = await requestLoginMail(page, email);
  expect(mail.confirmUrl).toContain("/auth/confirm?token_hash=");
  expect(mail.confirmUrl).toContain("type=email");

  // メールのリンクをタップした想定（同じブラウザで開く）
  await page.goto(mail.confirmUrl);
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  await expect(page.getByRole("navigation", { name: "メインメニュー" })).toBeVisible();
  await expect(page.getByTestId("post-card").first()).toBeVisible();

  await tab(page, "プロフィール").click();
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByText(email).first()).toBeVisible();
  await expectProfileCreated(email);

  // 使用済みのリンクは再利用できない（ログアウト後に開くとエラー表示のログイン画面）
  await page.context().clearCookies();
  await page.goto(mail.confirmUrl);
  await expect(page).toHaveURL(/\/login\?error=link/);
  await expect(page.locator("#login-error")).toContainText("ログインリンクが無効");
});

test("A2: メールの 6 桁コードでログインできる（ホーム画面 PWA 用）", async ({ page }) => {
  const email = uniqueEmail(`${test.info().project.name}-code`);
  created.push(email);

  await page.goto("/login");
  const mail = await requestLoginMail(page, email);
  expect(mail.code).toMatch(/^\d{6}$/);

  // 誤ったコードはエラー
  const wrong = mail.code === "000000" ? "111111" : "000000";
  await page.getByPlaceholder("確認コード").fill(wrong);
  await expect(page.locator("#login-error")).toContainText("確認コードが正しくない");
  await expect(page.getByRole("button", { name: "コードでログイン" })).toBeEnabled();
  await expect(page).toHaveURL(/\/login/);

  // 正しいコード（6 桁入力で自動送信）
  await page.getByPlaceholder("確認コード").fill(mail.code);
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  await expect(page.getByTestId("post-card").first()).toBeVisible();
  await expectProfileCreated(email);

  // ログアウト → 保護されたページはログイン画面へ
  await page.goto("/me");
  await expect(page.getByText(email).first()).toBeVisible();
  await page.getByRole("button", { name: "ログアウト" }).click();
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  await page.goto("/dm");
  await expect(page).toHaveURL(/\/login/);
});

test("A2: 誤ったコードの検証中に正しいコードを貼り付けても、自動でログインできる", async ({
  page,
}) => {
  const email = uniqueEmail(`${test.info().project.name}-code-race`);
  created.push(email);

  await page.goto("/login");
  const mail = await requestLoginMail(page, email);
  const wrong = mail.code === "000000" ? "111111" : "000000";
  const input = page.getByPlaceholder("確認コード");

  // 誤ったコードの検証結果を待たずに、すぐ正しいコードへ置き換える
  await input.fill(wrong);
  await input.fill(mail.code);
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  await expectProfileCreated(email);
});
