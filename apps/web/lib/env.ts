/**
 * 公開環境変数（NEXT_PUBLIC_*）の検証。
 *
 * - Next.js はビルド時に `process.env.NEXT_PUBLIC_XXX` の「静的な参照」だけをクライアントバンドルへ埋め込むため、
 *   readRawPublicEnv() では 1 つずつ明示的に参照している（動的アクセスにしないこと）。
 * - 不正・未設定の場合は分かりやすいメッセージで即座に例外を投げる（fail fast）。
 *   ルートレイアウトで getPublicEnv() を呼んでいるため、設定ミスはビルド/初回表示で必ず検知される。
 * - このモジュールはルートレイアウト配下のほぼ全画面のクライアントバンドルに入るため、検証ライブラリ（zod）を
 *   使わず手書きで検証している（zod を入れると全画面の初回 JS が約 24KB gzip 増える）。
 *   サーバー専用の値（BUNNY_*）は lib/env.server.ts（サーバー専用なので zod を使ってよい）。
 */

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
  /**
   * ビルド（デプロイ）ごとの ID。next.config.ts が導出する。Service Worker の登録 URL（/sw.js?v=）に付け、
   * デプロイのたびに新しい SW をインストールさせる（public/sw.js）。未設定なら "dev"
   */
  buildId: string;
}

export type PublicEnvKey =
  | "NEXT_PUBLIC_SUPABASE_URL"
  | "NEXT_PUBLIC_SUPABASE_ANON_KEY"
  | "NEXT_PUBLIC_API_BASE_URL"
  | "NEXT_PUBLIC_SITE_URL"
  | "NEXT_PUBLIC_STORAGE_DRIVER"
  | "NEXT_PUBLIC_CDN_BASE_URL"
  | "NEXT_PUBLIC_MEDIA_SIGNED"
  | "NEXT_PUBLIC_ENABLE_SW"
  | "NEXT_PUBLIC_BUILD_ID";

export type RawPublicEnv = Partial<Record<PublicEnvKey, string>>;

export class EnvValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EnvValidationError";
  }
}

const STORAGE_DRIVERS: readonly StorageDriver[] = ["passthrough", "bunny"];

const stripTrailingSlash = (url: string): string => url.replace(/\/+$/, "");

/** 空文字・空白のみは未設定として扱う */
function present(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

/** http(s) の絶対 URL か */
function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname !== "";
  } catch {
    return false;
  }
}

/** 生の環境変数を検証して PublicEnv に変換する（テスト可能な純粋関数）。 */
export function parsePublicEnv(raw: RawPublicEnv): PublicEnv {
  const issues: string[] = [];
  const issue = (key: PublicEnvKey, message: string) => issues.push(`  - ${key}: ${message}`);

  const url = (key: PublicEnvKey, requiredMessage?: string): string | undefined => {
    const value = present(raw[key]);
    if (value === undefined) {
      if (requiredMessage) issue(key, requiredMessage);
      return undefined;
    }
    if (!isHttpUrl(value)) {
      issue(key, requiredMessage ?? "http(s) の URL を設定してください");
      return undefined;
    }
    return stripTrailingSlash(value);
  };

  const flag = (key: PublicEnvKey): boolean => {
    const value = present(raw[key]) ?? "0";
    if (value !== "0" && value !== "1") issue(key, "0 または 1 を設定してください");
    return value === "1";
  };

  const supabaseUrl = url(
    "NEXT_PUBLIC_SUPABASE_URL",
    "Supabase プロジェクトのURLを設定してください（ローカル: http://127.0.0.1:54321）",
  );
  const supabaseAnonKey = present(raw.NEXT_PUBLIC_SUPABASE_ANON_KEY);
  if (!supabaseAnonKey) {
    issue(
      "NEXT_PUBLIC_SUPABASE_ANON_KEY",
      "Supabase の anon key（または publishable key）を設定してください",
    );
  }
  const apiBaseUrl = url(
    "NEXT_PUBLIC_API_BASE_URL",
    "Python API のベースURLを設定してください（ローカル: http://localhost:8000）",
  );
  const siteUrl = url("NEXT_PUBLIC_SITE_URL");

  const driverValue = present(raw.NEXT_PUBLIC_STORAGE_DRIVER) ?? "passthrough";
  const storageDriver = STORAGE_DRIVERS.find((driver) => driver === driverValue);
  if (!storageDriver) {
    issue(
      "NEXT_PUBLIC_STORAGE_DRIVER",
      "NEXT_PUBLIC_STORAGE_DRIVER は passthrough / bunny のいずれかです",
    );
  }
  const cdnBaseUrl = url("NEXT_PUBLIC_CDN_BASE_URL");
  if (storageDriver === "bunny" && present(raw.NEXT_PUBLIC_CDN_BASE_URL) === undefined) {
    issue(
      "NEXT_PUBLIC_CDN_BASE_URL",
      "NEXT_PUBLIC_STORAGE_DRIVER=bunny の場合は Bunny.net Pull Zone のURLが必須です",
    );
  }
  const mediaSigned = flag("NEXT_PUBLIC_MEDIA_SIGNED");
  const enableServiceWorker = flag("NEXT_PUBLIC_ENABLE_SW");
  // URL のクエリに入れるため英数字・_・- だけにする（next.config.ts でも同じ正規化をしている）
  const buildId = (present(raw.NEXT_PUBLIC_BUILD_ID) ?? "")
    .replace(/[^A-Za-z0-9_-]/g, "")
    .slice(0, 64);

  if (issues.length > 0 || !supabaseUrl || !supabaseAnonKey || !apiBaseUrl || !storageDriver) {
    throw new EnvValidationError(
      [
        "[everkano] Web の環境変数が不正です。apps/web/.env.local（本番は Vercel の Environment Variables）を確認してください。",
        ...issues,
        "  参考: リポジトリ直下の .env.example",
      ].join("\n"),
    );
  }

  return {
    supabaseUrl,
    supabaseAnonKey,
    apiBaseUrl,
    siteUrl,
    storageDriver,
    cdnBaseUrl,
    mediaSigned,
    enableServiceWorker,
    buildId: buildId || "dev",
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
    NEXT_PUBLIC_BUILD_ID: process.env.NEXT_PUBLIC_BUILD_ID,
  };
}

let cached: PublicEnv | undefined;

/** 検証済みの公開環境変数（初回呼び出し時に検証し、以降はキャッシュ）。 */
export function getPublicEnv(): PublicEnv {
  cached ??= parsePublicEnv(readRawPublicEnv());
  return cached;
}
