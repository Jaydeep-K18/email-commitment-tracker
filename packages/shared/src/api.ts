/**
 * Response shapes — what the server returns and the React app reads.
 *
 * Timestamps are ISO-8601 strings. Every instant ends in `Z` (UTC). The one
 * exception is a commitment's `deadline`, which is the wall-clock time written
 * in the email ("Friday 5pm") and carries no zone, so the UI shows exactly
 * what the email said rather than converting it.
 */
import type {
  Category,
  CommitmentStatus,
  CommitmentType,
  EventType,
  JobStatus,
  JobType,
  MatchType,
  Severity,
  Tier,
} from "./catalogue";
import type { Settings } from "./schemas";
import type { SyncDecision } from "./policy";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ApiError {
  error: { code: string; message: string; details?: unknown };
}

// --- Auth --------------------------------------------------------------------

export interface SessionInfo {
  authenticated: boolean;
  /** True until the owner account exists; the app shows first-run setup. */
  setupRequired: boolean;
  user: { email: string; displayName: string | null } | null;
  /** Echoed on every state-changing request as X-CSRF-Token. */
  csrfToken: string | null;
}

// --- Inbox -------------------------------------------------------------------

export interface Tag {
  id: number;
  name: string;
  color: string;
  emailCount?: number;
}

export interface EmailListItem {
  id: number;
  subject: string | null;
  senderName: string | null;
  senderEmail: string | null;
  receivedAt: string | null;
  snippet: string;
  category: Category | null;
  categoryReason: string | null;
  categorySource: "rule" | "llm" | "user" | null;
  vipTier: Tier | null;
  isRead: boolean;
  isStarred: boolean;
  archivedAt: string | null;
  deletedAt: string | null;
  hasInvite: boolean;
  hasAttachments: boolean;
  commitmentCount: number;
  tags: Tag[];
}

export interface EmailDetail extends EmailListItem {
  body: string | null;
  recipientEmail: string | null;
  cc: string | null;
  threadId: string | null;
  processed: boolean;
  commitments: Commitment[];
  timeline: ActivityEvent[];
  /** When you replied, if a reply was found in your Sent folder. */
  repliedAt: string | null;
  responseMinutes: number | null;
}

export interface InboxPage extends Page<EmailListItem> {
  counts: { byCategory: Record<Category, number>; unread: number };
}

export interface SavedView {
  id: number;
  name: string;
  filters: Record<string, unknown>;
  sort: { sort: string; dir: "asc" | "desc" };
  isPinned: boolean;
}

// --- Commitments and calendar ------------------------------------------------

export interface Commitment {
  id: number;
  emailId: number;
  type: CommitmentType;
  subject: string;
  /** Wall-clock time from the email — no zone, see the module comment. */
  deadline: string | null;
  allDay: boolean;
  counterpartyName: string | null;
  counterpartyEmail: string | null;
  direction: string | null;
  evidenceQuote: string;
  confidence: number;
  vipTier: Tier | null;
  status: CommitmentStatus;
  manuallyAdded: boolean;
  syncApproved: boolean;
  calendarSynced: boolean;
  googleEventId: string | null;
  createdAt: string;
  decision: SyncDecision;
  source: { emailId: number; subject: string | null; senderName: string | null; senderEmail: string | null };
}

/** A span of wall-clock time, like a deadline: no zone. */
export interface CalendarSpan {
  start: string;
  end: string;
}

/** Something already on the user's own Google Calendar. */
export interface ExternalEvent extends CalendarSpan {
  id: string;
  title: string;
  allDay: boolean;
  link: string | null;
}

/**
 * A possible duplicate or clash the worker found, for the user to settle.
 * `commitment` is the one to act on (for a clash, the one from the later email);
 * the other side is another commitment or an event on their own calendar.
 */
export interface CalendarFlag {
  id: number;
  kind: "duplicate" | "conflict";
  status: "open" | "resolved" | "dismissed";
  commitment: Commitment;
  other: Commitment | null;
  external: ExternalEvent | null;
  /** Duplicates: how alike the two subjects are, 0–1. */
  similarity: number | null;
  /** Clashes: when the two overlap, and free times in working hours instead. */
  overlap: CalendarSpan | null;
  suggestions: CalendarSpan[];
  createdAt: string;
  resolvedAt: string | null;
}

export interface CalendarInsights {
  from: string;
  to: string;
  days: Array<{ date: string; deadlines: number; meetings: number }>;
  busiestDay: { date: string; count: number } | null;
  /** Timed commitments outside the working hours set in Settings. */
  outsideHours: Array<{ id: number; type: CommitmentType; subject: string; deadline: string }>;
  openFlags: { conflicts: number; duplicates: number };
}

export interface RelationshipPerson {
  key: string;
  label: string;
  tier: Tier | "untiered";
  youOwe: number;
  theyOwe: number;
  commitments: Array<{
    id: number;
    emailId: number;
    type: CommitmentType;
    subject: string;
    deadline: string | null;
    status: CommitmentStatus;
    direction: "you_owe" | "they_owe";
  }>;
}

// --- Contacts ----------------------------------------------------------------

export interface Contact {
  id: number;
  matchType: MatchType;
  matchValue: string;
  tier: Tier;
  displayName: string | null;
  createdAt: string;
  matchingEmails: number;
}

// --- Activity, notifications, jobs -------------------------------------------

export interface ActivityEvent {
  id: number;
  type: EventType;
  entityType: string | null;
  entityId: string | null;
  correlationId: string | null;
  severity: Severity;
  message: string;
  payload: Record<string, unknown>;
  source: string;
  createdAt: string;
}

export interface Notification {
  id: number;
  kind: string;
  title: string;
  body: string | null;
  severity: Severity;
  link: string | null;
  readAt: string | null;
  createdAt: string;
}

export interface JobAttempt {
  attempt: number;
  status: "running" | "succeeded" | "failed";
  error: string | null;
  worker: string | null;
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
}

export interface Job {
  id: number;
  type: JobType;
  description: string;
  status: JobStatus;
  attempts: number;
  maxAttempts: number;
  lastError: string | null;
  nextAttemptAt: string | null;
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
  correlationId: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  history?: JobAttempt[];
}

export interface JobStats {
  byStatus: Record<JobStatus, number>;
  queue: { ready?: number; delayed?: number; processing?: number };
  dispatcher: "redis" | "database";
}

// --- Analytics ---------------------------------------------------------------

export interface Analytics {
  range: { from: string; to: string; days: number; timeZone: string };
  volume: Array<{ date: string; total: number } & Partial<Record<Category, number>>>;
  byCategory: Record<Category, number>;
  byTier: Record<string, number>;
  topSenders: Array<{ email: string; name: string | null; count: number; lastReceivedAt: string | null }>;
  actionVsInformational: { action: number; informational: number };
  responseTime: { replied: number; medianMinutes: number | null; p90Minutes: number | null };
  conversion: { analyzed: number; withCommitments: number; onCalendar: number; rate: number | null };
  processing: {
    succeeded: number;
    failed: number;
    successRate: number | null;
    avgLatencyMs: number | null;
    p95LatencyMs: number | null;
  };
  calendarActivity: Array<{ date: string; created: number; removed: number; failed: number }>;
  trends: { volumeChange: number | null; conversionChange: number | null; previousTotal: number };
}

// --- System ------------------------------------------------------------------

export type HealthState = "ok" | "degraded" | "down" | "unconfigured";

export interface ComponentHealth {
  state: HealthState;
  latencyMs?: number | null;
  detail?: string;
  meta?: Record<string, unknown>;
}

export interface SystemHealth {
  status: HealthState;
  checkedAt: string;
  components: {
    api: ComponentHealth;
    database: ComponentHealth;
    redis: ComponentHealth;
    worker: ComponentHealth;
    ollama: ComponentHealth;
    kafka: ComponentHealth;
    flink: ComponentHealth;
  };
}

export interface MetricWindow {
  source: "flink" | "fallback";
  window: "1m" | "15m";
  windowStart: string;
  windowEnd: string;
  metrics: {
    events: number;
    emailsProcessed: number;
    throughputPerMinute: number;
    avgLatencyMs: number | null;
    p95LatencyMs: number | null;
    successRate: number | null;
    failures: number;
    errorSpike: boolean;
    volumeAnomaly: boolean;
    byType?: Record<string, number>;
  };
}

// --- Setup -------------------------------------------------------------------

export interface SetupStatus {
  ollama: { running: boolean; modelPresent: boolean; model: string; problem: string };
  mailbox: { address: string | null; connected: boolean; viaGmailApi: boolean };
  /** `expired`: Google refused to renew the sign-in; only signing in again fixes it. */
  google: { signedIn: boolean; expired: boolean; email: string | null; calendar: boolean; clientConfigured: boolean };
  complete: boolean;
  missing: string;
}

export type { Settings };

// --- WebSocket ---------------------------------------------------------------

export type ServerMessage =
  | { type: "hello"; serverTime: string }
  | { type: "event"; event: ActivityEvent }
  | { type: "notification"; notification: Notification }
  | { type: "metrics"; window: MetricWindow };
