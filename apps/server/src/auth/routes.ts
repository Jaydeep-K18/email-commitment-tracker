import {
  changePasswordSchema,
  loginSchema,
  profilePatchSchema,
  setupSchema,
  type SessionInfo,
} from "@commitmail/shared";
import { Router, type Request } from "express";

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
} from "./sessions";

interface Owner {
  email: string;
  display_name: string | null;
  password_hash: string;
}

async function loadOwner(deps: Deps): Promise<Owner | null> {
  const { rows } = await deps.db.query<Owner>(
    "SELECT email, display_name, password_hash FROM owner_account WHERE id = 1",
  );
  return rows[0] ?? null;
}

function sessionInfo(owner: Owner | null, session: Session | undefined): SessionInfo {
  return {
    authenticated: !!session && !!owner,
    setupRequired: !owner,
    user: session && owner ? { email: owner.email, displayName: owner.display_name } : null,
    csrfToken: session?.csrfToken ?? null,
  };
}

function context(req: Request) {
  return { userAgent: req.headers["user-agent"] ?? null, ip: req.ip ?? null };
}

export function authRouter(deps: Deps, limits: Limits): Router {
  const router = Router();
  const { env } = deps;

  router.get("/session", async (req, res) => {
    res.json(sessionInfo(await loadOwner(deps), req.session));
  });

  /** First run only: create the single owner account and sign in. */
  router.post("/setup", limits.setup, async (req, res) => {
    const body = parse(setupSchema, req.body);
    const passwordHash = await hashPassword(body.password);

    const { token, session } = await deps.db.transaction(async (q) => {
      // ON CONFLICT plus the CHECK (id = 1) constraint: two racing setup
      // requests cannot both create an owner, whatever order they land in.
      const created = await q.query(
        `INSERT INTO owner_account (id, email, display_name, password_hash)
         VALUES (1, $1, $2, $3) ON CONFLICT (id) DO NOTHING`,
        [body.email, body.displayName, passwordHash],
      );
      if (created.rowCount !== 1) {
        throw new AppError(409, "setup_complete", "This app already has an owner. Sign in instead.");
      }
      await recordEvent(q, {
        type: "auth.setup",
        message: `Owner account created for ${body.email}`,
        entityType: "account",
        severity: "success",
      });
      return createSession(q, env.SESSION_TTL_HOURS, context(req));
    });

    setSessionCookie(res, token, env.COOKIE_SECURE, env.SESSION_TTL_HOURS);
    res.status(201).json(sessionInfo(await loadOwner(deps), session));
  });

  router.post("/login", limits.login, async (req, res) => {
    const body = parse(loginSchema, req.body);
    const owner = await loadOwner(deps);
    if (!owner) throw new AppError(409, "setup_required", "Create the owner account first.");

    // Always run a full verify, even for the wrong email, so the response time
    // does not reveal whether the email matched.
    const emailMatches = owner.email.toLowerCase() === body.email;
    const valid = await verifyPassword(emailMatches ? owner.password_hash : await decoyHash(), body.password);

    if (!emailMatches || !valid) {
      await recordEvent(deps.db, {
        type: "auth.login_failed",
        message: "A sign-in attempt failed",
        entityType: "account",
        severity: "warning",
        payload: { ip: req.ip ?? null },
      });
      throw new AppError(401, "invalid_credentials", "Email or password is incorrect.");
    }

    const { token, session } = await deps.db.transaction(async (q) => {
      // Rotate: a session id fixed before login (planted by an attacker) must
      // not survive becoming authenticated.
      if (req.session) await destroySession(q, req.session.id);
      await recordEvent(q, {
        type: "auth.login",
        message: "Signed in",
        entityType: "account",
        payload: { ip: req.ip ?? null, userAgent: req.headers["user-agent"] ?? null },
      });
      return createSession(q, env.SESSION_TTL_HOURS, context(req));
    });

    setSessionCookie(res, token, env.COOKIE_SECURE, env.SESSION_TTL_HOURS);
    res.json(sessionInfo(owner, session));
  });

  router.post("/logout", requireAuth, async (req, res) => {
    const session = req.session!;
    await deps.db.transaction(async (q) => {
      await destroySession(q, session.id);
      await recordEvent(q, { type: "auth.logout", message: "Signed out", entityType: "account" });
    });
    deps.hub?.closeSession(session.id);
    clearSessionCookie(res, env.COOKIE_SECURE);
    res.status(204).end();
  });

  router.post("/password", requireAuth, limits.sensitive, async (req, res) => {
    const body = parse(changePasswordSchema, req.body);
    const owner = (await loadOwner(deps))!;
    if (!(await verifyPassword(owner.password_hash, body.currentPassword))) {
      throw new AppError(400, "wrong_password", "Your current password is incorrect.");
    }
    const passwordHash = await hashPassword(body.newPassword);
    const signedOut = await deps.db.transaction(async (q) => {
      await q.query(
        `UPDATE owner_account
            SET password_hash = $1,
                password_changed_at = (now() at time zone 'utc'),
                updated_at = (now() at time zone 'utc')
          WHERE id = 1`,
        [passwordHash],
      );
      const others = await destroyOtherSessions(q, req.session!.id);
      await recordEvent(q, {
        type: "auth.password_changed",
        message: `Password changed; ${others} other session(s) signed out`,
        entityType: "account",
        severity: "success",
      });
      return others;
    });
    res.json({ ok: true, otherSessionsSignedOut: signedOut });
  });

  router.patch("/profile", requireAuth, async (req, res) => {
    const patch = parse(profilePatchSchema, req.body);
    await deps.db.transaction(async (q) => {
      await q.query(
        `UPDATE owner_account
            SET email = COALESCE($1, email),
                display_name = COALESCE($2, display_name),
                updated_at = (now() at time zone 'utc')
          WHERE id = 1`,
        [patch.email ?? null, patch.displayName ?? null],
      );
      await recordEvent(q, {
        type: "settings.updated",
        message: "Profile updated",
        entityType: "settings",
        entityId: "profile",
        payload: { changed: Object.keys(patch) },
      });
    });
    res.json(sessionInfo(await loadOwner(deps), req.session));
  });

  return router;
}
