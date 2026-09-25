import { basename } from "node:path";
import {
  test as base,
  expect,
  type BrowserContext,
  type BrowserContextOptions,
  type Page,
  type TestInfo,
} from "@playwright/test";
import { confirmMagicLink, confirmPathFor, createUser, type TestUser } from "./auth";
import { closeDb, deleteUsersById } from "./db";
import { deviceFor } from "./devices";
import { stubExternalImages } from "./images";

/**
 * 共通フィクスチャ
 * - context: 外部画像のスタブ + コンソールエラーの収集（失敗時の調査用にテスト結果へ添付）
 * - makeUser: テストごとに新しいユーザーを作り、テスト終了時に削除する
 * - login: 指定ユーザーでログインした状態にする（アプリの /auth/confirm の確認画面 →「ログインする」経由。
 *   メールは送らない）
 * - newDeviceContext: 同じ端末設定で別のブラウザコンテキスト（ダークモード・2 人目のユーザー）を作る
 *
 * フィクスチャの第 2 引数は Playwright の慣例では `use` だが、eslint の react-hooks/rules-of-hooks が
 * React の use() と誤認するため `provide` と命名している。
 */

interface Fixtures {
  makeUser: (label?: string) => Promise<TestUser>;
  login: (page: Page, user: TestUser, next?: string) => Promise<void>;
  newDeviceContext: (overrides?: BrowserContextOptions) => Promise<BrowserContext>;
}

interface WorkerFixtures {
  workerDb: void;
}

function watchConsole(context: BrowserContext, sink: string[]): void {
  const attach = (page: Page) => {
    page.on("console", (message) => {
      if (message.type() === "error") sink.push(`[console] ${page.url()} ${message.text()}`);
    });
    page.on("pageerror", (error) => sink.push(`[pageerror] ${page.url()} ${error.message}`));
  };
  context.pages().forEach(attach);
  context.on("page", attach);
}

async function attachConsole(testInfo: TestInfo, errors: readonly string[]): Promise<void> {
  if (errors.length === 0) return;
  await testInfo.attach("browser-console-errors", {
    body: errors.join("\n"),
    contentType: "text/plain",
  });
}

export const test = base.extend<Fixtures, WorkerFixtures>({
  // ワーカー終了時に DB プールを閉じる
  workerDb: [
    async ({}, provide) => {
      await provide();
      await closeDb();
    },
    { scope: "worker", auto: true },
  ],

  context: async ({ context }, provide, testInfo) => {
    const errors: string[] = [];
    await stubExternalImages(context);
    watchConsole(context, errors);
    await provide(context);
    await attachConsole(testInfo, errors);
  },

  makeUser: async ({}, provide, testInfo) => {
    const created: TestUser[] = [];
    await provide(async (label = basename(testInfo.file, ".spec.ts")) => {
      const user = await createUser(`${testInfo.project.name}-${label}`);
      created.push(user);
      return user;
    });
    if (process.env.E2E_KEEP_USERS !== "1") {
      await deleteUsersById(created.map((user) => user.id));
    }
  },

  login: async ({}, provide) => {
    await provide(async (page, user, next = "/") => {
      const path = await confirmPathFor(user.email, next);
      await confirmMagicLink(page, path);
      await page.waitForURL((url) => url.pathname === next.split("?")[0], { timeout: 30_000 });
    });
  },

  newDeviceContext: async ({ browser, baseURL }, provide, testInfo) => {
    const contexts: { context: BrowserContext; errors: string[] }[] = [];
    await provide(async (overrides = {}) => {
      const context = await browser.newContext({
        ...deviceFor(testInfo.project.name),
        baseURL,
        serviceWorkers: "block",
        ...overrides,
      });
      const errors: string[] = [];
      await stubExternalImages(context);
      watchConsole(context, errors);
      contexts.push({ context, errors });
      return context;
    });
    for (const { context, errors } of contexts) {
      await attachConsole(testInfo, errors);
      await context.close();
    }
  },
});

export { expect };
