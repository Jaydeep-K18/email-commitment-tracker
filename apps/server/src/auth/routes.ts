/**
 * Accounts and sessions. These routes run before anyone is known, so they use
 * the system connection; everything after requireAuth acts as the user.
 */
import {
  changePasswordSchema,
  loginSchema,
  profilePatchSchema,
  setupSchema,
  type SessionInfo,
} from "@commitmail/shared";
import { Router, type Request } from "express";

import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { recordEvent } from "../events/record";
import { AppError } from "../http/errors";
import type { Limits } from "../http/rateLimit";
import { parse } from "../http/validate";
import { requireAuth } from "./middleware";
import { decoyHash, hashPassword, verifyPassword } from "./password";
import {
  clearSessionCookie,
  createSession,
  destroyOtherSessions,
  destroySession,
  setSessionCookie,
  type Session,
  type SessionUser,
} from "./sessions";

/** Left by migration 0005 for mail imported before anyone signed up. */
const UNCLAIMED = "unclaimed@localhost";

interface UserRow {
  id: number;
  email: string;
  display_name: string | null;
  password_hash: string | null;
  is_admin: boolean;
  disabled_at: string | null;
}

const asSessionUser = (row: UserRow): SessionUser => ({
  id: row.id,
  email: row.email,
  displayName: row.display_name,
  isAdmin: row.is_admin,
});

/** Whether anyone can sign in yet. Until then the app offers to create the first account. */
async function hasAccount(q: Queryable): Promise<boolean> {
  const { rows } = await q.query("SELECT 1 FROM users WHERE email <> $1 LIMIT 1", [UNCLAIMED]);
  return rows.length > 0;
}

function sessionInfo(session: Session | undefined, setupRequired: boolean): SessionInfo {
  return {
    authenticated: !!session,
    setupRequired,
    user: session
      ? { email: session.user.email, displayName: session.user.displayName, isAdmin: session.user.isAdmin }
      : null,
    csrfToken: session?.csrfToken ?? null,
  };
}

function context(req: Request) {
  return { userAgent: req.headers["user-agent"] ?? null, ip: req.ip ?? null };
}

export function authRouter(deps: Deps, limits: Limits): Router {
  const router = Router();
  const { env, systemDb } = deps;

  router.get("/session", async (req, res) => {
    res.json(sessionInfo(req.session, !req.session && !(await hasAccount(systemDb))));
  });

  /**
   * First run only: create the first account, which runs the deployment. It
   * claims any mail imported before it existed.
   */
  router.post("/setup", limits.setup, async (req, res) => {
    const body = parse(setupSchema, req.body);
    const passwordHash = await hashPassword(body.password);

    const { token, session } = await systemDb.transaction(async (q) => {
      // Serialises racing setups: the second waits, then finds an account.
      await q.query("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE");
      if (await hasAccount(q)) {
        throw new AppError(409, "setup_complete", "This app already has an administrator. Sign in instead.");
      }
      const claimed = await q.query<UserRow>(
        `UPDATE users SET email = $1, display_name = $2, password_hash = $3, is_admin = true,
                          updated_at = (now() at time zone 'utc')
          WHERE email = $4 RETURNING *`,
        [body.email, body.displayName, passwordHash, UNCLAIMED],
      );
      const user = claimed.rows[0] ?? (await q.query<UserRow>(
        `INSERT INTO users (email, display_name, password_hash, is_admin)
         VALUES ($1, $2, $3, true) RETURNING *`,
        [body.email, body.displayName, passwordHash],
      )).rows[0]!;
      await recordEvent(q, {
        type: "auth.setup",
        message: `Account created for ${body.email}`,
        entityType: "account",
        severity: "success",
        userId: user.id,
      });
      return createSession(q, asSessionUser(user), env.SESSION_TTL_HOURS, context(req));
    });

    setSessionCookie(res, token, env.COOKIE_SECURE, env.SESSION_TTL_HOURS);
    res.status(201).json(sessionInfo(session, false));
  });

  router.post("/login", limits.login, async (req, res) => {
    const body = parse(loginSchema, req.body);
    const { rows } = await systemDb.query<UserRow>(
      "SELECT * FROM users WHERE email = $1 AND disabled_at IS NULL",
      [body.email],
    );
    const user = rows[0];

    // Always run a full verify, even for an unknown email or an account with
    // no password, so the response time does not reveal which it was.
    const valid = await verifyPassword(user?.password_hash ?? (await decoyHash()), body.password);
    if (!user?.password_hash || !valid) {
      await recordEvent(systemDb, {
        type: "auth.login_failed",
        message: "A sign-in attempt failed",
        entityType: "account",
        severity: "warning",
        payload: { ip: req.ip ?? null },
        userId: user?.id ?? null,
      });
      throw new AppError(401, "invalid_credentials", "Email or password is incorrect.");
    }

    const { token, session } = await systemDb.transaction(async (q) => {
      // Rotate: a session id fixed before login (planted by an attacker) must
      // not survive becoming authenticated.
      if (req.session) await destroySession(q, req.session.id);
      await q.query("UPDATE users SET last_login_at = (now() at time zone 'utc') WHERE id = $1", [user.id]);
      await recordEvent(q, {
        type: "auth.login",
        message: "Signed in",
        entityType: "account",
        payload: { ip: req.ip ?? null, userAgent: req.headers["user-agent"] ?? null },
        userId: user.id,
      });
      return createSession(q, asSessionUser(user), env.SESSION_TTL_HOURS, context(req));
    });

    setSessionCookie(res, token, env.COOKIE_SECURE, env.SESSION_TTL_HOURS);
    res.json(sessionInfo(session, false));
  });

  router.post("/logout", requireAuth, async (req, res) => {
    const session = req.session!;
    await systemDb.transaction(async (q) => {
      await destroySession(q, session.id);
      await recordEvent(q, { type: "auth.logout", message: "Signed out", entityType: "account", userId: session.user.id });
    });
    deps.hub?.closeSession(session.id);
    clearSessionCookie(res, env.COOKIE_SECURE);
    res.status(204).end();
  });

  router.post("/password", requireAuth, limits.sensitive, async (req, res) => {
    const body = parse(changePasswordSchema, req.body);
    const session = req.session!;
    const { rows } = await systemDb.query<UserRow>("SELECT * FROM users WHERE id = $1", [session.user.id]);
    const current = rows[0]?.password_hash;
    if (!current || !(await verifyPassword(current, body.currentPassword))) {
      throw new AppError(400, "wrong_password", "Your current password is incorrect.");
    }
    const passwordHash = await hashPassword(body.newPassword);
    const signedOut = await systemDb.transaction(async (q) => {
      await q.query(
        `UPDATE users
            SET password_hash = $1,
                password_changed_at = (now() at time zone 'utc'),
                updated_at = (now() at time zone 'utc')
          WHERE id = $2`,
        [passwordHash, session.user.id],
      );
      const others = await destroyOtherSessions(q, session.user.id, session.id);
      await recordEvent(q, {
        type: "auth.password_changed",
        message: `Password changed; ${others} other session(s) signed out`,
        entityType: "account",
        severity: "success",
        userId: session.user.id,
      });
      return others;
    });
    res.json({ ok: true, otherSessionsSignedOut: signedOut });
  });

  router.patch("/profile", requireAuth, async (req, res) => {
    const patch = parse(profilePatchSchema, req.body);
    const session = req.session!;
    const updated = await systemDb.transaction(async (q) => {
      if (patch.email) {
        const taken = await q.query("SELECT 1 FROM users WHERE email = $1 AND id <> $2", [patch.email, session.user.id]);
        if (taken.rows.length) throw new AppError(409, "email_taken", "Another account already uses that address.");
      }
      const { rows } = await q.query<UserRow>(
        `UPDATE users
            SET email = COALESCE($1, email),
                display_name = COALESCE($2, display_name),
                updated_at = (now() at time zone 'utc')
          WHERE id = $3 RETURNING *`,
        [patch.email ?? null, patch.displayName ?? null, session.user.id],
      );
      await recordEvent(q, {
        type: "settings.updated",
        message: "Profile updated",
        entityType: "settings",
        entityId: "profile",
        payload: { changed: Object.keys(patch) },
        userId: session.user.id,
      });
      return rows[0]!;
    });
    res.json(sessionInfo({ ...session, user: asSessionUser(updated) }, false));
  });

  return router;
}
