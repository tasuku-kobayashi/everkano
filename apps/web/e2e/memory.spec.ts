import type { Page } from "@playwright/test";
import type { MemoryDTO } from "@everkano/shared";
import { addMemory, createConversation } from "./support/api";
import { accessTokenFor } from "./support/auth";
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

  // 1 往復目: 覚えてほしい事実。
  // エンジン v1.0: 記憶の抽出は返答の後に非同期で行う（post_turn ジョブ。done の memories_created は常に空）。
  // 作られた記憶は Realtime（memories の INSERT）で届き、「覚えました」が出る。
  // API は ENGINE_POST_TURN_DELAY_SECONDS を小さくして起動しておくこと（既定 20 秒でも待てる長さにしてある）
  const first = await sendAndWaitReply(page, MISAKI, "来週、大阪に出張するんだ");
  expect(first.response.memories_created, "記憶の抽出は返答の後（非同期）").toEqual([]);
  let memories: { id: string; content: string }[] = [];
  await expect
    .poll(
      async () => {
        memories = await sql<{ id: string; content: string }>(
          "select id, content from public.memories where user_id = $1 and character_id = $2 and status = 'active'",
          [user.id, MISAKI.id],
        );
        return memories.some((m) => m.content.includes("大阪") && m.content.includes("出張"));
      },
      { message: "重要な発言から記憶が作られる", timeout: 60_000, intervals: [1_000] },
    )
    .toBe(true);
  await expect(page.getByText(`${MISAKI.name}があなたのことを覚えました`)).toBeVisible({
    timeout: 15_000,
  });
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

  // メモリパネルにも表示されている（記憶の一覧。同じ発言から「約束・予定」の一覧にも入る: M6）
  const panel = await openMemoryPanel(page);
  await expect(panel.getByRole("list", { name: `${MISAKI.name}が覚えていること` })).toContainText(
    "大阪に出張",
  );
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
  // 種類は既定の「事実」（エンジン v1.0 M2）
  await expect(item.getByTestId("memory-kind")).toHaveText("事実");
  // 自分で追加した記憶は「あなたが追加」（編集していないので「編集済み」ではない）
  await expect(item.getByText("あなたが追加", { exact: true })).toBeVisible();
  await expect(item.getByText("編集済み", { exact: true })).toHaveCount(0);
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
  // E5: 削除した記憶を会話から自動で覚え直さないことを説明する
  await expect(confirm).toContainText("自動で覚え直すこともありません");
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

const PLACEHOLDER = "例: 10月2日（金）に大事なプレゼンがある";

test("記憶の追加に失敗しても「保存中…」の記憶が残らず、入力した内容が戻る（パネルを閉じた後の失敗も）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, MISAKI);

  // 記憶の API だけ止める（一覧も読み込めない）
  await page.route(`${E2E.apiURL}/memories**`, (route) => route.abort("connectionrefused"));
  let panel = await openMemoryPanel(page);
  // 記憶の一覧の読み込みエラー（role=alert）の「再読み込み」（約束・自発メッセージの設定の行と区別する）
  await expect(panel.getByRole("alert").getByRole("button", { name: "再読み込み" })).toBeVisible({
    timeout: 15_000,
  });

  const TEXT = "大事なことを覚えてほしい（API停止中）";
  await panel.getByRole("button", { name: "覚えてほしいことを追加" }).click();
  await panel.getByPlaceholder(PLACEHOLDER).fill(TEXT);
  const form = panel.locator("form").first();
  await form.getByRole("radio", { name: "優先度: 高" }).click();
  await form.getByRole("button", { name: "追加", exact: true }).click();
  await expect(toast(page, /通信できませんでした/)).toBeVisible();
  await expect(
    panel.getByText("保存中…"),
    "保存されていない記憶を「保存中…」のまま残さない",
  ).toHaveCount(0);
  await expect(panel.getByRole("listitem").filter({ hasText: TEXT })).toHaveCount(0);
  await expect(panel.getByPlaceholder(PLACEHOLDER), "入力した内容がフォームに戻る").toHaveValue(
    TEXT,
  );
  await expect(
    panel.locator("form").first().getByRole("radio", { name: "優先度: 高" }),
  ).toBeChecked();

  // パネルを閉じた後に失敗した場合: 次に開いたときにフォームへ戻る
  await page.unroute(`${E2E.apiURL}/memories**`);
  await page.route(`${E2E.apiURL}/memories**`, async (route) => {
    if (route.request().method() === "POST") {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
      return route.abort("connectionrefused");
    }
    return route.continue();
  });
  const LATER = "閉じた後に失敗した記憶";
  await panel.getByPlaceholder(PLACEHOLDER).fill(LATER);
  const failed = page.waitForEvent(
    "requestfailed",
    (request) => request.method() === "POST" && request.url() === `${E2E.apiURL}/memories`,
  );
  await panel.locator("form").first().getByRole("button", { name: "追加", exact: true }).click();
  await closeMemoryPanelByBackdrop(page);
  await failed;
  panel = await openMemoryPanel(page);
  await expect(panel.getByPlaceholder(PLACEHOLDER)).toHaveValue(LATER);

  // 失敗が届く前にパネルを開き直していても、表示中の空のフォームに戻る
  const AGAIN = "開き直した後に失敗した記憶";
  await panel.getByPlaceholder(PLACEHOLDER).fill(AGAIN);
  const failedAgain = page.waitForEvent(
    "requestfailed",
    (request) => request.method() === "POST" && request.url() === `${E2E.apiURL}/memories`,
  );
  await panel.locator("form").first().getByRole("button", { name: "追加", exact: true }).click();
  await closeMemoryPanelByBackdrop(page);
  panel = await openMemoryPanel(page);
  await failedAgain;
  await expect(panel.getByPlaceholder(PLACEHOLDER)).toHaveValue(AGAIN);
  const saved = await sql("select 1 from public.memories where user_id = $1", [user.id]);
  expect(saved).toHaveLength(0);
});

test("記憶の編集に失敗したら、入力した内容のまま編集欄が残り、そのまま保存し直せる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const token = await accessTokenFor(user);
  await createConversation(token, MISAKI.id);
  const memory = await addMemory(token, MISAKI.id, "週末は実家に帰る");
  await login(page, user);
  await openConversation(page, MISAKI);
  const panel = await openMemoryPanel(page);
  const item = panel.getByRole("listitem").filter({ hasText: "週末は実家に帰る" });
  await expect(item.getByText("あなたが追加", { exact: true })).toBeVisible();

  await page.route(`${E2E.apiURL}/memories/*`, (route) =>
    route.request().method() === "PATCH" ? route.abort("connectionrefused") : route.continue(),
  );
  await item.getByRole("button", { name: "編集" }).click();
  const EDITED = "来週末は実家に帰る";
  await panel.getByLabel("記憶の内容").fill(EDITED);
  await panel.getByRole("button", { name: "保存", exact: true }).click();
  await expect(toast(page, /通信できませんでした/)).toBeVisible();
  await expect(panel.getByLabel("記憶の内容"), "編集欄が閉じずに入力した内容が残る").toHaveValue(
    EDITED,
  );

  await page.unroute(`${E2E.apiURL}/memories/*`);
  await panel.getByRole("button", { name: "保存", exact: true }).click();
  await expect(panel.getByLabel("記憶の内容")).toHaveCount(0);
  await expect(panel.getByRole("listitem").filter({ hasText: EDITED })).toBeVisible();
  const [row] = await sql<{ content: string }>(
    "select content from public.memories where id = $1",
    [memory.id],
  );
  expect(row?.content).toBe(EDITED);
});
