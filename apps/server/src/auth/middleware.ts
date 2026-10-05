import type { NextFunction, Request, RequestHandler, Response } from "express";

import type { Queryable } from "../db/types";
import { readCookie } from "../http/cookies";
import { AppError } from "../http/errors";
import { constantTimeEqual, findSession, SESSION_COOKIE, type Session } from "./sessions";

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      session?: Session;
    }
  }
}

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Attach the session, if the request carries a live one. Never rejects. */
export function loadSession(db: Queryable, ttlHours: number): RequestHandler {
  return async (req, _res, next) => {
    const token = readCookie(req, SESSION_COOKIE);
    if (token) req.session = (await findSession(db, token, ttlHours)) ?? undefined;
    next();
  };
}

export const requireAuth: RequestHandler = (req, _res, next) => {
  if (!req.session) throw new AppError(401, "unauthenticated", "Please sign in.");
  next();
};

/**
 * Two checks on every state-changing request, each enough to stop a forged
 * cross-site request on its own:
 *
 * 1. Origin. Browsers set it on cross-origin POSTs and pages cannot fake it; a
 *    request from any origin not on the allow-list is refused outright.
 * 2. The CSRF token. A signed-in session must echo its token in
 *    X-CSRF-Token. The forging page cannot read it — same-origin policy — so
 *    even an allowed-origin bug elsewhere would not be enough.
 *
 * SameSite=Strict on the cookie is a third, browser-side layer.
 */
export function csrfProtection(allowedOrigins: string[]): RequestHandler {
  const allowed = new Set(allowedOrigins);
  return (req: Request, _res: Response, next: NextFunction) => {
    if (!UNSAFE_METHODS.has(req.method)) return next();

    const origin = req.headers.origin;
    if (origin && !allowed.has(origin)) {
      throw new AppError(403, "bad_origin", "Requests from this origin are not allowed.");
    }
    if (req.session) {
      const supplied = req.headers["x-csrf-token"];
      if (typeof supplied !== "string" || !constantTimeEqual(supplied, req.session.csrfToken)) {
        throw new AppError(403, "bad_csrf_token", "Missing or invalid CSRF token. Reload the page.");
      }
    }
    next();
  };
}
