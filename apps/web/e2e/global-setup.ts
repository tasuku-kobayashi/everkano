import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { FullConfig } from "@playwright/test";
import { E2E } from "./support/env";

/**
 * 実行前の準備:
 * 1. Supabase の anon / service_role キーを解決して環境変数に入れる（ワーカーへ引き継がれる）
 * 2. この実行の ID（テストユーザーのメールアドレスに入る）を決める
 * 3. Web / API / Supabase / Mailpit / Postgres に接続できることを確認する（サーバーは起動しない）
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../..");

interface SupabaseStatus {
  ANON_KEY?: string;
  SERVICE_ROLE_KEY?: string;
}

function readSupabaseStatus(): SupabaseStatus {
  try {
    const out = execFileSync("supabase", ["status", "--workdir", "infra", "-o", "json"], {
      cwd: REPO_ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 60_000,
    });
    return JSON.parse(out) as SupabaseStatus;
  } catch {
    return {};
  }
}

function readEnvLocal(name: string): string | undefined {
  const file = resolve(HERE, "../.env.local");
  if (!existsSync(file)) return undefined;
  const line = readFileSync(file, "utf8")
    .split(/\r?\n/)
    .find((l) => l.startsWith(`${name}=`));
  const value = line?.slice(name.length + 1).trim();
  return value || undefined;
}

function resolveKeys(): void {
  let status: SupabaseStatus | null = null;
  const lazyStatus = () => (status ??= readSupabaseStatus());

  process.env.E2E_SUPABASE_ANON_KEY ||=
    process.env.SUPABASE_ANON_KEY ||
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
    readEnvLocal("NEXT_PUBLIC_SUPABASE_ANON_KEY") ||
    lazyStatus().ANON_KEY ||
    "";
  process.env.E2E_SUPABASE_SERVICE_ROLE_KEY ||=
    process.env.SUPABASE_SERVICE_ROLE_KEY || lazyStatus().SERVICE_ROLE_KEY || "";

  if (!process.env.E2E_SUPABASE_ANON_KEY || !process.env.E2E_SUPABASE_SERVICE_ROLE_KEY) {
    throw new Error(
      "Supabase のキーを取得できませんでした。`supabase start --workdir infra` を実行するか、" +
        "SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY を設定してください。",
    );
  }
}

async function check(name: string, url: string, init?: RequestInit): Promise<Response> {
  try {
    const res = await fetch(url, { redirect: "manual", ...init });
    return res;
  } catch (error) {
    throw new Error(
      `${name}（${url}）に接続できません: ${(error as Error).message}\n` +
        "E2E はサーバーを起動しません。apps/web/e2e/README.md の手順で先に起動してください。",
    );
  }
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  resolveKeys();
  process.env.E2E_RUN_ID ||= Date.now().toString(36);

  const baseURL = config.projects[0]?.use.baseURL ?? E2E.baseURL;
  const web = await check("Web", `${baseURL}/login`);
  if (web.status !== 200) throw new Error(`Web ${baseURL}/login returned ${web.status}`);

  const api = await check("API", `${E2E.apiURL}/health`);
  const health = (await api.json()) as { status: string; llm_mode: string; db: string };
  if (health.db !== "ok") throw new Error(`API /health: db=${health.db}`);
  if (health.llm_mode !== "mock") {
    // A8〜A10 の返答内容の検証はモック LLM（決定的な返答）を前提にしている
    console.warn(
      `[e2e] API は LLM_MODE=${health.llm_mode} です。記憶の検証（A9/A10）は mock 前提です`,
    );
  }

  await check("Supabase Auth", `${E2E.supabaseURL}/auth/v1/health`, {
    headers: { apikey: process.env.E2E_SUPABASE_ANON_KEY ?? "" },
  });
  await check("Mailpit", `${E2E.mailpitURL}/api/v1/messages?limit=1`);

  const { sql, closeDb } = await import("./support/db");
  try {
    await sql("select 1");
  } finally {
    await closeDb();
  }
  console.info(`[e2e] run id: ${process.env.E2E_RUN_ID}  web: ${baseURL}  api: ${E2E.apiURL}`);
}
