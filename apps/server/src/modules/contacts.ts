/**
 * VIP contacts: the rules that decide whose email the model reads and how
 * urgently its commitments are published.
 *
 * Every change queues `apply_vip_rules`, which re-tiers stored email and the
 * commitments it produced — so promoting or demoting a sender takes effect on
 * mail already in the inbox, not just on the next arrival.
 */
import {
  contactCreateSchema,
  contactPatchSchema,
  idParam,
  normalizeMatchValue,
  type Contact,
} from "@commitmail/shared";
import { Router } from "express";

import { utc } from "../db/time";
import type { Deps } from "../deps";
import { recordEvent } from "../events/record";
import { AppError, notFound } from "../http/errors";
import { parse } from "../http/validate";

/**
 * How many stored emails a rule would match — an approximation of Python's
 * matcher (fnmatch for name patterns) good enough for a count on screen.
 */
const MATCHING_EMAILS = `(SELECT count(*) FROM raw_emails e WHERE e.deleted_at IS NULL AND CASE v.match_type
    WHEN 'exact_email' THEN lower(e.sender_email) = v.match_value
    WHEN 'domain' THEN lower(e.sender_email) LIKE '%@' || v.match_value_like
                    OR lower(e.sender_email) LIKE '%.' || v.match_value_like
    ELSE e.sender_name ILIKE '%' || replace(replace(v.match_value_like, '*', '%'), '?', '_') || '%'
  END) AS matching_emails`;

function toContact(row: Record<string, any>): Contact {
  return {
    id: row.id,
    matchType: row.match_type,
    matchValue: row.match_value,
    tier: row.tier,
    displayName: row.display_name,
    createdAt: utc(row.created_at)!,
    matchingEmails: row.matching_emails ?? 0,
  };
}

async function loadContacts(deps: Deps, id?: number): Promise<Contact[]> {
  const { rows } = await deps.db.query(
    `WITH v AS (SELECT *, replace(replace(replace(match_value, '\\', '\\\\'), '%', '\\%'), '_', '\\_')
                           AS match_value_like FROM vip_contacts)
     SELECT v.*, ${MATCHING_EMAILS} FROM v
      ${id ? "WHERE v.id = $1" : ""}
      ORDER BY CASE v.tier WHEN 'CRITICAL' THEN 0 WHEN 'IMPORTANT' THEN 1 WHEN 'MONITOR' THEN 2 ELSE 3 END,
               lower(coalesce(v.display_name, v.match_value))`,
    id ? [id] : [],
  );
  return rows.map(toContact);
}

export function contactsRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (_req, res) => {
    res.json(await loadContacts(deps));
  });

  router.post("/", async (req, res) => {
    const body = parse(contactCreateSchema, req.body);
    const value = normalizeMatchValue(body.matchValue, body.matchType);
    const id = await deps.jobs.withJobs(async (q, jobs) => {
      const inserted = await q
        .query<{ id: number }>(
          `INSERT INTO vip_contacts (match_value, match_type, tier, display_name)
           VALUES ($1, $2, $3, $4) RETURNING id`,
          [value, body.matchType, body.tier, body.displayName || null],
        )
        .catch((error) => {
          if ((error as { code?: string }).code === "23505") {
            throw new AppError(409, "duplicate", "A rule for that sender already exists — change its tier instead.");
          }
          throw error;
        });
      const newId = inserted.rows[0]!.id;
      await recordEvent(q, {
        type: "contact.created",
        message: `${body.tier} rule added for ${value}`,
        entityType: "contact",
        entityId: newId,
        payload: { matchType: body.matchType, matchValue: value, tier: body.tier },
      });
      await jobs.enqueueUnlessActive("apply_vip_rules");
      return newId;
    });
    res.status(201).json((await loadContacts(deps, id))[0]);
  });

  router.patch("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const body = parse(contactPatchSchema, req.body);
    await deps.jobs.withJobs(async (q, jobs) => {
      const { rows } = await q.query<{ match_value: string; tier: string }>(
        `UPDATE vip_contacts
            SET tier = COALESCE($2, tier),
                display_name = CASE WHEN $4 THEN $3 ELSE display_name END
          WHERE id = $1 RETURNING match_value, tier`,
        [id, body.tier ?? null, body.displayName ?? null, body.displayName !== undefined],
      );
      if (!rows[0]) throw notFound("Contact");
      await recordEvent(q, {
        type: "contact.updated",
        message: `Rule for ${rows[0].match_value} is now ${rows[0].tier}`,
        entityType: "contact",
        entityId: id,
        payload: { changes: body },
      });
      if (body.tier) await jobs.enqueueUnlessActive("apply_vip_rules");
    });
    res.json((await loadContacts(deps, id))[0]);
  });

  router.delete("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    await deps.jobs.withJobs(async (q, jobs) => {
      const { rows } = await q.query<{ match_value: string }>(
        "DELETE FROM vip_contacts WHERE id = $1 RETURNING match_value",
        [id],
      );
      if (!rows[0]) throw notFound("Contact");
      await recordEvent(q, {
        type: "contact.deleted",
        message: `Rule for ${rows[0].match_value} removed`,
        entityType: "contact",
        entityId: id,
      });
      await jobs.enqueueUnlessActive("apply_vip_rules");
    });
    res.status(204).end();
  });

  return router;
}
