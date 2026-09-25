import type { ChatResponse } from "@everkano/shared";
import { sql } from "./support/db";
import { HINATA } from "./support/env";
import { openConversation, sendAndWaitReply } from "./support/dm";
import { expect, test } from "./support/fixtures";

/**
 * A12: audit_logs にチャットのリクエスト／レスポンスが記録されている（H6。検証方法「SQL確認」）
 * 画面から DM を 2 往復送り、audit_logs を SQL で確認する（docs/handover/06-operations.md の確認 SQL と同じ条件）。
 */

interface AuditRow {
  event_type: string;
  character_id: string | null;
  payload: Record<string, unknown>;
  created_at: Date;
}

test("A12: DM の送信ごとに chat.request / chat.response が audit_logs に記録される", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, HINATA);

  const lines = ["はじめまして！", "今日は仕事で疲れたよ"];
  const replies: ChatResponse[] = [];
  for (const line of lines) replies.push((await sendAndWaitReply(page, HINATA, line)).response);

  const rows = await sql<AuditRow>(
    `select event_type, character_id, payload, created_at
       from public.audit_logs
      where user_id = $1 and event_type in ('chat.request', 'chat.response', 'conversation.create')
      order by created_at, id`,
    [user.id],
  );
  await test.info().attach("audit_logs.json", {
    body: JSON.stringify(rows, null, 2),
    contentType: "application/json",
  });

  const requests = rows.filter((row) => row.event_type === "chat.request");
  const responses = rows.filter((row) => row.event_type === "chat.response");
  expect(rows.filter((row) => row.event_type === "conversation.create")).toHaveLength(1);
  expect(requests).toHaveLength(lines.length);
  expect(responses).toHaveLength(lines.length);

  lines.forEach((line, i) => {
    const request = requests[i];
    const response = responses[i];
    const chat = replies[i];
    expect(request?.character_id).toBe(HINATA.id);
    expect(request?.payload.message).toBe(line);
    expect(
      request?.payload.request_id,
      "request_id でリクエストとレスポンスを対にできる",
    ).toBeTruthy();
    expect(response?.payload.request_id).toBe(request?.payload.request_id);

    expect(response?.character_id).toBe(HINATA.id);
    expect(response?.payload.reply).toBe(chat?.reply);
    expect(response?.payload.message_id).toBe(chat?.character_message.id);
    expect(response?.payload.user_message_id).toBe(chat?.user_message.id);
    expect(response?.payload.conversation_id).toBe(chat?.user_message.conversation_id);
    expect(response?.payload.model).toBeTruthy();
    expect(typeof response?.payload.latency_ms).toBe("number");
    expect(Array.isArray(response?.payload.memories_used)).toBe(true);
    expect(response?.payload.moderated).toBe(false);
  });
});
