import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = fileURLToPath(new URL("./", import.meta.url));

export default defineConfig({
  resolve: {
    alias: [
      { find: /^@\//, replacement: root },
      // `import "server-only"` はテスト環境（node）では空モジュールに置き換える
      {
        find: /^server-only$/,
        replacement: fileURLToPath(new URL("./test/server-only-stub.ts", import.meta.url)),
      },
    ],
  },
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**", ".next-*/**"],
  },
});
