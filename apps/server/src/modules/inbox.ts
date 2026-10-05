/**
 * The smart inbox: list, search, filter, sort, open, and act on email.
 *
 * Every action here changes only the app's view of an email. IMAP access is
 * read-only, so archiving or deleting never touches the real mailbox.
 */
import {
  CATEGORIES,
  CATEGORY_LABELS,
  emailBulkSchema,
  emailPatchSchema,
  idParam,
  inboxQuerySchema,
  type Category,
  type EmailDetail,
  type EmailListItem,
  type InboxPage,
  type InboxQuery,
} from "@commitmail/shared";
import { randomUUID } from "node:crypto";

import { Router } from "express";

import { QueryParts, likeLiteral } from "../db/sql";
import { utc } from "../db/time";
import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { emailCorrelation, recordEvent } from "../events/record";
import { notFound } from "../http/errors";
import { parse } from "../http/validate";
import type { TxJobs } from "../jobs/queue";
import { COMMITMENT_COLUMNS, toActivityEvent, toCommitment } from "./mappers";

const LIST_COLUMNS = `
  e.id, e.subject, e.sender_name, e.sender_email, e.received_at,
  left(coalesce(e.body_text, ''), 240) AS snippet,
  e.category, e.category_reason, e.category_source, e.vip_tier,
  e.is_read, e.is_starred, e.archived_at, e.deleted_at,
  e.has_invite, e.has_attachments,
  (SELECT count(*) FROM commitments c
    WHERE c.email_id = e.id AND c.status <> 'superseded') AS commitment_count,
  COALESCE((SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'color', t.color) ORDER BY t.name)
              FROM email_tags et JOIN tags t ON t.id = et.tag_id
             WHERE et.email_id = e.id), '[]'::json) AS tags`;

const CATEGORY_RANK_SQL = `CASE e.category
  WHEN 'action_required' THEN 0 WHEN 'meeting' THEN 1 WHEN 'important' THEN 2
  WHEN 'update' THEN 3 WHEN 'low_priority' THEN 4 ELSE 5 END`;
const TIER_RANK_SQL = `CASE e.vip_tier
  WHEN 'CRITICAL' THEN 0 WHEN 'IMPORTANT' THEN 1 WHEN 'MONITOR' THEN 2 WHEN 'SKIP' THEN 4 ELSE 3 END`;

function toListItem(row: Record<string, any>): EmailListItem {
  return {
    id: row.id,
    subject: row.subject,
    senderName: row.sender_name,
    senderEmail: row.sender_email,
    receivedAt: utc(row.received_at),
    snippet: (row.snippet ?? "").replace(/\s+/g, " ").trim().slice(0, 200),
    category: row.category,
    categoryReason: row.category_reason,
    categorySource: row.category_source,
    vipTier: row.vip_tier,
    isRead: row.is_read,
    isStarred: row.is_starred,
    archivedAt: utc(row.archived_at),
    deletedAt: utc(row.deleted_at),
    hasInvite: row.has_invite,
    hasAttachments: row.has_attachments,
    commitmentCount: row.commitment_count,
    tags: row.tags ?? [],
  };
}

/** WHERE clause for everything except the category filter (used for tab counts). */
function baseFilters(query: InboxQuery): QueryParts {
  const parts = new QueryParts();
  const folder = {
    inbox: "e.archived_at IS NULL AND e.deleted_at IS NULL",
    archived: "e.archived_at IS NOT NULL AND e.deleted_at IS NULL",
    trash: "e.deleted_at IS NOT NULL",
    all: "e.deleted_at IS NULL",
  }[query.folder];
  parts.where(`(${folder})`);

  if (query.q) parts.where(`e.search_vector @@ websearch_to_tsquery('english', ${parts.param(query.q)})`);
  if (query.tier?.length) parts.where(`e.vip_tier = ANY(${parts.param(query.tier)}::text[])`);
  if (query.tag?.length) {
    parts.where(`EXISTS (SELECT 1 FROM email_tags et WHERE et.email_id = e.id
                          AND et.tag_id = ANY(${parts.param(query.tag)}::int[]))`);
  }
  if (query.unread !== undefined) parts.where(`e.is_read = ${parts.param(!query.unread)}`);
  if (query.starred !== undefined) parts.where(`e.is_starred = ${parts.param(query.starred)}`);
  if (query.hasCommitments !== undefined) {
    parts.where(`${query.hasCommitments ? "" : "NOT "}EXISTS (SELECT 1 FROM commitments c
                  WHERE c.email_id = e.id AND c.status <> 'superseded')`);
  }
  if (query.sender) {
    const pattern = parts.param(`%${likeLiteral(query.sender)}%`);
    parts.where(`(e.sender_email ILIKE ${pattern} OR e.sender_name ILIKE ${pattern})`);
  }
  if (query.from) parts.where(`e.received_at >= ${parts.param(query.from)}::date`);
  if (query.to) parts.where(`e.received_at < ${parts.param(query.to)}::date + 1`);
  return parts;
}

function orderBy(query: InboxQuery, rankExpr: string | null): string {
  const dir = query.dir === "asc" ? "ASC" : "DESC";
  const flip = query.dir === "asc" ? "DESC" : "ASC";
  switch (query.sort) {
    case "sender":
      return `lower(coalesce(e.sender_name, e.sender_email, '')) ${dir}, e.received_at DESC NULLS LAST`;
    case "subject":
      return `lower(coalesce(e.subject, '')) ${dir}, e.received_at DESC NULLS LAST`;
    case "category":
      return `e.category ${dir} NULLS LAST, e.received_at DESC NULLS LAST`;
    case "priority":
      // "desc" means most urgent first, so the ranks (0 = most urgent) run the other way.
      return `${CATEGORY_RANK_SQL} ${flip}, ${TIER_RANK_SQL} ${flip}, e.received_at DESC NULLS LAST`;
    case "relevance":
      if (rankExpr) return `${rankExpr} DESC, e.received_at DESC NULLS LAST`;
      return `e.received_at DESC NULLS LAST, e.id DESC`;
    case "received":
    default:
      return `e.received_at ${dir} NULLS LAST, e.id ${dir}`;
  }
}

export async function listEmails(q: Queryable, query: InboxQuery): Promise<InboxPage> {
  const base = baseFilters(query);
  const filtered = base.clone();
  if (query.category?.length) filtered.where(`e.category = ANY(${filtered.param(query.category)}::text[])`);

  // The page adds its own parameters (rank, limit, offset); the total must not see them.
  const listing = filtered.clone();
  // Bound only when it is used: Postgres rejects a parameter it cannot type.
  const rankExpr = query.q && query.sort === "relevance"
    ? `ts_rank(e.search_vector, websearch_to_tsquery('english', ${listing.param(query.q)}))`
    : null;
  const limit = listing.param(query.pageSize);
  const offset = listing.param((query.page - 1) * query.pageSize);

  const [items, total, counts] = await Promise.all([
    q.query(
      `SELECT ${LIST_COLUMNS} FROM raw_emails e ${listing.whereSql}
        ORDER BY ${orderBy(query, rankExpr)} LIMIT ${limit} OFFSET ${offset}`,
      listing.params,
    ),
    q.query<{ total: number }>(
      `SELECT count(*) AS total FROM raw_emails e ${filtered.whereSql}`,
      filtered.params,
    ),
    q.query<{ category: Category | null; count: number; unread: number }>(
      `SELECT e.category, count(*) AS count, count(*) FILTER (WHERE NOT e.is_read) AS unread
         FROM raw_emails e ${base.whereSql} GROUP BY e.category`,
      base.params,
    ),
  ]);

  const byCategory = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<Category, number>;
  let unread = 0;
  for (const row of counts.rows) {
    if (row.category) byCategory[row.category] = row.count;
    unread += row.unread;
  }
  return {
    items: items.rows.map(toListItem),
    total: total.rows[0]?.total ?? 0,
    page: query.page,
    pageSize: query.pageSize,
    counts: { byCategory, unread },
  };
}

export async function getEmail(q: Queryable, id: number): Promise<EmailDetail> {
  const { rows } = await q.query(
    `SELECT ${LIST_COLUMNS}, e.body_text, e.recipient_email, e.cc, e.thread_id,
            e.processed, e.message_id
       FROM raw_emails e WHERE e.id = $1`,
    [id],
  );
  const row = rows[0];
  if (!row) throw notFound("Email");

  const [commitments, timeline, reply] = await Promise.all([
    q.query(
      `SELECT ${COMMITMENT_COLUMNS} FROM commitments c JOIN raw_emails e ON e.id = c.email_id
        WHERE c.email_id = $1 ORDER BY c.deadline NULLS LAST, c.id`,
      [id],
    ),
    q.query("SELECT * FROM events WHERE correlation_id = $1 ORDER BY id", [emailCorrelation(id)]),
    q.query<{ sent_at: string; minutes: number }>(
      `SELECT s.sent_at, EXTRACT(EPOCH FROM (s.sent_at - e.received_at)) / 60 AS minutes
         FROM sent_messages s JOIN raw_emails e ON e.message_id = s.in_reply_to
        WHERE e.id = $1 AND s.sent_at >= e.received_at
        ORDER BY s.sent_at LIMIT 1`,
      [id],
    ),
  ]);

  return {
    ...toListItem(row),
    body: row.body_text,
    recipientEmail: row.recipient_email,
    cc: row.cc,
    threadId: row.thread_id,
    processed: row.processed,
    commitments: commitments.rows.map(toCommitment),
    timeline: timeline.rows.map(toActivityEvent),
    repliedAt: utc(reply.rows[0]?.sent_at),
    responseMinutes: reply.rows[0] ? Math.round(Number(reply.rows[0].minutes)) : null,
  };
}

/** A user's category is final; null hands the email back to the classifier. */
async function setCategory(q: Queryable, jobs: TxJobs, ids: number[], category: Category | null) {
  if (category) {
    await q.query(
      `UPDATE raw_emails SET category = $2, category_source = 'user',
              category_reason = 'Chosen by you.'
        WHERE id = ANY($1::int[])`,
      [ids, category],
    );
    return;
  }
  await q.query(
    "UPDATE raw_emails SET category_source = NULL WHERE id = ANY($1::int[]) AND category_source = 'user'",
    [ids],
  );
  // Cheap and idempotent, so each hand-back gets its own job rather than
  // coalescing with one that may cover different emails.
  await jobs.enqueue("classify_emails", { email_ids: ids }, { key: `classify_emails:${randomUUID()}` });
}

const NOW = "(now() at time zone 'utc')";

export function inboxRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (req, res) => {
    res.json(await listEmails(deps.db, parse(inboxQuerySchema, req.query)));
  });

  router.get("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    res.json(await getEmail(deps.db, id));
  });

  router.patch("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const patch = parse(emailPatchSchema, req.body);

    await deps.jobs.withJobs(async (q, jobs) => {
      const exists = await q.query("SELECT 1 FROM raw_emails WHERE id = $1 FOR UPDATE", [id]);
      if (!exists.rows[0]) throw notFound("Email");

      const notes: string[] = [];
      if (patch.isRead !== undefined) {
        await q.query("UPDATE raw_emails SET is_read = $2 WHERE id = $1", [id, patch.isRead]);
      }
      if (patch.isStarred !== undefined) {
        await q.query("UPDATE raw_emails SET is_starred = $2 WHERE id = $1", [id, patch.isStarred]);
        notes.push(patch.isStarred ? "Starred" : "Unstarred");
      }
      if (patch.archived !== undefined) {
        await q.query(
          `UPDATE raw_emails SET archived_at = ${patch.archived ? `COALESCE(archived_at, ${NOW})` : "NULL"} WHERE id = $1`,
          [id],
        );
        notes.push(patch.archived ? "Archived" : "Moved back to the inbox");
      }
      if (patch.deleted !== undefined) {
        await q.query(
          `UPDATE raw_emails SET deleted_at = ${patch.deleted ? `COALESCE(deleted_at, ${NOW})` : "NULL"} WHERE id = $1`,
          [id],
        );
        notes.push(patch.deleted ? "Moved to trash" : "Restored from trash");
      }
      if (patch.category !== undefined) {
        await setCategory(q, jobs, [id], patch.category);
        notes.push(patch.category ? `Filed under ${CATEGORY_LABELS[patch.category]} by you` : "Category handed back to automatic sorting");
      }
      // Opening an email marks it read; that alone is not worth an audit entry.
      if (notes.length) {
        await recordEvent(q, {
          type: "email.updated",
          message: notes.join(" · "),
          entityType: "email",
          entityId: id,
          correlationId: emailCorrelation(id),
          payload: { changes: patch },
        });
      }
    });
    res.json(await getEmail(deps.db, id));
  });

  router.post("/bulk", async (req, res) => {
    const body = parse(emailBulkSchema, req.body);
    const updated = await deps.jobs.withJobs(async (q, jobs) => {
      const ids = body.ids;
      const run = (sql: string, extra: unknown[] = []) =>
        q.query(`${sql} WHERE id = ANY($1::int[])`, [ids, ...extra]).then((r) => r.rowCount);
      let count = 0;
      let label = "";
      switch (body.action) {
        case "markRead": count = await run("UPDATE raw_emails SET is_read = true"); label = "Marked as read"; break;
        case "markUnread": count = await run("UPDATE raw_emails SET is_read = false"); label = "Marked as unread"; break;
        case "star": count = await run("UPDATE raw_emails SET is_starred = true"); label = "Starred"; break;
        case "unstar": count = await run("UPDATE raw_emails SET is_starred = false"); label = "Unstarred"; break;
        case "archive": count = await run(`UPDATE raw_emails SET archived_at = COALESCE(archived_at, ${NOW})`); label = "Archived"; break;
        case "unarchive": count = await run("UPDATE raw_emails SET archived_at = NULL"); label = "Moved back to the inbox"; break;
        case "delete": count = await run(`UPDATE raw_emails SET deleted_at = COALESCE(deleted_at, ${NOW})`); label = "Moved to trash"; break;
        case "restore": count = await run("UPDATE raw_emails SET deleted_at = NULL"); label = "Restored from trash"; break;
        case "setCategory":
          await setCategory(q, jobs, ids, body.category);
          count = ids.length;
          label = body.category ? `Filed under ${CATEGORY_LABELS[body.category]}` : "Handed back to automatic sorting";
          break;
        case "addTag": {
          const tag = await q.query<{ name: string }>("SELECT name FROM tags WHERE id = $1", [body.tagId]);
          if (!tag.rows[0]) throw notFound("Tag");
          const inserted = await q.query(
            `INSERT INTO email_tags (email_id, tag_id)
             SELECT id, $2 FROM raw_emails WHERE id = ANY($1::int[])
             ON CONFLICT DO NOTHING`,
            [ids, body.tagId],
          );
          count = inserted.rowCount;
          label = `Tagged "${tag.rows[0].name}"`;
          break;
        }
        case "removeTag": {
          const removed = await q.query(
            "DELETE FROM email_tags WHERE email_id = ANY($1::int[]) AND tag_id = $2",
            [ids, body.tagId],
          );
          count = removed.rowCount;
          label = "Tag removed";
          break;
        }
      }
      await recordEvent(q, {
        type: "email.bulk_updated",
        message: `${label}: ${count} email${count === 1 ? "" : "s"}`,
        entityType: "email",
        payload: { action: body.action, ids: ids.slice(0, 100), count },
      });
      return count;
    });
    res.json({ updated });
  });

  return router;
}
