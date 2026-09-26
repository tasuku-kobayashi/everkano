import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { CreateCommentResponse } from "@everkano/shared";
import type { Locator, Page } from "@playwright/test";
import { accessTokenFor, uniqueEmail, waitForLoginMail } from "./support/auth";
import { addMemory, apiCall, chat, createConversation, isApiError } from "./support/api";
import { freePostWithComments, paidPost } from "./support/data";
import { deleteUsersByEmail, sql } from "./support/db";
import { HINATA, MISAKI } from "./support/env";
import {
  composer,
  messageLog,
  openConversation,
  sendAndWaitReply,
  typingIndicator,
} from "./support/dm";
import { expect, test } from "./support/fixtures";
import { lockModal, toast } from "./support/ui";

/**
 * 受け入れ確認用のスクリーンショット（A1 の実機撮影の代わりの参考資料。iPhone 相当 390×844 @3x）。
 * 全画面をライト / ダークの両方で撮る。キャラクターエンジン v1.0 の画面（「AIキャラクター」バッジはフィード・
 * プロフィール・DM の各画面に写る）: 受信中の返答（23）・相談窓口のカード（24。返答を受け取っていない端末で
 * 履歴から表示）・メモリパネルの種類・約束（17）と以前の記憶（25）・自発メッセージの設定（26）。
 * API は e2e/README.md のとおり ENGINE_SCHEDULER_ENABLED=false・ENGINE_POST_TURN_DELAY_SECONDS=1 で起動しておく。
 *
 * 保存先: E2E_SCREENSHOTS_DIR（apps/web からの相対パス可）。未指定なら test-results/screenshots。
 * docs/acceptance/screenshots を更新するには:
 *   E2E_SCREENSHOTS_DIR=../../docs/acceptance/screenshots pnpm e2e --project=iphone e2e/screenshots.spec.ts
 */

const OUT_DIR = resolve(process.env.E2E_SCREENSHOTS_DIR ?? "test-results/screenshots");

type Scheme = "light" | "dark";

async function settle(page: Page): Promise<void> {
  // 画面内に見えている画像の読み込み完了を待つ（画面外の loading="lazy" は読み込まれないので対象外）
  await page
    .waitForFunction(
      () =>
        Array.from(document.images)
          .filter((img) => {
            const rect = img.getBoundingClientRect();
            return rect.bottom > 0 && rect.top < innerHeight && rect.width > 0;
          })
          .every((img) => img.complete),
      undefined,
      { timeout: 5_000 },
    )
    .catch(() => undefined);
  await page.waitForTimeout(450); // 画像のフェードイン・シートのアニメーション
}

/** 要素の先頭を、固定ヘッダーのすぐ下に来るようにスクロールする */
async function scrollBelowHeader(locator: Locator): Promise<void> {
  await locator.first().evaluate((el) => {
    el.scrollIntoView({ block: "start" });
    const header = document.querySelector("header");
    window.scrollBy(0, -((header?.getBoundingClientRect().height ?? 0) + 12));
  });
}

async function shot(page: Page, index: number, name: string, scheme: Scheme): Promise<void> {
  await settle(page);
  const file = `${String(index).padStart(2, "0")}-${name}-${scheme}.png`;
  await page.screenshot({ path: resolve(OUT_DIR, file), animations: "disabled", caret: "hide" });
}

test("スクリーンショット（全画面 × ライト / ダーク）", async ({
  page,
  makeUser,
  login,
  newDeviceContext,
}) => {
  test.skip(test.info().project.name !== "iphone", "iPhone 相当（390×844 @3x）のみで撮影する");
  test.setTimeout(300_000);
  mkdirSync(OUT_DIR, { recursive: true });

  // ------------------------------------------------------------------ 表示用のデータを用意
  const user = await makeUser("screens");
  await sql("update public.profiles set display_name = $1 where id = $2", ["ゆうと", user.id]);
  const token = await accessTokenFor(user);
  const post = await freePostWithComments(4);
  const paid = await paidPost(0);

  await login(page, user);
  await openConversation(page, MISAKI);
  await sendAndWaitReply(page, MISAKI, "こんばんは！今日もおつかれさま");
  await sendAndWaitReply(page, MISAKI, "来週、大阪に出張するんだ");
  await sendAndWaitReply(page, MISAKI, "ぼくは料理が好きなんだ");
  await addMemory(token, MISAKI.id, "ぼくの誕生日は3月3日");
  // 会話から自動で覚える記憶（返答の後の post_turn ジョブ）: 事実 → 転職で置き換わる（以前の記憶）・約束
  const misakiConversation = (await createConversation(token, MISAKI.id)).conversation.id;
  await chat(token, MISAKI.id, misakiConversation, "広告代理店で働いてるんだ");
  await chat(token, MISAKI.id, misakiConversation, "来週の木曜に面接なんだ。ちょっと緊張する");
  const engineCounts = () =>
    sql<{ job: number; interview: number; superseded: number }>(
      `select (select count(*) from public.memories
                where user_id = $1 and kind = 'fact' and content like '%広告代理店%')::int as job,
              (select count(*) from public.promises where user_id = $1 and content like '%面接%')::int as interview,
              (select count(*) from public.memories where user_id = $1 and status = 'superseded')::int as superseded`,
      [user.id],
    ).then((rows) => rows[0]!);
  // 仕事の記憶ができてから転職を話す（同じ返答の後の処理にまとめられると、置き換えではなく 2 件の追加になる）
  await expect(async () => {
    const counts = await engineCounts();
    expect(counts.job).toBeGreaterThan(0);
    expect(counts.interview).toBeGreaterThan(0);
  }).toPass({ timeout: 60_000 });
  await chat(token, MISAKI.id, misakiConversation, "転職して、今は銀行で働いてるよ");
  await expect(async () => {
    expect((await engineCounts()).superseded).toBeGreaterThan(0);
  }).toPass({ timeout: 60_000 });
  // E6: 安全対応をした返答（相談窓口のカード）。撮影する端末とは別の端末（API）で送っておき、履歴から表示する
  const hinataConversation = (await createConversation(token, HINATA.id)).conversation.id;
  const crisis = await chat(
    token,
    HINATA.id,
    hinataConversation,
    "最近ずっとしんどくて、もう消えたいって思っちゃう",
  );
  expect(crisis.safety?.triggered).toBe(true);
  expect(crisis.character_message.safety_triggered).toBe(true);
  const comment = await apiCall<CreateCommentResponse>(token, "POST", "/comments", {
    post_id: post.id,
    body: "素敵な写真！どこで撮ったの？",
  });
  expect(comment.status).toBe(201);
  const commentId = isApiError(comment.body) ? "" : (comment.body?.comment.id ?? "");
  // キャラの返信（バックグラウンドで生成）を待つ
  await expect(async () => {
    const rows = await sql(
      "select 1 from public.comments where parent_comment_id = $1 and author_type = 'character'",
      [commentId],
    );
    expect(rows.length).toBeGreaterThan(0);
  }).toPass({ timeout: 30_000 });

  const storageState = await page.context().storageState();
  const loginEmails: string[] = [];

  try {
    for (const scheme of ["light", "dark"] as const) {
      // ---------------------------------------------------------------- ログイン画面（未ログイン）
      const anonContext = await newDeviceContext({ colorScheme: scheme });
      const anon = await anonContext.newPage();
      await anon.goto("/login");
      await expect(anon.getByPlaceholder("メールアドレス")).toBeVisible();
      await shot(anon, 1, "login", scheme);
      const email = uniqueEmail(`screens-login-${scheme}`);
      loginEmails.push(email);
      await anon.getByPlaceholder("メールアドレス").fill(email);
      const since = Date.now();
      await anon.getByRole("button", { name: "ログインリンクを送信" }).click();
      await expect(anon.getByRole("heading", { name: "メールを確認してください" })).toBeVisible();
      await waitForLoginMail(email, since);
      await shot(anon, 2, "login-code", scheme);
      await anonContext.close();

      // ---------------------------------------------------------------- ログイン後
      const context = await newDeviceContext({ colorScheme: scheme, storageState });
      const p = await context.newPage();

      await p.goto("/");
      await expect(p.getByTestId("post-card").first()).toBeVisible();
      await shot(p, 3, "home", scheme);

      const paidCard = p.locator('[data-testid="post-card"][data-paid="true"]').first();
      await expect(async () => {
        if ((await paidCard.count()) === 0) await p.mouse.wheel(0, 3000);
        await expect(paidCard).toBeAttached({ timeout: 500 });
      }).toPass({ timeout: 30_000 });
      // 有料投稿のカードをヘッダー（44px）の直下に合わせる
      await paidCard.evaluate((el) =>
        window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 44),
      );
      await shot(p, 4, "home-paid-post", scheme);
      await paidCard.getByTestId("post-media").click();
      await expect(lockModal(p)).toBeVisible();
      await shot(p, 5, "paid-lock-modal", scheme);
      await lockModal(p).getByRole("button", { name: "購入する（準備中）" }).click();
      await expect(toast(p, "課金機能は現在準備中です")).toBeVisible();
      await shot(p, 6, "paid-lock-toast", scheme);
      await lockModal(p).getByRole("button", { name: "閉じる" }).click();

      await p.goto(`/posts/${post.id}`);
      await expect(p.getByTestId("comment-list")).toBeVisible();
      await shot(p, 7, "post-detail", scheme);
      await p.getByTestId("comment-list").getByTestId("comment").last().scrollIntoViewIfNeeded();
      await shot(p, 8, "post-comments", scheme);

      await p.goto(`/posts/${paid.id}`);
      await expect(p.getByText("有料コンテンツ")).toBeVisible();
      await shot(p, 9, "post-detail-paid", scheme);

      await p.goto(`/c/${MISAKI.handle}`);
      await expect(p.getByTestId("grid-free-tile").first()).toBeVisible();
      await shot(p, 10, "profile-free", scheme);
      await p.getByTestId("profile-tab-paid").click();
      await expect(p.getByTestId("grid-paid-tile").first()).toBeVisible();
      await shot(p, 11, "profile-paid", scheme);

      await p.goto("/search");
      await expect(p.getByTestId("explore-grid")).toBeVisible();
      await shot(p, 12, "search", scheme);
      await p.getByTestId("search-input").fill("ひなた");
      await expect(p.getByTestId("search-result").first()).toBeVisible();
      await p.getByTestId("search-input").blur();
      await shot(p, 13, "search-results", scheme);

      await p.goto("/dm");
      await expect(p.getByRole("link", { name: new RegExp(`^${MISAKI.name}、`) })).toBeVisible();
      await shot(p, 14, "dm-inbox", scheme);

      await openConversation(p, MISAKI);
      await expect(messageLog(p, MISAKI.name).getByText("ぼくは料理が好きなんだ")).toBeVisible();
      await shot(p, 15, "dm-conversation", scheme);

      // 入力中インジケーター
      await composer(p).fill(
        scheme === "light" ? "週末は映画を見に行く予定なんだ" : "おやすみ前にちょっとだけ話そう",
      );
      await p.getByRole("button", { name: "送信", exact: true }).click();
      await expect(typingIndicator(p, MISAKI.name)).toBeVisible();
      await p.screenshot({ path: resolve(OUT_DIR, `16-dm-typing-${scheme}.png`), caret: "hide" });
      await expect(typingIndicator(p, MISAKI.name)).toBeHidden({ timeout: 20_000 });

      // メモリパネル
      await p.getByRole("button", { name: `${MISAKI.name}が覚えていること` }).click();
      const panel = p.getByRole("dialog", { name: `${MISAKI.name}が覚えていること` });
      // 記憶の一覧（約束・予定の一覧と区別する）
      const memoryList = panel.getByRole("list", { name: `${MISAKI.name}が覚えていること` });
      await expect(memoryList.getByRole("listitem").first()).toBeVisible();
      await expect(panel.getByTestId("proactive-character-switch")).toBeVisible();
      await shot(p, 17, "memory-panel", scheme);
      await panel.getByRole("button", { name: "覚えてほしいことを追加" }).click();
      await panel
        .getByPlaceholder("例: 10月2日（金）に大事なプレゼンがある")
        .fill("週末は実家に帰る予定");
      await shot(p, 18, "memory-add", scheme);
      await panel.getByRole("button", { name: "キャンセル" }).click();
      await memoryList
        .getByRole("listitem")
        .first()
        .getByRole("button", { name: "この記憶を削除" })
        .click();
      await expect(p.getByRole("alertdialog", { name: "この記憶を削除しますか？" })).toBeVisible();
      await shot(p, 19, "memory-delete-confirm", scheme);
      await p.getByRole("alertdialog").getByRole("button", { name: "キャンセル" }).click();
      // 以前の記憶（転職で置き換わった記憶の履歴）と種類の絞り込み
      await panel.getByTestId("superseded-toggle").click();
      await expect(panel.getByRole("list", { name: "以前の記憶" })).toBeVisible();
      await panel
        .getByRole("group", { name: "記憶の種類で絞り込む" })
        .getByRole("button", { name: "事実" })
        .click();
      await panel.getByRole("heading", { name: "以前の記憶" }).scrollIntoViewIfNeeded();
      await shot(p, 25, "memory-history", scheme);

      // E6: 相談窓口のカード（返答を受け取っていない端末でも、履歴の安全対応の返答の下に出る）
      await openConversation(p, HINATA);
      const safetyCard = p.getByRole("region", { name: "話を聞いてくれる窓口があります" });
      await expect(safetyCard.getByTestId("safety-resource").first()).toBeVisible();
      // キャラの返答（気づかいの言葉 + 窓口）と、その下のカードの先頭が見える位置
      await scrollBelowHeader(
        messageLog(p, HINATA.name).getByText(crisis.reply.split("\n")[0]!, { exact: false }),
      );
      await shot(p, 24, "dm-safety-card", scheme);

      // 受信中の返答（POST /chat/stream の delta を順に表示する吹き出し）。途中までの delta を返して止める
      // 偽の応答で撮る（別のページ。何も保存しない）
      const streamingPage = await context.newPage();
      await openConversation(streamingPage, MISAKI);
      await streamingPage.evaluate(() => {
        const original = window.fetch.bind(window);
        window.fetch = async (input, init) => {
          const url =
            typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
          if (!url.endsWith("/chat/stream")) return original(input, init);
          const encoder = new TextEncoder();
          const chunks = ["おかえり！今日はね、", "会社の近くに新しいカフェができてて、"];
          const body = new ReadableStream<Uint8Array>({
            start(controller) {
              for (const text of chunks) {
                controller.enqueue(
                  encoder.encode(`event: delta\ndata: ${JSON.stringify({ text })}\n\n`),
                );
              }
              // 続き（done）は送らない: 受信中のまま撮る
            },
          });
          return new Response(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        };
      });
      await composer(streamingPage).fill("ただいま！今日はどうだった？");
      await streamingPage.getByRole("button", { name: "送信", exact: true }).click();
      await expect(
        messageLog(streamingPage, MISAKI.name).getByText(
          "おかえり！今日はね、会社の近くに新しいカフェができてて、",
        ),
      ).toBeVisible();
      await shot(streamingPage, 23, "dm-streaming", scheme);
      await streamingPage.close();

      await p.goto("/me");
      await expect(p.getByRole("button", { name: "ログアウト" })).toBeVisible();
      // 「キャラからのメッセージ」の設定（GET /proactive/settings）が表示されてから撮る
      await expect(p.getByTestId("proactive-global-switch")).toBeVisible();
      await shot(p, 20, "me", scheme);
      // 自発メッセージの設定（全体のオン・オフと送らない時間帯。E4）
      await scrollBelowHeader(p.getByTestId("proactive-settings"));
      await shot(p, 26, "me-proactive", scheme);

      await p.goto("/offline");
      await expect(p.getByRole("heading", { name: "オフラインです" })).toBeVisible();
      await shot(p, 21, "offline", scheme);

      await p.goto("/posts/00000000-0000-4000-8001-999999999999");
      await expect(p.getByText("このページはご利用いただけません")).toBeVisible();
      await shot(p, 22, "not-found", scheme);
      await context.close();
    }
  } finally {
    await deleteUsersByEmail(loginEmails);
  }
});
