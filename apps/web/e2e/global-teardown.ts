import { closeDb, deleteUsersByEmailPattern } from "./support/db";
import { EMAIL_DOMAIN, EMAIL_PREFIX, runId } from "./support/env";

/**
 * 後片付けの最終確認: この実行で作ったテストユーザー（メールアドレスに run id を含む）が
 * 残っていれば削除する（テストが途中で落ちてフィクスチャの後片付けが走らなかった場合など）。
 * E2E_KEEP_USERS=1 のときは残す（調査用）。
 */
export default async function globalTeardown(): Promise<void> {
  if (process.env.E2E_KEEP_USERS === "1") return;
  try {
    const removed = await deleteUsersByEmailPattern(`${EMAIL_PREFIX}${runId()}-%@${EMAIL_DOMAIN}`);
    if (removed > 0) console.info(`[e2e] teardown: removed ${removed} leftover test user(s)`);
  } finally {
    await closeDb();
  }
}
