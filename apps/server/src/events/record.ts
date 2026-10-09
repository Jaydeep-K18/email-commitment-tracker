/**
 * Server-side events: what the user did, in the same audit trail as what the
 * worker did. Written in the caller's transaction, like the worker's, so an
 * action and its record commit or roll back together.
 */
import type { EventType, Severity } from "@commitmail/shared";

import type { Queryable } from "../db/types";

export interface NewEvent {
  type: EventType;
  message: string;
  entityType?: string | null;
  entityId?: string | number | null;
  correlationId?: string | null;
  severity?: Severity;
  payload?: Record<string, unknown>;
  /**
   * Whose event it is. Leave it out inside a user's request: the database
   * fills in that user. Sign-in code, which runs on the system connection,
   * names the account the event is about.
   */
  userId?: number | null;
}

export async function recordEvent(q: Queryable, event: NewEvent): Promise<number> {
  const { rows } = await q.query<{ id: number }>(
    `INSERT INTO events (type, message, entity_type, entity_id, correlation_id, severity, payload, source, user_id)
     VALUES ($1, $2, $3, $4, $5, $6, $7, 'server', COALESCE($8::int, app_user_id())) RETURNING id`,
    [
      event.type,
      event.message,
      event.entityType ?? null,
      event.entityId == null ? null : String(event.entityId),
      event.correlationId ?? null,
      event.severity ?? "info",
      JSON.stringify(event.payload ?? {}),
      event.userId ?? null,
    ],
  );
  return rows[0]!.id;
}

export const emailCorrelation = (emailId: number) => `email:${emailId}`;
