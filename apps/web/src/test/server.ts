/**
 * A fake API for component tests: MSW answers the app's real fetch calls, so
 * tests exercise the same request code as production. Every endpoint the shell
 * touches has a sensible default; a test overrides just what it is about with
 * `server.use(...)`, and reads what the app sent from `sent`.
 */
import {
  defaultSettings,
  type ActivityEvent,
  type EmailDetail,
  type EmailListItem,
  type InboxPage,
  type Notification,
  type SessionInfo,
  type Settings,
} from "@commitmail/shared";
import { http, HttpResponse, ws, type JsonBodyType, type WebSocketHandlerConnection } from "msw";
import { setupServer } from "msw/node";

export const owner: SessionInfo = {
  authenticated: true,
  setupRequired: false,
  user: { email: "owner@example.com", displayName: "Test Owner", isAdmin: true },
  csrfToken: "csrf-test-token",
};

export const signedOut: SessionInfo = { authenticated: false, setupRequired: false, user: null, csrfToken: null };

export function email(overrides: Partial<EmailListItem> = {}): EmailListItem {
  return {
    id: 1,
    subject: "Project review on Friday",
    senderName: "Asha Rao",
    senderEmail: "asha@example.com",
    receivedAt: "2026-10-07T09:30:00Z",
    snippet: "Can you send the slides before the review?",
    category: "action_required",
    categoryReason: "asks you to do something",
    categorySource: "rule",
    vipTier: "CRITICAL",
    isRead: false,
    isStarred: false,
    archivedAt: null,
    deletedAt: null,
    hasInvite: false,
    hasAttachments: false,
    commitmentCount: 1,
    tags: [],
    ...overrides,
  } as EmailListItem;
}

export function inbox(items: EmailListItem[]): InboxPage {
  return {
    items,
    total: items.length,
    page: 1,
    pageSize: 25,
    counts: {
      byCategory: { important: 0, action_required: 1, meeting: 1, update: 0, low_priority: 0 },
      unread: items.filter((e) => !e.isRead).length,
    },
  } as InboxPage;
}

export function detail(item: EmailListItem): EmailDetail {
  return {
    ...item,
    body: "Hi — can you send the slides before Friday's review?",
    recipientEmail: "owner@example.com",
    cc: null,
    threadId: null,
    processed: true,
    commitments: [],
    timeline: [],
    repliedAt: null,
    responseMinutes: null,
  };
}

export const notifications: Notification[] = [
  { id: 1, kind: "important_email", title: "Important: Project review", body: "From Asha Rao", severity: "info", link: "/inbox/1", readAt: null, createdAt: "2026-10-07T09:31:00Z" },
  { id: 2, kind: "job_failed", title: "Couldn't analyse an email", body: "The model timed out", severity: "error", link: "/jobs/7", readAt: null, createdAt: "2026-10-07T08:00:00Z" },
];

export const events: ActivityEvent[] = [
  { id: 10, type: "email.received", entityType: "email", entityId: "1", correlationId: "email:1", severity: "info", message: "Email received from Asha Rao", payload: {}, source: "worker", createdAt: new Date().toISOString() },
];

/** Every request the app made, newest last, with its parsed JSON body. */
export const sent: Array<{ method: string; url: URL; body: unknown }> = [];

export function lastRequest(method: string, path: string) {
  return [...sent].reverse().find((r) => r.method === method && r.url.pathname === path);
}

let settings: Settings = defaultSettings();
export const resetState = () => {
  settings = defaultSettings();
  sent.length = 0;
  liveClients.length = 0;
};

const json = (body: JsonBodyType, status = 200) => HttpResponse.json(body, { status });
const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, pageSize: 25 });

export const handlers = [
  http.get("/api/auth/session", () => json(owner as unknown as JsonBodyType)),
  http.get("/api/settings", () => json(settings as unknown as JsonBodyType)),
  http.put("/api/settings/:section", async ({ params, request }) => {
    settings = { ...settings, [params.section as string]: await request.json() };
    return json(settings as unknown as JsonBodyType);
  }),
  http.get("/api/notifications", () => json({ items: notifications, unread: 2 } as unknown as JsonBodyType)),
  http.post("/api/notifications/read-all", () => new HttpResponse(null, { status: 204 })),
  http.post("/api/notifications/:id/read", () => new HttpResponse(null, { status: 204 })),
  http.get("/api/emails", () => json(inbox([email(), email({ id: 2, subject: "Standup moved to 10", category: "meeting", isRead: true, senderName: "Ben" })]) as unknown as JsonBodyType)),
  http.get("/api/emails/:id", ({ params }) => json(detail(email({ id: Number(params.id) })) as unknown as JsonBodyType)),
  http.patch("/api/emails/:id", async ({ params, request }) =>
    json(detail(email({ id: Number(params.id), ...((await request.json()) as object) })) as unknown as JsonBodyType)),
  http.get("/api/tags", () => json([])),
  http.get("/api/views", () => json([])),
  http.get("/api/commitments", () => json(page([]))),
  http.get("/api/calendar", () => json({ items: [] })),
  http.get("/api/calendar/flags", () => json({ items: [] })),
  http.post("/api/calendar/flags/:id/resolve", ({ params }) => json({ id: Number(params.id), status: "resolved" })),
  http.post("/api/calendar/flags/:id/dismiss", ({ params }) => json({ id: Number(params.id), status: "dismissed" })),
  http.post("/api/calendar/scan", () => json({ jobId: 1, queued: true })),
  http.get("/api/calendar/insights", () =>
    json({ from: "2026-10-12", to: "2026-10-25", days: [], busiestDay: null, outsideHours: [], openFlags: { conflicts: 0, duplicates: 0 } })),
  http.get("/api/activity", () => json({ items: events, nextBefore: null } as unknown as JsonBodyType)),
  http.get("/api/jobs/stats", () =>
    json({ byStatus: { queued: 0, running: 0, retrying: 0, succeeded: 4, failed: 0, cancelled: 0 }, queue: { ready: 0, delayed: 0 }, dispatcher: "redis" })),
  http.get("/api/jobs", () => json(page([]))),
  http.get("/api/setup/status", () =>
    json({
      ollama: { running: true, modelPresent: true, model: "qwen2.5:7b", problem: "" },
      mailbox: { address: "owner@example.com", connected: true, viaGmailApi: false },
      google: { signedIn: false, expired: false, email: null, calendar: false, clientConfigured: false },
      complete: true,
      missing: "",
    })),
  http.get("/api/system/health", () => json({ status: "ok", checkedAt: new Date().toISOString(), components: {} })),
];

/** The live socket. Tests push server messages through `liveClients`. */
export const live = ws.link(`ws://${location.host}/ws`);
export const liveClients: Array<WebSocketHandlerConnection["client"]> = [];

export const server = setupServer(
  ...handlers,
  live.addEventListener("connection", ({ client }) => {
    liveClients.push(client);
  }),
);

// Record every request the app makes (bodies are read from a clone).
server.events.on("request:start", async ({ request }) => {
  let body: unknown = undefined;
  if (request.method !== "GET") body = await request.clone().json().catch(() => undefined);
  sent.push({ method: request.method, url: new URL(request.url), body });
});
