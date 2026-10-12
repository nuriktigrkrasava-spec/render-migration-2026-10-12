import { describe, it, expect } from "vitest";
import { createHmac } from "node:crypto";
import { validateInitData } from "@/lib/telegram/initData";

const BOT = "123456:TEST_TOKEN";

function buildInitData(params: Record<string, string>, token: string): string {
  const dataCheckString = Object.keys(params).sort()
    .map((k) => `${k}=${params[k]}`).join("\n");
  const secret = createHmac("sha256", "WebAppData").update(token).digest();
  const hash = createHmac("sha256", secret).update(dataCheckString).digest("hex");
  const usp = new URLSearchParams(params);
  usp.set("hash", hash);
  return usp.toString();
}

const user = JSON.stringify({ id: 42, first_name: "Аня", last_name: "П", username: "anya" });

describe("validateInitData", () => {
  it("accepts a correctly signed initData and returns the user", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, BOT);
    expect(validateInitData(initData, BOT)).toEqual({
      id: "42", firstName: "Аня", lastName: "П", username: "anya",
    });
  });
  it("rejects when hash is wrong", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, BOT);
    const bad = initData.replace(/hash=[a-f0-9]+/, "hash=deadbeef");
    expect(validateInitData(bad, BOT)).toBeNull();
  });
  it("rejects when signed with a different token", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, "other:TOKEN");
    expect(validateInitData(initData, BOT)).toBeNull();
  });
  it("rejects when a field was tampered after signing", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, BOT);
    const tampered = initData.replace("auth_date=1700000000", "auth_date=1700000001");
    expect(validateInitData(tampered, BOT)).toBeNull();
  });
  it("returns null on missing hash", () => {
    expect(validateInitData("user=" + encodeURIComponent(user), BOT)).toBeNull();
  });
});
