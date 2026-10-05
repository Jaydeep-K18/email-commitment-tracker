/** The activity timeline and job management. */
import {
  JOB_STATUSES,
  activityQuerySchema,
  idParam,
  jobQuerySchema,
  type JobStats,
  type JobStatus,
} from "@commitmail/shared";
import { Router } from "express";

import { QueryParts } from "../db/sql";
import type { Deps } from "../deps";
import { notFound } from "../http/errors";
import { parse } from "../http/validate";
import { toActivityEvent, toJob } from "./mappers";

export function activityRouter(deps: Deps): Router {
  const router = Router();

  /** Newest first, paged by id ("before") so new events never shift the pages. */
  router.get("/", async (req, res) => {
    const query = parse(activityQuerySchema, req.query);
    const parts = new QueryParts();
    if (query.before) parts.where(`id < ${parts.param(query.before)}`);
    if (query.type?.length) parts.where(`type = ANY(${parts.param(query.type)}::text[])`);
    if (query.severity?.length) parts.where(`severity = ANY(${parts.param(query.severity)}::text[])`);
    if (query.correlation) parts.where(`correlation_id = ${parts.param(query.correlation)}`);
    const limit = parts.param(query.limit + 1);
    const { rows } = await deps.db.query(
      `SELECT * FROM events ${parts.whereSql} ORDER BY id DESC LIMIT ${limit}`,
      parts.params,
    );
    const page = rows.slice(0, query.limit).map(toActivityEvent);
    res.json({
      items: page,
      nextBefore: rows.length > query.limit ? page[page.length - 1]!.id : null,
    });
  });

  return router;
}

export function jobsRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (req, res) => {
    const query = parse(jobQuerySchema, req.query);
    const parts = new QueryParts();
    if (query.status?.length) parts.where(`status = ANY(${parts.param(query.status)}::text[])`);
    if (query.type?.length) parts.where(`type = ANY(${parts.param(query.type)}::text[])`);
    const counted = parts.clone();
    const limit = parts.param(query.pageSize);
    const offset = parts.param((query.page - 1) * query.pageSize);
    const [items, total] = await Promise.all([
      deps.db.query(
        `SELECT * FROM jobs ${parts.whereSql} ORDER BY updated_at DESC, id DESC LIMIT ${limit} OFFSET ${offset}`,
        parts.params,
      ),
      deps.db.query<{ total: number }>(`SELECT count(*) AS total FROM jobs ${counted.whereSql}`, counted.params),
    ]);
    res.json({ items: items.rows.map((row) => toJob(row)), total: total.rows[0]?.total ?? 0, page: query.page, pageSize: query.pageSize });
  });

  router.get("/stats", async (_req, res) => {
    const { rows } = await deps.db.query<{ status: JobStatus; count: number }>(
      "SELECT status, count(*) AS count FROM jobs GROUP BY status",
    );
    const byStatus = Object.fromEntries(JOB_STATUSES.map((s) => [s, 0])) as Record<JobStatus, number>;
    for (const row of rows) byStatus[row.status] = row.count;
    const stats: JobStats = { byStatus, queue: await deps.jobs.depth(), dispatcher: deps.jobs.dispatcher };
    res.json(stats);
  });

  router.get("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const [job, attempts] = await Promise.all([
      deps.db.query("SELECT * FROM jobs WHERE id = $1", [id]),
      deps.db.query("SELECT * FROM job_attempts WHERE job_id = $1 ORDER BY attempt", [id]),
    ]);
    if (!job.rows[0]) throw notFound("Job");
    res.json(toJob(job.rows[0], attempts.rows));
  });

  router.post("/:id/retry", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const retried = await deps.jobs.withJobs((_q, jobs) => jobs.retry(id));
    if (!retried) {
      const exists = await deps.db.query("SELECT status FROM jobs WHERE id = $1", [id]);
      if (!exists.rows[0]) throw notFound("Job");
      res.status(409).json({ error: { code: "not_retryable", message: "Only failed or cancelled jobs can be retried." } });
      return;
    }
    const { rows } = await deps.db.query("SELECT * FROM jobs WHERE id = $1", [id]);
    res.json(toJob(rows[0]!));
  });

  /** Retry every failed job — after fixing whatever made them all fail. */
  router.post("/retry-failed", async (_req, res) => {
    const retried = await deps.jobs.withJobs(async (q, jobs) => {
      const { rows } = await q.query<{ id: number }>(
        "SELECT id FROM jobs WHERE status = 'failed' ORDER BY id LIMIT 500",
      );
      let count = 0;
      for (const row of rows) count += (await jobs.retry(row.id)) ? 1 : 0;
      return count;
    });
    res.json({ retried });
  });

  return router;
}

export function syncRouter(deps: Deps): Router {
  const router = Router();

  /** "Sync now": check the mailbox and republish, as ordinary jobs. */
  router.post("/", async (_req, res) => {
    const queued = await deps.jobs.withJobs(async (_q, jobs) => ({
      fetch: await jobs.enqueueUnlessActive("fetch_mailbox"),
      publish: await jobs.enqueueUnlessActive("publish_calendar"),
    }));
    res.status(202).json(queued);
  });

  return router;
}
