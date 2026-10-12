import { describe, it, expect } from "vitest";
import { signSession, verifySession } from "@/lib/auth/session";

describe("session token", () => {
  const secret = "test-secret";
  it("round-trips a signed value", () => {
    const token = signSession("teacher", secret);
    expect(verifySession(token, secret)).toBe("teacher");
  });
  it("rejects a tampered payload", () => {
    const token = signSession("teacher", secret);
    const tampered = "admin." + token.split(".")[1];
    expect(verifySession(tampered, secret)).toBeNull();
  });
  it("rejects a wrong secret", () => {
    const token = signSession("teacher", secret);
    expect(verifySession(token, "other-secret")).toBeNull();
  });
  it("rejects malformed token", () => {
    expect(verifySession("garbage", secret)).toBeNull();
  });
});
