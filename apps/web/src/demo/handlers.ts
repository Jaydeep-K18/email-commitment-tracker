/**
 * The demo API: the same routes as the Express server, answered in the
 * browser from the generated mailbox. Filters, sorting, paging and every edit
 * work — on an in-memory copy that resets when the page reloads.
 */
import {
  CATEGORY_RANK,
  type Analytics,
  type Category,
  type Commitment,
  type EmailDetail,
  type EmailListItem,
  type InboxPage,
  type JobStats,
  type RelationshipPerson,
  type SessionInfo,
  type SystemHealth,
} from "@commitmail/shared";
import { http, HttpResponse, ws, type JsonBodyType } from "msw";

import {
  addEvent,
  categoriesInOrder,
  commitments,
  contacts,
  DAY,
  emails,
  events,
  iso,
  jobs,
  metricWindows,
  MINUTE,
  notifications,
  NOW,
  refreshDecision,
  state,
  tags,
  views,
  type DemoEmail,
} from "./data";

const json = (body: unknown, status = 200) => HttpResponse.json(body as JsonBodyType, { status });
const noContent = () => new HttpResponse(null, { status: 204 });
const list = (value: string | null) => value?.split(",").filter(Boolean) ?? [];
const pageOf = <T,>(items: T[], url: URL, size = 25) => {
  const page = Number(url.searchParams.get("page") || 1);
  const pageSize = Number(url.searchParams.get("pageSize") || size);
  return { items: items.slice((page - 1) * pageSize, page * pageSize), total: items.length, page, pageSize };
};
const localDay = (ms: number) => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const wallMs = (deadline: string) => {
  const [date, time = "00:00"] = deadline.split("T");
  const [y, m, d] = date!.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  return new Date(y!, m! - 1, d!, hh, mm).getTime();
};

function session(): SessionInfo {
  return state.signedIn
    ? { authenticated: true, setupRequired: false, user: state.user, csrfToken: "demo" }
    : { authenticated: false, setupRequired: false, user: null, csrfToken: null };
}

function toItem(e: DemoEmail): EmailListItem {
  const { body: _body, template: _template, tagIds, repliedAt: _replied, ...item } = e;
  return { ...item, tags: tags.filter((t) => tagIds.includes(t.id)) };
}

function toDetail(e: DemoEmail): EmailDetail {
  return {
    ...toItem(e),
    body: e.body,
    recipientEmail: state.user.email,
    cc: null,
    threadId: null,
    processed: true,
    commitments: commitments.filter((c) => c.emailId === e.id),
    timeline: events.filter((ev) => ev.correlationId === `email:${e.id}`).slice().reverse(),
    repliedAt: e.repliedAt,
    responseMinutes: e.repliedAt ? Math.round((Date.parse(e.repliedAt) - Date.parse(e.receivedAt!)) / MINUTE) : null,
  };
}

function inbox(url: URL): InboxPage {
  const p = url.searchParams;
  const folder = p.get("folder") || "inbox";
  const q = p.get("q")?.toLowerCase();
  const inFolder = emails.filter((e) =>
    folder === "trash" ? e.deletedAt : folder === "archived" ? e.archivedAt && !e.deletedAt : folder === "all" ? !e.deletedAt : !e.archivedAt && !e.deletedAt,
  );
  const searched = q ? inFolder.filter((e) => `${e.subject} ${e.senderName} ${e.senderEmail} ${e.body}`.toLowerCase().includes(q)) : inFolder;
  const categories = list(p.get("category"));
  const tiers = list(p.get("tier"));
  const tagIds = list(p.get("tag")).map(Number);
  const filtered = searched.filter(
    (e) =>
      (!categories.length || categories.includes(e.category!)) &&
      (!tiers.length || tiers.includes(e.vipTier ?? "")) &&
      (!tagIds.length || tagIds.some((id) => e.tagIds.includes(id))) &&
      (p.get("unread") !== "true" || !e.isRead) &&
      (p.get("starred") !== "true" || e.isStarred) &&
      (p.get("hasCommitments") !== "true" || e.commitmentCount > 0),
  );
  const sort = p.get("sort") || "received";
  const dir = p.get("dir") === "asc" ? 1 : -1;
  const key = (e: DemoEmail): string | number =>
    sort === "sender" ? (e.senderName ?? "").toLowerCase()
      : sort === "subject" ? (e.subject ?? "").toLowerCase()
        : sort === "category" || sort === "priority" ? -CATEGORY_RANK[e.category!] * 1e13 + Date.parse(e.receivedAt!)
          : Date.parse(e.receivedAt!);
  const sorted = filtered.slice().sort((a, b) => (key(a) > key(b) ? dir : key(a) < key(b) ? -dir : 0));
  const byCategory = Object.fromEntries(categoriesInOrder.map((c) => [c, searched.filter((e) => e.category === c).length])) as Record<Category, number>;
  const page = pageOf(sorted, url);
  return { ...page, items: page.items.map(toItem), counts: { byCategory, unread: searched.filter((e) => !e.isRead).length } };
}

function listCommitments(url: URL) {
  const p = url.searchParams;
  const view = p.get("view") || "all";
  const now = Date.now();
  const open = (c: Commitment) => c.status === "pending" || c.status === "overdue";
  let rows = commitments.filter((c) =>
    view === "review" ? c.decision.awaitingApproval
      : view === "calendar" ? c.decision.shouldSync
        : view === "upcoming" ? open(c) && c.deadline && wallMs(c.deadline) >= now
          : view === "overdue" ? open(c) && c.deadline && wallMs(c.deadline) < now
            : c.status !== "superseded",
  );
  const statuses = list(p.get("status"));
  const tiers = list(p.get("tier"));
  const types = list(p.get("type"));
  const q = p.get("q")?.toLowerCase();
  rows = rows.filter(
    (c) =>
      (!statuses.length || statuses.includes(c.status)) &&
      (!tiers.length || tiers.includes(c.vipTier ?? "")) &&
      (!types.length || types.includes(c.type)) &&
      (!q || `${c.subject} ${c.counterpartyName} ${c.source.subject}`.toLowerCase().includes(q)),
  );
  rows.sort((a, b) =>
    view === "all" ? Date.parse(b.createdAt) - Date.parse(a.createdAt) : (a.deadline ? wallMs(a.deadline) : Infinity) - (b.deadline ? wallMs(b.deadline) : Infinity),
  );
  return pageOf(rows, url);
}

function relationships(): RelationshipPerson[] {
  const people = new Map<string, RelationshipPerson>();
  for (const c of commitments.filter((x) => x.status === "pending" || x.status === "overdue")) {
    const key = c.counterpartyEmail ?? c.counterpartyName ?? "unknown";
    const person = people.get(key) ?? { key, label: c.counterpartyName ?? key, tier: c.vipTier ?? "untiered", youOwe: 0, theyOwe: 0, commitments: [] };
    const direction = ["deadline_from_others", "question_pending"].includes(c.type) ? "they_owe" : "you_owe";
    person[direction === "you_owe" ? "youOwe" : "theyOwe"] += 1;
    person.commitments.push({ id: c.id, emailId: c.emailId, type: c.type, subject: c.subject, deadline: c.deadline, status: c.status, direction });
    people.set(key, person);
  }
  return [...people.values()];
}

function analytics(url: URL): Analytics {
  const days = { "7d": 7, "30d": 30, "90d": 90 }[url.searchParams.get("range") ?? "30d"] ?? 30;
  const start = new Date(NOW - (days - 1) * DAY);
  start.setHours(0, 0, 0, 0);
  const inRange = emails.filter((e) => Date.parse(e.receivedAt!) >= start.getTime());
  const previous = emails.filter((e) => Date.parse(e.receivedAt!) >= start.getTime() - days * DAY && Date.parse(e.receivedAt!) < start.getTime());

  const volume = new Map<string, Analytics["volume"][number]>();
  for (let i = 0; i < days; i++) {
    const date = localDay(start.getTime() + i * DAY + 12 * 60 * MINUTE);
    volume.set(date, { date, total: 0, ...Object.fromEntries(categoriesInOrder.map((c) => [c, 0])) });
  }
  for (const e of inRange) {
    const row = volume.get(localDay(Date.parse(e.receivedAt!)));
    if (!row) continue;
    row.total += 1;
    row[e.category!] = (row[e.category!] ?? 0) + 1;
  }
  const byCategory = Object.fromEntries(categoriesInOrder.map((c) => [c, inRange.filter((e) => e.category === c).length])) as Record<Category, number>;
  const byTier: Record<string, number> = {};
  for (const e of inRange) byTier[e.vipTier ?? "UNTIERED"] = (byTier[e.vipTier ?? "UNTIERED"] ?? 0) + 1;

  const senders = new Map<string, { email: string; name: string | null; count: number; lastReceivedAt: string | null }>();
  for (const e of inRange) {
    const s = senders.get(e.senderEmail!) ?? { email: e.senderEmail!, name: e.senderName, count: 0, lastReceivedAt: e.receivedAt };
    s.count += 1;
    senders.set(e.senderEmail!, s);
  }

  const replies = inRange.filter((e) => e.repliedAt).map((e) => (Date.parse(e.repliedAt!) - Date.parse(e.receivedAt!)) / MINUTE).sort((a, b) => a - b);
  const quantile = (q: number) => (replies.length ? Math.round(replies[Math.min(replies.length - 1, Math.floor(q * replies.length))]!) : null);
  const ids = new Set(inRange.map((e) => e.id));
  const found = commitments.filter((c) => ids.has(c.emailId));
  const withCommitments = new Set(found.map((c) => c.emailId)).size;
  const onCalendar = new Set(found.filter((c) => c.calendarSynced).map((c) => c.emailId)).size;
  const rate = inRange.length ? Math.round((onCalendar / inRange.length) * 1000) / 10 : null;
  const action = byCategory.action_required + byCategory.meeting;

  const calendarActivity = [...volume.keys()].map((date) => {
    const created = found.filter((c) => c.calendarSynced && localDay(Date.parse(c.createdAt)) === date).length;
    return { date, created, removed: created && date.endsWith("3") ? 1 : 0, failed: date === localDay(NOW) ? 1 : 0 };
  });

  return {
    range: { from: localDay(start.getTime()), to: localDay(NOW), days, timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    volume: [...volume.values()],
    byCategory,
    byTier,
    topSenders: [...senders.values()].sort((a, b) => b.count - a.count).slice(0, 8),
    actionVsInformational: { action, informational: inRange.length - action },
    responseTime: { replied: replies.length, medianMinutes: quantile(0.5), p90Minutes: quantile(0.9) },
    conversion: { analyzed: inRange.length, withCommitments, onCalendar, rate },
    processing: { succeeded: inRange.length - 2, failed: 7, successRate: inRange.length ? Math.round(((inRange.length - 2) / inRange.length) * 1000) / 10 : null, avgLatencyMs: 3_840, p95LatencyMs: 7_210 },
    calendarActivity,
    trends: {
      volumeChange: previous.length ? Math.round(((inRange.length - previous.length) / previous.length) * 1000) / 10 : null,
      conversionChange: 3.4,
      previousTotal: previous.length,
    },
  };
}

function health(): SystemHealth {
  return {
    status: "ok",
    checkedAt: new Date().toISOString(),
    components: {
      api: { state: "ok" },
      database: { state: "ok", latencyMs: 2, detail: "PostgreSQL 16.4" },
      redis: { state: "ok", latencyMs: 1, meta: { queue: { ready: 0, delayed: 1, processing: 1 } } },
      worker: { state: "ok", detail: "last heartbeat 4s ago" },
      ollama: { state: "ok", latencyMs: 38, detail: "llama3.2:latest" },
      kafka: { state: "unconfigured", detail: "KAFKA_BROKERS not set — events stream from Postgres" },
      flink: { state: "unconfigured", detail: "FLINK_URL not set — metrics computed from the database" },
    },
  };
}

function jobStats(): JobStats {
  const byStatus = { queued: 0, running: 0, retrying: 0, succeeded: 0, failed: 0, cancelled: 0 };
  for (const job of jobs) byStatus[job.status] += 1;
  return { byStatus, queue: { ready: byStatus.queued, delayed: byStatus.retrying, processing: byStatus.running }, dispatcher: "redis" };
}

const emailById = (id: unknown) => emails.find((e) => e.id === Number(id));

function applyEmailPatch(e: DemoEmail, patch: Record<string, unknown>) {
  if ("isRead" in patch) e.isRead = !!patch.isRead;
  if ("isStarred" in patch) e.isStarred = !!patch.isStarred;
  if ("archived" in patch) e.archivedAt = patch.archived ? new Date().toISOString() : null;
  if ("deleted" in patch) e.deletedAt = patch.deleted ? new Date().toISOString() : null;
  if ("category" in patch) {
    e.category = (patch.category as Category | null) ?? e.category;
    e.categorySource = patch.category ? "user" : "rule";
    e.categoryReason = patch.category ? "you chose this category" : e.categoryReason;
  }
}

export const handlers = [
  // --- Auth
  http.get("/api/auth/session", () => json(session())),
  http.post("/api/auth/login", () => {
    state.signedIn = true;
    addEvent("auth.login", "Signed in", Date.now());
    return json(session());
  }),
  http.post("/api/auth/logout", () => {
    state.signedIn = false;
    return noContent();
  }),
  http.post("/api/auth/password", () => json({ ok: true, otherSessionsSignedOut: 0 })),
  http.patch("/api/auth/profile", async ({ request }) => {
    Object.assign(state.user, await request.json());
    return json(session());
  }),

  // --- Inbox
  http.get("/api/emails", ({ request }) => json(inbox(new URL(request.url)))),
  http.get("/api/emails/:id", ({ params }) => {
    const e = emailById(params.id);
    return e ? json(toDetail(e)) : json({ error: { code: "not_found", message: "No such email." } }, 404);
  }),
  http.patch("/api/emails/:id", async ({ params, request }) => {
    const e = emailById(params.id)!;
    applyEmailPatch(e, (await request.json()) as Record<string, unknown>);
    return json(toDetail(e));
  }),
  http.post("/api/emails/bulk", async ({ request }) => {
    const body = (await request.json()) as { action: string; ids: number[]; category?: Category | null; tagId?: number };
    const patches: Record<string, Record<string, unknown>> = {
      markRead: { isRead: true }, markUnread: { isRead: false }, star: { isStarred: true }, unstar: { isStarred: false },
      archive: { archived: true }, unarchive: { archived: false }, delete: { deleted: true }, restore: { deleted: false },
      setCategory: { category: body.category },
    };
    for (const id of body.ids) {
      const e = emailById(id);
      if (!e) continue;
      if (body.action === "addTag" && !e.tagIds.includes(body.tagId!)) e.tagIds.push(body.tagId!);
      else if (body.action === "removeTag") e.tagIds = e.tagIds.filter((t) => t !== body.tagId);
      else if (patches[body.action]) applyEmailPatch(e, patches[body.action]!);
    }
    return json({ updated: body.ids.length });
  }),
  http.get("/api/tags", () => json(tags.map((t) => ({ ...t, emailCount: emails.filter((e) => e.tagIds.includes(t.id)).length })))),
  http.post("/api/tags", async ({ request }) => {
    const body = (await request.json()) as { name: string; color: string };
    const tag = { id: Math.max(0, ...tags.map((t) => t.id)) + 1, name: body.name.trim(), color: body.color };
    tags.push(tag);
    return json(tag, 201);
  }),
  http.delete("/api/tags/:id", ({ params }) => {
    tags.splice(tags.findIndex((t) => t.id === Number(params.id)), 1);
    for (const e of emails) e.tagIds = e.tagIds.filter((t) => t !== Number(params.id));
    return noContent();
  }),
  http.get("/api/views", () => json(views)),
  http.post("/api/views", async ({ request }) => {
    const view = { id: Math.max(0, ...views.map((v) => v.id)) + 1, ...((await request.json()) as object) };
    views.push(view as (typeof views)[number]);
    return json(view, 201);
  }),
  http.delete("/api/views/:id", ({ params }) => {
    views.splice(views.findIndex((v) => v.id === Number(params.id)), 1);
    return noContent();
  }),

  // --- Commitments and calendar
  http.get("/api/commitments", ({ request }) => json(listCommitments(new URL(request.url)))),
  http.patch("/api/commitments/:id", async ({ params, request }) => {
    const c = commitments.find((x) => x.id === Number(params.id))!;
    const patch = (await request.json()) as { status?: Commitment["status"]; approved?: boolean };
    if (patch.status) c.status = patch.status;
    if (patch.approved != null) c.syncApproved = patch.approved;
    refreshDecision(c);
    return json(c);
  }),
  http.post("/api/commitments/bulk", async ({ request }) => {
    const body = (await request.json()) as { ids: number[]; action: string };
    for (const c of commitments.filter((x) => body.ids.includes(x.id))) {
      if (body.action === "approve") c.syncApproved = true;
      if (body.action === "unapprove") c.syncApproved = false;
      if (body.action === "dismiss") c.status = "dismissed";
      if (body.action === "fulfil") c.status = "fulfilled";
      if (body.action === "reopen") c.status = "pending";
      refreshDecision(c);
    }
    return json({ updated: body.ids.length });
  }),
  http.get("/api/calendar", ({ request }) => {
    const url = new URL(request.url);
    const from = url.searchParams.get("from")!;
    const to = url.searchParams.get("to")!;
    return json({ items: commitments.filter((c) => c.deadline && c.deadline.slice(0, 10) >= from && c.deadline.slice(0, 10) <= to && c.status !== "dismissed") });
  }),
  http.get("/api/relationships", () => json({ people: relationships() })),

  // --- Contacts
  http.get("/api/contacts", () => json(contacts)),
  http.post("/api/contacts", async ({ request }) => {
    const body = (await request.json()) as Omit<(typeof contacts)[number], "id" | "createdAt" | "matchingEmails">;
    const contact = { id: Math.max(0, ...contacts.map((c) => c.id)) + 1, createdAt: new Date().toISOString(), matchingEmails: 0, ...body, displayName: body.displayName ?? null };
    contacts.push(contact);
    return json(contact, 201);
  }),
  http.patch("/api/contacts/:id", async ({ params, request }) => {
    const contact = contacts.find((c) => c.id === Number(params.id))!;
    Object.assign(contact, await request.json());
    return json(contact);
  }),
  http.delete("/api/contacts/:id", ({ params }) => {
    contacts.splice(contacts.findIndex((c) => c.id === Number(params.id)), 1);
    return noContent();
  }),

  // --- Activity, jobs, notifications
  http.get("/api/activity", ({ request }) => {
    const p = new URL(request.url).searchParams;
    const types = list(p.get("type"));
    const severities = list(p.get("severity"));
    const before = Number(p.get("before") || Infinity);
    const limit = Number(p.get("limit") || 50);
    const rows = events
      .filter((e) => e.id < before && (!types.length || types.includes(e.type)) && (!severities.length || severities.includes(e.severity)))
      .filter((e) => !p.get("correlation") || e.correlationId === p.get("correlation"))
      .slice()
      .reverse();
    const items = rows.slice(0, limit);
    return json({ items, nextBefore: rows.length > limit ? items.at(-1)!.id : null });
  }),
  http.get("/api/jobs/stats", () => json(jobStats())),
  http.get("/api/jobs", ({ request }) => {
    const url = new URL(request.url);
    const statuses = list(url.searchParams.get("status"));
    const rows = jobs.filter((j) => !statuses.length || statuses.includes(j.status)).slice().sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
    return json(pageOf(rows.map(({ history: _h, ...job }) => job), url));
  }),
  http.get("/api/jobs/:id", ({ params }) => json(jobs.find((j) => j.id === Number(params.id)))),
  http.post("/api/jobs/retry-failed", () => {
    const failed = jobs.filter((j) => j.status === "failed");
    for (const job of failed) retry(job.id);
    return json({ retried: failed.length });
  }),
  http.post("/api/jobs/:id/retry", ({ params }) => json(retry(Number(params.id)))),
  http.post("/api/sync", () => {
    addEvent("job.queued", "Mailbox check queued", Date.now());
    return json({ fetch: { created: true } });
  }),
  http.get("/api/notifications", () => json({ items: notifications, unread: notifications.filter((n) => !n.readAt).length })),
  http.post("/api/notifications/read-all", () => {
    for (const n of notifications) n.readAt ??= new Date().toISOString();
    return noContent();
  }),
  http.post("/api/notifications/:id/read", ({ params }) => {
    const n = notifications.find((x) => x.id === Number(params.id));
    if (n) n.readAt ??= new Date().toISOString();
    return noContent();
  }),

  // --- Settings, privacy, analytics, system, setup
  http.get("/api/settings", () => json(state.settings)),
  http.put("/api/settings/:section", async ({ params, request }) => {
    state.settings = { ...state.settings, [params.section as string]: await request.json() };
    addEvent("settings.updated", `${String(params.section)[0]!.toUpperCase()}${String(params.section).slice(1)} settings changed`, Date.now());
    return json(state.settings);
  }),
  http.get("/api/privacy/export", () => json({ exportedAt: new Date().toISOString(), format: 1, data: { raw_emails: emails.map(toItem), commitments, vip_contacts: contacts, tags } })),
  http.post("/api/privacy/purge", async ({ request }) => {
    const { scope } = (await request.json()) as { scope: string };
    if (scope === "email_bodies") for (const e of emails) e.body = "";
    if (scope === "activity" || scope === "everything") events.length = 0;
    if (scope === "everything") {
      emails.length = 0;
      commitments.length = 0;
      jobs.length = 0;
    }
    return json({ scope });
  }),
  http.get("/api/analytics", ({ request }) => json(analytics(new URL(request.url)))),
  http.get("/api/system/health", () => json(health())),
  http.get("/api/system/metrics", ({ request }) => json({ source: "fallback", windows: metricWindows(Number(new URL(request.url).searchParams.get("minutes") || 60)) })),
  http.get("/api/system/events", () => json({ items: events.filter((e) => e.type.startsWith("system.") || e.severity === "warning" || e.severity === "error").slice().reverse().slice(0, 50) })),
  http.get("/api/setup/status", () =>
    json({
      ollama: { running: true, modelPresent: true, model: "llama3.2:latest", problem: "" },
      mailbox: { address: "demo@commitmail.dev", connected: true, viaGmailApi: false },
      google: { signedIn: true, email: "demo@commitmail.dev", calendar: true, clientConfigured: true },
      complete: true,
      missing: "",
    })),
  http.post("/api/setup/mailbox/test", () => json({ ok: true, message: "Connected — this is the demo, so nothing was really checked." })),
  http.post("/api/setup/mailbox", () => json({ ok: true, message: "Saved (demo only)." })),
  http.post("/api/setup/google/sign-in", () => json({ email: "demo@commitmail.dev" })),
  http.post("/api/setup/google/calendar", () => json({ calendar: true })),
  http.delete("/api/setup/google", () => noContent()),
  http.get("/api/setup/extension-token", () => json({ token: "demo-extension-token-not-real" })),
  http.post("/api/setup/extension-token/rotate", () => json({ token: `demo-extension-token-${Date.now().toString(36)}` })),
  http.get("/calendar.ics", () => new HttpResponse("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//CommitMail demo//EN\r\nEND:VCALENDAR\r\n", { headers: { "Content-Type": "text/calendar" } })),

  // --- The live socket: accept it, and say hello now and then like the real one.
  ws.link(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`).addEventListener("connection", ({ client }) => {
    client.send(JSON.stringify({ type: "hello", serverTime: new Date().toISOString() }));
  }),
];

function retry(id: number) {
  const job = jobs.find((j) => j.id === id)!;
  job.status = "queued";
  job.lastError = null;
  job.updatedAt = iso(Date.now());
  addEvent("job.retried", `Retrying: ${job.description}`, Date.now());
  // The worker picks it up a moment later and, this time, it works.
  setTimeout(() => {
    const started = Date.now();
    job.status = "succeeded";
    job.attempts += 1;
    job.history = [...(job.history ?? []), { attempt: job.attempts, status: "succeeded", error: null, worker: "worker-1", startedAt: iso(started), finishedAt: iso(started + 2_900), durationMs: 2_900 }];
    job.updatedAt = job.finishedAt = iso(started + 2_900);
    addEvent("job.completed", `Succeeded on retry: ${job.description}`, started + 2_900);
  }, 2_500);
  return job;
}
