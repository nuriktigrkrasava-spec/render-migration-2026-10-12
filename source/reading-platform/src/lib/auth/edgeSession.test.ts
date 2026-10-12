import { describe, it, expect } from "vitest";
import { signSession } from "@/lib/auth/session";
import { verifySessionEdge } from "@/lib/auth/edgeSession";

describe("edge session token", () => {
  const secret = "test-secret";
  it("round-trips a signed value", async () => {
    const token = signSession("teacher", secret);
    expect(await verifySessionEdge(token, secret)).toBe("teacher");
  });
  it("rejects a tampered payload", async () => {
    const token = signSession("teacher", secret);
    const tampered = "admin." + token.split(".")[1];
    expect(await verifySessionEdge(tampered, secret)).toBeNull();
  });
  it("rejects a wrong secret", async () => {
    const token = signSession("teacher", secret);
    expect(await verifySessionEdge(token, "other-secret")).toBeNull();
  });
  it("rejects malformed token", async () => {
    expect(await verifySessionEdge("garbage", secret)).toBeNull();
  });
});
