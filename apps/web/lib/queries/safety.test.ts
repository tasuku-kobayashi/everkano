import type { SafetyResource } from "@everkano/shared";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { safeExternalUrl, telHref } from "@/components/dm/safety-links";
import { normalizeMessageDTO, normalizeSafetyResourcesResponse } from "@/lib/api/normalize";
import { queryKeys } from "./keys";
import { parseMessageRow } from "./messages";
import { seedSafetyResources } from "./safety";

const resources: SafetyResource[] = [
  { name: "よりそいホットライン", phone: "0120-279-338", hours: "24時間", url: null },
  {
    name: "まもろうよ こころ",
    phone: null,
    hours: null,
    url: "https://www.mhlw.go.jp/mamorouyokokoro/",
  },
];

describe("相談窓口の一覧（E6）", () => {
  it("返答で受け取った窓口はキャッシュに入れる（空なら入れない。GET /safety/resources を待たずに出す）", () => {
    const queryClient = new QueryClient();
    seedSafetyResources(queryClient, []);
    expect(queryClient.getQueryData(queryKeys.safetyResources())).toBeUndefined();
    seedSafetyResources(queryClient, resources);
    expect(queryClient.getQueryData(queryKeys.safetyResources())).toEqual(resources);
    queryClient.clear();
  });

  it("GET /safety/resources の応答を検証する（名前の無い窓口は捨て、空の文字列は null）", () => {
    expect(normalizeSafetyResourcesResponse({ resources })).toEqual({ resources });
    expect(
      normalizeSafetyResourcesResponse({
        resources: [{ name: "" }, "x", { name: "窓口", phone: "", hours: "24時間" }],
      }),
    ).toEqual({ resources: [{ name: "窓口", phone: null, hours: "24時間", url: null }] });
    expect(normalizeSafetyResourcesResponse({})).toBeNull();
    expect(normalizeSafetyResourcesResponse(null)).toBeNull();
  });

  it("安全対応の印（safety_triggered）はキャラの発言だけ。無い古い行・API は false", () => {
    const base = {
      id: "m1",
      conversation_id: "c1",
      body: "話してくれてありがとう",
      created_at: "2026-09-26T12:00:00Z",
    };
    expect(
      parseMessageRow({ ...base, sender_type: "character", safety_triggered: true }),
    ).toMatchObject({
      safety_triggered: true,
    });
    expect(parseMessageRow({ ...base, sender_type: "user", safety_triggered: true })).toMatchObject(
      {
        safety_triggered: false,
      },
    );
    expect(parseMessageRow({ ...base, sender_type: "character" })).toMatchObject({
      safety_triggered: false,
    });
    expect(
      normalizeMessageDTO({ ...base, sender_type: "character", safety_triggered: true }),
    ).toMatchObject({
      safety_triggered: true,
    });
    expect(normalizeMessageDTO({ ...base, sender_type: "character" })?.safety_triggered).toBe(
      false,
    );
  });
});

describe("相談窓口のリンク", () => {
  it("電話番号は数字だけの tel: にする（短すぎれば null）", () => {
    expect(telHref("0120-279-338")).toBe("tel:0120279338");
    expect(telHref(" 0570 783 556 ")).toBe("tel:0570783556");
    expect(telHref("+81-3-1234-5678")).toBe("tel:+81312345678");
    expect(telHref("#7")).toBeNull();
  });

  it("外部リンクは http / https だけ", () => {
    expect(safeExternalUrl("https://www.mhlw.go.jp/mamorouyokokoro/")).toBe(
      "https://www.mhlw.go.jp/mamorouyokokoro/",
    );
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("not a url")).toBeNull();
  });
});
