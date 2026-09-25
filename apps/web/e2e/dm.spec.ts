import { sql } from "./support/db";
import { MISAKI } from "./support/env";
import { messageLog, openConversation, sendAndWaitReply } from "./support/dm";
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
