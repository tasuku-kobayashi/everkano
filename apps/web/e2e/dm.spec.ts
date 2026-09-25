import type { ChatResponse } from "@everkano/shared";
import type { Page } from "@playwright/test";
import { chat, createConversation } from "./support/api";
import { accessTokenFor } from "./support/auth";
import { sql } from "./support/db";
import { E2E, MISAKI } from "./support/env";
import {
  composer,
  messageLog,
  openConversation,
  sendAndWaitReply,
  typingIndicator,
} from "./support/dm";
import { expect, test } from "./support/fixtures";
import { tabBar } from "./support/ui";

/**
 * A8: DM で送信するとキャラが文脈に合った返答を返す（§5.6。仕様の検証方法「手動で 10 往復」）
 * - 10 往復すべてで「入力中…」が表示され、キャラの返答が表示される
 * - 挨拶には挨拶で返す（モック LLM でも直前の発言に沿った返答になる）
 * - 会話は保存され、再読み込みしても全件表示される（ユーザー右 / キャラ左の吹き出し）
 *
 * 本番の LLM（LLM_MODE=live）の返答品質はこのテストの対象外（staging で確認する）。
 */

const LINES = [
  "こんばんは！今日もおつかれさま",
  "今日は仕事がすごく忙しかったよ",
  "お昼は駅前のパスタ屋さんに行ったんだ",
  "週末は映画を見に行く予定なんだ",
  "最近ちょっと寝不足でさ",
  "猫を飼いたいなって思ってるんだ",
  "美咲さんは休みの日なにしてるの？",
  "ぼくは料理が好きなんだ",
  "話してたら元気出た、ありがとう",
  "そろそろ寝るね、おやすみ",
] as const;

test("A8: DM で 10 往復して、毎回「入力中…」のあとにキャラの返答が届く", async ({
  page,
  makeUser,
  login,
}) => {
  test.setTimeout(180_000);
  const user = await makeUser();
  await login(page, user, "/dm");

  // DM 一覧（会話なし）→ おすすめからキャラを選ぶ
  await expect(page.getByText("メッセージはまだありません")).toBeVisible();
  await page.getByRole("link", { name: `${MISAKI.name}にメッセージを送る` }).click();
  await expect(page).toHaveURL(new RegExp(`/dm/${MISAKI.id}$`));
  const log = messageLog(page, MISAKI.name);
  await expect(log).toBeVisible({ timeout: 20_000 });
  await expect(tabBar(page), "DM 会話ではタブバーを隠して入力欄を固定").toHaveCount(0);

  const replies: string[] = [];
  for (const [index, line] of LINES.entries()) {
    const { response, sawTyping } = await sendAndWaitReply(page, MISAKI, line);
    expect(sawTyping, `${index + 1} 往復目: 入力中インジケーター`).toBe(true);
    expect(response.reply.trim().length, `${index + 1} 往復目: 返答がある`).toBeGreaterThan(0);
    expect(response.moderated).toBe(false);
    expect(response.user_message.body).toBe(line);
    expect(response.character_message.body).toBe(response.reply);
    replies.push(response.reply);
  }

  await test.info().attach("conversation.txt", {
    body: LINES.map((line, i) => `> ${line}\n< ${replies[i]}`).join("\n"),
    contentType: "text/plain",
  });

  // 文脈: 挨拶には挨拶、おやすみにはおやすみで返す
  expect(replies[0]).toContain("こんばんは");
  expect(replies[9]).toContain("おやすみ");
  // 同じ定型文の繰り返しではない
  expect(new Set(replies).size).toBeGreaterThanOrEqual(8);

  // 保存: 挨拶 1 + 10 往復 = 21 件、ユーザーとキャラが交互
  const rows = await sql<{ sender_type: string; body: string }>(
    `select m.sender_type, m.body from public.messages m
       join public.conversations c on c.id = m.conversation_id
      where c.user_id = $1 and c.character_id = $2 order by m.created_at, m.id`,
    [user.id, MISAKI.id],
  );
  expect(rows).toHaveLength(21);
  expect(rows[0]?.sender_type).toBe("character");
  rows.slice(1).forEach((row, i) => {
    expect(row.sender_type).toBe(i % 2 === 0 ? "user" : "character");
    expect(row.body).toBe(i % 2 === 0 ? LINES[i / 2] : replies[(i - 1) / 2]);
  });

  // 吹き出しの配置: 自分は右、キャラは左
  const viewportWidth = page.viewportSize()?.width ?? 390;
  const own = await log.getByText(LINES[9], { exact: true }).boundingBox();
  const theirs = await log.getByText(replies[9] ?? "", { exact: true }).boundingBox();
  expect(own && own.x + own.width).toBeGreaterThan(viewportWidth * 0.8);
  expect(theirs?.x ?? viewportWidth).toBeLessThan(viewportWidth * 0.3);

  // 再読み込みしても会話が残っている
  await openConversation(page, MISAKI);
  await expect(log.getByText(replies[9] ?? "", { exact: true })).toBeVisible();
  await expect(log.getByText(LINES[9], { exact: true })).toBeVisible();

  // DM 一覧: 最新メッセージのプレビュー
  await page.goto("/dm");
  await expect(page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、`) })).toBeVisible();
});

/** messages の取得を「ページの取得（新しい順）」と「差分の取得（created_at 以降）」に分けて記録する */
function watchMessageFetches(page: Page) {
  const pages: string[] = [];
  const deltas: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (!url.includes("/rest/v1/messages")) return;
    (url.includes("created_at=gte.") ? deltas : pages).push(url);
  });
  return { pages, deltas };
}

test("既存の会話は API を経由せずに開き、API が止まっていても履歴を読める（取りこぼしの回収は差分だけ）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const token = await accessTokenFor(user);
  const { conversation } = await createConversation(token, MISAKI.id);
  const LINE = "昨日の続きを話そう";
  const sent = await chat(token, MISAKI.id, conversation.id, LINE);
  await login(page, user);

  // Python API を止める（再起動中・障害を想定）
  const apiRequests: string[] = [];
  await page.route(`${E2E.apiURL}/**`, (route) => {
    apiRequests.push(`${route.request().method()} ${route.request().url()}`);
    return route.abort("connectionrefused");
  });
  const fetches = watchMessageFetches(page);

  // ---- URL を直接開く: 会話と最新ページを 1 回の読み取りで取得する
  await openConversation(page, MISAKI);
  const log = messageLog(page, MISAKI.name);
  await expect(log.getByText(LINE, { exact: true })).toBeVisible();
  await expect(log.getByText(sent.reply, { exact: true })).toBeVisible();
  expect(apiRequests, "既存の会話を開くのに POST /conversations を呼ばない").toEqual([]);
  // 購読開始時の取りこぼし回収は差分（小さな応答）で、1 ページ目を取り直さない
  await expect.poll(() => fetches.deltas.length, { timeout: 15_000 }).toBeGreaterThan(0);
  expect(fetches.pages, "messages のページ取得は無い（会話と一緒に読んだ）").toEqual([]);

  // ---- 画面復帰（visibilitychange）でも読み込み済みのページは取り直さない
  const deltasBefore = fetches.deltas.length;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => fetches.deltas.length).toBeGreaterThan(deltasBefore);
  expect(fetches.pages).toEqual([]);

  // ---- ヘッダーは不透明（スクロールした吹き出しが名前の下に透けない）
  const background = await page
    .locator("header")
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(background, "ヘッダーの背景に透明度が無い").not.toMatch(
    /\/\s*0?\.\d+\)|rgba\([^)]*,\s*0?\.\d+\)/,
  );

  // ---- DM 一覧の行から開く: 一覧にある会話はそのまま開き、ページの取得は 1 回だけ
  await page.goto("/dm");
  const row = page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、`) });
  await expect(row).toBeVisible();
  const pagesBefore = fetches.pages.length;
  await row.click();
  await expect(page).toHaveURL(new RegExp(`/dm/${MISAKI.id}$`));
  await expect(log.getByText(sent.reply, { exact: true })).toBeVisible();
  await expect.poll(() => fetches.deltas.length).toBeGreaterThan(deltasBefore + 1);
  expect(fetches.pages.length - pagesBefore, "1 ページ目は 1 回だけ取得する").toBe(1);
  expect(apiRequests).toEqual([]);
});

test("返答待ちのまま画面を離れて戻っても、送った発言と「入力中…」が残り、二重に送れない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/dm");
  await page.getByRole("link", { name: `${MISAKI.name}にメッセージを送る` }).click();
  const log = messageLog(page, MISAKI.name);
  await expect(log).toBeVisible({ timeout: 20_000 });

  // 本番の LLM のように返答に時間がかかる状態にする
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route(`${E2E.apiURL}/chat`, async (route) => {
    await gate;
    await route.continue();
  });
  const LINE = "大事な相談があるんだけど";
  await composer(page).fill(LINE);
  const responsePromise = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/chat` && res.request().method() === "POST",
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: "送信", exact: true }).click();
  await expect(log.getByText(LINE, { exact: true })).toBeVisible();
  await expect(typingIndicator(page, MISAKI.name)).toBeVisible();

  // 戻る → 一覧からもう一度開く（同じタブ内の遷移）
  await page.getByRole("button", { name: "戻る" }).click();
  await expect(page).toHaveURL(/\/dm$/);
  await page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、`) }).click();
  await expect(page).toHaveURL(new RegExp(`/dm/${MISAKI.id}$`));
  await expect(log.getByText(LINE, { exact: true }), "送った発言が消えない").toBeVisible();
  await expect(typingIndicator(page, MISAKI.name), "返答待ちの「入力中…」").toBeVisible();
  await composer(page).fill("あれ、送れてない？");
  await expect(
    page.getByRole("button", { name: "送信", exact: true }),
    "返答待ちの間は送れない",
  ).toBeDisabled();

  release();
  const response = (await (await responsePromise).json()) as ChatResponse;
  await expect(log.getByText(response.reply, { exact: true })).toBeVisible();
  await expect(typingIndicator(page, MISAKI.name)).toBeHidden();
  await expect(page.getByRole("button", { name: "送信", exact: true })).toBeEnabled();
  await expect(log.getByText(LINE, { exact: true })).toHaveCount(1);
  const rows = await sql(
    `select 1 from public.messages m join public.conversations c on c.id = m.conversation_id
      where c.user_id = $1 and m.sender_type = 'user'`,
    [user.id],
  );
  expect(rows, "送信は 1 回だけ").toHaveLength(1);
});

test("会話ログは吹き出しごとに送り手（あなた / キャラ名）を読み上げる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, MISAKI);
  const { response } = await sendAndWaitReply(page, MISAKI, "こんにちは");
  const snapshot = await messageLog(page, MISAKI.name).ariaSnapshot();
  expect(snapshot).toContain("あなた: こんにちは");
  expect(snapshot).toContain(`${MISAKI.name}: ${response.reply}`);
});

test("DM 一覧の Realtime 購読は自分の会話だけに絞る（全ユーザーの発言を購読しない）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const { conversation } = await createConversation(await accessTokenFor(user), MISAKI.id);
  const joins: string[] = [];
  page.on("websocket", (ws) =>
    ws.on("framesent", ({ payload }) => {
      const text = typeof payload === "string" ? payload : payload.toString("utf8");
      if (text.includes("phx_join") && text.includes("dm-threads")) joins.push(text);
    }),
  );
  await login(page, user, "/dm");
  await expect(page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、`) })).toBeVisible();
  await expect.poll(() => joins.length, { timeout: 15_000 }).toBeGreaterThan(0);
  for (const join of joins) {
    expect(join).toContain(`conversation_id=in.(${conversation.id})`);
  }
});
