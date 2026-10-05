/**
 * Turning events into notifications.
 *
 * Unlike the live feed, this must not miss anything: a job that failed while
 * the server was restarting still deserves a notification. So it keeps its own
 * cursor in the database and, on every wake-up, reads every event past it. The
 * insert is `ON CONFLICT (event_id, kind) DO NOTHING`, so seeing an event twice
 * — after a crash between insert and cursor save, or from Kafka's at-least-once
 * delivery — produces one notification, not two.
 */
import {
  defaultSettings,
  notificationSettingsSchema,
  type ActivityEvent,
  type Notification,
  type Severity,
} from "@commitmail/shared";
import type { Logger } from "pino";

import type { Db, Queryable } from "../db/types";
import { toActivityEvent, toNotification } from "../modules/mappers";

type Prefs = ReturnType<typeof defaultSettings>["notifications"];

interface Draft {
  kind: string;
  title: string;
  body: string | null;
  severity: Severity;
  link: string | null;
}

const CURSOR_KEY = "server_state";
const BATCH = 500;

function emailIdFrom(event: ActivityEvent): number | null {
  const match = /^email:(\d+)$/.exec(event.correlationId ?? "");
  return match ? Number(match[1]) : null;
}

/** The rules. Pure, so they are tested without a database. */
export function notificationFor(event: ActivityEvent, prefs: Prefs): Draft | null {
  const p = event.payload as Record<string, any>;
  switch (event.type) {
    case "email.received": {
      if (!prefs.importantEmail || !["CRITICAL", "IMPORTANT"].includes(p.vip_tier)) return null;
      const sender = event.message.replace(/^Email from /, "").split(":")[0] ?? "a priority contact";
      return {
        kind: "important_email",
        title: `Important email from ${sender}`,
        body: p.subject ?? null,
        severity: "info",
        link: event.entityId ? `/inbox/${event.entityId}` : null,
      };
    }
    case "job.failed":
      if (!prefs.jobFailed) return null;
      return {
        kind: "job_failed",
        title: "A background job failed",
        body: event.message,
        severity: "error",
        link: event.entityId ? `/jobs/${event.entityId}` : "/jobs",
      };
    case "job.completed":
      if (!prefs.retrySucceeded || !(Number(p.attempts) > 1)) return null;
      return {
        kind: "job_retry_succeeded",
        title: "Recovered after a retry",
        body: event.message,
        severity: "success",
        link: event.entityId ? `/jobs/${event.entityId}` : "/jobs",
      };
    case "calendar.event_failed": {
      if (!prefs.calendarFailed) return null;
      const emailId = emailIdFrom(event);
      return {
        kind: "calendar_failed",
        title: "Could not add an event to your calendar",
        body: event.message,
        severity: "error",
        link: emailId ? `/inbox/${emailId}` : "/calendar",
      };
    }
    case "system.error":
      return { kind: "system", title: "System problem", body: event.message, severity: "error", link: "/system" };
    default:
      return null;
  }
}

export class Notifier {
  private running = false;
  private rerun = false;
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly db: Db,
    private readonly log: Logger,
    private readonly announce: (notification: Notification) => void,
    private readonly pollMs = 5_000,
  ) {}

  async start(): Promise<void> {
    await this.ensureCursor();
    this.timer = setInterval(() => void this.wake(), this.pollMs);
    this.timer.unref();
    await this.wake();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
  }

  /** Process everything past the cursor. Safe to call as often as you like. */
  async wake(): Promise<void> {
    if (this.running) {
      this.rerun = true;
      return;
    }
    this.running = true;
    try {
      do {
        this.rerun = false;
        const processed = await this.processBatch();
        if (processed === BATCH) this.rerun = true;
      } while (this.rerun);
    } catch (error) {
      this.log.warn({ err: error }, "notification processing failed; will retry");
    } finally {
      this.running = false;
    }
  }

  private async ensureCursor(): Promise<void> {
    // First start: begin from the newest event, so a fresh install does not
    // announce its entire history at once.
    await this.db.query(
      `INSERT INTO settings (section, value)
       SELECT $1, jsonb_build_object('notifierCursor', COALESCE(MAX(id), 0)) FROM events
       ON CONFLICT (section) DO NOTHING`,
      [CURSOR_KEY],
    );
  }

  private async processBatch(): Promise<number> {
    const created: Notification[] = [];
    const count = await this.db.transaction(async (q) => {
      const cursor = await readCursor(q);
      const { rows } = await q.query(
        "SELECT * FROM events WHERE id > $1 ORDER BY id LIMIT $2",
        [cursor, BATCH],
      );
      if (rows.length === 0) return 0;

      const prefs = await readPrefs(q);
      for (const row of rows) {
        const event = toActivityEvent(row);
        const draft = notificationFor(event, prefs);
        if (!draft) continue;
        const inserted = await q.query(
          `INSERT INTO notifications (kind, title, body, severity, link, event_id)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (event_id, kind) DO NOTHING
           RETURNING *`,
          [draft.kind, draft.title, draft.body, draft.severity, draft.link, event.id],
        );
        if (inserted.rows[0]) created.push(toNotification(inserted.rows[0]));
      }
      const last = (rows[rows.length - 1] as { id: number }).id;
      await q.query(
        `UPDATE settings SET value = jsonb_set(value, '{notifierCursor}', to_jsonb($2::bigint)),
                             updated_at = (now() at time zone 'utc')
          WHERE section = $1`,
        [CURSOR_KEY, last],
      );
      return rows.length;
    });
    // Announced only after the transaction committed, so the browser is never
    // told about a notification that then rolled back.
    for (const notification of created) this.announce(notification);
    return count;
  }
}

async function readCursor(q: Queryable): Promise<number> {
  const { rows } = await q.query<{ cursor: number | null }>(
    "SELECT (value->>'notifierCursor')::bigint AS cursor FROM settings WHERE section = $1",
    [CURSOR_KEY],
  );
  return rows[0]?.cursor ?? 0;
}

async function readPrefs(q: Queryable): Promise<Prefs> {
  const { rows } = await q.query<{ value: unknown }>(
    "SELECT value FROM settings WHERE section = 'notifications'",
  );
  const parsed = notificationSettingsSchema.safeParse(rows[0]?.value ?? {});
  return parsed.success ? parsed.data : defaultSettings().notifications;
}
