/**
 * The Express application, assembled from its dependencies.
 *
 * Order matters, and is deliberate:
 *
 *  1. Security headers and request logging wrap everything.
 *  2. /ext/* (the Gmail panel) and /calendar.ics are forwarded to the worker
 *     *before* sessions and CSRF: neither carries a browser session — the panel
 *     authenticates with its own token, checked by the worker, and calendar
 *     apps cannot authenticate at all.
 *  3. /api/* gets JSON parsing, the session, CSRF protection and rate limits.
 *  4. The built React app is served for every other GET, so client-side routes
 *     survive a page reload.
 */
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

import express, { Router, type Express } from "express";
import helmet from "helmet";
import { pinoHttp } from "pino-http";

import { csrfProtection, loadSession, requireAuth } from "./auth/middleware";
import { authRouter } from "./auth/routes";
import type { Deps } from "./deps";
import { findRepoRoot } from "./env";
import { errorHandler, unknownRoute } from "./http/errors";
import { buildLimits } from "./http/rateLimit";
import { notificationsRouter, privacyRouter, settingsRouter } from "./modules/account";
import { activityRouter, jobsRouter, syncRouter } from "./modules/activity";
import { analyticsRouter } from "./modules/analytics";
import { calendarIntelRouter } from "./modules/calendarIntel";
import { calendarRouter, commitmentsRouter, relationshipsRouter } from "./modules/commitments";
import { contactsRouter } from "./modules/contacts";
import { inboxRouter } from "./modules/inbox";
import { tagsRouter, viewsRouter } from "./modules/organize";
import { setupRouter } from "./modules/setup";
import { systemRouter } from "./modules/system";

export function createApp(deps: Deps): Express {
  const app = express();
  const { env } = deps;
  const limits = buildLimits(deps.redis, env.REDIS_KEY_PREFIX);

  app.disable("x-powered-by");
  app.set("trust proxy", env.TRUST_PROXY);

  app.use(
    helmet({
      contentSecurityPolicy: {
        directives: {
          defaultSrc: ["'self'"],
          scriptSrc: ["'self'"],
          // Charting and animation libraries set inline style attributes.
          styleSrc: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
          fontSrc: ["'self'", "https://fonts.gstatic.com", "data:"],
          imgSrc: ["'self'", "data:"],
          connectSrc: ["'self'", "ws:", "wss:"],
          frameAncestors: ["'none'"],
          objectSrc: ["'none'"],
          baseUri: ["'self'"],
          formAction: ["'self'"],
        },
      },
      // The Gmail panel fetches /ext/* from an extension origin.
      crossOriginResourcePolicy: { policy: "cross-origin" },
    }),
  );

  app.use(
    pinoHttp({
      logger: deps.log,
      quietReqLogger: true,
      autoLogging: { ignore: (req) => req.url === "/api/system/health" },
      redact: {
        paths: [
          "req.headers.cookie",
          "req.headers['x-csrf-token']",
          "req.headers['x-tracker-token']",
          "res.headers['set-cookie']",
        ],
        censor: "[redacted]",
      },
    }),
  );

  // --- Forwarded to the worker, no session --------------------------------
  app.get("/calendar.ics", (req, res) => deps.worker.forward(req, res, "/calendar.ics"));
  app.use("/ext", express.json({ limit: "512kb" }), (req, res) =>
    deps.worker.forward(req, res, `/api${req.path}`),
  );

  // --- The API --------------------------------------------------------------
  const api = Router();
  api.use(express.json({ limit: "100kb" }));
  api.use(loadSession(deps.systemDb, env.SESSION_TTL_HOURS));
  api.use(csrfProtection(env.APP_ORIGINS));
  api.use(limits.api);

  api.get("/health", (_req, res) => {
    res.json({ ok: true });
  });
  api.use("/auth", authRouter(deps, limits));

  // Everything below needs a signed-in owner.
  const owner = Router();
  owner.use(requireAuth);
  owner.use("/emails", inboxRouter(deps));
  owner.use("/tags", tagsRouter(deps));
  owner.use("/views", viewsRouter(deps));
  owner.use("/commitments", commitmentsRouter(deps));
  owner.use("/calendar", calendarRouter(deps));
  owner.use("/calendar", calendarIntelRouter(deps));
  owner.use("/relationships", relationshipsRouter(deps));
  owner.use("/contacts", contactsRouter(deps));
  owner.use("/activity", activityRouter(deps));
  owner.use("/jobs", jobsRouter(deps));
  owner.use("/sync", syncRouter(deps));
  owner.use("/notifications", notificationsRouter(deps));
  owner.use("/settings", settingsRouter(deps));
  owner.use("/privacy", privacyRouter(deps, limits));
  owner.use("/analytics", analyticsRouter(deps));
  owner.use("/system", systemRouter(deps));
  owner.use("/setup", setupRouter(deps, limits));
  api.use(owner);
  api.use(unknownRoute);

  app.use("/api", api);

  // --- The React app ----------------------------------------------------------
  // Relative to the repository, not the working directory: npm runs workspace
  // scripts from apps/server, where "apps/web/dist" would not exist.
  const webDist = env.WEB_DIST ? resolve(findRepoRoot(), env.WEB_DIST) : null;
  if (webDist && existsSync(join(webDist, "index.html"))) {
    app.use(express.static(webDist, { index: false, maxAge: "1h" }));
    app.get(/^(?!\/(api|ext|ws)\b).*/, (_req, res) => {
      res.setHeader("Cache-Control", "no-cache");
      res.sendFile(join(webDist, "index.html"));
    });
  }

  app.use(unknownRoute);
  app.use(errorHandler(deps.log));
  return app;
}
