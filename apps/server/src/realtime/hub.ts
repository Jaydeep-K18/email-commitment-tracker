/**
 * The WebSocket hub: pushes events, notifications and metrics to open browsers.
 *
 * A connection is only accepted after two checks on the upgrade request:
 *
 * - The session cookie must belong to a live session. Browsers send cookies
 *   with a WebSocket handshake, so this is the same login as the REST API.
 * - The Origin must be one of ours. WebSockets are not covered by the
 *   same-origin policy, so without this a page on any site the user visits
 *   could open a socket with the user's cookie and read the live feed
 *   ("cross-site WebSocket hijacking").
 *
 * Signing out closes that session's sockets immediately.
 */
import type { IncomingMessage, Server } from "node:http";
import type { Duplex } from "node:stream";

import type { ServerMessage } from "@commitmail/shared";
import type { Logger } from "pino";
import { WebSocket, WebSocketServer } from "ws";

import { findSession, SESSION_COOKIE } from "../auth/sessions";
import type { Queryable } from "../db/types";
import { readCookie } from "../http/cookies";

const HEARTBEAT_MS = 30_000;
/** Close code meaning "your session ended" — the app sends you to sign-in. */
export const CLOSE_SESSION_ENDED = 4001;

interface Client {
  sessionId: string;
  userId: number;
  isAdmin: boolean;
  alive: boolean;
}

export class Hub {
  private readonly wss = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 });
  private readonly clients = new Map<WebSocket, Client>();
  private heartbeat: NodeJS.Timeout | null = null;

  constructor(
    private readonly db: Queryable,
    private readonly allowedOrigins: string[],
    private readonly ttlHours: number,
    private readonly log: Logger,
  ) {}

  attach(server: Server): void {
    server.on("upgrade", (req, socket, head) => {
      void this.upgrade(req, socket, head);
    });
    this.heartbeat = setInterval(() => this.checkAlive(), HEARTBEAT_MS);
    this.heartbeat.unref();
  }

  get size(): number {
    return this.clients.size;
  }

  /**
   * Send to one user's open tabs; with no user (the deployment's own events),
   * to the admins'. Never to everyone: every message is someone's data.
   */
  sendTo(userId: number | null, message: ServerMessage): void {
    const data = JSON.stringify(message);
    for (const [socket, client] of this.clients) {
      const recipient = userId === null ? client.isAdmin : client.userId === userId;
      if (recipient && socket.readyState === WebSocket.OPEN) socket.send(data);
    }
  }

  closeSession(sessionId: string): void {
    for (const [socket, client] of this.clients) {
      if (client.sessionId === sessionId) socket.close(CLOSE_SESSION_ENDED, "signed out");
    }
  }

  async close(): Promise<void> {
    if (this.heartbeat) clearInterval(this.heartbeat);
    for (const socket of this.clients.keys()) socket.terminate();
    await new Promise<void>((resolve) => this.wss.close(() => resolve()));
  }

  private async upgrade(req: IncomingMessage, socket: Duplex, head: Buffer): Promise<void> {
    const reject = (status: number, reason: string) => {
      socket.write(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\n\r\n`);
      socket.destroy();
    };
    try {
      const path = (req.url ?? "").split("?")[0];
      if (path !== "/ws") return reject(404, "Not Found");

      const origin = req.headers.origin;
      if (!origin || !this.allowedOrigins.includes(origin)) return reject(403, "Forbidden");

      const token = readCookie(req, SESSION_COOKIE);
      const session = token ? await findSession(this.db, token, this.ttlHours) : null;
      if (!session) return reject(401, "Unauthorized");

      this.wss.handleUpgrade(req, socket, head, (ws) => {
        this.clients.set(ws, { sessionId: session.id, userId: session.user.id, isAdmin: session.user.isAdmin, alive: true });
        ws.on("pong", () => {
          const client = this.clients.get(ws);
          if (client) client.alive = true;
        });
        ws.on("close", () => this.clients.delete(ws));
        ws.on("error", (error) => this.log.debug({ err: error }, "websocket error"));
        // The browser has nothing to tell us over this channel; actions go
        // through the REST API, where they are validated and CSRF-checked.
        ws.on("message", () => undefined);
        ws.send(JSON.stringify({ type: "hello", serverTime: new Date().toISOString() } satisfies ServerMessage));
      });
    } catch (error) {
      this.log.warn({ err: error }, "websocket upgrade failed");
      reject(500, "Internal Server Error");
    }
  }

  private checkAlive(): void {
    for (const [socket, client] of this.clients) {
      if (!client.alive) {
        socket.terminate();
        this.clients.delete(socket);
        continue;
      }
      client.alive = false;
      socket.ping();
    }
  }
}
