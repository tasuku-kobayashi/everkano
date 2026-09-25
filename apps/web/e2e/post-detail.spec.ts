import { randomUUID } from "node:crypto";
import type { CreateCommentResponse } from "@everkano/shared";
import type { Locator, Page } from "@playwright/test";
import { freePostWithComments } from "./support/data";
import { sql, sqlOne } from "./support/db";
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

// ---------------------------------------------------------------------------
// コメント欄の細部（返信のメンション・送信中の入力・「返信を書いています…」・取得の並行化・ハッシュタグ）
// ---------------------------------------------------------------------------

/** 2 プロジェクトが並列に書き込むため、プロジェクトごとに別の投稿を使う */
function projectOffset(iphone: number, android: number): number {
  return test.info().project.name === "android" ? android : iphone;
}

function commentById(page: Page, id: string): Locator {
  return page.locator(`[data-testid="comment"][data-comment-id="${id}"]`);
}

/** 入力欄の内容をそのまま投稿し、API のレスポンスを返す */
async function submitComment(page: Page): Promise<CreateCommentResponse> {
  const responsePromise = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/comments` && res.request().method() === "POST",
  );
  await page.getByTestId("comment-submit").click();
  const res = await responsePromise;
  expect(res.status()).toBe(201);
  return (await res.json()) as CreateCommentResponse;
}

/** スクロール（smooth）が止まるまで待つ */
async function waitForScrollIdle(page: Page): Promise<void> {
  let last = Number.NaN;
  await expect
    .poll(
      async () => {
        const y = await page.evaluate(() => window.scrollY);
        const idle = y === last;
        last = y;
        return idle;
      },
      { intervals: [300], timeout: 10_000 },
    )
    .toBe(true);
}

test("A4: 返信の @メンションは公開名だけ（自分のコメントへの返信に表示名・メールアドレスを入れない）", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(projectOffset(4, 5));
  const user = await makeUser();
  // 表示名の既定値はメールアドレスの @ より前（profiles.display_name。30 文字に切り詰める）
  const localPart = user.email.split("@")[0] ?? "";
  expect(localPart.length).toBeGreaterThan(0);
  const { display_name: displayName } = await sqlOne<{ display_name: string }>(
    "select display_name from public.profiles where id = $1",
    [user.id],
  );
  expect(localPart.startsWith(displayName)).toBe(true);
  await login(page, user, `/posts/${post.id}`);
  const input = page.getByTestId("comment-input");
  await expect(page.getByTestId("comment-list")).toBeVisible();

  await input.fill("最初のコメント");
  const first = await submitComment(page);
  const mine = commentById(page, first.comment.id);
  await expect(mine).toBeVisible();

  // 自分のコメントに「返信する」: 返信中の表示は出るが、本文にメンションは入れない
  await mine.getByRole("button", { name: "返信する" }).click();
  await expect(page.getByText(/さんに返信中$/)).toBeVisible();
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("");
  await input.fill("追記です");
  const followUp = await submitComment(page);
  expect(followUp.comment.parent_comment_id).toBe(first.comment.id);
  expect(followUp.comment.body).toBe("追記です");
  await expect(commentById(page, followUp.comment.id)).toBeVisible();

  // キャラのコメントに「返信する」: handle のメンションが入る（本文として公開されてよい名前）
  const characterComment = page
    .getByTestId("comment-list")
    .locator('[data-testid="comment"][data-author-type="character"]')
    .first();
  const characterCommentId = (await characterComment.getAttribute("data-comment-id")) ?? "";
  const { handle } = await sqlOne<{ handle: string }>(
    `select c.handle from public.comments cm join public.characters c on c.id = cm.author_character_id
      where cm.id = $1`,
    [characterCommentId],
  );
  await characterComment.getByRole("button", { name: "返信する" }).click();
  await expect(input).toHaveValue(`@${handle} `);

  // 返信先を自分のコメントに切り替えると、前の返信先のメンションは外れる
  await mine.getByRole("button", { name: "返信する" }).click();
  await expect(input).toHaveValue("");

  // もう一度キャラに返信して投稿: メンションはリンク色で表示される
  await characterComment.getByRole("button", { name: "返信する" }).click();
  await input.press("End");
  await input.pressSequentially("ありがとう");
  const toCharacter = await submitComment(page);
  expect(toCharacter.comment.body).toBe(`@${handle} ありがとう`);
  expect(toCharacter.comment.parent_comment_id).toBe(characterCommentId);
  const mention = commentById(page, toCharacter.comment.id).locator('[data-rich-text="mention"]');
  await expect(mention).toHaveText(`@${handle}`);

  // 保存された本文（他ユーザーにも見える）に、自分の表示名・メールアドレスが含まれない
  const bodies = await sql<{ body: string }>(
    "select body from public.comments where author_user_id = $1",
    [user.id],
  );
  expect(bodies).toHaveLength(3);
  for (const { body } of bodies) {
    expect(body).not.toContain(displayName);
    expect(body).not.toContain(user.email);
  }
});

test("A4: 投稿中に入力欄へ書いた次のコメントは、投稿の完了で消えない", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(projectOffset(6, 7));
  const user = await makeUser();
  await login(page, user, `/posts/${post.id}`);
  const input = page.getByTestId("comment-input");
  await expect(page.getByTestId("comment-list")).toBeVisible();

  // 通信が遅い状態（POST /comments を 1.5 秒遅らせる）
  await page.route(`${E2E.apiURL}/comments`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    return route.fallback();
  });

  await input.fill("1 件目のコメント");
  const responsePromise = submitComment(page);
  // 送信中に次のコメントを書き始める
  await expect(page.getByTestId("comment-submit")).toBeDisabled();
  await input.fill("次のコメントの下書き");
  const created = await responsePromise;
  await expect(commentById(page, created.comment.id)).toContainText("1 件目のコメント");
  await expect(input).toHaveValue("次のコメントの下書き");
});

test("A4: キャラの返信を待つ「返信を書いています…」が入力欄の裏に隠れない", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(8);
  const user = await makeUser();
  await login(page, user, `/posts/${post.id}`);
  await expect(page.getByTestId("comment-list")).toBeVisible();

  // 返信は予約されたがまだ届かない状態を作る（本番の LLM は返信に数秒かかる。モックは即座に返すため、
  // API の応答を差し替えて DB には保存しない = Realtime の返信も来ない）
  const body = "返信を待っています";
  const fake: CreateCommentResponse = {
    comment: {
      id: randomUUID(),
      post_id: post.id,
      parent_comment_id: null,
      author_type: "user",
      author_user_id: user.id,
      author_character_id: null,
      body,
      created_at: new Date().toISOString(),
    },
    reply_scheduled: true,
  };
  await page.route(`${E2E.apiURL}/comments`, (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 201,
          contentType: "application/json",
          headers: { "access-control-allow-origin": new URL(E2E.baseURL).origin },
          body: JSON.stringify(fake),
        })
      : route.fallback(),
  );

  await page.getByTestId("comment-input").fill(body);
  await page.getByTestId("comment-submit").click();
  const typing = page.getByTestId("reply-typing");
  await expect(typing).toBeVisible();
  await expect(typing).toContainText(`${post.name}さんが返信を書いています`);
  await waitForScrollIdle(page);

  const layout = await page.evaluate((commentId) => {
    const row = document.querySelector('[data-testid="reply-typing"]')?.getBoundingClientRect();
    const comment = document
      .querySelector(`[data-testid="comment"][data-comment-id="${commentId}"]`)
      ?.getBoundingClientRect();
    const composer = document
      .querySelector('[data-testid="comment-input"]')
      ?.closest(".fixed")
      ?.getBoundingClientRect();
    return {
      rowBottom: row?.bottom ?? Number.NaN,
      commentTop: comment?.top ?? Number.NaN,
      composerTop: composer?.top ?? Number.NaN,
    };
  }, fake.comment.id);
  expect(layout.rowBottom, "「返信を書いています…」が入力欄より上に見えている").toBeLessThanOrEqual(
    layout.composerTop,
  );
  expect(layout.commentTop, "投稿したコメントも見えている").toBeGreaterThanOrEqual(0);
});

test("A4: 投稿詳細を直接開くと、コメントの取得は投稿本体の取得を待たずに始まる", async ({
  page,
  makeUser,
  login,
}) => {
  const post = await freePostWithComments(3);
  const user = await makeUser();

  // 投稿本体の取得を 1 秒遅らせ、その間にコメントの取得が始まっていることを確かめる
  let postReleasedAt = 0;
  let commentsRequestedAt = 0;
  await page.route(/\/rest\/v1\/posts\?/, async (route) => {
    if (!route.request().url().includes(`id=eq.${post.id}`)) return route.fallback();
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    postReleasedAt = Date.now();
    return route.fallback();
  });
  page.on("request", (request) => {
    const url = request.url();
    if (!commentsRequestedAt && /\/rest\/v1\/comments\?/.test(url) && url.includes(post.id)) {
      commentsRequestedAt = Date.now();
    }
  });

  await login(page, user, `/posts/${post.id}`);
  await expect(page.getByTestId("comment-list")).toBeVisible();
  expect(commentsRequestedAt).toBeGreaterThan(0);
  expect(postReleasedAt).toBeGreaterThan(0);
  expect(commentsRequestedAt, "コメントの取得が投稿本体の応答より先に始まる").toBeLessThan(
    postReleasedAt,
  );
});

test("A4: キャプションのハッシュタグはリンク色で表示される", async ({ page, makeUser, login }) => {
  const { id } = await sqlOne<{ id: string }>(
    `select p.id from public.posts p join public.characters c on c.id = p.character_id
      where not p.is_paid and c.is_active and p.published_at <= now() and p.caption ~ '(^|\\s)#\\S'
      order by p.published_at, p.id limit 1`,
  );
  const user = await makeUser();
  await login(page, user, `/posts/${id}`);
  const hashtag = page.getByTestId("post-card").locator('[data-rich-text="hashtag"]').first();
  await expect(hashtag).toBeVisible();
  await expect(hashtag).toHaveText(/^[#＃]\S+$/);
  const colors = await hashtag.evaluate((el) => {
    const probe = document.createElement("span");
    probe.style.color = "var(--ig-link)";
    document.body.append(probe);
    const link = getComputedStyle(probe).color;
    probe.remove();
    return { hashtag: getComputedStyle(el).color, link };
  });
  expect(colors.hashtag).toBe(colors.link);
});
