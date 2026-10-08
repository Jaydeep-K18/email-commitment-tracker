/**
 * Talking to the Python worker.
 *
 * Two kinds of call. `internal()` hits the worker's private /internal API with
 * the shared token, for setup steps only it can perform. `forward()` relays a
 * request untouched — the Gmail panel's endpoints and the .ics feed — so the
 * browser and calendar apps only ever need to know one address: this server's.
 */
import type { Request, Response } from "express";

import { AppError } from "../http/errors";

export interface WorkerClient {
  internal<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown, timeoutMs?: number): Promise<T>;
  forward(req: Request, res: Response, targetPath: string): Promise<void>;
  ping(): Promise<{ ok: boolean; latencyMs: number | null }>;
}

/**
 * Request headers worth relaying; everything else (cookies!) stays here. The
 * two access-control ones carry the Gmail panel's CORS preflight: its token
 * header makes every POST preflighted, and the worker owns that CORS policy.
 */
const FORWARDED_REQUEST_HEADERS = [
  "content-type",
  "accept",
  "x-tracker-token",
  "origin",
  "if-none-match",
  "access-control-request-method",
  "access-control-request-headers",
];
/** Hop-by-hop headers that must not be copied onto our own response. */
const HOP_BY_HOP = new Set(["connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"]);

export function createWorkerClient(baseUrl: string, internalToken: string | undefined): WorkerClient {
  const base = baseUrl.replace(/\/$/, "");

  return {
    async internal<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown, timeoutMs = 15_000) {
      if (!internalToken) {
        throw new AppError(503, "worker_not_configured", "INTERNAL_API_TOKEN is not set, so setup actions are unavailable.");
      }
      let response: globalThis.Response;
      try {
        response = await fetch(`${base}/internal${path}`, {
          method,
          headers: { "Content-Type": "application/json", "X-Internal-Token": internalToken },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: AbortSignal.timeout(timeoutMs),
        });
      } catch (error) {
        const timedOut = (error as Error).name === "TimeoutError";
        throw new AppError(
          timedOut ? 504 : 503,
          "worker_unavailable",
          timedOut
            ? "The worker took too long to answer."
            : "The background worker is not running. Start it with: python -m src.jobs.worker",
        );
      }
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) {
        throw new AppError(response.status === 401 ? 502 : response.status, "worker_error",
          payload.detail ?? `The worker answered ${response.status}.`);
      }
      return payload as T;
    },

    async forward(req, res, targetPath) {
      const query = req.originalUrl.includes("?") ? req.originalUrl.slice(req.originalUrl.indexOf("?")) : "";
      const headers: Record<string, string> = {};
      for (const name of FORWARDED_REQUEST_HEADERS) {
        const value = req.headers[name];
        if (typeof value === "string") headers[name] = value;
      }
      const hasBody = !["GET", "HEAD", "OPTIONS"].includes(req.method);
      let upstream: globalThis.Response;
      try {
        upstream = await fetch(`${base}${targetPath}${query}`, {
          method: req.method,
          headers,
          body: hasBody ? JSON.stringify(req.body ?? {}) : undefined,
          signal: AbortSignal.timeout(300_000),   // the panel's analyze waits on the model
        });
      } catch {
        res.status(503).json({ error: { code: "worker_unavailable", message: "The background worker is not running." } });
        return;
      }
      res.status(upstream.status);
      upstream.headers.forEach((value, name) => {
        if (!HOP_BY_HOP.has(name)) res.setHeader(name, value);
      });
      res.send(Buffer.from(await upstream.arrayBuffer()));
    },

    async ping() {
      const started = performance.now();
      try {
        const response = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2_000) });
        return { ok: response.ok, latencyMs: Math.round(performance.now() - started) };
      } catch {
        return { ok: false, latencyMs: null };
      }
    },
  };
}
