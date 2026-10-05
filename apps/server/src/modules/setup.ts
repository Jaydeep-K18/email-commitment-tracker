/**
 * First-run setup and integrations.
 *
 * Input is validated here, then the action is forwarded to the worker's
 * internal API — the worker is the process with the keyring, the local Ollama
 * and the browser for Google's sign-in. Nothing secret comes back: the mailbox
 * password goes one way, into the OS keyring.
 */
import { mailboxSchema, type SetupStatus } from "@commitmail/shared";
import { Router } from "express";

import type { Deps } from "../deps";
import type { Limits } from "../http/rateLimit";
import { parse } from "../http/validate";

/** Google's consent screen waits on a person; give them time to read it. */
const SIGN_IN_TIMEOUT_MS = 5 * 60_000;

export function setupRouter(deps: Deps, limits: Limits): Router {
  const router = Router();
  const { worker } = deps;

  router.get("/status", async (_req, res) => {
    res.json(await worker.internal<SetupStatus>("GET", "/setup/status"));
  });

  router.post("/mailbox/test", limits.sensitive, async (req, res) => {
    const body = parse(mailboxSchema, req.body);
    res.json(await worker.internal("POST", "/setup/mailbox/test", body, 30_000));
  });

  router.post("/mailbox", limits.sensitive, async (req, res) => {
    const body = parse(mailboxSchema, req.body);
    const result = await worker.internal<{ ok: boolean; message: string }>("POST", "/setup/mailbox", body, 30_000);
    if (result.ok) {
      // A newly connected mailbox should not wait for the next scheduled check.
      await deps.jobs.withJobs((_q, jobs) => jobs.enqueueUnlessActive("fetch_mailbox"));
    }
    res.json(result);
  });

  router.post("/google/sign-in", async (_req, res) => {
    res.json(await worker.internal("POST", "/setup/google/sign-in", undefined, SIGN_IN_TIMEOUT_MS));
  });

  router.post("/google/calendar", async (_req, res) => {
    const result = await worker.internal("POST", "/setup/google/calendar", undefined, SIGN_IN_TIMEOUT_MS);
    await deps.jobs.withJobs((_q, jobs) => jobs.enqueueUnlessActive("publish_calendar"));
    res.json(result);
  });

  router.delete("/google", async (_req, res) => {
    res.json(await worker.internal("DELETE", "/setup/google"));
  });

  router.get("/extension-token", async (_req, res) => {
    res.json(await worker.internal("GET", "/extension-token"));
  });

  router.post("/extension-token/rotate", limits.sensitive, async (_req, res) => {
    res.json(await worker.internal("POST", "/extension-token/rotate"));
  });

  return router;
}
