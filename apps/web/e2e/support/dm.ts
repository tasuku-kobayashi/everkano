import type { ChatResponse } from "@everkano/shared";
import { expect, type Page, type Response } from "@playwright/test";
import { E2E } from "./env";

/** DM 会話画面の操作ヘルパー */

/** DM の送信 API（エンジン v1.0 から Web は POST /chat/stream で送る） */
export const CHAT_STREAM_URL = `${E2E.apiURL}/chat/stream`;

/** POST /chat/stream の応答か */
export function isChatStreamResponse(res: Response): boolean {
  return res.url() === CHAT_STREAM_URL && res.request().method() === "POST";
}

/** SSE の本文（event: / data:）をイベントの配列にする（テスト用の簡易パーサー） */
export function parseSse(text: string): Array<{ event: string; data: unknown }> {
  const events: Array<{ event: string; data: unknown }> = [];
  for (const block of text.replace(/\r\n?/g, "\n").split("\n\n")) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length > 0) events.push({ event, data: JSON.parse(data.join("\n")) as unknown });
  }
  return events;
}

/** SSE の本文を組み立てる（page.route で /chat/stream の応答を差し替えるテスト用） */
export function sseBody(events: ReadonlyArray<{ event: string; data: unknown }>): string {
  return events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

/**
 * POST /chat/stream の応答（SSE の本文）から done の ChatResponse を取り出す。
 * error で終わった・done が無い場合は内容つきで失敗させる。
 */
export function chatStreamResult(captured: CapturedChatStream): ChatResponse {
  expect(captured.status, `POST /chat/stream: ${captured.status} ${captured.body}`).toBe(200);
  const events = parseSse(captured.body);
  const done = events.find((e) => e.event === "done");
  if (!done) throw new Error(`/chat/stream に done がありません: ${JSON.stringify(events)}`);
  return done.data as ChatResponse;
}

export interface CapturedChatStream {
  status: number;
  body: string;
}

export interface ChatStreamCapture {
  /** 次の POST /chat/stream の応答（送信の前に呼び、送信後に await する） */
  next(): Promise<CapturedChatStream>;
}

const captures = new WeakMap<Page, ChatStreamCapture>();

/**
 * POST /chat/stream の応答の本文をテストから読めるようにする（ページごとに 1 回）。
 *
 * Chromium は fetch のストリームで読まれた応答の本文を保持しない（Response.text() が
 * 「No data found for resource」になる）ため、テスト側で API に送って全文を受け取り、それをそのままページに返す。
 * ページには本文が一度に届く（途中の delta を順に表示する様子は、応答を差し替えるテストと単体テストで確認する）。
 * このハンドラより後に登録した page.route（遅延させる等）は、最後に route.fallback() を呼ぶこと。
 */
export async function captureChatStreams(page: Page): Promise<ChatStreamCapture> {
  const existing = captures.get(page);
  if (existing) return existing;
  const queue: CapturedChatStream[] = [];
  const waiters: Array<(value: CapturedChatStream) => void> = [];
  await page.route(CHAT_STREAM_URL, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const response = await route.fetch({ timeout: 60_000 });
    const body = await response.text();
    const captured = { status: response.status(), body };
    const waiter = waiters.shift();
    if (waiter) waiter(captured);
    else queue.push(captured);
    await route.fulfill({ response, body });
  });
  const capture: ChatStreamCapture = {
    next: () => {
      const ready = queue.shift();
      return ready ? Promise.resolve(ready) : new Promise((resolve) => waiters.push(resolve));
    },
  };
  captures.set(page, capture);
  return capture;
}

export function messageLog(page: Page, characterName: string) {
  return page.getByRole("log", { name: `${characterName}とのメッセージ` });
}

export function typingIndicator(page: Page, characterName: string) {
  return page.getByRole("status", { name: `${characterName}が入力中` });
}

/**
 * DM 一覧の「おすすめ」の行（「〇〇にメッセージを送る（AIキャラクター）」）。
 * 同じ名前のキャラ（API のテストが一時的に作るキャラなど）と区別するため、リンク先でも絞り込む。
 */
export function suggestionLink(page: Page, character: { id: string; name: string }) {
  return page
    .getByRole("link", {
      name: `${character.name}にメッセージを送る（AIキャラクター）`,
      exact: true,
    })
    .and(page.locator(`[href="/dm/${character.id}"]`));
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
 * 返答は POST /chat/stream の done（ChatResponse）から取り、同じ文面の吹き出しが会話ログに出ることを確認する。
 */
export async function sendAndWaitReply(
  page: Page,
  character: { name: string },
  text: string,
): Promise<RoundTrip> {
  const box = composer(page);
  await box.fill(text);
  const capture = await captureChatStreams(page);
  const responsePromise = capture.next();
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

  const response = chatStreamResult(await responsePromise);

  await expect(typing).toBeHidden({ timeout: 20_000 });
  await expect(log.getByText(response.reply, { exact: true }).last()).toBeVisible();
  // 入力欄は送信後に空になっている
  await expect(box).toHaveValue("");
  return { response, sawTyping };
}
