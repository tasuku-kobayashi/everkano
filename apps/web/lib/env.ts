import { z } from "zod";

/**
 * 公開環境変数（NEXT_PUBLIC_*）の検証。
 *
 * - Next.js はビルド時に `process.env.NEXT_PUBLIC_XXX` の「静的な参照」だけをクライアントバンドルへ埋め込むため、
 *   readRawPublicEnv() では 1 つずつ明示的に参照している（動的アクセスにしないこと）。
 * - 不正・未設定の場合は分かりやすいメッセージで即座に例外を投げる（fail fast）。
 *   ルートレイアウトで getPublicEnv() を呼んでいるため、設定ミスはビルド/初回表示で必ず検知される。
 * - サーバー専用の値（BUNNY_*）は lib/env.server.ts を使う。
 */

const emptyToUndefined = (value: unknown): unknown =>
  typeof value === "string" && value.trim() === "" ? undefined : value;

const flag = z.preprocess(emptyToUndefined, z.enum(["0", "1"]).default("0"));

const publicEnvSchema = z
  .object({
    NEXT_PUBLIC_SUPABASE_URL: z.url({
      error: "Supabase プロジェクトのURLを設定してください（ローカル: http://127.0.0.1:54321）",
    }),
    NEXT_PUBLIC_SUPABASE_ANON_KEY: z
      .string({ error: "Supabase の anon key（または publishable key）を設定してください" })
      .min(1, { error: "Supabase の anon key（または publishable key）を設定してください" }),
    NEXT_PUBLIC_API_BASE_URL: z.url({
      error: "Python API のベースURLを設定してください（ローカル: http://localhost:8000）",
    }),
    NEXT_PUBLIC_SITE_URL: z.preprocess(emptyToUndefined, z.url().optional()),
    NEXT_PUBLIC_STORAGE_DRIVER: z.preprocess(
      emptyToUndefined,
      z
        .enum(["passthrough", "bunny"], {
          error: "NEXT_PUBLIC_STORAGE_DRIVER は passthrough / bunny のいずれかです",
        })
        .default("passthrough"),
    ),
    NEXT_PUBLIC_CDN_BASE_URL: z.preprocess(emptyToUndefined, z.url().optional()),
    NEXT_PUBLIC_MEDIA_SIGNED: flag,
    NEXT_PUBLIC_ENABLE_SW: flag,
  })
  .superRefine((env, ctx) => {
    if (env.NEXT_PUBLIC_STORAGE_DRIVER === "bunny" && !env.NEXT_PUBLIC_CDN_BASE_URL) {
      ctx.addIssue({
        code: "custom",
        path: ["NEXT_PUBLIC_CDN_BASE_URL"],
        message: "NEXT_PUBLIC_STORAGE_DRIVER=bunny の場合は Bunny.net Pull Zone のURLが必須です",
      });
    }
  });

export type StorageDriver = "passthrough" | "bunny";

export interface PublicEnv {
  supabaseUrl: string;
  supabaseAnonKey: string;
  /** 末尾スラッシュなし */
  apiBaseUrl: string;
  /** 末尾スラッシュなし。未設定なら undefined（ブラウザの origin を使う） */
  siteUrl: string | undefined;
  storageDriver: StorageDriver;
  /** 末尾スラッシュなし。storageDriver=bunny のとき必須 */
  cdnBaseUrl: string | undefined;
  /** BUNNY_TOKEN_AUTH_KEY が設定されている（= /media 経由の署名URLを使う）。next.config.ts で導出 */
  mediaSigned: boolean;
  /** 開発環境でも Service Worker を登録する（NEXT_PUBLIC_ENABLE_SW=1） */
  enableServiceWorker: boolean;
}

export type RawPublicEnv = Partial<Record<keyof z.input<typeof publicEnvSchema>, string>>;

export class EnvValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EnvValidationError";
  }
}

const stripTrailingSlash = (url: string): string => url.replace(/\/+$/, "");

/** 生の環境変数を検証して PublicEnv に変換する（テスト可能な純粋関数）。 */
export function parsePublicEnv(raw: RawPublicEnv): PublicEnv {
  const result = publicEnvSchema.safeParse(raw);
  if (!result.success) {
    const lines = result.error.issues.map((issue) => {
      const key = issue.path.join(".") || "(env)";
      return `  - ${key}: ${issue.message}`;
    });
    throw new EnvValidationError(
      [
        "[everkano] Web の環境変数が不正です。apps/web/.env.local（本番は Vercel の Environment Variables）を確認してください。",
        ...lines,
        "  参考: リポジトリ直下の .env.example",
      ].join("\n"),
    );
  }
  const env = result.data;
  return {
    supabaseUrl: stripTrailingSlash(env.NEXT_PUBLIC_SUPABASE_URL),
    supabaseAnonKey: env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    apiBaseUrl: stripTrailingSlash(env.NEXT_PUBLIC_API_BASE_URL),
    siteUrl: env.NEXT_PUBLIC_SITE_URL ? stripTrailingSlash(env.NEXT_PUBLIC_SITE_URL) : undefined,
    storageDriver: env.NEXT_PUBLIC_STORAGE_DRIVER,
    cdnBaseUrl: env.NEXT_PUBLIC_CDN_BASE_URL
      ? stripTrailingSlash(env.NEXT_PUBLIC_CDN_BASE_URL)
      : undefined,
    mediaSigned: env.NEXT_PUBLIC_MEDIA_SIGNED === "1",
    enableServiceWorker: env.NEXT_PUBLIC_ENABLE_SW === "1",
  };
}

function readRawPublicEnv(): RawPublicEnv {
  return {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
    NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL,
    NEXT_PUBLIC_STORAGE_DRIVER: process.env.NEXT_PUBLIC_STORAGE_DRIVER,
    NEXT_PUBLIC_CDN_BASE_URL: process.env.NEXT_PUBLIC_CDN_BASE_URL,
    NEXT_PUBLIC_MEDIA_SIGNED: process.env.NEXT_PUBLIC_MEDIA_SIGNED,
    NEXT_PUBLIC_ENABLE_SW: process.env.NEXT_PUBLIC_ENABLE_SW,
  };
}

let cached: PublicEnv | undefined;

/** 検証済みの公開環境変数（初回呼び出し時に検証し、以降はキャッシュ）。 */
export function getPublicEnv(): PublicEnv {
  cached ??= parsePublicEnv(readRawPublicEnv());
  return cached;
}
