import "server-only";
import { z } from "zod";

/**
 * サーバー専用の環境変数（クライアントバンドルに含めてはいけない値）。
 * `import "server-only"` により、クライアントコンポーネントから import するとビルドエラーになる。
 */

const serverEnvSchema = z.object({
  /** Bunny.net Pull Zone の Token Authentication セキュリティキー。未設定なら署名URLを使わない */
  BUNNY_TOKEN_AUTH_KEY: z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.string().min(8).optional(),
  ),
  /** 署名URLの有効期限（秒） */
  BUNNY_TOKEN_TTL_SECONDS: z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.coerce.number().int().min(60).max(604_800).default(3600),
  ),
});

export interface ServerEnv {
  bunnyTokenAuthKey: string | undefined;
  bunnyTokenTtlSeconds: number;
}

let cached: ServerEnv | undefined;

export function getServerEnv(): ServerEnv {
  if (cached) return cached;
  const result = serverEnvSchema.safeParse({
    BUNNY_TOKEN_AUTH_KEY: process.env.BUNNY_TOKEN_AUTH_KEY,
    BUNNY_TOKEN_TTL_SECONDS: process.env.BUNNY_TOKEN_TTL_SECONDS,
  });
  if (!result.success) {
    const lines = result.error.issues.map((i) => `  - ${i.path.join(".")}: ${i.message}`);
    throw new Error(
      ["[everkano] サーバー専用の環境変数（BUNNY_*）が不正です。", ...lines].join("\n"),
    );
  }
  cached = {
    bunnyTokenAuthKey: result.data.BUNNY_TOKEN_AUTH_KEY,
    bunnyTokenTtlSeconds: result.data.BUNNY_TOKEN_TTL_SECONDS,
  };
  return cached;
}
