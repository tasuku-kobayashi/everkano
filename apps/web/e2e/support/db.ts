import pg from "pg";
import { E2E } from "./env";

/**
 * 検証用の SQL（audit_logs の確認など）と後片付けに使う Postgres 接続。
 * postgres ロールで接続するため RLS はバイパスされる。テストの前提確認と後片付けにだけ使い、
 * アプリの動作確認は必ずブラウザ / API / supabase-js（ユーザーのトークン）経由で行う。
 */

let pool: pg.Pool | null = null;

function getPool(): pg.Pool {
  pool ??= new pg.Pool({ connectionString: E2E.databaseURL, max: 4 });
  return pool;
}

export async function sql<T extends object = Record<string, unknown>>(
  text: string,
  params: readonly unknown[] = [],
): Promise<T[]> {
  const result = await getPool().query<T>(text, params as unknown[]);
  return result.rows;
}

export async function sqlOne<T extends object = Record<string, unknown>>(
  text: string,
  params: readonly unknown[] = [],
): Promise<T> {
  const rows = await sql<T>(text, params);
  const row = rows[0];
  if (!row) throw new Error(`no row: ${text}`);
  return row;
}

export async function closeDb(): Promise<void> {
  if (pool) {
    const current = pool;
    pool = null;
    await current.end();
  }
}

/**
 * テストユーザーを物理削除する。
 * ADR-0004: ユーザーが書いたコメントがあると auth.users の削除が check 制約で失敗するため、
 * 先にそのユーザーのコメント（返信は parent_comment_id の cascade で消える）を削除する。
 * profiles → conversations / messages / memories / likes は cascade で消える。
 */
export async function deleteUsersById(ids: readonly string[]): Promise<number> {
  if (ids.length === 0) return 0;
  await sql("delete from public.comments where author_user_id = any($1::uuid[])", [ids]);
  const rows = await sql("delete from auth.users where id = any($1::uuid[]) returning id", [ids]);
  return rows.length;
}

/** メールアドレスのパターン（LIKE）に一致するテストユーザーをまとめて削除する */
export async function deleteUsersByEmailPattern(pattern: string): Promise<number> {
  const rows = await sql<{ id: string }>("select id from auth.users where email like $1", [
    pattern,
  ]);
  return deleteUsersById(rows.map((row) => row.id));
}

export async function deleteUsersByEmail(emails: readonly string[]): Promise<number> {
  if (emails.length === 0) return 0;
  const rows = await sql<{ id: string }>(
    "select id from auth.users where lower(email) = any($1::text[])",
    [emails.map((email) => email.toLowerCase())],
  );
  return deleteUsersById(rows.map((row) => row.id));
}
