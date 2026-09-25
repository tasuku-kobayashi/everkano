import { randomBytes } from "node:crypto";
import { EMAIL_DOMAIN, EMAIL_PREFIX, E2E, anonKey, runId, serviceRoleKey } from "./env";

/**
 * テストユーザーの作成とログイン。
 *
 * ログイン画面そのものの検証（A2）以外では、メール送信のレート制限（Supabase Auth の email_sent）に
 * かからないよう、管理 API の generate_link で発行したトークンをアプリの /auth/confirm（マジックリンクの
 * 着地点と同じルート）に渡してログインする。メールは送信されない。
 */

export interface TestUser {
  id: string;
  email: string;
  password: string;
}

function adminHeaders(): Record<string, string> {
  const key = serviceRoleKey();
  return { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" };
}

/** 一意なテスト用メールアドレス（global-teardown が runId で一括削除する） */
export function uniqueEmail(label: string): string {
  const safe = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 24);
  return `${EMAIL_PREFIX}${runId()}-${safe}-${randomBytes(3).toString("hex")}@${EMAIL_DOMAIN}`;
}

/** 管理 API でメール確認済みのユーザーを作る（on_auth_user_created トリガーで profiles も作られる） */
export async function createUser(label: string): Promise<TestUser> {
  const email = uniqueEmail(label);
  const password = `pw-${randomBytes(12).toString("hex")}`;
  const res = await fetch(`${E2E.supabaseURL}/auth/v1/admin/users`, {
    method: "POST",
    headers: adminHeaders(),
    body: JSON.stringify({ email, password, email_confirm: true }),
  });
  if (!res.ok) throw new Error(`admin create user failed: ${res.status} ${await res.text()}`);
  const body = (await res.json()) as { id: string };
  return { id: body.id, email, password };
}

/**
 * ログイン用 URL（アプリの /auth/confirm。メールの「ログインする」ボタンと同じルート）。
 * 管理 API の generate_link はメールを送らずに token_hash を返す。
 */
export async function confirmPathFor(email: string, next = "/"): Promise<string> {
  const res = await fetch(`${E2E.supabaseURL}/auth/v1/admin/generate_link`, {
    method: "POST",
    headers: adminHeaders(),
    body: JSON.stringify({ type: "magiclink", email }),
  });
  if (!res.ok) throw new Error(`generate_link failed: ${res.status} ${await res.text()}`);
  const body = (await res.json()) as {
    hashed_token?: string;
    properties?: { hashed_token?: string };
  };
  const tokenHash = body.hashed_token ?? body.properties?.hashed_token;
  if (!tokenHash) throw new Error("generate_link returned no hashed_token");
  const params = new URLSearchParams({ token_hash: tokenHash, type: "magiclink", next });
  return `/auth/confirm?${params.toString()}`;
}

/** パスワードグラントでアクセストークンを取得（API / supabase-js を直接叩く検証用） */
export async function accessTokenFor(user: TestUser): Promise<string> {
  const res = await fetch(`${E2E.supabaseURL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: anonKey(), "Content-Type": "application/json" },
    body: JSON.stringify({ email: user.email, password: user.password }),
  });
  if (!res.ok) throw new Error(`password grant failed: ${res.status} ${await res.text()}`);
  const body = (await res.json()) as { access_token: string };
  return body.access_token;
}

// ---------------------------------------------------------------------------
// Mailpit（ローカルの Supabase Auth が送るメールの受信箱）
// ---------------------------------------------------------------------------

export interface LoginMail {
  subject: string;
  /** 6 桁の確認コード */
  code: string;
  /** メールの「ログインする」リンク（{{ .SiteURL }}/auth/confirm?token_hash=...&type=email&next=/） */
  confirmUrl: string;
}

interface MailpitAddress {
  Address: string;
}
interface MailpitSummary {
  ID: string;
  To: MailpitAddress[];
  Created: string;
  Subject: string;
}
interface MailpitMessage {
  Text: string;
  HTML: string;
}

/** sinceMs 以降に email 宛てに届いたログインメールを待つ */
export async function waitForLoginMail(
  email: string,
  sinceMs: number,
  timeoutMs = 20_000,
): Promise<LoginMail> {
  const deadline = Date.now() + timeoutMs;
  const target = email.toLowerCase();
  while (Date.now() < deadline) {
    const res = await fetch(`${E2E.mailpitURL}/api/v1/messages?limit=100`);
    if (res.ok) {
      const { messages } = (await res.json()) as { messages: MailpitSummary[] };
      const summary = messages.find(
        (m) =>
          m.To.some((to) => to.Address.toLowerCase() === target) &&
          Date.parse(m.Created) >= sinceMs - 2_000,
      );
      if (summary) {
        const detailRes = await fetch(`${E2E.mailpitURL}/api/v1/message/${summary.ID}`);
        const detail = (await detailRes.json()) as MailpitMessage;
        const code =
          /\b(\d{6})\b/.exec(detail.Text)?.[1] ?? />\s*(\d{6})\s*</.exec(detail.HTML)?.[1];
        const href = /href="([^"]*\/auth\/confirm\?[^"]+)"/.exec(detail.HTML)?.[1];
        if (!code || !href) throw new Error(`login mail without code/link: ${summary.Subject}`);
        return { subject: summary.Subject, code, confirmUrl: href.replace(/&amp;/g, "&") };
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`login mail for ${email} did not arrive within ${timeoutMs}ms`);
}
