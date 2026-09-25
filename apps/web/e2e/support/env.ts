/**
 * E2E テストの接続先（すべて環境変数で上書きできる。既定値はローカル開発環境）。
 *
 * - E2E_BASE_URL          Web（Next.js）          既定 http://localhost:3000
 * - E2E_SITE_URL          Supabase Auth の Site URL（ログインメールのリンクのオリジン。
 *                         infra/supabase/config.toml の site_url）   既定 http://localhost:3000
 * - E2E_API_URL           Python API（FastAPI）   既定 http://localhost:8000
 * - E2E_SUPABASE_URL      Supabase（Auth / REST） 既定 http://127.0.0.1:54321
 * - MAILPIT_URL           ログインメールの受信箱   既定 http://127.0.0.1:54324
 * - DATABASE_URL          Postgres（検証 SQL と後片付け用）
 * - SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY
 *     未設定なら global-setup が `supabase status --workdir infra -o json` から取得して
 *     E2E_SUPABASE_ANON_KEY / E2E_SUPABASE_SERVICE_ROLE_KEY に入れる（ワーカーへ引き継がれる）。
 */

export const E2E = {
  baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
  /**
   * ログインメールのリンクのオリジン（Supabase Auth の site_url）。E2E_BASE_URL とは独立した設定なので、
   * Web を別のポート・ホストで動かす場合もメールのリンクはこのオリジンになる（auth.spec.ts はパスだけを使う）
   */
  siteURL: process.env.E2E_SITE_URL ?? "http://localhost:3000",
  apiURL: process.env.E2E_API_URL ?? "http://localhost:8000",
  supabaseURL: process.env.E2E_SUPABASE_URL ?? "http://127.0.0.1:54321",
  mailpitURL: process.env.MAILPIT_URL ?? "http://127.0.0.1:54324",
  databaseURL:
    process.env.DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
} as const;

/** シードのキャラクター（infra/supabase/seed.sql） */
export const MISAKI = {
  id: "00000000-0000-4000-8000-0000000000c1",
  handle: "misaki_ol",
  name: "美咲",
} as const;

export const HINATA = {
  id: "00000000-0000-4000-8000-0000000000c2",
  handle: "hinata_umi",
  name: "ひなた",
} as const;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} が設定されていません（global-setup が設定します。playwright test 経由で実行してください）`,
    );
  }
  return value;
}

export function anonKey(): string {
  return required("E2E_SUPABASE_ANON_KEY");
}

export function serviceRoleKey(): string {
  return required("E2E_SUPABASE_SERVICE_ROLE_KEY");
}

/** この実行の ID（作成するユーザーのメールアドレスに入れ、global-teardown で一括削除する） */
export function runId(): string {
  return process.env.E2E_RUN_ID ?? "local";
}

/** テストユーザーのメールアドレスの共通接頭辞 */
export const EMAIL_PREFIX = "e2e-";
export const EMAIL_DOMAIN = "example.com";
