import type { Page, Request } from "@playwright/test";
import {
  banUser,
  confirmMagicLink,
  confirmPathFor,
  deleteAuthUser,
  uniqueEmail,
  waitForLoginMail,
} from "./support/auth";
import { freePostWithComments } from "./support/data";
import { deleteUsersByEmail, sql, sqlOne } from "./support/db";
import { E2E } from "./support/env";
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

/**
 * メールのリンクをテスト対象の Web のパスにする。リンクのオリジンは Supabase Auth の Site URL
 * （infra/supabase/config.toml の site_url = E2E_SITE_URL）で、E2E_BASE_URL（別ポート・別ホストで動かす場合）とは
 * 一致しないことがある。パス（相対 URL）にすれば Playwright の baseURL（E2E_BASE_URL）に対して開く。
 */
function sameAppPath(url: string): string {
  const parsed = new URL(url);
  expect(parsed.origin, "メールのリンクは Supabase の Site URL（E2E_SITE_URL）のオリジン").toBe(
    new URL(E2E.siteURL).origin,
  );
  return `${parsed.pathname}${parsed.search}`;
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

  // メールのリンクをタップした想定（同じブラウザで開く）→ 確認画面の「ログインする」。
  // リンクのオリジンは Supabase の Site URL（環境ごとの設定）なので、テスト対象の Web に対してパスを開く
  const confirmPath = sameAppPath(mail.confirmUrl);
  await confirmMagicLink(page, confirmPath);
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  await expect(page.getByRole("navigation", { name: "メインメニュー" })).toBeVisible();
  await expect(page.getByTestId("post-card").first()).toBeVisible();

  await tab(page, "プロフィール").click();
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByText(email).first()).toBeVisible();
  await expectProfileCreated(email);

  // 使用済みのリンクは再利用できない（ログアウト後に開くとエラー表示のログイン画面）
  await page.context().clearCookies();
  await confirmMagicLink(page, confirmPath);
  await expect(page).toHaveURL(/\/login\?error=link/);
  await expect(page.locator("#login-error")).toContainText("ログインリンクが無効");
});

test("A2: 共有されたリンク（/posts/<id>）→ ログイン → メールのリンクで元のページに戻る", async ({
  page,
}) => {
  const email = uniqueEmail(`${test.info().project.name}-deeplink`);
  created.push(email);
  const post = await freePostWithComments(0);
  const postPath = `/posts/${post.id}`;

  // 未ログインで共有リンクを開く → ログイン画面（next に元のページ）
  await page.goto(postPath);
  await expect(page).toHaveURL(`/login?next=${encodeURIComponent(postPath)}`);
  const mail = await requestLoginMail(page, email);

  // メールのリンクは redirect_to（emailRedirectTo = /auth/callback?next=<元のページ>）で遷移先を運ぶ
  const redirectTo = new URL(new URL(mail.confirmUrl).searchParams.get("redirect_to") ?? "");
  expect(redirectTo.pathname).toBe("/auth/callback");
  expect(redirectTo.searchParams.get("next")).toBe(postPath);

  await confirmMagicLink(page, sameAppPath(mail.confirmUrl));
  await expect(page).toHaveURL(postPath, { timeout: 30_000 });
  await expect(page.getByTestId("post-card")).toHaveAttribute("data-post-id", post.id);
  await expectProfileCreated(email);

  // 使用済みのリンクを開き直した場合も、ログインし直せば元のページへ戻れる（next を引き継ぐ）
  await page.context().clearCookies();
  await confirmMagicLink(page, sameAppPath(mail.confirmUrl));
  await expect(page).toHaveURL(`/login?error=link&next=${encodeURIComponent(postPath)}`);
});

test("A2: 共有されたリンク（/dm/<id>）→ ログイン → 6 桁コードで元のページに戻る", async ({
  page,
}) => {
  const email = uniqueEmail(`${test.info().project.name}-deeplink-code`);
  created.push(email);
  const character = await sqlOne<{ id: string; name: string }>(
    "select id, name from public.characters where is_active order by handle limit 1",
  );
  const dmPath = `/dm/${character.id}`;

  await page.goto(dmPath);
  await expect(page).toHaveURL(`/login?next=${encodeURIComponent(dmPath)}`);
  const mail = await requestLoginMail(page, email);
  await page.getByPlaceholder("確認コード").fill(mail.code);
  await expect(page).toHaveURL(dmPath, { timeout: 30_000 });
  await expect(page.getByRole("link", { name: `${character.name}のプロフィール` })).toBeVisible();
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

// ---------------------------------------------------------------------------
// マジックリンクの安全性（ログイン CSRF・メールスキャナーの先読み）
// ---------------------------------------------------------------------------

/** レスポンスが Supabase のセッション Cookie（sb-*-auth-token）を発行しているか */
function setsSessionCookie(headers: { name: string; value: string }[]): boolean {
  return headers.some(
    (header) =>
      header.name.toLowerCase() === "set-cookie" &&
      /(^|\s)sb-[^=]*auth-token[^=]*=[^;]/.test(header.value),
  );
}

test("マジックリンクは開いただけではログインせず、トークンも消費しない（スキャナーの先読み対策）", async ({
  page,
  request,
  makeUser,
}) => {
  const user = await makeUser();
  const path = await confirmPathFor(user.email);

  // メールスキャナー相当: Cookie なしで GET（リダイレクトも追う）
  const scanned = await request.get(path);
  expect(scanned.status()).toBe(200);
  expect(setsSessionCookie(scanned.headersArray()), "GET でセッションを発行しない").toBe(false);

  // 他サイトからのフォーム送信（ログイン CSRF）は拒否され、トークンも消費しない
  const token = new URL(path, "http://x").searchParams;
  const crossSite = await request.post("/auth/confirm/verify", {
    form: { token_hash: token.get("token_hash") ?? "", type: token.get("type") ?? "", next: "/" },
    headers: { Origin: "https://evil.example", "Sec-Fetch-Site": "cross-site" },
    maxRedirects: 0,
  });
  expect(crossSite.status()).toBe(303);
  expect(crossSite.headers()["location"]).toContain("/login?error=link");
  expect(setsSessionCookie(crossSite.headersArray())).toBe(false);

  // GET /auth/confirm/verify ではログインできない（POST 専用）
  const getVerify = await request.get(`/auth/confirm/verify?${token.toString()}`, {
    maxRedirects: 0,
  });
  expect(getVerify.status()).toBe(405);

  // 本人がブラウザで開いて「ログインする」を押せばログインできる（先読みでトークンが消えていない）
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "everkano にログインしますか？" })).toBeVisible();
  await expect(page.getByTestId("confirm-account-switch")).toHaveCount(0);
  await page.getByRole("button", { name: "ログインする" }).click();
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  await expect(tab(page, "ホーム")).toBeVisible();
});

test("別のアカウントでログイン中にマジックリンクを開くと、切り替わることを明示する", async ({
  page,
  makeUser,
  login,
}) => {
  const current = await makeUser("current");
  const other = await makeUser("other");
  await login(page, current);

  const path = await confirmPathFor(other.email, "/me");
  await page.goto(path);
  await expect(page.getByTestId("confirm-account-switch")).toContainText(current.email);
  // 「ログインせずにホームへ」ならアカウントは切り替わらない
  await page.getByRole("link", { name: "ログインせずにホームへ" }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.goto("/me");
  await expect(page.getByText(current.email).first()).toBeVisible();

  // 明示的に「ログインする」を押したときだけ切り替わる
  await confirmMagicLink(page, path);
  await expect(page).toHaveURL(/\/me$/, { timeout: 30_000 });
  await expect(page.getByText(other.email).first()).toBeVisible();
});

// ---------------------------------------------------------------------------
// 使えなくなったセッション（ユーザー削除・利用停止）: リダイレクトループにならない
// ---------------------------------------------------------------------------

/** 一定時間のメインフレームのナビゲーション（ドキュメント要求）を数える */
function countDocumentRequests(page: Page): { count: () => number } {
  let count = 0;
  const onRequest = (req: Request) => {
    if (req.isNavigationRequest() && req.frame() === page.mainFrame()) count += 1;
  };
  page.on("request", onRequest);
  return { count: () => count };
}

async function expectSessionCookieCleared(page: Page): Promise<void> {
  await expect
    .poll(
      async () =>
        (await page.context().cookies()).filter((c) => /^sb-.*auth-token/.test(c.name)).length,
    )
    .toBe(0);
}

test("開いている間にアカウントが削除されたら、1 回でログイン画面へ（/ ⇄ /login のループなし）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await expect(tab(page, "ホーム")).toBeVisible();

  await deleteAuthUser(user.id); // 運用手順のアカウント削除（アクセストークンはまだ期限内）
  const navigations = countDocumentRequests(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?error=session/, { timeout: 15_000 });
  await expect(page.locator("#login-error")).toContainText("ログインの有効期限が切れました");
  await page.waitForTimeout(3_000); // ループしていれば、この間にも遷移が続く
  expect(navigations.count(), "ナビゲーション回数（ループしていない）").toBeLessThanOrEqual(3);
  await expect(page).toHaveURL(/\/login\?error=session/);
  await expectSessionCookieCleared(page);
});

test("ログアウト: サーバーへの失効要求が失敗しても、端末のセッションが消えていればログイン画面へ（失敗と表示しない）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/me");
  await expect(page.getByRole("button", { name: "ログアウト" })).toBeVisible();

  // /auth/v1/logout だけ届かない（圏外になった瞬間・Auth の一時的な障害）。
  // supabase-js はこの場合もこの端末のセッション（Cookie）を消してからエラーを返す
  let logoutRequests = 0;
  await page.route("**/auth/v1/logout**", (route) => {
    logoutRequests += 1;
    return route.abort("internetdisconnected");
  });
  await page.getByRole("button", { name: "ログアウト" }).click();

  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  expect(logoutRequests, "失効要求は送っている").toBeGreaterThan(0);
  await expect(page.getByText("ログアウトできませんでした")).toHaveCount(0);
  await expectSessionCookieCleared(page);
  await page.goto("/me");
  await expect(page).toHaveURL(/\/login\?next=%2Fme$/);
});

test("利用停止（ban）されたアカウントは、理由を表示してログイン画面へ（ループなし）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await expect(tab(page, "ホーム")).toBeVisible();

  await banUser(user.id);
  const navigations = countDocumentRequests(page);
  await page.goto("/me");
  await expect(page).toHaveURL(/\/login\?error=banned/, { timeout: 15_000 });
  await expect(page.locator("#login-error")).toContainText("利用停止中");
  await page.waitForTimeout(3_000);
  expect(navigations.count()).toBeLessThanOrEqual(3);
  await expect(page).toHaveURL(/\/login\?error=banned/);
  await expectSessionCookieCleared(page);
});

test("退会の確認ダイアログは「キャンセル」に初期フォーカスがあり、Enter 1 回では退会しない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/me");
  await page.getByRole("button", { name: "退会する" }).click();
  const dialog = page.getByRole("alertdialog", { name: "退会しますか？" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "キャンセル" })).toBeFocused();

  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/me$/);
  const profile = await sqlOne<{ deleted_at: string | null }>(
    "select deleted_at from public.profiles where id = $1",
    [user.id],
  );
  expect(profile.deleted_at, "退会していない").toBeNull();
});

// ---------------------------------------------------------------------------
// iOS のホーム画面アプリ: コードを読みにメールアプリへ切り替えている間にアプリが再起動されても続けられる
// ---------------------------------------------------------------------------

const PENDING_LOGIN_KEY = "everkano:pending-login";

test("コード入力中にアプリが再起動しても、コード入力画面から続けてログインできる", async ({
  page,
}) => {
  const email = uniqueEmail(`${test.info().project.name}-relaunch`);
  created.push(email);

  await page.goto("/login");
  const mail = await requestLoginMail(page, email);

  // iOS がアプリを破棄 → start_url（/）から起動し直した想定
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "メールを確認してください" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
  // 再送信の待ち時間も送信時刻から引き継ぐ（すぐに送り直して送信間隔の制限に当たらない）
  await expect(page.getByRole("button", { name: /コードを再送信（\d+秒）/ })).toBeDisabled();

  // 保存状態が失われていても（別の保存領域など）、「確認コードをお持ちの場合」から入力できる
  await page.evaluate((key) => localStorage.removeItem(key), PENDING_LOGIN_KEY);
  await page.reload();
  await expect(page.getByRole("button", { name: "ログインリンクを送信" })).toBeVisible();
  await page.getByPlaceholder("メールアドレス").fill(email);
  await page.getByRole("button", { name: "確認コードをお持ちの場合" }).click();
  await expect(page.getByRole("heading", { name: "メールを確認してください" })).toBeVisible();

  await page.getByPlaceholder("確認コード").fill(mail.code);
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });
  await expectProfileCreated(email);
  // ログインしたら入力待ちの状態は消える（次にログイン画面を開いたとき古い画面を出さない）
  expect(await page.evaluate((key) => localStorage.getItem(key), PENDING_LOGIN_KEY)).toBeNull();
});

test("送信間隔の制限に当たっても、送信済みのコードを入力できる（「上限」で行き止まりにしない）", async ({
  page,
}) => {
  const email = uniqueEmail(`${test.info().project.name}-throttle`);
  // Supabase Auth の 429（同じアドレスへの送信間隔の制限）を再現する。メールは送られない
  await page.route("**/auth/v1/otp**", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        code: 429,
        error_code: "over_email_send_rate_limit",
        msg: "For security purposes, you can only request this after 42 seconds.",
      }),
    }),
  );
  await page.goto("/login");
  await page.getByPlaceholder("メールアドレス").fill(email);
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  await expect(page.getByRole("heading", { name: "メールを確認してください" })).toBeVisible();
  await expect(
    page.getByRole("status").filter({ hasText: "確認コードは送信済みです" }),
  ).toBeVisible();
  await expect(page.locator("#login-error")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /コードを再送信（(4[0-2]|3\d)秒）/ }),
  ).toBeDisabled();

  // プロジェクト全体の送信上限（このアドレスに送った記録も無い）は、従来どおりエラーを表示する
  await page.getByRole("button", { name: "メールアドレスを変更" }).click();
  await page.unroute("**/auth/v1/otp**");
  await page.route("**/auth/v1/otp**", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        code: 429,
        error_code: "over_email_send_rate_limit",
        msg: "Email rate limit exceeded",
      }),
    }),
  );
  await page.getByPlaceholder("メールアドレス").fill(uniqueEmail("throttle-global"));
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  await expect(page.locator("#login-error")).toContainText("送信回数の上限");
  await expect(page.getByRole("button", { name: "ログインリンクを送信" })).toBeVisible();
});

// ---------------------------------------------------------------------------
// 退会（§5.7）
// ---------------------------------------------------------------------------

test("退会すると全端末のセッションが無効になり、同じアカウントではログインできない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/me");
  await page.getByRole("button", { name: "退会する" }).click();
  const dialog = page.getByRole("alertdialog", { name: "退会しますか？" });
  await dialog.getByRole("button", { name: "退会する" }).click();

  await expect(page).toHaveURL(/\/login\?error=withdrawn/, { timeout: 15_000 });
  await expect(page.locator("#login-error")).toContainText("このアカウントは退会済みです");
  const profile = await sqlOne<{ deleted_at: string | null }>(
    "select deleted_at from public.profiles where id = $1",
    [user.id],
  );
  expect(profile.deleted_at, "profiles.deleted_at が記録される").not.toBeNull();
  await expectSessionCookieCleared(page);

  // 保護ページはログイン画面へ
  await page.goto("/dm");
  await expect(page).toHaveURL(/\/login\?next=%2Fdm/);

  // 同じメールアドレスのマジックリンクでログインしても、退会済みとしてログイン画面へ戻される
  await confirmMagicLink(page, await confirmPathFor(user.email));
  await expect(page).toHaveURL(/\/login\?error=withdrawn/, { timeout: 30_000 });
  await expect(page.locator("#login-error")).toContainText("このアカウントは退会済みです");
  await expectSessionCookieCleared(page);
});
