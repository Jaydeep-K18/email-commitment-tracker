/** Tags and saved views: how the user organises the inbox. */
import {
  idParam,
  savedViewPatchSchema,
  savedViewSchema,
  tagCreateSchema,
  tagPatchSchema,
  type SavedView,
  type Tag,
} from "@commitmail/shared";
import { Router } from "express";

import type { Deps } from "../deps";
import { AppError, notFound } from "../http/errors";
import { parse } from "../http/validate";

/** Postgres's unique_violation, turned into a message about the name. */
function duplicateName(error: unknown, what: string): never {
  if ((error as { code?: string }).code === "23505") {
    throw new AppError(409, "duplicate", `A ${what} with that name already exists.`);
  }
  throw error;
}

function toTag(row: Record<string, any>): Tag {
  return { id: row.id, name: row.name, color: row.color, emailCount: row.email_count ?? 0 };
}

export function tagsRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (_req, res) => {
    const { rows } = await deps.db.query(
      `SELECT t.*, (SELECT count(*) FROM email_tags et
                      JOIN raw_emails e ON e.id = et.email_id AND e.deleted_at IS NULL
                     WHERE et.tag_id = t.id) AS email_count
         FROM tags t ORDER BY lower(t.name)`,
    );
    res.json(rows.map(toTag));
  });

  router.post("/", async (req, res) => {
    const body = parse(tagCreateSchema, req.body);
    const { rows } = await deps.db
      .query("INSERT INTO tags (name, color) VALUES ($1, $2) RETURNING *", [body.name, body.color])
      .catch((error) => duplicateName(error, "tag"));
    res.status(201).json(toTag(rows[0]!));
  });

  router.patch("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const body = parse(tagPatchSchema, req.body);
    const { rows } = await deps.db
      .query(
        "UPDATE tags SET name = COALESCE($2, name), color = COALESCE($3, color) WHERE id = $1 RETURNING *",
        [id, body.name ?? null, body.color ?? null],
      )
      .catch((error) => duplicateName(error, "tag"));
    if (!rows[0]) throw notFound("Tag");
    res.json(toTag(rows[0]));
  });

  router.delete("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const { rowCount } = await deps.db.query("DELETE FROM tags WHERE id = $1", [id]);
    if (!rowCount) throw notFound("Tag");
    res.status(204).end();
  });

  return router;
}

function toView(row: Record<string, any>): SavedView {
  return { id: row.id, name: row.name, filters: row.filters, sort: row.sort, isPinned: row.is_pinned };
}

export function viewsRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (_req, res) => {
    const { rows } = await deps.db.query(
      "SELECT * FROM saved_views ORDER BY is_pinned DESC, lower(name)",
    );
    res.json(rows.map(toView));
  });

  router.post("/", async (req, res) => {
    const body = parse(savedViewSchema, req.body);
    const { rows } = await deps.db
      .query(
        "INSERT INTO saved_views (name, filters, sort, is_pinned) VALUES ($1, $2, $3, $4) RETURNING *",
        [body.name, JSON.stringify(body.filters), JSON.stringify(body.sort), body.isPinned],
      )
      .catch((error) => duplicateName(error, "view"));
    res.status(201).json(toView(rows[0]!));
  });

  router.patch("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const body = parse(savedViewPatchSchema, req.body);
    const { rows } = await deps.db
      .query(
        `UPDATE saved_views
            SET name = COALESCE($2, name), filters = COALESCE($3, filters), sort = COALESCE($4, sort),
                is_pinned = COALESCE($5, is_pinned), updated_at = (now() at time zone 'utc')
          WHERE id = $1 RETURNING *`,
        [
          id,
          body.name ?? null,
          body.filters ? JSON.stringify(body.filters) : null,
          body.sort ? JSON.stringify(body.sort) : null,
          body.isPinned ?? null,
        ],
      )
      .catch((error) => duplicateName(error, "view"));
    if (!rows[0]) throw notFound("Saved view");
    res.json(toView(rows[0]));
  });

  router.delete("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const { rowCount } = await deps.db.query("DELETE FROM saved_views WHERE id = $1", [id]);
    if (!rowCount) throw notFound("Saved view");
    res.status(204).end();
  });

  return router;
}
