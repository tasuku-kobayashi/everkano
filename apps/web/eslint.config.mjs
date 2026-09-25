// ESLint flat config（eslint-config-next 15 は eslintrc 形式のため FlatCompat で読み込む）
import { createRequire } from "node:module";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const require = createRequire(import.meta.url);

// pnpm は依存を公開ホイストしないため、eslint-config-next が参照するプラグイン
// （react-hooks / jsx-a11y など）は eslint-config-next 自身の位置から解決させる。
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
  resolvePluginsRelativeTo: dirname(require.resolve("eslint-config-next/package.json")),
});

const config = [
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      ".next-*/**",
      "next-env.d.ts",
      "coverage/**",
      "test-results/**",
      "playwright-report/**",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      // 仕様: next/image は使わず、StorageAdapter + CDN 変換パラメータ付きの <img> を使う
      "@next/next/no-img-element": "off",
      "no-console": ["warn", { allow: ["warn", "error", "info"] }],
    },
  },
  {
    // Service Worker / Node スクリプトはプレーン JS
    files: ["public/**/*.js", "scripts/**/*.mjs"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
      "no-console": "off",
    },
  },
];

export default config;
