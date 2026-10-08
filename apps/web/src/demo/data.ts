/**
 * The demo mailbox: three months of believable mail, the commitments found in
 * it, and the jobs, events and metrics that processing it would leave behind.
 *
 * Generated from a fixed seed, so every load shows the same data, and relative
 * to the moment the page opened, so "due tomorrow" is always tomorrow.
 */
import {
  CATEGORIES,
  decide,
  defaultSettings,
  type ActivityEvent,
  type CalendarFlag,
  type CalendarInsights,
  type Category,
  type Commitment,
  type CommitmentType,
  type Contact,
  type EmailListItem,
  type EventType,
  type Job,
  type JobAttempt,
  type MetricWindow,
  type Notification,
  type SavedView,
  type Settings,
  type Severity,
  type Tag,
  type Tier,
} from "@commitmail/shared";

// --- Deterministic randomness ----------------------------------------------------------

function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const random = mulberry32(20261008);
const pick = <T,>(items: readonly T[]): T => items[Math.floor(random() * items.length)]!;
const between = (low: number, high: number) => low + Math.floor(random() * (high - low + 1));

const NOW = Date.now();
const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
const iso = (ms: number) => new Date(ms).toISOString();

/** A zone-less wall-clock deadline, `days` from today at `hour`:00. */
function wallClock(days: number, hour: number): string {
  const d = new Date(NOW + days * DAY);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(hour)}:00:00`;
}

// --- People and their rules --------------------------------------------------------

interface Person {
  name: string;
  email: string;
  tier: Tier | null;
}

const PEOPLE: Person[] = [
  { name: "Prof. Asha Rao", email: "asha.rao@northfield.edu", tier: "CRITICAL" },
  { name: "Placement Cell", email: "placements@northfield.edu", tier: "CRITICAL" },
  { name: "Ben Carter", email: "ben@acme.io", tier: "IMPORTANT" },
  { name: "Priya Nair", email: "priya@acme.io", tier: "IMPORTANT" },
  { name: "Rahul Mehta", email: "rahul.mehta@gmail.com", tier: null },
  { name: "Acme HR", email: "people@acme.io", tier: "IMPORTANT" },
  { name: "Lena Fischer", email: "lena@studio-kite.de", tier: "MONITOR" },
  { name: "GitHub", email: "notifications@github.com", tier: "SKIP" },
  { name: "The Weekly Byte", email: "hello@weeklybyte.dev", tier: null },
  { name: "Campus Store", email: "orders@campus-store.com", tier: null },
];

export const contacts: Contact[] = [
  { id: 1, matchType: "exact_email", matchValue: "asha.rao@northfield.edu", tier: "CRITICAL", displayName: "Prof. Asha Rao", createdAt: iso(NOW - 80 * DAY), matchingEmails: 0 },
  { id: 2, matchType: "name_pattern", matchValue: "Placement", tier: "CRITICAL", displayName: "Placement Cell", createdAt: iso(NOW - 80 * DAY), matchingEmails: 0 },
  { id: 3, matchType: "domain", matchValue: "acme.io", tier: "IMPORTANT", displayName: "Acme (internship)", createdAt: iso(NOW - 60 * DAY), matchingEmails: 0 },
  { id: 4, matchType: "exact_email", matchValue: "lena@studio-kite.de", tier: "MONITOR", displayName: "Lena — freelance client", createdAt: iso(NOW - 30 * DAY), matchingEmails: 0 },
  { id: 5, matchType: "domain", matchValue: "github.com", tier: "SKIP", displayName: null, createdAt: iso(NOW - 75 * DAY), matchingEmails: 0 },
];

// --- Mail ---------------------------------------------------------------------------

interface Template {
  subject: string;
  body: string;
  from: string[];
  commitment?: { type: CommitmentType; subject: string; inDays: [number, number]; hour: number; allDay?: boolean; quote: string };
  invite?: boolean;
}

const TEMPLATES: Record<Category, Template[]> = {
  action_required: [
    {
      subject: "Please send the project synopsis by Monday 5pm",
      body: "Hi,\n\nPlease send me the final project synopsis by Monday 5pm so I can sign it off before the committee meets.\n\nThanks,\nAsha",
      from: ["asha.rao@northfield.edu"],
      commitment: { type: "deadline_on_you", subject: "Send the project synopsis", inDays: [1, 6], hour: 17, quote: "send me the final project synopsis by Monday 5pm" },
    },
    {
      subject: "Sign and return the internship offer",
      body: "Hello,\n\nCongratulations again! Please sign and return the attached offer letter by Friday.\n\nBest,\nAcme People Team",
      from: ["people@acme.io"],
      commitment: { type: "deadline_on_you", subject: "Return the signed offer letter", inDays: [2, 9], hour: 18, quote: "sign and return the attached offer letter by Friday" },
    },
    {
      subject: "Can you review my PR before tomorrow's demo?",
      body: "Hey — could you review the auth PR before tomorrow's demo? It's small, promise.\n\nBen",
      from: ["ben@acme.io"],
      commitment: { type: "deadline_on_you", subject: "Review Ben's auth PR", inDays: [0, 2], hour: 10, quote: "could you review the auth PR before tomorrow's demo" },
    },
    {
      subject: "Invoice for the logo work",
      body: "Hi! I've attached the invoice for the logo work. I'll send the final files by Thursday once it's settled.\n\nLena",
      from: ["lena@studio-kite.de"],
      commitment: { type: "deadline_from_others", subject: "Lena sends the final logo files", inDays: [1, 7], hour: 12, quote: "I'll send the final files by Thursday" },
    },
    {
      subject: "Register for the placement drive",
      body: "Dear students,\n\nRegistration for the campus placement drive closes on the 20th. Register on the portal before the deadline.\n\nPlacement Cell",
      from: ["placements@northfield.edu"],
      commitment: { type: "deadline_on_you", subject: "Register for the placement drive", inDays: [3, 12], hour: 23, quote: "Register on the portal before the deadline" },
    },
    {
      subject: "Did you get a chance to look at the budget?",
      body: "Hi, did you get a chance to look at the budget sheet? Let me know what you think.\n\nRahul",
      from: ["rahul.mehta@gmail.com"],
      commitment: { type: "question_pending", subject: "Reply to Rahul about the budget", inDays: [0, 0], hour: 9, quote: "did you get a chance to look at the budget sheet?" },
    },
  ],
  meeting: [
    {
      subject: "Invitation: Design sync",
      body: "Priya Nair has invited you to Design sync.\n\nWe'll walk through the onboarding flow.",
      from: ["priya@acme.io"],
      invite: true,
      commitment: { type: "meeting", subject: "Design sync with Priya", inDays: [1, 8], hour: 15, quote: "invited you to Design sync" },
    },
    {
      subject: "Viva for the final-year project",
      body: "Your project viva is scheduled for Wednesday at 11am in Lab 3. Please bring a printed copy of the report.",
      from: ["asha.rao@northfield.edu"],
      commitment: { type: "meeting", subject: "Project viva, Lab 3", inDays: [4, 14], hour: 11, quote: "Your project viva is scheduled for Wednesday at 11am" },
    },
    {
      subject: "Standup moved to 10:00",
      body: "Heads up — standup is at 10:00 instead of 9:30 tomorrow.",
      from: ["ben@acme.io"],
      commitment: { type: "meeting", subject: "Team standup", inDays: [1, 1], hour: 10, quote: "standup is at 10:00 instead of 9:30 tomorrow" },
    },
    { subject: "Coffee next week?", body: "Free for a coffee next week? Any afternoon works for me.", from: ["rahul.mehta@gmail.com"] },
  ],
  important: [
    { subject: "Your offer letter — Software Engineering Intern", body: "We're delighted to offer you the Software Engineering Intern position.", from: ["people@acme.io"] },
    { subject: "Hall tickets for the end-semester exams", body: "Hall tickets are now available on the student portal.", from: ["placements@northfield.edu"] },
    { subject: "Feedback on your draft report", body: "Good progress. Chapter 3 needs a clearer evaluation section; otherwise this is in good shape.", from: ["asha.rao@northfield.edu"] },
  ],
  update: [
    { subject: "[acme/web] CI passed on main", body: "All checks have passed.", from: ["notifications@github.com"] },
    { subject: "Your order has shipped", body: "Your order is on its way and should arrive in 2–3 days.", from: ["orders@campus-store.com"] },
    { subject: "Weekly engineering update", body: "Shipped: the new billing page. Next: search improvements.", from: ["ben@acme.io"] },
    { subject: "[acme/api] New comment on #412", body: "priya commented: looks good to me.", from: ["notifications@github.com"] },
  ],
  low_priority: [
    { subject: "This week in developer tools", body: "Five tools worth a look this week…", from: ["hello@weeklybyte.dev"] },
    { subject: "Weekend sale — 30% off hoodies", body: "Only this weekend at the campus store.", from: ["orders@campus-store.com"] },
    { subject: "Tips to get the most from your account", body: "Did you know you can…", from: ["hello@weeklybyte.dev"] },
  ],
};

const CATEGORY_WEIGHTS: Array<[Category, number]> = [
  ["update", 30],
  ["low_priority", 26],
  ["action_required", 18],
  ["meeting", 14],
  ["important", 12],
];
function weightedCategory(): Category {
  let roll = random() * 100;
  for (const [category, weight] of CATEGORY_WEIGHTS) {
    if ((roll -= weight) < 0) return category;
  }
  return "update";
}

export interface DemoEmail extends EmailListItem {
  body: string;
  template: Template;
  tagIds: number[];
  repliedAt: string | null;
}

export const tags: Tag[] = [
  { id: 1, name: "thesis", color: "violet" },
  { id: 2, name: "internship", color: "blue" },
  { id: 3, name: "receipts", color: "slate" },
];

const person = (email: string) => PEOPLE.find((p) => p.email === email)!;

function buildEmails(): DemoEmail[] {
  const emails: DemoEmail[] = [];
  let id = 1;
  for (let daysAgo = 90; daysAgo >= 0; daysAgo--) {
    const day = new Date(NOW - daysAgo * DAY);
    const weekend = day.getDay() === 0 || day.getDay() === 6;
    // More mail lately, less at weekends, and a busy stretch three weeks ago.
    const count = Math.max(0, Math.round((weekend ? 1.2 : 2.6) + (90 - daysAgo) / 40 + (daysAgo > 18 && daysAgo < 24 ? 3 : 0) + (random() - 0.5) * 3));
    for (let n = 0; n < count; n++) {
      const received = NOW - daysAgo * DAY - between(daysAgo === 0 ? 5 : 0, daysAgo === 0 ? 8 * 60 : 14 * 60) * MINUTE;
      if (received > NOW) continue;
      const category = weightedCategory();
      const template = pick(TEMPLATES[category]);
      const sender = person(pick(template.from));
      const recent = daysAgo <= 3;
      const tagIds = sender.email.endsWith("northfield.edu") && category !== "low_priority" ? [1] : sender.email === "people@acme.io" ? [2] : sender.email.startsWith("orders@") ? [3] : [];
      emails.push({
        id: id++,
        subject: template.subject,
        senderName: sender.name,
        senderEmail: sender.email,
        receivedAt: iso(received),
        snippet: template.body.replace(/\s+/g, " ").slice(0, 120),
        body: template.body,
        category,
        categoryReason: {
          action_required: "asks you to do something by a date",
          meeting: template.invite ? "contains a calendar invitation" : "proposes a time to meet",
          important: sender.tier === "CRITICAL" ? "from a CRITICAL contact" : "mentions an offer or exam",
          update: "an automated notice or status update",
          low_priority: "a newsletter or promotion",
        }[category],
        categorySource: "rule",
        vipTier: sender.tier,
        isRead: !recent || random() < 0.35,
        isStarred: category === "important" && random() < 0.4,
        archivedAt: daysAgo > 45 && random() < 0.3 ? iso(received + DAY) : null,
        deletedAt: null,
        hasInvite: !!template.invite,
        hasAttachments: /attached|invoice|offer/i.test(template.body),
        commitmentCount: 0,
        tags: [],
        tagIds,
        template,
        repliedAt: category === "action_required" && random() < 0.6 ? iso(received + between(20, 900) * MINUTE) : null,
      });
    }
  }
  return emails.sort((a, b) => Date.parse(b.receivedAt!) - Date.parse(a.receivedAt!));
}

export const emails = buildEmails();

// --- Commitments --------------------------------------------------------------------

function buildCommitments(): Commitment[] {
  const out: Commitment[] = [];
  let id = 1;
  for (const email of emails) {
    const spec = email.template.commitment;
    const age = (NOW - Date.parse(email.receivedAt!)) / DAY;
    if (!spec || age > 21) continue;
    const offset = between(spec.inDays[0], spec.inDays[1]) - Math.floor(age);
    const deadline = spec.type === "question_pending" ? null : wallClock(offset, spec.hour);
    const past = offset < 0;
    const status = past ? (random() < 0.6 ? "fulfilled" : "overdue") : "pending";
    const input = {
      type: spec.type,
      hasDeadline: !!deadline,
      status,
      vipTier: email.vipTier,
      manuallyAdded: false,
      syncApproved: email.vipTier === "MONITOR" && random() < 0.3,
    };
    const decision = decide(input);
    out.push({
      id: id++,
      emailId: email.id,
      type: spec.type,
      subject: spec.subject,
      deadline,
      allDay: !!spec.allDay,
      counterpartyName: email.senderName,
      counterpartyEmail: email.senderEmail,
      direction: ["deadline_from_others", "question_pending"].includes(spec.type) ? "they_owe" : "you_owe",
      evidenceQuote: spec.quote,
      confidence: Math.round((0.78 + random() * 0.2) * 100) / 100,
      vipTier: email.vipTier,
      status,
      manuallyAdded: false,
      syncApproved: input.syncApproved,
      calendarSynced: decision.shouldSync,
      googleEventId: decision.shouldSync ? `ect${(id * 7919).toString(32)}demo` : null,
      createdAt: iso(Date.parse(email.receivedAt!) + 2 * MINUTE),
      decision,
      source: { emailId: email.id, subject: email.subject, senderName: email.senderName, senderEmail: email.senderEmail },
    });
    email.commitmentCount += 1;
  }
  return out;
}

export const commitments = buildCommitments();

/** Recompute the policy after the user approves, fulfils or dismisses. */
export function refreshDecision(c: Commitment): void {
  c.decision = decide({ type: c.type, hasDeadline: !!c.deadline, status: c.status, vipTier: c.vipTier, manuallyAdded: c.manuallyAdded, syncApproved: c.syncApproved });
  c.calendarSynced = c.decision.shouldSync;
}

// --- Calendar flags -------------------------------------------------------------------

/** A wall-clock time moved by `minutes`, still without a zone. */
const shift = (wall: string, minutes: number) => new Date(Date.parse(`${wall}Z`) + minutes * MINUTE).toISOString().slice(0, 19);

/** One of each thing the worker's scan finds: a clash, an invite already on the calendar, a forwarded copy. */
function buildFlags(): CalendarFlag[] {
  const flags: CalendarFlag[] = [];
  const now = new Date(NOW);
  const nowWall = `${wallClock(0, now.getHours()).slice(0, 14)}${String(now.getMinutes()).padStart(2, "0")}:00`;
  const upcoming = commitments.filter((c) => c.status === "pending" && c.deadline && c.deadline > nowWall && !c.allDay && c.decision.shouldSync);
  const base = { status: "open" as const, other: null, external: null, similarity: null, overlap: null, suggestions: [], resolvedAt: null };

  const meetings = upcoming.filter((c) => c.type === "meeting");
  const clash = meetings[0];
  if (clash) {
    const day = clash.deadline!.slice(0, 10);
    const dentist = { start: shift(clash.deadline!, -30), end: shift(clash.deadline!, 30) };
    flags.push({
      ...base, id: 1, kind: "conflict", commitment: clash, createdAt: iso(NOW - 40 * MINUTE),
      external: { id: "demo-dentist", title: "Dentist appointment", ...dentist, allDay: false, link: null },
      overlap: { start: clash.deadline!, end: dentist.end },
      suggestions: [`${day}T14:00:00`, `${day}T16:30:00`, shift(`${day}T${clash.deadline!.slice(11, 19)}`, 24 * 60)]
        .map((start) => ({ start, end: shift(start, 60) })),
    });
  }

  const invited = meetings.find((m) => clash && m.subject !== clash.subject) ?? meetings[1];
  if (invited) {
    flags.push({
      ...base, id: 2, kind: "duplicate", commitment: invited, similarity: 0.67, createdAt: iso(NOW - 35 * MINUTE),
      external: {
        id: "demo-invite", title: invited.subject.split(/ with |,/)[0]!, start: invited.deadline!,
        end: shift(invited.deadline!, 60), allDay: false, link: null,
      },
    });
  }

  const original = upcoming.find((c) => c.type === "deadline_on_you");
  const forwarder = emails.find((e) => e.senderName === "Ben Carter");
  if (original && forwarder) {
    const copy: Commitment = {
      ...original,
      id: Math.max(...commitments.map((c) => c.id)) + 1,
      emailId: forwarder.id,
      deadline: `${original.deadline!.slice(0, 10)}T12:00:00`,
      counterpartyName: forwarder.senderName,
      counterpartyEmail: forwarder.senderEmail,
      source: { emailId: forwarder.id, subject: `Fwd: ${original.source.subject}`, senderName: forwarder.senderName, senderEmail: forwarder.senderEmail },
    };
    commitments.push(copy);
    flags.push({ ...base, id: 3, kind: "duplicate", commitment: copy, other: original, similarity: 1, createdAt: iso(NOW - 30 * MINUTE) });
  }
  return flags;
}

export const calendarFlags = buildFlags();

/** The server's /calendar/insights, over the demo commitments, with default working hours. */
export function calendarInsights(): CalendarInsights {
  const days = Array.from({ length: 14 }, (_, i) => ({ date: wallClock(i, 0).slice(0, 10), deadlines: 0, meetings: 0 }));
  const outsideHours: CalendarInsights["outsideHours"] = [];
  for (const c of commitments) {
    if (!c.deadline || !c.decision.shouldSync || !["pending", "overdue"].includes(c.status)) continue;
    const day = days.find((d) => d.date === c.deadline!.slice(0, 10));
    if (!day) continue;
    if (c.type === "meeting") day.meetings += 1;
    else day.deadlines += 1;
    const time = c.deadline.slice(11, 16);
    const weekday = new Date(`${day.date}T00:00:00Z`).getUTCDay();
    if (!c.allDay && (weekday === 0 || weekday === 6 || time < "09:00" || time > "18:00")) {
      outsideHours.push({ id: c.id, type: c.type, subject: c.subject, deadline: c.deadline });
    }
  }
  const busiest = days.reduce<CalendarInsights["busiestDay"]>((best, d) => {
    const count = d.deadlines + d.meetings;
    return count >= 3 && count > (best?.count ?? 0) ? { date: d.date, count } : best;
  }, null);
  const open = calendarFlags.filter((f) => f.status === "open");
  return {
    from: days[0]!.date,
    to: days[13]!.date,
    days,
    busiestDay: busiest,
    outsideHours,
    openFlags: { conflicts: open.filter((f) => f.kind === "conflict").length, duplicates: open.filter((f) => f.kind === "duplicate").length },
  };
}

// --- Jobs and events ----------------------------------------------------------------

export const jobs: Job[] = [];
export const events: ActivityEvent[] = [];
let eventId = 1;

export function addEvent(type: EventType, message: string, at: number, extra: Partial<ActivityEvent> = {}): ActivityEvent {
  const severity: Severity =
    type.endsWith("failed") || type === "system.error" ? "error" : type === "job.retrying" ? "warning" : type.startsWith("calendar.event_created") || type === "job.retried" ? "success" : "info";
  const event: ActivityEvent = {
    id: eventId++,
    type,
    entityType: type.split(".")[0]!,
    entityId: null,
    correlationId: null,
    severity,
    message,
    payload: {},
    source: type.startsWith("auth") || type.startsWith("settings") ? "server" : "worker",
    createdAt: iso(at),
    ...extra,
  };
  events.push(event);
  return event;
}

function attempt(n: number, status: JobAttempt["status"], startedAt: number, ms: number, error: string | null = null): JobAttempt {
  return { attempt: n, status, error, worker: "worker-1", startedAt: iso(startedAt), finishedAt: status === "running" ? null : iso(startedAt + ms), durationMs: status === "running" ? null : ms };
}

function buildActivity(): void {
  let jobId = 1;
  const addJob = (job: Omit<Job, "id" | "createdAt" | "updatedAt" | "finishedAt" | "payload" | "result" | "maxAttempts"> & { at: number; result?: Job["result"] }) => {
    const last = job.history?.at(-1);
    jobs.push({
      id: jobId++,
      maxAttempts: 5,
      payload: {},
      createdAt: iso(job.at),
      updatedAt: last?.finishedAt ?? last?.startedAt ?? iso(job.at),
      finishedAt: job.status === "succeeded" || job.status === "failed" ? (last?.finishedAt ?? null) : null,
      result: job.result ?? null,
      ...job,
    });
  };

  // The last two days of mail, step by step.
  const recent = emails.filter((e) => NOW - Date.parse(e.receivedAt!) < 2 * DAY).reverse();
  for (const email of recent) {
    const at = Date.parse(email.receivedAt!);
    const correlationId = `email:${email.id}`;
    const ref = { correlationId, entityId: String(email.id) };
    addEvent("email.received", `Email received from ${email.senderName}`, at + MINUTE, ref);
    addEvent("email.classified", `Classified as ${email.category!.replace("_", " ")}: ${email.categoryReason}`, at + MINUTE + 2_000, ref);
    const ms = between(1_800, 7_500);
    addJob({ type: "process_email", description: `Analyse “${email.subject}”`, status: "succeeded", attempts: 1, lastError: null, nextAttemptAt: null, correlationId, at: at + MINUTE, history: [attempt(1, "succeeded", at + MINUTE, ms)], result: { commitments: email.commitmentCount } });
    addEvent("email.analyzed", email.commitmentCount ? `Found ${email.commitmentCount} commitment in “${email.subject}”` : `No commitments in “${email.subject}”`, at + MINUTE + ms, ref);
    for (const c of commitments.filter((x) => x.emailId === email.id)) {
      addEvent("commitment.created", `Commitment: ${c.subject}`, at + MINUTE + ms + 500, { ...ref, entityType: "commitment" });
      if (c.calendarSynced) addEvent("calendar.event_created", `Added to Google Calendar: ${c.subject}`, at + MINUTE + ms + 3_000, ref);
      else if (c.decision.awaitingApproval) addEvent("commitment.updated", `Waiting for your approval: ${c.subject}`, at + MINUTE + ms + 600, ref);
    }
  }

  // Scheduled mailbox checks, every 15 minutes for the last 6 hours.
  for (let t = NOW - 6 * 60 * MINUTE; t < NOW; t += 15 * MINUTE) {
    addJob({ type: "fetch_mailbox", description: "Check the mailbox for new mail", status: "succeeded", attempts: 1, lastError: null, nextAttemptAt: null, correlationId: null, at: t, history: [attempt(1, "succeeded", t, between(900, 2_400))], result: { fetched: between(0, 3) } });
  }
  addJob({ type: "publish_calendar", description: "Publish the calendar file", status: "succeeded", attempts: 1, lastError: null, nextAttemptAt: null, correlationId: null, at: NOW - 40 * MINUTE, history: [attempt(1, "succeeded", NOW - 40 * MINUTE, 310)] });

  // The ones that went wrong.
  const flaky = emails.find((e) => e.category === "update" && NOW - Date.parse(e.receivedAt!) < DAY) ?? emails[3]!;
  addJob({
    type: "process_email", description: `Analyse “${flaky.subject}”`, status: "failed", attempts: 5, correlationId: `email:${flaky.id}`, nextAttemptAt: null,
    lastError: "Ollama did not answer within 120s (model still loading?)", at: NOW - 95 * MINUTE,
    history: [1, 2, 3, 4, 5].map((n) => attempt(n, "failed", NOW - 95 * MINUTE + n * n * 30_000, 120_000, "Ollama did not answer within 120s (model still loading?)")),
  });
  addEvent("job.failed", `Gave up analysing “${flaky.subject}” after 5 attempts`, NOW - 70 * MINUTE, { correlationId: `email:${flaky.id}` });
  addEvent("extraction.failed", "Ollama did not answer within 120s", NOW - 70 * MINUTE + 1_000, { correlationId: `email:${flaky.id}` });
  addJob({
    type: "push_google_event", description: "Add “Design sync with Priya” to Google Calendar", status: "retrying", attempts: 2, correlationId: null,
    lastError: "Google Calendar API 503: backend error", nextAttemptAt: iso(NOW + 3 * MINUTE), at: NOW - 6 * MINUTE,
    history: [attempt(1, "failed", NOW - 6 * MINUTE, 820, "Google Calendar API 503: backend error"), attempt(2, "failed", NOW - 4 * MINUTE, 790, "Google Calendar API 503: backend error")],
  });
  addEvent("job.retrying", "Google Calendar was unavailable; trying again in 3 minutes", NOW - 4 * MINUTE);
  addJob({ type: "classify_emails", description: "Re-sort mail after a rule change", status: "running", attempts: 1, lastError: null, nextAttemptAt: null, correlationId: null, at: NOW - 20_000, history: [attempt(1, "running", NOW - 20_000, 0)] });

  addEvent("auth.login", "Signed in", NOW - 3 * 60 * MINUTE);
  addEvent("settings.updated", "Notification settings changed", NOW - 2 * 60 * MINUTE);
  addEvent("system.worker_started", "Worker started with 2 parallel jobs", NOW - 7 * 60 * MINUTE);

  events.sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt));
  events.forEach((e, index) => (e.id = index + 1));
  eventId = events.length + 1;
}

buildActivity();

export const notifications: Notification[] = [
  { id: 4, kind: "calendar_failed", title: "Couldn't add an event yet", body: "Google Calendar was unavailable. Retrying automatically.", severity: "warning", link: "/jobs", readAt: null, createdAt: iso(NOW - 4 * MINUTE) },
  { id: 3, kind: "job_failed", title: "Couldn't analyse an email", body: "The local model didn't answer in time. Retry it from Jobs.", severity: "error", link: "/jobs?status=failed", readAt: null, createdAt: iso(NOW - 70 * MINUTE) },
  { id: 2, kind: "important_email", title: "From Prof. Asha Rao", body: "Please send the project synopsis by Monday 5pm", severity: "info", link: `/inbox/${emails.find((e) => e.senderEmail === "asha.rao@northfield.edu")?.id ?? 1}`, readAt: null, createdAt: iso(NOW - 3 * 60 * MINUTE) },
  { id: 1, kind: "retry_succeeded", title: "Retry worked", body: "“Weekly engineering update” was analysed on the second try.", severity: "success", link: null, readAt: iso(NOW - 20 * 60 * MINUTE), createdAt: iso(NOW - 22 * 60 * MINUTE) },
];

export const views: SavedView[] = [
  { id: 1, name: "Unread from VIPs", filters: { unread: true, tier: ["CRITICAL", "IMPORTANT"] }, sort: { sort: "received", dir: "desc" }, isPinned: true },
];

export const state = {
  signedIn: true,
  user: { email: "demo@commitmail.dev", displayName: "Demo Owner" },
  settings: { ...defaultSettings(), calendar: { ...defaultSettings().calendar, timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone } } as Settings,
};

// Contacts count the mail they match, like the server does.
for (const contact of contacts) {
  contact.matchingEmails = emails.filter((e) =>
    contact.matchType === "exact_email" ? e.senderEmail === contact.matchValue
      : contact.matchType === "domain" ? e.senderEmail?.endsWith(`@${contact.matchValue}`)
        : e.senderName?.toLowerCase().includes(contact.matchValue.toLowerCase()),
  ).length;
}

// --- Live metrics ---------------------------------------------------------------------

/** One-minute windows for the System page, with one error spike to find. */
export function metricWindows(minutes: number): MetricWindow[] {
  const series = mulberry32(7);
  const start = Math.floor(NOW / MINUTE) * MINUTE - (minutes - 1) * MINUTE;
  return Array.from({ length: minutes }, (_, i) => {
    const t = start + i * MINUTE;
    const minuteOfHour = new Date(t).getMinutes();
    const burst = minuteOfHour % 15 < 2;
    const processed = burst ? 2 + Math.floor(series() * 4) : series() < 0.25 ? 1 : 0;
    const failures = i === minutes - 37 ? 4 : series() < 0.04 ? 1 : 0;
    const avg = processed ? Math.round(2_400 + series() * 3_200) : null;
    return {
      source: "fallback",
      window: "1m",
      windowStart: iso(t),
      windowEnd: iso(t + MINUTE),
      metrics: {
        events: processed * 4 + Math.floor(series() * 3) + failures,
        emailsProcessed: processed,
        throughputPerMinute: processed,
        avgLatencyMs: avg,
        p95LatencyMs: avg ? Math.round(avg * (1.35 + series() * 0.5)) : null,
        successRate: processed + failures ? Math.round((processed / (processed + failures)) * 1000) / 10 : null,
        failures,
        errorSpike: failures >= 3,
        volumeAnomaly: false,
      },
    };
  });
}

export const categoriesInOrder = CATEGORIES;
export { NOW, DAY, MINUTE, iso };
