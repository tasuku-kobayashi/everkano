import type { Database } from "@everkano/shared";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { accessTokenFor } from "./support/auth";
import { addMemory, apiCall, chat, createConversation, isApiError } from "./support/api";
import { sqlOne } from "./support/db";
import { E2E, MISAKI, anonKey } from "./support/env";
import { messageLog, openConversation } from "./support/dm";
import { expect, test } from "./support/fixtures";

/**
 * A13: RLS により、他ユーザーの会話・メモリが取得できない（2 アカウントで検証）
 * ユーザー A が美咲と会話し記憶を持つ状態で、ユーザー B のトークンから
 * - supabase-js（ブラウザと同じ経路 = PostgREST + RLS）で A の conversations / messages / memories を読めない・書けない
 * - Python API（postgres ロールで接続し、所有者チェックで守る）で A の会話・記憶に触れない（404）
 * - 画面でも B の DM に A のメッセージは出ない
 * ことを確認する。A 自身のトークンでは読める（ポジティブコントロール）。
 */

function clientFor(token: string | null): SupabaseClient<Database> {
  return createClient<Database>(E2E.supabaseURL, anonKey(), {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: token ? { headers: { Authorization: `Bearer ${token}` } } : {},
  });
}

test("A13: 他ユーザーの会話・メッセージ・記憶は RLS と API の所有者チェックで取得できない", async ({
  page,
  makeUser,
  login,
}) => {
  const userA = await makeUser("rls-a");
  const userB = await makeUser("rls-b");
  const tokenA = await accessTokenFor(userA);
  const tokenB = await accessTokenFor(userB);

  // ---- A: 会話・メッセージ・記憶を作る
  const secret = `Aの秘密のメッセージ-${Date.now()}`;
  const { conversation } = await createConversation(tokenA, MISAKI.id);
  const sent = await chat(tokenA, MISAKI.id, conversation.id, secret);
  const memory = await addMemory(tokenA, MISAKI.id, "Aは来月引っ越す予定");

  // ---- ポジティブコントロール: A 本人は読める
  const asA = clientFor(tokenA);
  const ownConversations = await asA.from("conversations").select("id").eq("id", conversation.id);
  expect(ownConversations.error).toBeNull();
  expect(ownConversations.data).toHaveLength(1);
  const ownMessages = await asA
    .from("messages")
    .select("id, body")
    .eq("conversation_id", conversation.id);
  expect(ownMessages.data?.map((m) => m.body)).toContain(secret);
  const ownMemories = await asA.from("memories").select("id").eq("id", memory.id);
  expect(ownMemories.data).toHaveLength(1);

  // ---- B（supabase-js + B のトークン）: 何も見えない
  const asB = clientFor(tokenB);
  const bConversations = await asB
    .from("conversations")
    .select("id, user_id")
    .eq("id", conversation.id);
  expect(bConversations.error).toBeNull();
  expect(bConversations.data, "B から A の会話は 0 件").toEqual([]);

  const bMessages = await asB
    .from("messages")
    .select("id, body")
    .eq("conversation_id", conversation.id);
  expect(bMessages.error).toBeNull();
  expect(bMessages.data, "B から A のメッセージは 0 件").toEqual([]);
  const bAllMessages = await asB.from("messages").select("id, body");
  expect(bAllMessages.data?.some((m) => m.body === secret)).toBe(false);

  const bMemories = await asB.from("memories").select("id, content").eq("user_id", userA.id);
  expect(bMemories.error).toBeNull();
  expect(bMemories.data, "B から A の記憶は 0 件").toEqual([]);
  const bMemoryById = await asB.from("memories").select("id").eq("id", memory.id);
  expect(bMemoryById.data).toEqual([]);

  const bProfile = await asB.from("profiles").select("id").eq("id", userA.id);
  expect(bProfile.data, "他人のプロフィールも読めない").toEqual([]);

  const bThreads = await asB.rpc("list_dm_threads");
  expect(bThreads.error).toBeNull();
  expect(bThreads.data ?? []).toEqual([]);

  // 書き込みもできない（メッセージの直接 INSERT は権限なし / 他人の会話の既読化は無効）
  const bInsert = await asB
    .from("messages")
    .insert({ conversation_id: conversation.id, sender_type: "user", body: "なりすまし" });
  expect(bInsert.error, "messages への直接 INSERT は拒否される").not.toBeNull();
  const bDelete = await asB.from("memories").delete().eq("id", memory.id).select("id");
  expect(bDelete.data ?? []).toEqual([]);
  const readAtBefore = await sqlOne<{ at: Date }>(
    "select user_last_read_at as at from public.conversations where id = $1",
    [conversation.id],
  );
  // エラーでも 0 件更新でもよいが、A の会話の既読時刻は変わらない
  await asB.rpc("mark_conversation_read", { p_conversation_id: conversation.id });
  const readAtAfter = await sqlOne<{ at: Date }>(
    "select user_last_read_at as at from public.conversations where id = $1",
    [conversation.id],
  );
  expect(readAtAfter.at.getTime()).toBe(readAtBefore.at.getTime());

  // 未ログイン（anon キーのみ）も読めない
  const anon = clientFor(null);
  const anonMessages = await anon
    .from("messages")
    .select("id")
    .eq("conversation_id", conversation.id);
  expect(anonMessages.data ?? []).toEqual([]);

  // ---- B（Python API + B のトークン）: A の会話・記憶は 404
  const chatAsB = await apiCall(tokenB, "POST", "/chat", {
    character_id: MISAKI.id,
    conversation_id: conversation.id,
    message: "Aの会話に書き込めるか",
  });
  expect(chatAsB.status).toBe(404);
  expect(isApiError(chatAsB.body) && chatAsB.body.error.code).toBe("not_found");

  const patchAsB = await apiCall(tokenB, "PATCH", `/memories/${memory.id}`, { content: "改ざん" });
  expect(patchAsB.status).toBe(404);
  const deleteAsB = await apiCall(tokenB, "DELETE", `/memories/${memory.id}`);
  expect(deleteAsB.status).toBe(404);
  const listAsB = await apiCall<{ memories: { id: string }[] }>(
    tokenB,
    "GET",
    `/memories?character_id=${MISAKI.id}`,
  );
  expect(listAsB.status).toBe(200);
  expect(
    isApiError(listAsB.body) ? [] : (listAsB.body?.memories ?? []).map((m) => m.id),
  ).not.toContain(memory.id);
  const noAuth = await apiCall(null, "GET", `/memories?character_id=${MISAKI.id}`);
  expect(noAuth.status).toBe(401);

  // A のデータは無傷
  const afterA = await asA.from("memories").select("content").eq("id", memory.id).single();
  expect(afterA.data?.content).toBe("Aは来月引っ越す予定");
  const afterMessages = await asA
    .from("messages")
    .select("body")
    .eq("conversation_id", conversation.id);
  expect(afterMessages.data?.map((m) => m.body)).not.toContain("なりすまし");
  expect(afterMessages.data?.map((m) => m.body)).not.toContain("Aの会話に書き込めるか");
  expect(sent.reply.length).toBeGreaterThan(0);

  // ---- 画面: B が同じキャラの DM を開いても、B 自身の新しい会話（挨拶のみ）で A のメッセージは出ない
  await login(page, userB);
  await openConversation(page, MISAKI);
  const log = messageLog(page, MISAKI.name);
  await expect(log.getByText(sent.reply, { exact: true })).toHaveCount(0);
  await expect(page.getByText(secret)).toHaveCount(0);
  await page.getByRole("button", { name: `${MISAKI.name}が覚えていること` }).click();
  const panel = page.getByRole("dialog", { name: `${MISAKI.name}が覚えていること` });
  await expect(panel).toBeVisible();
  await expect(panel.getByText("まだ覚えていることはありません")).toBeVisible();
  await expect(panel.getByText("Aは来月引っ越す予定")).toHaveCount(0);
});
