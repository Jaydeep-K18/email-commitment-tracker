/** Notifications, settings, and privacy controls. */
import {
  SETTINGS_SCHEMAS,
  SETTINGS_SECTIONS,
  boolish,
  defaultSettings,
  idParam,
  purgeSchema,
  type Settings,
  type SettingsSection,
} from "@commitmail/shared";
import { Router } from "express";
import { z } from "zod";

import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { recordEvent } from "../events/record";
import { notFound } from "../http/errors";
import type { Limits } from "../http/rateLimit";
import { parse } from "../http/validate";
import { toNotification } from "./mappers";

// --- Notifications -------------------------------------------------------------

export function notificationsRouter(deps: Deps): Router {
  const router = Router();
  const listQuery = z.object({
    unread: boolish.optional(),
    limit: z.coerce.number().int().min(1).max(100).default(30),
  });

  router.get("/", async (req, res) => {
    const query = parse(listQuery, req.query);
    const [items, unread] = await Promise.all([
      deps.db.query(
        `SELECT * FROM notifications ${query.unread ? "WHERE read_at IS NULL" : ""}
          ORDER BY id DESC LIMIT $1`,
        [query.limit],
      ),
      deps.db.query<{ count: number }>("SELECT count(*) AS count FROM notifications WHERE read_at IS NULL"),
    ]);
    res.json({ items: items.rows.map(toNotification), unread: unread.rows[0]?.count ?? 0 });
  });

  router.post("/:id/read", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const { rowCount } = await deps.db.query(
      "UPDATE notifications SET read_at = COALESCE(read_at, (now() at time zone 'utc')) WHERE id = $1",
      [id],
    );
    if (!rowCount) throw notFound("Notification");
    res.status(204).end();
  });

  router.post("/read-all", async (_req, res) => {
    const { rowCount } = await deps.db.query(
      "UPDATE notifications SET read_at = (now() at time zone 'utc') WHERE read_at IS NULL",
    );
    res.json({ marked: rowCount });
  });

  return router;
}

// --- Settings -------------------------------------------------------------------

export async function loadSettings(q: Queryable): Promise<Settings> {
  const settings = defaultSettings();
  const { rows } = await q.query<{ section: string; value: unknown }>(
    "SELECT section, value FROM settings WHERE section = ANY($1::text[])",
    [SETTINGS_SECTIONS],
  );
  for (const row of rows) {
    const section = row.section as SettingsSection;
    // Stored values were validated on the way in, but a schema can gain fields
    // since; parsing fills defaults for anything added later.
    const parsed = SETTINGS_SCHEMAS[section].safeParse(row.value);
    if (parsed.success) (settings as Record<string, unknown>)[section] = parsed.data;
  }
  return settings;
}

export function settingsRouter(deps: Deps): Router {
  const router = Router();
  const sectionParam = z.object({ section: z.enum(SETTINGS_SECTIONS as [SettingsSection, ...SettingsSection[]]) });

  router.get("/", async (_req, res) => {
    res.json(await loadSettings(deps.db));
  });

  /** Replace one section. The whole section is sent, so the result is exact. */
  router.put("/:section", async (req, res) => {
    const { section } = parse(sectionParam, req.params);
    const value = parse(SETTINGS_SCHEMAS[section], req.body);
    await deps.jobs.withJobs(async (q, jobs) => {
      await q.query(
        `INSERT INTO settings (section, value) VALUES ($1, $2)
         ON CONFLICT (section) DO UPDATE SET value = EXCLUDED.value, updated_at = (now() at time zone 'utc')`,
        [section, JSON.stringify(value)],
      );
      await recordEvent(q, {
        type: "settings.updated",
        message: `${section[0]!.toUpperCase()}${section.slice(1)} settings saved`,
        entityType: "settings",
        entityId: section,
        payload: { section },
      });
      // Working hours decide which times are suggested instead of a clash.
      if (section === "calendar") await jobs.enqueueUnlessActive("scan_calendar");
    });
    res.json(await loadSettings(deps.db));
  });

  return router;
}

// --- Privacy --------------------------------------------------------------------

export function privacyRouter(deps: Deps, limits: Limits): Router {
  const router = Router();

  /** Everything this app holds about the user, as one JSON file. */
  router.get("/export", limits.sensitive, async (_req, res) => {
    const tables = ["raw_emails", "commitments", "vip_contacts", "tags", "email_tags", "saved_views", "sent_messages", "settings"];
    const data: Record<string, unknown[]> = {};
    for (const table of tables) {
      // search_vector is derived and unreadable; leave it out of the export.
      const { rows } = await deps.db.query(`SELECT * FROM ${table} ORDER BY 1`);
      data[table] = rows.map(({ search_vector: _drop, ...rest }) => rest);
    }
    const events = await deps.db.query("SELECT * FROM events ORDER BY id DESC LIMIT 5000");
    data.events = events.rows;

    await recordEvent(deps.db, {
      type: "data.exported",
      message: "Data exported",
      entityType: "privacy",
      payload: { counts: Object.fromEntries(Object.entries(data).map(([k, v]) => [k, v.length])) },
    });
    const stamp = new Date().toISOString().slice(0, 10);
    res.setHeader("Content-Disposition", `attachment; filename="commitmail-export-${stamp}.json"`);
    res.json({ exportedAt: new Date().toISOString(), format: 1, data });
  });

  /**
   * Delete data. "everything" removes all mail-derived data but keeps the
   * account, settings and contact rules, so the app still works afterwards.
   * Events already on Google Calendar are queued for removal first — deleting
   * their commitments alone would leave them orphaned there.
   */
  router.post("/purge", limits.sensitive, async (req, res) => {
    const { scope } = parse(purgeSchema, req.body);
    const result = await deps.jobs.withJobs(async (q, jobs) => {
      let summary: Record<string, number> = {};
      if (scope === "email_bodies") {
        const { rowCount } = await q.query("UPDATE raw_emails SET body_text = NULL WHERE body_text IS NOT NULL");
        summary = { bodies: rowCount };
      } else if (scope === "activity") {
        const { rowCount } = await q.query("DELETE FROM events");
        summary = { events: rowCount };
      } else {
        const published = await q.query<{ id: number; gcal_event_id: string }>(
          "SELECT id, gcal_event_id FROM commitments WHERE gcal_event_id IS NOT NULL",
        );
        const counts: Record<string, number> = {};
        for (const table of ["notifications", "job_attempts", "jobs", "calendar_flags", "sync_log",
                             "commitments", "email_tags", "raw_emails", "sent_messages", "events",
                             "metric_snapshots"]) {
          counts[table] = (await q.query(`DELETE FROM ${table}`)).rowCount;
        }
        for (const row of published.rows) {
          await jobs.enqueue("remove_google_event", { commitment_id: row.id, event_id: row.gcal_event_id }, {
            key: `gcal_remove:${row.id}:${row.gcal_event_id}`,
          });
        }
        await jobs.enqueueUnlessActive("publish_calendar");   // rewrites the .ics empty
        summary = { ...counts, googleRemovalsQueued: published.rows.length };
      }
      await recordEvent(q, {
        type: "data.purged",
        message: `Deleted ${scope.replace("_", " ")}`,
        entityType: "privacy",
        severity: "warning",
        payload: { scope, ...summary },
      });
      return summary;
    });
    res.json({ scope, ...result });
  });

  return router;
}
