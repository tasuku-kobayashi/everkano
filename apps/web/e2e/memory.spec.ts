import type { Page } from "@playwright/test";
import type { MemoryDTO } from "@everkano/shared";
import { sql } from "./support/db";
import { E2E, MISAKI } from "./support/env";
import { openConversation, sendAndWaitReply } from "./support/dm";
import { expect, test } from "./support/fixtures";
import { toast } from "./support/ui";

/**
 * A9: 同じキャラとの会話で、以前話した内容を踏まえた返答が返る（メモリ。§9）
 * A10: メモリパネルで記憶の追加・削除ができ、削除後は返答に反映されない（§5.6 / §9.4）
 *
 * モック LLM（LLM_MODE=mock）は、検索された長期記憶のうち発言と話題が重なるものに
 * 「そういえば、〜って言ってたよね」と触れる。短期の会話履歴だけでは触れないため、
 * 記憶に触れた = 長期記憶（pgvector 検索）が効いている、と判定できる。
 */

test.describe.configure({ timeout: 180_000 });

const FILLERS = [
  "今日はいい天気だったね",
  "お昼はパスタを食べたよ",
  "駅前に新しいカフェができてた",
  "最近ドラマを見始めたんだ",
  "雨の日は眠くなるよね",
  "コンビニの新作スイーツがおいしかった",
  "週末は部屋の片付けをする予定",
  "電車がちょっと遅れてた",
  "夜は涼しくなってきたね",
] as const;

function memoryPanel(page: Page) {
  return page.getByRole("dialog", { name: `${MISAKI.name}が覚えていること` });
}

/** シートの外（上部の暗い背景）をタップして閉じる */
async function closeMemoryPanelByBackdrop(page: Page) {
  // 確認ダイアログの閉じるアニメーション中は、その背景が上に重なっている
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  const width = page.viewportSize()?.width ?? 390;
  await page.mouse.click(width / 2, 40);
  await expect(memoryPanel(page)).toBeHidden();
}

async function openMemoryPanel(page: Page) {
  await page.getByRole("button", { name: `${MISAKI.name}が覚えていること` }).click();
  const panel = memoryPanel(page);
  await expect(panel).toBeVisible();
  return panel;
}

test("A9: 1 往復目に話したことを、10 往復後に話題にすると覚えている", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, MISAKI);

  // 1 往復目: 覚えてほしい事実
  const first = await sendAndWaitReply(page, MISAKI, "来週、大阪に出張するんだ");
  expect(first.response.memories_created.length, "重要な発言から記憶が作られる").toBeGreaterThan(0);
  await expect(page.getByText(`${MISAKI.name}があなたのことを覚えました`)).toBeVisible();
  const memories = await sql<{ id: string; content: string }>(
    "select id, content from public.memories where user_id = $1 and character_id = $2",
    [user.id, MISAKI.id],
  );
  const osaka = memories.find((m) => m.content.includes("大阪") && m.content.includes("出張"));
  expect(osaka, `抽出された記憶: ${JSON.stringify(memories)}`).toBeTruthy();

  // 2〜10 往復目: 別の話題
  for (const line of FILLERS) {
    const { response } = await sendAndWaitReply(page, MISAKI, line);
    expect(response.reply).not.toContain("出張");
  }

  // 11 往復目: 話題を振る → 出張の話に触れる
  const recall = await sendAndWaitReply(page, MISAKI, "大阪でおすすめの場所ある？");
  await test.info().attach("recall.txt", {
    body: `memories: ${JSON.stringify(memories)}\n> 大阪でおすすめの場所ある？\n< ${recall.response.reply}`,
    contentType: "text/plain",
  });
  expect(recall.response.memories_used).toContain(osaka?.id);
  expect(recall.response.reply, "以前話した「大阪に出張する」ことを踏まえた返答").toContain("出張");

  // メモリパネルにも表示されている
  const panel = await openMemoryPanel(page);
  await expect(panel.getByRole("list")).toContainText("大阪に出張");
});

test("A10: メモリパネルで記憶を追加・削除でき、削除した記憶は返答に反映されない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, MISAKI);
  const FACT = "ぼくの誕生日は3月3日";
  const QUESTION = "ぼくの誕生日、覚えてる？";

  // ---- 追加（優先度: 高 / 二人だけの秘密）
  let panel = await openMemoryPanel(page);
  await panel.getByRole("button", { name: "覚えてほしいことを追加" }).click();
  await panel.getByPlaceholder("例: 10月2日（金）に大事なプレゼンがある").fill(FACT);
  const form = panel.locator("form").first();
  await form.getByRole("radio", { name: "優先度: 高" }).click();
  await form.getByRole("button", { name: "二人だけの秘密" }).click();
  const created = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/memories` && res.request().method() === "POST",
  );
  await form.getByRole("button", { name: "追加", exact: true }).click();
  const createdRes = await created;
  expect(createdRes.status()).toBe(201);
  const memory = (await createdRes.json()) as MemoryDTO;
  await expect(toast(page, `${MISAKI.name}が覚えました`)).toBeVisible();
  const item = panel.getByRole("listitem").filter({ hasText: FACT });
  await expect(item).toBeVisible();
  await expect(item.getByRole("button", { name: "二人だけの秘密" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const [row] = await sql<{ is_user_edited: boolean; tags: string[]; importance: string }>(
    "select is_user_edited, tags, importance::text from public.memories where id = $1 and user_id = $2",
    [memory.id, user.id],
  );
  expect(row?.is_user_edited).toBe(true);
  expect(row?.tags).toContain("secret");
  await closeMemoryPanelByBackdrop(page);

  // ---- 追加した記憶が返答に使われる
  const withMemory = await sendAndWaitReply(page, MISAKI, QUESTION);
  expect(withMemory.response.memories_used).toContain(memory.id);
  expect(withMemory.response.reply, "追加した記憶を踏まえた返答").toContain("3月3日");

  // ---- 削除
  panel = await openMemoryPanel(page);
  const target = panel.getByRole("listitem").filter({ hasText: FACT });
  await target.getByRole("button", { name: "この記憶を削除" }).click();
  const confirm = page.getByRole("alertdialog", { name: "この記憶を削除しますか？" });
  await expect(confirm).toContainText(`削除すると${MISAKI.name}はこのことを忘れます`);
  const deleted = page.waitForResponse(
    (res) =>
      res.url() === `${E2E.apiURL}/memories/${memory.id}` && res.request().method() === "DELETE",
  );
  await confirm.getByRole("button", { name: "削除" }).click();
  expect((await deleted).status()).toBe(204);
  await expect(toast(page, "記憶を削除しました")).toBeVisible();
  await expect(panel.getByRole("listitem").filter({ hasText: FACT })).toHaveCount(0);
  const remaining = await sql("select 1 from public.memories where id = $1", [memory.id]);
  expect(remaining).toHaveLength(0);
  await closeMemoryPanelByBackdrop(page);

  // ---- 削除後は同じ話題を振っても返答に反映されない
  const afterDelete = await sendAndWaitReply(page, MISAKI, QUESTION);
  await test.info().attach("a10.txt", {
    body: `> ${QUESTION}（記憶あり）\n< ${withMemory.response.reply}\n> ${QUESTION}（削除後）\n< ${afterDelete.response.reply}`,
    contentType: "text/plain",
  });
  expect(afterDelete.response.memories_used).not.toContain(memory.id);
  expect(afterDelete.response.reply, "削除した記憶に触れない").not.toContain("3月3日");

  // 再度開いたパネルにも出てこない（再取得）
  await page.reload();
  panel = await openMemoryPanel(page);
  await expect(panel.getByText(FACT)).toHaveCount(0);
});
