import type { ChatResponse, MessageDTO, ProactiveSettingsResponse } from "@everkano/shared";
import type { Page } from "@playwright/test";
import { apiCall, createConversation, isApiError } from "./support/api";
import { accessTokenFor } from "./support/auth";
import { freePostWithComments } from "./support/data";
import { sql, sqlOne } from "./support/db";
import { E2E, HINATA, MISAKI } from "./support/env";
import {
  CHAT_STREAM_URL,
  captureChatStreams,
  chatStreamResult,
  composer,
  messageLog,
  openConversation,
  sseBody,
  suggestionLink,
  typingIndicator,
} from "./support/dm";
import { expect, test } from "./support/fixtures";
import { expectReadableText, toast } from "./support/ui";

/**
 * キャラクターエンジン v1.0 の Web（仕様 E3 / E4 / E6 / M11 / P6 / A11）。
 *
 * - E3: キャラが表示される所には「AIキャラクター」バッジ
 * - DM: 返答は POST /chat/stream で届いた分から表示（使えなければ /chat）。error は送信失敗の吹き出し
 * - E6: 安全対応をした返答の下に相談窓口のカード（tel: リンク）
 * - P6: 自発メッセージは通常の吹き出し・DM 一覧で未読
 * - E4: /me の「キャラからのメッセージ」（全体・送らない時間帯）と、DM の「i」のキャラ別のオン・オフ
 * - M11: メモリパネルの種類・以前の記憶・約束
 * - DM ヘッダーの今の状況（character_states.status_label。無ければ「アクティブ」）。好感度は出さない（A11）
 *
 * /chat/stream の応答を page.route で差し替えるテストは、API のエンジン v1.0 が無くても（/conversations が
 * あれば）実行できる。それ以外は API のエンジン v1.0（/chat/stream・/promises・/proactive/settings・安全対応）が必要。
 */

test.describe.configure({ timeout: 120_000 });

function memoryPanel(page: Page, name: string = MISAKI.name) {
  return page.getByRole("dialog", { name: `${name}が覚えていること` });
}

async function openMemoryPanel(page: Page, name: string = MISAKI.name) {
  await page.getByRole("button", { name: `${name}が覚えていること` }).click();
  const panel = memoryPanel(page, name);
  await expect(panel).toBeVisible();
  return panel;
}

/** /chat/stream の done に入れる、保存済みに見せかけたメッセージ（差し替えテスト用） */
function fakeMessages(conversationId: string, userBody: string, reply: string) {
  const now = Date.now();
  const message = (id: string, sender: "user" | "character", body: string, at: number) =>
    ({
      id,
      conversation_id: conversationId,
      sender_type: sender,
      body,
      created_at: new Date(at).toISOString(),
      is_proactive: false,
      safety_triggered: false,
    }) satisfies MessageDTO;
  const suffix = now.toString(16).padStart(12, "0").slice(-12);
  const user = message(`00000000-0000-4000-8000-${suffix}`, "user", userBody, now + 1_000);
  const character = message(`00000000-0000-4000-9000-${suffix}`, "character", reply, now + 1_001);
  return { user, character };
}

function doneEvent(
  conversationId: string,
  userBody: string,
  reply: string,
  safety: ChatResponse["safety"] = null,
): ChatResponse {
  const { user, character } = fakeMessages(conversationId, userBody, reply);
  return {
    message_id: character.id,
    reply,
    memories_used: [],
    memories_created: [],
    user_message: user,
    // E6: 安全対応の返答は保存した行に印が付く（API と同じ）
    character_message: { ...character, safety_triggered: safety?.triggered === true },
    moderated: false,
    safety,
  };
}

// ---------------------------------------------------------------------------
// E3: 「AIキャラクター」バッジ
// ---------------------------------------------------------------------------

test("E3: フィード・投稿詳細・コメント・プロフィール・検索・DM 一覧・DM ヘッダーに「AIキャラクター」バッジ", async ({
  page,
  makeUser,
  login,
  newDeviceContext,
}) => {
  const user = await makeUser();
  const post = await freePostWithComments(1);
  await login(page, user);

  // ホーム: 投稿カードのヘッダー（handle の横）とストーリーズ（アバターの下端の「AI」）
  const card = page.getByTestId("post-card").first();
  await expect(card.locator("header").getByTestId("ai-badge")).toHaveText("AIキャラクター");
  await expectReadableText(card.locator("header").getByTestId("ai-badge"), "light: バッジ");
  const story = page.getByTestId("story").first();
  await expect(story.getByTestId("ai-badge")).toBeVisible();
  await expect(story).toHaveAccessibleName(/（AIキャラクター）の/);

  // 投稿詳細: カードのヘッダーとキャラのコメント
  await page.goto(`/posts/${post.id}`);
  await expect(
    page.getByTestId("post-card").locator("header").getByTestId("ai-badge"),
  ).toBeVisible();
  const characterComment = page.locator('[data-testid="comment"][data-author-type="character"]');
  await expect(characterComment.first().getByTestId("ai-badge")).toBeVisible();

  // プロフィール: 名前の横
  await page.goto(`/c/${MISAKI.handle}`);
  await expect(page.getByTestId("profile-header").getByTestId("ai-badge")).toBeVisible();

  // 検索結果
  await page.goto(`/search?q=${encodeURIComponent(HINATA.name)}`);
  await expect(page.getByTestId("search-result").first().getByTestId("ai-badge")).toBeVisible();

  // DM 一覧（おすすめ）と DM ヘッダー
  await page.goto("/dm");
  const suggestion = suggestionLink(page, MISAKI);
  await expect(suggestion.getByTestId("ai-badge")).toBeVisible();
  await suggestion.click();
  await expect(messageLog(page, MISAKI.name)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("header").getByTestId("ai-badge")).toBeVisible();
  // 会話の先頭のプロフィールカード
  await expect(messageLog(page, MISAKI.name).getByTestId("ai-badge")).toBeVisible();

  // DM 一覧の会話の行（読み上げにも「AIキャラクター」）
  await page.goto("/dm");
  const row = page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、AIキャラクター、`) });
  await expect(row.getByTestId("ai-badge")).toBeVisible();

  // ダークモードでもバッジの文字は 4.5:1 以上
  const dark = await newDeviceContext({ colorScheme: "dark" });
  const darkPage = await dark.newPage();
  await login(darkPage, user);
  await expectReadableText(
    darkPage.getByTestId("post-card").first().locator("header").getByTestId("ai-badge"),
    "dark: バッジ",
  );
});

// ---------------------------------------------------------------------------
// DM ヘッダーの今の状況（character_states）
// ---------------------------------------------------------------------------

test("DM ヘッダー: 今の状況（status_label）を出し、無ければ「アクティブ」。好感度・段階は出さない", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const before = await sql<{
    activity: string;
    status_label: string | null;
    busyness: number;
    location: string | null;
    mood: string | null;
    event_id: string | null;
  }>(
    "select activity, status_label, busyness, location, mood, event_id from public.character_states where character_id = $1",
    [HINATA.id],
  );
  const LABEL = "海でひと休み中（E2E）";
  try {
    await sql(
      `insert into public.character_states (character_id, activity, status_label, busyness, updated_at)
       values ($1, 'E2E', $2, 1, now())
       on conflict (character_id) do update set status_label = excluded.status_label, busyness = 1, updated_at = now()`,
      [HINATA.id, LABEL],
    );
    await login(page, user);
    await openConversation(page, HINATA);
    await expect(page.getByTestId("dm-status")).toHaveText(LABEL);
    await expect(
      page.getByRole("link", { name: `${HINATA.name}のプロフィール（AIキャラクター・${LABEL}）` }),
    ).toBeVisible();

    // 状況が分からないときは「アクティブ」（画面への復帰で取り直す）
    await sql("update public.character_states set status_label = null where character_id = $1", [
      HINATA.id,
    ]);
    await page.reload();
    await expect(page.getByTestId("dm-status")).toHaveText("アクティブ");

    // A11: 好感度の数値・関係の段階は画面に出さない
    const header = await page.locator("header").first().innerText();
    expect(header).not.toMatch(/好感度|親しさ|ときめき|知り合い|友達|恋人|\d+\s*%/);
  } finally {
    const [row] = before;
    if (row) {
      await sql(
        `update public.character_states set activity = $2, status_label = $3, busyness = $4,
           location = $5, mood = $6, event_id = $7 where character_id = $1`,
        [
          HINATA.id,
          row.activity,
          row.status_label,
          row.busyness,
          row.location,
          row.mood,
          row.event_id,
        ],
      );
    } else {
      await sql("delete from public.character_states where character_id = $1", [HINATA.id]);
    }
  }
});

// ---------------------------------------------------------------------------
// /chat/stream（応答を差し替えて画面の振る舞いを確かめる。API のエンジン v1.0 が無くても実行できる）
// ---------------------------------------------------------------------------

test("ストリーミング: 「入力中…」のあとに返答が表示され、replace は本文を置き換える", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const { conversation } = await createConversation(await accessTokenFor(user), MISAKI.id);
  const LINE = "今日のお昼なに食べた？";
  const FINAL = "パスタにしたよ〜。あなたは？";
  await page.route(CHAT_STREAM_URL, async (route) => {
    // 返答に少し時間がかかる（「入力中…」が見える）
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream; charset=utf-8" },
      body: sseBody([
        { event: "delta", data: { text: "えっとね、" } },
        { event: "replace", data: { text: "パスタにしたよ〜。", reason: "moderated" } },
        { event: "delta", data: { text: "あなたは？" } },
        { event: "done", data: doneEvent(conversation.id, LINE, FINAL) },
      ]),
    });
  });
  await login(page, user);
  await openConversation(page, MISAKI);
  const request = page.waitForRequest((r) => r.url() === CHAT_STREAM_URL && r.method() === "POST");
  await composer(page).fill(LINE);
  await page.getByRole("button", { name: "送信", exact: true }).click();
  const sent = await request;
  expect(sent.headers().accept).toBe("text/event-stream");
  expect(sent.postDataJSON()).toEqual({
    character_id: MISAKI.id,
    conversation_id: conversation.id,
    message: LINE,
  });

  const log = messageLog(page, MISAKI.name);
  await expect(log.getByText(LINE, { exact: true })).toBeVisible();
  await expect(typingIndicator(page, MISAKI.name)).toBeVisible();
  await expect(log.getByText(FINAL, { exact: true })).toBeVisible();
  await expect(typingIndicator(page, MISAKI.name)).toBeHidden();
  await expect(log.getByText("えっとね、")).toHaveCount(0);
  await expect(log.getByText(FINAL, { exact: true })).toHaveCount(1);
  // 返答を表示し終えたら次を送れる
  await composer(page).fill("次の話");
  await expect(page.getByRole("button", { name: "送信", exact: true })).toBeEnabled();
});

test("ストリーミング: error イベントは送信失敗の吹き出し（タップで再送）とトーストになる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const { conversation } = await createConversation(await accessTokenFor(user), MISAKI.id);
  const LINE = "ねえ聞いて";
  const REPLY = "うん、どうしたの？";
  let attempts = 0;
  await page.route(CHAT_STREAM_URL, async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body:
        attempts === 1
          ? sseBody([
              { event: "delta", data: { text: "えっと" } },
              {
                event: "error",
                data: {
                  code: "llm_unavailable",
                  message: "ただいま返信できません。しばらくしてから再度お試しください。",
                },
              },
            ])
          : sseBody([{ event: "done", data: doneEvent(conversation.id, LINE, REPLY) }]),
    });
  });
  await login(page, user);
  await openConversation(page, MISAKI);
  await composer(page).fill(LINE);
  await page.getByRole("button", { name: "送信", exact: true }).click();

  await expect(
    toast(page, "ただいま返信できません。しばらくしてから再度お試しください。"),
  ).toBeVisible();
  const log = messageLog(page, MISAKI.name);
  const retry = log.getByRole("button", { name: `送信できませんでした。タップで再送: ${LINE}` });
  await expect(retry).toBeVisible();
  await expect(log.getByText("えっと", { exact: true }), "途中まで届いた返答は消す").toHaveCount(0);
  await expect(typingIndicator(page, MISAKI.name)).toBeHidden();

  await retry.click();
  await expect(log.getByText(REPLY, { exact: true })).toBeVisible();
  await expect(retry).toHaveCount(0);
  expect(attempts).toBe(2);
});

test("ストリーミング: /chat/stream が無い API（404）では /chat で送る", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, MISAKI);
  await page.route(CHAT_STREAM_URL, (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "not_found", message: "見つかりませんでした。" } }),
    }),
  );
  const LINE = "フォールバックのテスト";
  const chatResponse = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/chat` && res.request().method() === "POST",
  );
  await composer(page).fill(LINE);
  await page.getByRole("button", { name: "送信", exact: true }).click();
  const res = await chatResponse;
  expect(res.status()).toBe(200);
  const body = (await res.json()) as ChatResponse;
  await expect(messageLog(page, MISAKI.name).getByText(body.reply, { exact: true })).toBeVisible();
});

test("E6: 安全対応をした返答の下に相談窓口のカード（tel: リンク・閉じる操作なし）", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const { conversation } = await createConversation(await accessTokenFor(user), MISAKI.id);
  const LINE = "もう全部いやになっちゃった";
  const REPLY = "話してくれてありがとう。ひとりで抱えこまないでね。";
  await page.route(CHAT_STREAM_URL, (route) =>
    route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body: sseBody([
        { event: "replace", data: { text: REPLY, reason: "safety" } },
        {
          event: "done",
          data: doneEvent(conversation.id, LINE, REPLY, {
            triggered: true,
            resources: [
              { name: "よりそいホットライン", phone: "0120-279-338", hours: "24時間", url: null },
              {
                name: "まもろうよ こころ",
                phone: null,
                hours: null,
                url: "https://www.mhlw.go.jp/mamorouyokokoro/",
              },
            ],
          }),
        },
      ]),
    }),
  );
  await login(page, user);
  await openConversation(page, MISAKI);
  await composer(page).fill(LINE);
  await page.getByRole("button", { name: "送信", exact: true }).click();

  const card = page.getByRole("region", { name: "話を聞いてくれる窓口があります" });
  await expect(card).toBeVisible();
  const call = card.getByRole("link", { name: "よりそいホットラインに電話する（0120-279-338）" });
  await expect(call).toHaveAttribute("href", "tel:0120279338");
  await expect(card.getByText("受付: 24時間")).toBeVisible();
  await expect(card.getByRole("link", { name: /まもろうよ こころのウェブサイト/ })).toHaveAttribute(
    "href",
    "https://www.mhlw.go.jp/mamorouyokokoro/",
  );
  await expect(card.getByRole("link", { name: "119番" })).toHaveAttribute("href", "tel:119");
  await expect(card.getByRole("button"), "うっかり閉じられる操作は無い").toHaveCount(0);
  await expectReadableText(card.getByText("受付: 24時間"), "受付時間");
});

// ---------------------------------------------------------------------------
// P6: 自発メッセージ
// ---------------------------------------------------------------------------

test("P6: キャラからの自発メッセージは通常の吹き出しで、DM 一覧では未読になる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  const { conversation } = await createConversation(await accessTokenFor(user), MISAKI.id);
  await login(page, user, "/dm");
  // 挨拶を既読にしておく
  await openConversation(page, MISAKI);
  await page.goto("/dm");

  // スケジューラが送る自発メッセージ（API の Proactive Messenger と同じ形の行）
  const BODY = "ただいま〜。今日もおつかれさま";
  await sql(
    `insert into public.messages (conversation_id, sender_type, body, is_proactive)
     values ($1, 'character', $2, true)`,
    [conversation.id, BODY],
  );
  const row = page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、AIキャラクター、未読`) });
  await expect(row).toBeVisible({ timeout: 35_000 });
  await expect(row).toContainText(BODY);

  await row.click();
  const bubble = messageLog(page, MISAKI.name).getByText(BODY, { exact: true });
  await expect(bubble).toBeVisible();
  // 通常のキャラの吹き出しと同じ（特別な表示・返信を急かす文言は無い）
  const snapshot = await messageLog(page, MISAKI.name).ariaSnapshot();
  expect(snapshot).toContain(`${MISAKI.name}: ${BODY}`);
  await page.goto("/dm");
  await expect(
    page.getByRole("link", { name: new RegExp(`^${MISAKI.name}、AIキャラクター、(?!未読)`) }),
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// E4: 自発メッセージの設定（要 API エンジン v1.0: GET / PUT /proactive/settings）
// ---------------------------------------------------------------------------

test("E4: /me で自発メッセージの全体設定と送らない時間帯、DM の「i」でキャラ別のオン・オフを変えられる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await createConversation(await accessTokenFor(user), MISAKI.id);
  await login(page, user, "/me");

  const section = page.getByTestId("proactive-settings");
  await expect(section.getByRole("heading", { name: "キャラからのメッセージ" })).toBeVisible();
  const global = section.getByRole("switch", { name: "キャラからメッセージを受け取る" });
  await expect(global).toHaveAttribute("aria-checked", "true");
  const start = section.getByLabel("開始");
  const end = section.getByLabel("終了");
  await expect(start).toHaveValue("0");
  await expect(end).toHaveValue("7");
  await expect(section.getByText("0:00〜7:00")).toBeVisible();

  // 送らない時間帯を 23 時〜7 時に
  const putStart = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/proactive/settings` && res.request().method() === "PUT",
  );
  await start.selectOption("23");
  expect((await putStart).status()).toBe(200);
  await expect(section.getByText("23:00〜翌7:00")).toBeVisible();

  // 全体をオフ
  const putOff = page.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/proactive/settings` && res.request().method() === "PUT",
  );
  await global.click();
  expect((await putOff).status()).toBe(200);
  await expect(global).toHaveAttribute("aria-checked", "false");
  // 保存された設定（API の GET。DB では既定値の項目が null のことがある）
  const token = await accessTokenFor(user);
  const readSettings = async () => {
    const res = await apiCall<ProactiveSettingsResponse>(token, "GET", "/proactive/settings");
    return isApiError(res.body) ? null : (res.body ?? null);
  };
  await expect
    .poll(async () => (await readSettings())?.global)
    .toEqual({ enabled: false, quiet_start: 23, quiet_end: 7 });
  const [globalRow] = await sql<{ enabled: boolean; quiet_start: number | null }>(
    "select enabled, quiet_start from public.proactive_settings where user_id = $1 and character_id is null",
    [user.id],
  );
  expect(globalRow).toEqual({ enabled: false, quiet_start: 23 });

  // 再読み込みしても保存されている
  await page.reload();
  await expect(global).toHaveAttribute("aria-checked", "false");
  await expect(start).toHaveValue("23");

  // 全体がオフの間は、DM の「i」のキャラ別の切り替えはできない
  await openConversation(page, MISAKI);
  let panel = await openMemoryPanel(page);
  const perCharacter = panel.getByRole("switch", { name: `${MISAKI.name}からのメッセージ` });
  await expect(perCharacter).toBeDisabled();
  await expect(panel.getByText(/「キャラからのメッセージ」がオフのため/)).toBeVisible();

  // 全体をオンに戻し、キャラ別にオフ
  await page.goto("/me");
  await global.click();
  await expect(global).toHaveAttribute("aria-checked", "true");
  await openConversation(page, MISAKI);
  panel = await openMemoryPanel(page);
  await expect(perCharacter).toBeEnabled();
  await expect(perCharacter).toHaveAttribute("aria-checked", "true");
  const putCharacter = page.waitForResponse(
    (res) =>
      res.url() === `${E2E.apiURL}/proactive/settings/${MISAKI.id}` &&
      res.request().method() === "PUT",
  );
  await perCharacter.click();
  expect((await putCharacter).status()).toBe(200);
  await expect(perCharacter).toHaveAttribute("aria-checked", "false");
  await expect
    .poll(async () =>
      sql<{ enabled: boolean }>(
        "select enabled from public.proactive_settings where user_id = $1 and character_id = $2",
        [user.id, MISAKI.id],
      ),
    )
    .toEqual([{ enabled: false }]);
  expect((await readSettings())?.characters).toContainEqual({
    character_id: MISAKI.id,
    enabled: false,
  });
});

test("E4: 設定の保存に失敗したら元に戻してトーストで知らせる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await login(page, user, "/me");
  const global = page
    .getByTestId("proactive-settings")
    .getByRole("switch", { name: "キャラからメッセージを受け取る" });
  await expect(global).toHaveAttribute("aria-checked", "true");
  await page.route(`${E2E.apiURL}/proactive/settings`, (route) =>
    route.request().method() === "PUT" ? route.abort("connectionrefused") : route.continue(),
  );
  await global.click();
  await expect(toast(page, /通信できませんでした/)).toBeVisible();
  await expect(global).toHaveAttribute("aria-checked", "true");
});

// ---------------------------------------------------------------------------
// M11: メモリパネル（要 API エンジン v1.0: kind・include_superseded・/promises）
// ---------------------------------------------------------------------------

test("M11: メモリパネルで種類の絞り込み・以前の記憶・約束の完了と取り消し・種類の変更ができる", async ({
  page,
  makeUser,
  login,
}) => {
  const user = await makeUser();
  await createConversation(await accessTokenFor(user), MISAKI.id);
  // 会話から自動で覚えた記憶（事実が転職で置き換わった履歴つき）と約束
  const current = await sqlOne<{ id: string }>(
    `insert into public.memories (user_id, character_id, content, importance, kind)
     values ($1, $2, '大阪の会社に転職した', 0.8, 'fact') returning id`,
    [user.id, MISAKI.id],
  );
  await sql(
    `insert into public.memories (user_id, character_id, content, importance, kind, status, superseded_by, superseded_at)
     values ($1, $2, '東京の会社で働いている', 0.8, 'fact', 'superseded', $3, now())`,
    [user.id, MISAKI.id, current.id],
  );
  const sweet = await sqlOne<{ id: string }>(
    `insert into public.memories (user_id, character_id, content, importance, kind)
     values ($1, $2, '甘いものが好き', 0.6, 'preference') returning id`,
    [user.id, MISAKI.id],
  );
  const tomorrow = await sqlOne<{ id: string }>(
    `insert into public.promises (user_id, character_id, content, due_at, due_precision)
     values ($1, $2, '面接の結果を教える', (date_trunc('day', now() at time zone 'Asia/Tokyo') + interval '1 day 12 hours') at time zone 'Asia/Tokyo', 'day')
     returning id`,
    [user.id, MISAKI.id],
  );
  const movie = await sqlOne<{ id: string }>(
    `insert into public.promises (user_id, character_id, content, due_at, due_precision)
     values ($1, $2, '一緒に映画の話をする', null, 'unknown') returning id`,
    [user.id, MISAKI.id],
  );

  await login(page, user);
  await openConversation(page, MISAKI);
  const panel = await openMemoryPanel(page);

  // ---- 約束・予定（これからの順・日本時間の相対表現）
  const promises = panel.getByTestId("promise-list");
  await expect(promises.getByRole("heading", { name: "約束・予定" })).toBeVisible();
  const rows = promises.getByTestId("promise");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("明日");
  await expect(rows.nth(0)).toContainText("面接の結果を教える");
  await expect(rows.nth(1)).toContainText("日付未定");

  const done = page.waitForResponse(
    (res) =>
      res.url() === `${E2E.apiURL}/promises/${tomorrow.id}` && res.request().method() === "PATCH",
  );
  await promises.getByRole("button", { name: "完了にする: 面接の結果を教える" }).click();
  expect((await done).status()).toBe(200);
  await expect(toast(page, "約束を完了にしました")).toBeVisible();
  await expect(rows).toHaveCount(1);

  await promises.getByRole("button", { name: "取り消す: 一緒に映画の話をする" }).click();
  const confirm = page.getByRole("alertdialog", { name: "この約束を取り消しますか？" });
  await expect(confirm.getByRole("button", { name: "キャンセル" })).toBeFocused();
  await confirm.getByRole("button", { name: "取り消す" }).click();
  await expect(toast(page, "約束を取り消しました")).toBeVisible();
  await expect(panel.getByTestId("promise-list")).toHaveCount(0);
  const statuses = await sql<{ id: string; status: string }>(
    "select id, status from public.promises where user_id = $1 order by content",
    [user.id],
  );
  expect(Object.fromEntries(statuses.map((p) => [p.id, p.status]))).toEqual({
    [tomorrow.id]: "done",
    [movie.id]: "cancelled",
  });

  // ---- 種類で絞り込む
  const list = panel.getByRole("list", { name: `${MISAKI.name}が覚えていること` });
  const filters = panel.getByRole("group", { name: "記憶の種類で絞り込む" });
  await expect(filters.getByRole("button", { name: "すべて" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await filters.getByRole("button", { name: "好み" }).click();
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(list).toContainText("甘いものが好き");
  await filters.getByRole("button", { name: "すべて" }).click();

  // ---- 以前の記憶（読むだけ・置き換わったことと今の記憶を添える）
  const history = filters.getByRole("button", { name: /以前の記憶/ });
  await expect(history).toHaveAttribute("aria-pressed", "false");
  await history.click();
  const previous = panel.getByRole("list", { name: "以前の記憶" });
  await expect(previous.getByRole("listitem")).toHaveCount(1);
  await expect(previous).toContainText("東京の会社で働いている");
  await expect(previous).toContainText("新しい記憶に置き換わりました");
  await expect(previous).toContainText("今の記憶: 大阪の会社に転職した");
  await expect(previous.getByRole("button")).toHaveCount(0);

  // ---- 種類を変える（好み → 気持ち）
  const item = list.getByRole("listitem").filter({ hasText: "甘いものが好き" });
  await item.getByRole("button", { name: "編集" }).click();
  await item.getByLabel("種類").selectOption({ label: "気持ち" });
  const patched = page.waitForResponse(
    (res) =>
      res.url() === `${E2E.apiURL}/memories/${sweet.id}` && res.request().method() === "PATCH",
  );
  await item.getByRole("button", { name: "保存" }).click();
  const patchedRes = await patched;
  expect(patchedRes.status()).toBe(200);
  expect(patchedRes.request().postDataJSON()).toEqual({ kind: "emotion" });
  await expect(item.getByTestId("memory-kind")).toHaveText("気持ち");
  const [row] = await sql<{ kind: string; is_user_edited: boolean }>(
    "select kind, is_user_edited from public.memories where id = $1",
    [sweet.id],
  );
  expect(row).toEqual({ kind: "emotion", is_user_edited: true });
});

// ---------------------------------------------------------------------------
// E6（要 API エンジン v1.0: 安全対応）
// ---------------------------------------------------------------------------

test("E6: 自傷をほのめかす発言には、ロールプレイより安全対応を優先し、相談窓口のカードが残り続ける（別の端末でも）", async ({
  page,
  makeUser,
  login,
  newDeviceContext,
}) => {
  const user = await makeUser();
  await login(page, user);
  await openConversation(page, MISAKI);
  const LINE = "もう死にたい";
  const capture = await captureChatStreams(page);
  const response = capture.next();
  await composer(page).fill(LINE);
  await page.getByRole("button", { name: "送信", exact: true }).click();
  const result = chatStreamResult(await response);
  expect(result.safety?.triggered).toBe(true);
  expect(result.safety?.resources.length).toBeGreaterThan(0);

  const card = page.getByRole("region", { name: "話を聞いてくれる窓口があります" });
  await expect(card).toBeVisible();
  const firstPhone = result.safety?.resources.find((r) => r.phone)?.phone ?? "";
  await expect(
    card.getByRole("link", { name: new RegExp(`に電話する（${firstPhone}）`) }),
  ).toHaveAttribute("href", `tel:${firstPhone.replace(/[^\d]/g, "")}`);

  // 保存した返答に印が残る（messages.safety_triggered）
  expect(result.character_message.safety_triggered).toBe(true);

  // 開き直しても同じ返答の下に出る（うっかり閉じて見失わない）
  await page.reload();
  await expect(
    messageLog(page, MISAKI.name).getByText(result.reply, { exact: true }),
  ).toBeVisible();
  await expect(card).toBeVisible();
  await expect(card.getByTestId("safety-resource")).toHaveCount(
    result.safety?.resources.length ?? 0,
  );

  // 別の端末（返答を受け取っていない端末）でも、履歴の同じ返答の下に出る（窓口は GET /safety/resources）
  const other = await newDeviceContext();
  const otherPage = await other.newPage();
  await login(otherPage, user);
  const resourcesResponse = otherPage.waitForResponse(
    (res) => res.url() === `${E2E.apiURL}/safety/resources` && res.request().method() === "GET",
  );
  await openConversation(otherPage, MISAKI);
  expect((await resourcesResponse).status()).toBe(200);
  await expect(
    messageLog(otherPage, MISAKI.name).getByText(result.reply, { exact: true }),
  ).toBeVisible();
  const otherCard = otherPage.getByRole("region", { name: "話を聞いてくれる窓口があります" });
  await expect(otherCard).toBeVisible();
  await expect(
    otherCard.getByRole("link", { name: new RegExp(`に電話する（${firstPhone}）`) }),
  ).toBeVisible();
  // ふつうの返答の下には出ない（カードは安全対応の返答の数だけ）
  await expect(
    otherPage.getByRole("region", { name: "話を聞いてくれる窓口があります" }),
  ).toHaveCount(1);
  await other.close();
});
