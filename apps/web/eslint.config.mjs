// ESLint flat config（eslint-config-next 15 は eslintrc 形式のため FlatCompat で読み込む）
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

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
