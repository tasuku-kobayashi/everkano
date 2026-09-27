import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/** Generate synthetic face fixtures (marker faces understood by the mock face engine) — nothing binary in git. */
export default function globalSetup(): void {
  const dir = resolve(import.meta.dirname, ".tmp/fixtures");
  mkdirSync(dir, { recursive: true });
  execFileSync("uv", ["run", "--directory", resolve(import.meta.dirname, "../../api"), "python", "-m", "tools.make_e2e_fixtures", dir], { stdio: "inherit" });
  mkdirSync(process.env.E2E_SCREENSHOTS_DIR ?? resolve(import.meta.dirname, "../../docs/screenshots"), { recursive: true });
}
