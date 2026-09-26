import { MISAKI } from "./support/env";
import { expect, test } from "./support/fixtures";

/**
 * 本番ビルドのハイドレーション: 端末が遅い（CPU に負荷がかかっている）状況でページをフルロードしても、
 * React のハイドレーションエラー（#418。サーバーの HTML とクライアントの描画の不一致）が出ないこと。
 *
 * 以前は MainShell の <main> の直下で RSC の未解決の子（lazy）を解決しており、ハイドレーション中に中断 → 再開
 * したときに React が <main> を最初の子（<!--$-->）に対応付けようとして不一致になっていた（ルート全体を
 * クライアントで描画し直す。components/ui/main-shell.tsx の RouteContent）。RSC の到着とハイドレーションが
 * 重なったときだけ起きるので、CPU を 8 倍遅くして繰り返し開く（修正前は /dm で約半数のロードで発生）。
 */

const CPU_SLOWDOWN = 8;
const HYDRATION_ERROR = /Minified React error #4(18|19|22|23|25)|hydrat/i;

test("遅い端末でフルロードしてもハイドレーションエラー（React #418）が出ない", async ({
  page,
  makeUser,
  login,
}, testInfo) => {
  // CPU の制限は端末ごとに変わらないので 1 端末だけで確認する（所要時間を抑える）
  test.skip(testInfo.project.name !== "iphone", "iphone のみ");
  test.setTimeout(240_000);

  const user = await makeUser();
  await login(page, user);
  await expect(page.getByTestId("post-card").first()).toBeVisible();

  const errors: string[] = [];
  let current = "";
  page.on("console", (message) => {
    if (message.type() === "error" && HYDRATION_ERROR.test(message.text())) {
      errors.push(`${current}: ${message.text().slice(0, 200)}`);
    }
  });
  page.on("pageerror", (error) => {
    if (HYDRATION_ERROR.test(error.message))
      errors.push(`${current}: ${error.message.slice(0, 200)}`);
  });

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: CPU_SLOWDOWN });
  try {
    const paths = ["/dm", `/dm/${MISAKI.id}`, "/me", `/c/${MISAKI.handle}`, "/"];
    for (let round = 0; round < 6; round += 1) {
      for (const path of paths) {
        current = path;
        await page.goto(path, { waitUntil: "load" });
        // ハイドレーションが終わるまで待つ（遅延したハイドレーションの不一致もここまでに出る）
        await page.waitForTimeout(700);
      }
    }
  } finally {
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
    await cdp.detach();
  }
  expect(errors, "ハイドレーションエラー").toEqual([]);
});
