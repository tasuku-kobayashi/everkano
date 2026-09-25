import type { CreateCommentResponse } from "@everkano/shared";
import { freePostWithComments } from "./support/data";
import { sql } from "./support/db";
import { E2E } from "./support/env";
import { expect, test } from "./support/fixtures";
import { tabBar } from "./support/ui";

/**
 * A4: 個別投稿でコメント一覧と投稿欄が動作する（§5.3）
 * - コメント一覧は時系列昇順（返信はトップレベルの下にまとめて表示）
 * - 固定フッターの入力欄から投稿（Python API POST /comments）→ 一覧に追加される
 * - 投稿者のキャラが自動で返信し、Realtime で（再読み込みなしで）表示される
 */

interface CommentRow {
  id: string;
  parent_comment_id: string | null;
  created_at: Date;
}

/** DB のコメントを画面の表示順（トップレベルは作成順、各スレッドの返信はその下に作成順）に並べる */
function expectedDisplayOrder(rows: readonly CommentRow[]): string[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const rootOf = (row: CommentRow): string => {
    let current = row;
    const seen = new Set([current.id]);
    while (current.parent_comment_id) {
      const parent = byId.get(current.parent_comment_id);
      if (!parent || seen.has(parent.id)) break;
      seen.add(parent.id);
      current = parent;
    }
    return current.id;
  };
  const sorted = [...rows].sort(
    (a, b) => a.created_at.getTime() - b.created_at.getTime() || (a.id < b.id ? -1 : 1),
  );
  const order: string[] = [];
  for (const root of sorted.filter((row) => rootOf(row) === row.id)) {
    order.push(root.id);
    for (const reply of sorted) {
      if (reply.id !== root.id && rootOf(reply) === root.id) order.push(reply.id);
    }
  }
  return order;
}

test("A4: 投稿詳細でコメント一覧と投稿欄が動作し、キャラの返信が Realtime で届く", async ({
  page,
  makeUser,
  login,
}) => {
  // 2 プロジェクトが並列に書き込むため、プロジェクトごとに別の投稿を使う
  const post = await freePostWithComments(test.info().project.name === "android" ? 1 : 0);
  expect(post.comment_count).toBeGreaterThan(0);
  const user = await makeUser();
  await login(page, user, `/posts/${post.id}`);

  // ---- 表示: 投稿 + コメント一覧（時系列昇順）、タブバーは非表示（固定の入力欄がある）
  await expect(page.getByTestId("post-card")).toHaveAttribute("data-post-id", post.id);
  await expect(tabBar(page)).toHaveCount(0);
  const list = page.getByTestId("comment-list");
  await expect(list).toBeVisible();
  const rows = await sql<CommentRow>(
    "select id, parent_comment_id, created_at from public.comments where post_id = $1 and created_at <= now()",
    [post.id],
  );
  const comments = list.getByTestId("comment");
  await expect(comments).toHaveCount(rows.length);
  const shown = await comments.evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-comment-id") ?? ""),
  );
  expect(shown, "コメントは時系列昇順（返信はスレッドの下）").toEqual(expectedDisplayOrder(rows));

  // ---- 入力欄: 空のときは送信できない
  const input = page.getByTestId("comment-input");
  const submit = page.getByTestId("comment-submit");
  await expect(input).toBeVisible();
  await expect(submit).toBeDisabled();

  // これ以降、コメント一覧の再取得（REST）を遮断する。キャラの返信は Realtime でしか届かない状態にする
  const commentsRest = /\/rest\/v1\/comments\?/;
  await page.route(commentsRest, (route) =>
    route.request().method() === "GET" ? route.abort() : route.fallback(),
  );

  // ---- 投稿（Python API POST /comments）
  const body = `E2E のコメントです（${test.info().project.name}）`;
  await input.fill(body);
  const responsePromise = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/comments` && res.request().method() === "POST",
  );
  await submit.click();
  const res = await responsePromise;
  expect(res.status()).toBe(201);
  const created = (await res.json()) as CreateCommentResponse;
  expect(created.comment.body).toBe(body);
  expect(created.reply_scheduled, "COMMENT_AUTO_REPLY_PROBABILITY=1.0 なら返信が予約される").toBe(
    true,
  );

  const mine = page.locator(`[data-testid="comment"][data-comment-id="${created.comment.id}"]`);
  await expect(mine).toBeVisible();
  await expect(mine).toContainText(body);
  await expect(mine.getByRole("button", { name: "このコメントを削除" })).toBeVisible();
  await expect(input).toHaveValue("");
  const saved = await sql("select 1 from public.comments where id = $1 and author_user_id = $2", [
    created.comment.id,
    user.id,
  ]);
  expect(saved).toHaveLength(1);

  // ---- キャラの自動返信（parent_comment_id = 自分のコメント）が Realtime で表示される
  let replyId: string | undefined;
  await expect(async () => {
    const replies = await sql<{ id: string; author_character_id: string }>(
      "select id, author_character_id from public.comments where parent_comment_id = $1 and author_type = 'character'",
      [created.comment.id],
    );
    replyId = replies[0]?.id;
    expect(replies[0]?.author_character_id).toBe(post.character_id);
  }).toPass({ timeout: 30_000 });
  const reply = page.locator(`[data-testid="comment"][data-comment-id="${replyId}"]`);
  await expect(reply).toBeVisible({ timeout: 15_000 });
  await expect(reply).toHaveAttribute("data-author-type", "character");
  await expect(reply).toContainText("作成者");
  await expect(page.getByTestId("reply-typing")).toHaveCount(0);
  // 返信は自分のコメントの直後（同じスレッド）に並ぶ
  const order = await list
    .getByTestId("comment")
    .evaluateAll((els) => els.map((el) => el.getAttribute("data-comment-id")));
  expect(order.indexOf(replyId ?? "")).toBe(order.indexOf(created.comment.id) + 1);

  // ---- 自分のコメントを削除できる（返信もまとめて消える）
  await page.unroute(commentsRest);
  await mine.getByRole("button", { name: "このコメントを削除" }).click();
  const confirm = page.getByRole("alertdialog", { name: "コメントを削除しますか？" });
  await confirm.getByRole("button", { name: "削除" }).click();
  await expect(mine).toHaveCount(0);
  await expect(reply).toHaveCount(0);
  const remaining = await sql("select 1 from public.comments where id = any($1::uuid[])", [
    [created.comment.id, replyId],
  ]);
  expect(remaining).toHaveLength(0);
});

test("A4: モデレーション（Gate #1）で拒否されたコメントは保存されず、理由が表示される", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(2);
  const user = await makeUser();
  await login(page, user, `/posts/${post.id}`);
  const input = page.getByTestId("comment-input");
  await input.fill("中学生です");
  const responsePromise = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/comments` && res.request().method() === "POST",
  );
  await page.getByTestId("comment-submit").click();
  const res = await responsePromise;
  expect(res.status()).toBe(422);
  expect(((await res.json()) as { error: { code: string } }).error.code).toBe("moderation_blocked");
  await expect(page.locator('[aria-live="polite"] [role="alert"]')).toBeVisible();
  const saved = await sql("select 1 from public.comments where author_user_id = $1", [user.id]);
  expect(saved).toHaveLength(0);
});
