/**
 * Integration tests for the same-origin API proxy (Checkpoint I2). The
 * "upstream API" here is a plain `node:http` server bound to 127.0.0.1 —
 * never the real FastAPI app, never OpenAI/Tavily. `GROUNDWORK_API_ORIGIN`
 * is pointed at that loopback server for the duration of each test, so this
 * suite makes zero real network calls and triggers zero provider spend.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { DELETE, GET, POST } from "./route";

type FakeHandler = (req: IncomingMessage, res: ServerResponse) => void;

let server: Server;
let origin: string;
let handler: FakeHandler = (_req, res) => {
  res.writeHead(404, { "content-type": "application/json" });
  res.end(JSON.stringify({ detail: "no handler configured for this test" }));
};

beforeEach(async () => {
  server = createServer((req, res) => handler(req, res));
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  origin = `http://127.0.0.1:${port}`;
  process.env.GROUNDWORK_API_ORIGIN = origin;
});

afterEach(async () => {
  delete process.env.GROUNDWORK_API_ORIGIN;
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

function browserRequest(path: string, init?: ConstructorParameters<typeof NextRequest>[1]): NextRequest {
  return new NextRequest(`https://groundwork-web-febu.onrender.com${path}`, init);
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf-8");
}

describe("api proxy — no provider calls", () => {
  it("only ever targets the loopback fake upstream, never a real host", () => {
    expect(origin.startsWith("http://127.0.0.1:")).toBe(true);
    expect(process.env.OPENAI_API_KEY).toBeUndefined();
    expect(process.env.TAVILY_API_KEY).toBeUndefined();
  });
});

describe("api proxy — GET", () => {
  it("forwards a normal GET and preserves status/body", async () => {
    handler = (req, res) => {
      expect(req.method).toBe("GET");
      expect(req.url).toBe("/api/health");
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "ok", mode: "demo" }));
    };

    const res = await GET(browserRequest("/api/health"));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok", mode: "demo" });
  });

  it("preserves query strings exactly", async () => {
    let seenUrl = "";
    handler = (req, res) => {
      seenUrl = req.url ?? "";
      res.writeHead(200, { "content-type": "application/json" });
      res.end("[]");
    };

    await GET(browserRequest("/api/runs/run-1/events?after_seq=42&x=y"));

    expect(seenUrl).toBe("/api/runs/run-1/events?after_seq=42&x=y");
  });

  it("forwards the Cookie header sent by the browser to the upstream", async () => {
    let seenCookie: string | undefined;
    handler = (req, res) => {
      seenCookie = req.headers.cookie;
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
    };

    const res = await GET(
      browserRequest("/api/runs/run-1", {
        headers: { cookie: "groundwork_operator_session=abc123" },
      }),
    );
    await res.text();

    expect(seenCookie).toBe("groundwork_operator_session=abc123");
  });

  it("preserves an upstream error status and body verbatim", async () => {
    handler = (_req, res) => {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ type: "about:blank", title: "Not Found", detail: "no run with id 'x'", status: 404 }));
    };

    const res = await GET(browserRequest("/api/runs/x"));

    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ detail: "no run with id 'x'" });
  });

  it("strips hop-by-hop headers but keeps ordinary ones", async () => {
    handler = (_req, res) => {
      res.writeHead(200, {
        "content-type": "application/json",
        connection: "keep-alive",
        "keep-alive": "timeout=5",
        "x-request-id": "req-123",
      });
      res.end("{}");
    };

    const res = await GET(browserRequest("/api/health"));
    await res.text();

    expect(res.headers.get("connection")).toBeNull();
    expect(res.headers.get("keep-alive")).toBeNull();
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(res.headers.get("x-request-id")).toBe("req-123");
  });
});

describe("api proxy — POST", () => {
  it("forwards the request body and method to the upstream", async () => {
    let seenMethod = "";
    let seenBody = "";
    handler = async (req, res) => {
      seenMethod = req.method ?? "";
      seenBody = await readBody(req);
      res.writeHead(201, { "content-type": "application/json" });
      res.end(JSON.stringify({ echoed: JSON.parse(seenBody) }));
    };

    const res = await POST(
      browserRequest("/api/plays", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ objective: "find prospects" }),
      }),
    );

    expect(seenMethod).toBe("POST");
    expect(JSON.parse(seenBody)).toEqual({ objective: "find prospects" });
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ echoed: { objective: "find prospects" } });
  });

  it("returns a single upstream Set-Cookie to the browser", async () => {
    handler = (_req, res) => {
      res.writeHead(200, {
        "content-type": "application/json",
        "set-cookie": "groundwork_operator_session=abc123; Path=/; HttpOnly; Secure; SameSite=Lax",
      });
      res.end("{}");
    };

    const res = await POST(
      browserRequest("/api/operator/session", {
        method: "POST",
        headers: { "content-type": "application/json", origin: "https://groundwork-web-febu.onrender.com" },
        body: JSON.stringify({ passphrase: "x" }),
      }),
    );
    await res.text();

    const cookies = res.headers.getSetCookie();
    expect(cookies).toHaveLength(1);
    expect(cookies[0]).toContain("groundwork_operator_session=abc123");
  });

  it("returns every upstream Set-Cookie as a distinct cookie, not comma-joined", async () => {
    handler = (_req, res) => {
      res.writeHead(200, {
        "content-type": "application/json",
        "set-cookie": ["first=1; Path=/", "second=2; Path=/"],
      });
      res.end("{}");
    };

    const res = await POST(browserRequest("/api/operator/session", { method: "POST", body: "{}" }));
    await res.text();

    const cookies = res.headers.getSetCookie();
    expect(cookies).toHaveLength(2);
    expect(cookies).toContain("first=1; Path=/");
    expect(cookies).toContain("second=2; Path=/");
  });

  it("forwards the Origin header (the CSRF signal require_allowed_origin checks)", async () => {
    let seenOrigin: string | undefined;
    handler = (req, res) => {
      seenOrigin = req.headers.origin;
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
    };

    const res = await POST(
      browserRequest("/api/operator/session", {
        method: "POST",
        headers: { origin: "https://groundwork-web-febu.onrender.com" },
        body: "{}",
      }),
    );
    await res.text();

    expect(seenOrigin).toBe("https://groundwork-web-febu.onrender.com");
  });
});

describe("api proxy — DELETE", () => {
  it("forwards a bodyless DELETE and preserves the response", async () => {
    let seenMethod = "";
    handler = (req, res) => {
      seenMethod = req.method ?? "";
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "ok" }));
    };

    const res = await DELETE(browserRequest("/api/operator/session", { method: "DELETE" }));

    expect(seenMethod).toBe("DELETE");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
  });
});

describe("api proxy — SSE streaming", () => {
  it("streams the response as it arrives instead of buffering it in memory", async () => {
    handler = (_req, res) => {
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });
      res.write("id: 1\nevent: run.started\ndata: {}\n\n");
      setTimeout(() => {
        res.write("id: 2\nevent: run.completed\ndata: {}\n\n");
        res.end();
      }, 200);
    };

    const started = Date.now();
    const res = await GET(browserRequest("/api/runs/run-1/events?after_seq=0"));
    const elapsedUntilHeaders = Date.now() - started;

    // The route handler must resolve as soon as upstream RESPONSE HEADERS
    // arrive, not once the (still-open, still-writing) body finishes —
    // proof it isn't awaiting `.text()`/`.json()` on the upstream response.
    expect(elapsedUntilHeaders).toBeLessThan(100);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    // hop-by-hop, stripped even though this handler set it explicitly
    expect(res.headers.get("connection")).toBeNull();

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();

    const firstReadStarted = Date.now();
    const { value: firstChunk, done: firstDone } = await reader.read();
    expect(firstDone).toBe(false);
    expect(decoder.decode(firstChunk)).toContain("run.started");
    // the first chunk must arrive well before the upstream's 200ms delay —
    // otherwise the proxy is waiting for the full response, not streaming
    expect(Date.now() - firstReadStarted).toBeLessThan(100);

    const { value: secondChunk, done: secondDone } = await reader.read();
    expect(secondDone).toBe(false);
    expect(decoder.decode(secondChunk)).toContain("run.completed");

    const { done: finalDone } = await reader.read();
    expect(finalDone).toBe(true);
  });
});

describe("api proxy — upstream unreachable", () => {
  it("returns 502 instead of throwing when the API can't be reached", async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    // nothing listens on this port anymore
    process.env.GROUNDWORK_API_ORIGIN = origin;

    const res = await GET(browserRequest("/api/health"));

    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.detail).toMatch(/could not reach/i);

    // afterEach still tries server.close(); recreate a stub so it can.
    server = createServer((req, res2) => res2.end());
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  });
});
