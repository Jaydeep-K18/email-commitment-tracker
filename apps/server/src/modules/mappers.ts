/**
 * Database rows -> API shapes, in one place.
 *
 * Centralised because the timestamp rule (instants are UTC, deadlines are wall
 * clock) is easy to apply inconsistently when every route converts its own.
 */
import {
  decide,
  type ActivityEvent,
  type Commitment,
  type Job,
  type JobAttempt,
  type Notification,
} from "@commitmail/shared";

import { isAllDay, utc, wallClock } from "../db/time";
import { describeJob } from "../jobs/queue";

type Row = Record<string, any>;

export function toActivityEvent(row: Row): ActivityEvent {
  return {
    id: row.id,
    type: row.type,
    entityType: row.entity_type,
    entityId: row.entity_id,
    correlationId: row.correlation_id,
    severity: row.severity,
    message: row.message,
    payload: row.payload ?? {},
    source: row.source,
    createdAt: utc(row.created_at)!,
  };
}

export function toNotification(row: Row): Notification {
  return {
    id: row.id,
    kind: row.kind,
    title: row.title,
    body: row.body,
    severity: row.severity,
    link: row.link,
    readAt: utc(row.read_at),
    createdAt: utc(row.created_at)!,
  };
}

export function toJob(row: Row, history?: Row[]): Job {
  const payload = row.payload ?? {};
  return {
    id: row.id,
    type: row.type,
    description: describeJob(row.type, payload),
    status: row.status,
    attempts: row.attempts,
    maxAttempts: row.max_attempts,
    lastError: row.last_error,
    nextAttemptAt: utc(row.next_attempt_at),
    createdAt: utc(row.created_at)!,
    updatedAt: utc(row.updated_at)!,
    finishedAt: utc(row.finished_at),
    correlationId: row.correlation_id,
    payload,
    result: row.result,
    ...(history ? { history: history.map(toJobAttempt) } : {}),
  };
}

export function toJobAttempt(row: Row): JobAttempt {
  return {
    attempt: row.attempt,
    status: row.status,
    error: row.error,
    worker: row.worker,
    startedAt: utc(row.started_at)!,
    finishedAt: utc(row.finished_at),
    durationMs: row.duration_ms,
  };
}

/** Expects commitment columns plus the source email's as `email_*`. */
export function toCommitment(row: Row): Commitment {
  return {
    id: row.id,
    emailId: row.email_id,
    type: row.type,
    subject: row.subject,
    deadline: wallClock(row.deadline),
    allDay: isAllDay(row.deadline),
    counterpartyName: row.counterparty_name,
    counterpartyEmail: row.counterparty_email,
    direction: row.direction,
    evidenceQuote: row.evidence_quote,
    confidence: row.confidence,
    vipTier: row.vip_tier,
    status: row.status,
    manuallyAdded: row.manually_added,
    syncApproved: row.sync_approved,
    calendarSynced: row.calendar_synced,
    googleEventId: row.gcal_event_id,
    createdAt: utc(row.created_at)!,
    decision: decide({
      type: row.type,
      hasDeadline: row.deadline != null,
      status: row.status,
      vipTier: row.vip_tier,
      manuallyAdded: row.manually_added,
      syncApproved: row.sync_approved,
    }),
    source: {
      emailId: row.email_id,
      subject: row.email_subject ?? null,
      senderName: row.email_sender_name ?? null,
      senderEmail: row.email_sender_email ?? null,
    },
  };
}

/** The columns `toCommitment` needs, for a query joining raw_emails as `e`. */
export const COMMITMENT_COLUMNS = `
  c.*, e.subject AS email_subject, e.sender_name AS email_sender_name,
  e.sender_email AS email_sender_email`;
