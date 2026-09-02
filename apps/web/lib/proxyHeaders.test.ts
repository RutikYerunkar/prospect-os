import { describe, expect, it } from "vitest";
import { buildProxyRequestHeaders, buildProxyResponseHeaders, HOP_BY_HOP_HEADERS } from "@/lib/proxyHeaders";

describe("buildProxyRequestHeaders", () => {
  it("forwards only the allow-listed browser -> API headers", () => {
    const source = new Headers({
      "content-type": "application/json",
      accept: "application/json",
      cookie: "groundwork_operator_session=abc123",
      origin: "https://groundwork-web-febu.onrender.com",
      // must never be forwarded: it would make the upstream fetch address
      // the browser's Next.js host instead of the real API origin
      host: "groundwork-web-febu.onrender.com",
      "user-agent": "test-agent",
      "x-forwarded-for": "203.0.113.9",
    });

    const out = buildProxyRequestHeaders(source);

    expect(out.get("content-type")).toBe("application/json");
    expect(out.get("accept")).toBe("application/json");
    expect(out.get("cookie")).toBe("groundwork_operator_session=abc123");
    expect(out.get("origin")).toBe("https://groundwork-web-febu.onrender.com");
    expect(out.get("host")).toBeNull();
    expect(out.get("user-agent")).toBeNull();
    expect(out.get("x-forwarded-for")).toBeNull();
  });

  it("omits a header entirely when the browser didn't send it", () => {
    const out = buildProxyRequestHeaders(new Headers());
    expect([...out.keys()]).toEqual([]);
  });
});

describe("buildProxyResponseHeaders", () => {
  it("strips every hop-by-hop header case-insensitively", () => {
    const source = new Headers();
    source.set("Content-Type", "application/json");
    for (const name of HOP_BY_HOP_HEADERS) {
      source.set(name.toUpperCase(), "irrelevant-value");
    }

    const out = buildProxyResponseHeaders(source);

    expect(out.get("content-type")).toBe("application/json");
    for (const name of HOP_BY_HOP_HEADERS) {
      expect(out.has(name)).toBe(false);
    }
  });

  it("strips content-encoding/content-length (already-decoded, re-wrapped body)", () => {
    const source = new Headers({
      "content-type": "application/json",
      "content-encoding": "gzip",
      "content-length": "1234",
    });

    const out = buildProxyResponseHeaders(source);

    expect(out.get("content-type")).toBe("application/json");
    expect(out.has("content-encoding")).toBe(false);
    expect(out.has("content-length")).toBe(false);
  });

  it("preserves an ordinary response header like Content-Type/Cache-Control", () => {
    const source = new Headers({
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      "x-request-id": "req-1",
    });

    const out = buildProxyResponseHeaders(source);

    expect(out.get("content-type")).toBe("text/event-stream");
    expect(out.get("cache-control")).toBe("no-cache");
    expect(out.get("x-request-id")).toBe("req-1");
  });

  it("relays every Set-Cookie value as a separate cookie, never comma-joined", () => {
    const source = new Headers();
    source.append("set-cookie", "groundwork_operator_session=abc123; Path=/; HttpOnly; Secure; SameSite=Lax");
    source.append("set-cookie", "second=1; Path=/");

    const out = buildProxyResponseHeaders(source);

    const cookies = out.getSetCookie();
    expect(cookies).toHaveLength(2);
    expect(cookies[0]).toContain("groundwork_operator_session=abc123");
    expect(cookies[1]).toContain("second=1");
  });
});
