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
}

export async function recordEvent(q: Queryable, event: NewEvent): Promise<number> {
  const { rows } = await q.query<{ id: number }>(
    `INSERT INTO events (type, message, entity_type, entity_id, correlation_id, severity, payload, source)
     VALUES ($1, $2, $3, $4, $5, $6, $7, 'server') RETURNING id`,
    [
      event.type,
      event.message,
      event.entityType ?? null,
      event.entityId == null ? null : String(event.entityId),
      event.correlationId ?? null,
      event.severity ?? "info",
      JSON.stringify(event.payload ?? {}),
    ],
  );
  return rows[0]!.id;
}

export const emailCorrelation = (emailId: number) => `email:${emailId}`;
