/**
 * The relay to the worker, against a real HTTP server standing in for it:
 * what crosses (the panel's token, its CORS preflight) and what must not
 * (the user's session cookie).
 */
import { createServer, type IncomingHttpHeaders, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import express from "express";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createWorkerClient } from "../src/worker/client";

let upstream: Server;
let received: { method: string; url: string; headers: IncomingHttpHeaders; body: string } | null = null;
let base = "";

beforeAll(async () => {
  upstream = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      received = { method: req.method!, url: req.url!, headers: req.headers, body };
      // Answer the way the worker's CORS middleware does.
      if (req.method === "OPTIONS" && req.headers["access-control-request-method"]) {
        res.writeHead(200, {
          "access-control-allow-origin": String(req.headers.origin),
          "access-control-allow-methods": "GET, POST, OPTIONS",
          "access-control-allow-headers": "Content-Type, X-Tracker-Token",
        });
        res.end();
        return;
      }
      res.writeHead(200, { "content-type": "application/json", "access-control-allow-origin": String(req.headers.origin ?? "") });
      res.end(JSON.stringify({ open_commitments: 3 }));
    });
  });
  await new Promise<void>((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`;
});

afterAll(() => new Promise<void>((resolve) => upstream.close(() => resolve())));

function app() {
  const worker = createWorkerClient(base, "internal-token");
  const server = express();
  server.use("/ext", express.json(), (req, res) => worker.forward(req, res, `/api${req.path}`));
  return server;
}

describe("forwarding the Gmail panel to the worker", () => {
  it("relays the CORS preflight so the panel's token header is allowed", async () => {
    const response = await request(app())
      .options("/ext/analyze")
      .set("Origin", "chrome-extension://abcdef")
      .set("Access-Control-Request-Method", "POST")
      .set("Access-Control-Request-Headers", "content-type,x-tracker-token");

    expect(received?.method).toBe("OPTIONS");
    expect(received?.url).toBe("/api/analyze");
    expect(received?.headers["access-control-request-method"]).toBe("POST");
    expect(received?.headers["access-control-request-headers"]).toBe("content-type,x-tracker-token");
    expect(response.status).toBe(200);
    expect(response.headers["access-control-allow-origin"]).toBe("chrome-extension://abcdef");
    expect(response.headers["access-control-allow-headers"]).toMatch(/X-Tracker-Token/i);
  });

  it("passes the token, query and body through, and never the session cookie", async () => {
    const response = await request(app())
      .post("/ext/commitments?thread_id=t1")
      .set("Origin", "chrome-extension://abcdef")
      .set("X-Tracker-Token", "panel-token")
      .set("Cookie", "cm_session=secret-session")
      .send({ subject: "Send the report" });

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ open_commitments: 3 });
    expect(received?.url).toBe("/api/commitments?thread_id=t1");
    expect(received?.headers["x-tracker-token"]).toBe("panel-token");
    expect(received?.headers.cookie).toBeUndefined();
    expect(JSON.parse(received!.body)).toEqual({ subject: "Send the report" });
  });

  it("says plainly when the worker is not running", async () => {
    const worker = createWorkerClient("http://127.0.0.1:9", undefined);
    const server = express();
    server.use("/ext", (req, res) => worker.forward(req, res, `/api${req.path}`));
    const response = await request(server).get("/ext/status");
    expect(response.status).toBe(503);
    expect(response.body.error.code).toBe("worker_unavailable");
  });
});
