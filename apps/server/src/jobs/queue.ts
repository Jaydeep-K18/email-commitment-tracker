/**
 * Enqueueing and retrying jobs from the server. The Python worker runs them.
 *
 * Same rules as the worker's own queue (src/jobs/queue.py), because both write
 * the same table:
 *
 * - The jobs row is the job. `INSERT ... ON CONFLICT (user_id, idempotency_key) DO
 *   NOTHING` makes asking twice for the same work a no-op.
 * - Redis only carries the id to a worker, and only after the transaction that
 *   created the row has committed. If Redis is down or unset, nothing is lost:
 *   the worker's sweeper finds runnable rows that Redis does not know about.
 */
import { randomUUID } from "node:crypto";

import { redisKey, type JobType } from "@commitmail/shared";
import type { Redis } from "ioredis";
import type { Logger } from "pino";

import type { Db, Queryable } from "../db/types";
import { recordEvent } from "../events/record";

export interface Enqueued {
  jobId: number;
  created: boolean;
}

const DESCRIPTIONS: Record<string, string> = {
  fetch_mailbox: "check the mailbox for new email",
  process_email: "analyze email #{email_id}",
  publish_calendar: "publish the calendar",
  push_google_event: "send commitment #{commitment_id} to Google Calendar",
  remove_google_event: "remove commitment #{commitment_id} from Google Calendar",
  apply_vip_rules: "re-apply your contact rules to stored email",
  enforce_retention: "apply your data-retention settings",
  classify_emails: "re-classify selected emails",
};

/** The same sentence the worker uses, so a job reads identically everywhere. */
export function describeJob(type: string, payload: Record<string, unknown>): string {
  const template = DESCRIPTIONS[type] ?? type.replaceAll("_", " ");
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in payload ? String(payload[key]) : match,
  );
}

/** Collects job ids created inside one transaction, to dispatch after commit. */
export class TxJobs {
  readonly created: number[] = [];

  constructor(
    private readonly q: Queryable,
    private readonly maxAttempts: number,
  ) {}

  async enqueue(
    type: JobType,
    payload: Record<string, unknown>,
    options: { key: string; correlationId?: string | null },
  ): Promise<Enqueued> {
    const inserted = await this.q.query<{ id: number }>(
      `INSERT INTO jobs (type, idempotency_key, payload, max_attempts, correlation_id)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (user_id, idempotency_key) DO NOTHING
       RETURNING id`,
      [type, options.key, JSON.stringify(payload), this.maxAttempts, options.correlationId ?? null],
    );
    if (inserted.rows[0]) {
      const jobId = inserted.rows[0].id;
      await recordEvent(this.q, {
        type: "job.queued",
        message: `Queued: ${describeJob(type, payload)}`,
        entityType: "job",
        entityId: jobId,
        correlationId: options.correlationId ?? null,
        payload: { job_type: type, key: options.key },
      });
      this.created.push(jobId);
      return { jobId, created: true };
    }
    const existing = await this.q.query<{ id: number }>(
      "SELECT id FROM jobs WHERE idempotency_key = $1",
      [options.key],
    );
    return { jobId: existing.rows[0]!.id, created: false };
  }

  /** Enqueue unless the same work is already queued, retrying or running. */
  async enqueueUnlessActive(
    type: JobType,
    payload: Record<string, unknown> = {},
    options: { scope?: string; correlationId?: string | null } = {},
  ): Promise<Enqueued> {
    const scope = options.scope ?? type;
    const active = await this.q.query<{ id: number }>(
      `SELECT id FROM jobs
        WHERE idempotency_key LIKE $1 AND status IN ('queued', 'retrying', 'running')
        LIMIT 1`,
      [`${scope.replace(/[%_\\]/g, "\\$&")}:%`],
    );
    if (active.rows[0]) return { jobId: active.rows[0].id, created: false };
    return this.enqueue(type, payload, {
      key: `${scope}:${randomUUID().replaceAll("-", "").slice(0, 12)}`,
      correlationId: options.correlationId ?? null,
    });
  }

  /** Give a failed (or cancelled) job a fresh budget of attempts. */
  async retry(jobId: number, extraAttempts = 3): Promise<boolean> {
    const updated = await this.q.query<{ type: string; payload: Record<string, unknown>; attempts: number; correlation_id: string | null }>(
      `UPDATE jobs
          SET status = 'queued', next_attempt_at = NULL, finished_at = NULL,
              max_attempts = attempts + $2, updated_at = (now() at time zone 'utc')
        WHERE id = $1 AND status IN ('failed', 'cancelled')
        RETURNING type, payload, attempts, correlation_id`,
      [jobId, extraAttempts],
    );
    const job = updated.rows[0];
    if (!job) return false;
    await recordEvent(this.q, {
      type: "job.retried",
      message: `Retry requested: ${describeJob(job.type, job.payload)}`,
      entityType: "job",
      entityId: jobId,
      correlationId: job.correlation_id,
      payload: { job_type: job.type, previous_attempts: job.attempts },
    });
    this.created.push(jobId);
    return true;
  }
}

export class JobQueue {
  constructor(
    private readonly db: Db,
    private readonly redis: Redis | null,
    private readonly prefix: string,
    private readonly maxAttempts: number,
    private readonly log: Logger,
  ) {}

  get dispatcher(): "redis" | "database" {
    return this.redis ? "redis" : "database";
  }

  /**
   * Run `fn` in a transaction with a job handle; dispatch what it enqueued
   * only once the transaction has committed.
   */
  async withJobs<T>(fn: (q: Queryable, jobs: TxJobs) => Promise<T>): Promise<T> {
    let jobs!: TxJobs;
    const result = await this.db.transaction(async (q) => {
      jobs = new TxJobs(q, this.maxAttempts);
      return fn(q, jobs);
    });
    await this.dispatch(jobs.created);
    return result;
  }

  /** Hand a job's id to waiting workers. Failure is logged, never thrown. */
  async dispatch(jobIds: number[]): Promise<void> {
    if (!this.redis || jobIds.length === 0) return;
    const ready = redisKey("ready", this.prefix);
    const processing = redisKey("processing", this.prefix);
    const delayed = redisKey("delayed", this.prefix);
    for (const id of jobIds) {
      const member = String(id);
      try {
        const [inReady, inProcessing, inDelayed] = await Promise.all([
          this.redis.lpos(ready, member),
          this.redis.lpos(processing, member),
          this.redis.zscore(delayed, member),
        ]);
        if (inReady === null && inProcessing === null && inDelayed === null) {
          await this.redis.lpush(ready, member);
        }
      } catch (error) {
        this.log.warn({ err: error, jobId: id }, "could not dispatch job; the worker will sweep it");
      }
    }
  }

  async depth(): Promise<{ ready?: number; delayed?: number; processing?: number }> {
    if (!this.redis) return {};
    try {
      const [ready, delayed, processing] = await Promise.all([
        this.redis.llen(redisKey("ready", this.prefix)),
        this.redis.zcard(redisKey("delayed", this.prefix)),
        this.redis.llen(redisKey("processing", this.prefix)),
      ]);
      return { ready, delayed, processing };
    } catch {
      return {};
    }
  }
}
