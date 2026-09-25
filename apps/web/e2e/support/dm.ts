import type { ChatResponse } from "@everkano/shared";
import { expect, type Page } from "@playwright/test";
import { E2E } from "./env";

/** DM 会話画面の操作ヘルパー */

export function messageLog(page: Page, characterName: string) {
  return page.getByRole("log", { name: `${characterName}とのメッセージ` });
}

export function typingIndicator(page: Page, characterName: string) {
  return page.getByRole("status", { name: `${characterName}が入力中` });
}

export function composer(page: Page) {
  return page.getByLabel("メッセージ", { exact: true });
}

/** /dm/[characterId] を開き、履歴（初回はキャラの挨拶）が表示されるまで待つ */
export async function openConversation(
  page: Page,
  character: { id: string; name: string },
): Promise<void> {
  await page.goto(`/dm/${character.id}`);
  await expect(messageLog(page, character.name)).toBeVisible({ timeout: 20_000 });
}

export interface RoundTrip {
  response: ChatResponse;
  /** 返答を待つ間に「入力中…」が表示されたか */
  sawTyping: boolean;
}

/**
 * メッセージを入力して送信し、キャラの返答が画面に表示されるまで待つ。
 * 返答は POST /chat のレスポンスから取り、同じ文面の吹き出しが会話ログに出ることを確認する。
 */
export async function sendAndWaitReply(
  page: Page,
  character: { name: string },
  text: string,
): Promise<RoundTrip> {
  const box = composer(page);
  await box.fill(text);
  const responsePromise = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/chat` && res.request().method() === "POST",
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: "送信", exact: true }).click();

  // 自分の発言はすぐ（楽観的に）表示される
  const log = messageLog(page, character.name);
  await expect(log.getByText(text, { exact: true }).last()).toBeVisible();

  const typing = typingIndicator(page, character.name);
  let sawTyping = true;
  try {
    await expect(typing).toBeVisible({ timeout: 10_000 });
  } catch {
    sawTyping = false;
  }

  const res = await responsePromise;
  expect(res.status(), `POST /chat for 「${text}」`).toBe(200);
  const response = (await res.json()) as ChatResponse;

  await expect(typing).toBeHidden({ timeout: 20_000 });
  await expect(log.getByText(response.reply, { exact: true }).last()).toBeVisible();
  // 入力欄は送信後に空になっている
  await expect(box).toHaveValue("");
  return { response, sawTyping };
}
