/**
 * Server-side sessions, stored in Postgres.
 *
 * The browser holds a random 256-bit token in an HttpOnly cookie; the database
 * holds only its SHA-256. Reading the sessions table therefore does not give
 * anyone a usable session, and logging out deletes the row, so a stolen cookie
 * stops working at once — which a self-contained JWT cannot do.
 *
 * Each session also carries a CSRF token. The app sends it back in a header on
 * every state-changing request; a forged cross-site request has the cookie
 * (browsers attach it) but cannot read the token, so it is refused.
 */
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

import type { CookieOptions, Response } from "express";

import type { Queryable } from "../db/types";
import { nowUtcText } from "../db/time";

export const SESSION_COOKIE = "cm_session";

export interface SessionUser {
  id: number;
  email: string;
  displayName: string | null;
  isAdmin: boolean;
}

export interface Session {
  id: string;
  csrfToken: string;
  expiresAt: string;
  user: SessionUser;
}

/** Sliding expiry is renewed at most this often, to avoid a write per request. */
const RENEW_AFTER_MS = 10 * 60 * 1000;

export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

function randomToken(bytes: number): string {
  return randomBytes(bytes).toString("base64url");
}

function addHours(hours: number): string {
  return nowUtcText(new Date(Date.now() + hours * 3_600_000));
}

export async function createSession(
  q: Queryable,
  user: SessionUser,
  ttlHours: number,
  context: { userAgent?: string | null; ip?: string | null },
): Promise<{ token: string; session: Session }> {
  const token = randomToken(32);
  const session: Session = {
    id: hashToken(token),
    csrfToken: randomToken(24),
    expiresAt: addHours(ttlHours),
    user,
  };
  await q.query(
    `INSERT INTO sessions (id, user_id, csrf_token, expires_at, user_agent, ip)
     VALUES ($1, $2, $3, $4, $5, $6)`,
    [
      session.id,
      user.id,
      session.csrfToken,
      session.expiresAt,
      context.userAgent?.slice(0, 255) ?? null,
      context.ip?.slice(0, 64) ?? null,
    ],
  );
  return { token, session };
}

/** The live session for a cookie token, renewing its expiry as it is used. */
export async function findSession(q: Queryable, token: string, ttlHours: number): Promise<Session | null> {
  const { rows } = await q.query<{
    id: string; csrf_token: string; expires_at: string; last_seen_at: string;
    user_id: number; email: string; display_name: string | null; is_admin: boolean;
  }>(
    // A disabled account's sessions stop working at once, not when they expire.
    `SELECT s.id, s.csrf_token, s.expires_at, s.last_seen_at,
            u.id AS user_id, u.email, u.display_name, u.is_admin
       FROM sessions s JOIN users u ON u.id = s.user_id
      WHERE s.id = $1 AND s.expires_at > (now() at time zone 'utc') AND u.disabled_at IS NULL`,
    [hashToken(token)],
  );
  const row = rows[0];
  if (!row) return null;

  const lastSeen = Date.parse(row.last_seen_at.replace(" ", "T") + "Z");
  let expiresAt = row.expires_at;
  if (Date.now() - lastSeen > RENEW_AFTER_MS) {
    expiresAt = addHours(ttlHours);
    await q.query(
      "UPDATE sessions SET last_seen_at = (now() at time zone 'utc'), expires_at = $2 WHERE id = $1",
      [row.id, expiresAt],
    );
  }
  return {
    id: row.id,
    csrfToken: row.csrf_token,
    expiresAt,
    user: { id: row.user_id, email: row.email, displayName: row.display_name, isAdmin: row.is_admin },
  };
}

export async function destroySession(q: Queryable, sessionId: string): Promise<void> {
  await q.query("DELETE FROM sessions WHERE id = $1", [sessionId]);
}

/** Sign one user out everywhere, optionally keeping the session making the request. */
export async function destroyOtherSessions(q: Queryable, userId: number, keepSessionId?: string): Promise<number> {
  const { rowCount } = await q.query(
    "DELETE FROM sessions WHERE user_id = $1 AND id <> $2",
    [userId, keepSessionId ?? ""],
  );
  return rowCount;
}

export async function deleteExpiredSessions(q: Queryable): Promise<number> {
  const { rowCount } = await q.query(
    "DELETE FROM sessions WHERE expires_at <= (now() at time zone 'utc')",
  );
  return rowCount;
}

export function constantTimeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function cookieOptions(secure: boolean, ttlHours: number): CookieOptions {
  return {
    httpOnly: true,       // unreadable from JavaScript, so XSS cannot steal it
    sameSite: "strict",   // never sent on a request another site initiates
    secure,
    path: "/",
    maxAge: ttlHours * 3_600_000,
  };
}

export function setSessionCookie(res: Response, token: string, secure: boolean, ttlHours: number) {
  res.cookie(SESSION_COOKIE, token, cookieOptions(secure, ttlHours));
}

export function clearSessionCookie(res: Response, secure: boolean) {
  res.clearCookie(SESSION_COOKIE, { ...cookieOptions(secure, 0), maxAge: undefined });
}
