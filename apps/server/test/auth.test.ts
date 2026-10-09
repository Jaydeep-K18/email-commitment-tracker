import request from "supertest";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { hashToken } from "../src/auth/sessions";
import type { Db } from "../src/db/types";
import { buildApp, createTestDb, eventTypes, ORIGIN, OWNER, resetDb, signedIn, type TestApp } from "./helpers";

let db: Db;
let t: TestApp;

beforeAll(async () => {
  ({ db } = await createTestDb());
});
afterAll(async () => db.close());
beforeEach(async () => {
  await resetDb(db);
  t = buildApp(db);
});

const post = (url: string, body: object) => request(t.app).post(url).set("Origin", ORIGIN).send(body);

describe("first-run setup", () => {
  it("reports that setup is needed before an owner exists", async () => {
    const res = await request(t.app).get("/api/auth/session");
    expect(res.body).toEqual({ authenticated: false, setupRequired: true, user: null, csrfToken: null });
  });

  it("creates the owner and signs them in with a locked-down cookie", async () => {
    const res = await post("/api/auth/setup", OWNER);

    expect(res.status).toBe(201);
    expect(res.body.authenticated).toBe(true);
    expect(res.body.csrfToken).toMatch(/^[\w-]{20,}$/);
    const cookie = res.headers["set-cookie"]![0]!;
    expect(cookie).toMatch(/^cm_session=/);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=Strict/i);
  });

  it("allows exactly one owner, ever", async () => {
    await post("/api/auth/setup", OWNER);
    const second = await post("/api/auth/setup", { ...OWNER, email: "intruder@example.com" });
    expect(second.status).toBe(409);
    expect(second.body.error.code).toBe("setup_complete");
  });

  it("refuses a weak password", async () => {
    const res = await post("/api/auth/setup", { ...OWNER, password: "aaaaaaaaaaaa" });
    expect(res.status).toBe(400);
    expect(JSON.stringify(res.body.error.details)).toContain("too repetitive");
  });

  it("stores an argon2id hash, never the password", async () => {
    await post("/api/auth/setup", OWNER);
    const { rows } = await db.query("SELECT password_hash FROM users");
    expect(rows[0]!.password_hash).toMatch(/^\$argon2id\$/);
    expect(rows[0]!.password_hash).not.toContain(OWNER.password);
  });

  it("stores only a hash of the session token", async () => {
    const res = await post("/api/auth/setup", OWNER);
    const token = /cm_session=([^;]+)/.exec(res.headers["set-cookie"]![0]!)![1]!;
    const { rows } = await db.query("SELECT id FROM sessions");
    expect(rows[0]!.id).toBe(hashToken(decodeURIComponent(token)));
    expect(rows[0]!.id).not.toBe(token);
  });
});

describe("signing in", () => {
  beforeEach(async () => {
    await post("/api/auth/setup", OWNER);
  });

  it("rejects a wrong password with a message that does not say which part was wrong", async () => {
    const res = await post("/api/auth/login", { email: OWNER.email, password: "not the password" });
    expect(res.status).toBe(401);
    expect(res.body.error.message).toBe("Email or password is incorrect.");
    expect(await eventTypes(db)).toContain("auth.login_failed");
  });

  it("gives the same answer for an unknown email", async () => {
    const res = await post("/api/auth/login", { email: "nobody@example.com", password: OWNER.password });
    expect(res.status).toBe(401);
    expect(res.body.error.message).toBe("Email or password is incorrect.");
  });

  it("accepts the right password, case-insensitively for the email", async () => {
    const res = await post("/api/auth/login", { email: OWNER.email.toUpperCase(), password: OWNER.password });
    expect(res.status).toBe(200);
    expect(res.body.user.email).toBe(OWNER.email);
  });

  it("issues a new session on login rather than trusting one fixed beforehand", async () => {
    const agent = request.agent(t.app);
    const first = await agent.post("/api/auth/login").set("Origin", ORIGIN).send({ email: OWNER.email, password: OWNER.password });
    const second = await agent.post("/api/auth/login").set("Origin", ORIGIN).send({ email: OWNER.email, password: OWNER.password });
    expect(first.body.csrfToken).not.toBe(second.body.csrfToken);
    const { rows } = await db.query("SELECT count(*)::int AS n FROM sessions");
    expect(rows[0]!.n).toBe(2);   // setup's session + the latest; the middle one was rotated out
  });

  it("locks out after five failed attempts", async () => {
    for (let i = 0; i < 5; i++) {
      await post("/api/auth/login", { email: OWNER.email, password: `wrong-${i}` });
    }
    const blocked = await post("/api/auth/login", { email: OWNER.email, password: OWNER.password });
    expect(blocked.status).toBe(429);
    expect(blocked.body.error.code).toBe("rate_limited");
  });
});

describe("protecting the API", () => {
  it("refuses protected routes without a session", async () => {
    const res = await request(t.app).get("/api/emails");
    expect(res.status).toBe(401);
  });

  it("refuses a state change without the CSRF token", async () => {
    const { agent } = await signedIn(t.app);
    const res = await agent.post("/api/sync").set("Origin", ORIGIN);
    expect(res.status).toBe(403);
    expect(res.body.error.code).toBe("bad_csrf_token");
  });

  it("refuses a state change from another site even with a token", async () => {
    const session = await signedIn(t.app);
    const res = await session.agent.post("/api/sync").set("Origin", "https://evil.example").set("X-CSRF-Token", session.csrf);
    expect(res.status).toBe(403);
    expect(res.body.error.code).toBe("bad_origin");
  });

  it("accepts a state change with both", async () => {
    const session = await signedIn(t.app);
    expect((await session.post("/api/sync")).status).toBe(202);
  });

  it("signing out ends the session for good", async () => {
    const session = await signedIn(t.app);
    expect((await session.post("/api/auth/logout")).status).toBe(204);
    expect((await session.get("/api/emails")).status).toBe(401);
  });

  it("changing the password signs out every other session", async () => {
    const first = await signedIn(t.app);
    const second = request.agent(t.app);
    await second.post("/api/auth/login").set("Origin", ORIGIN).send({ email: OWNER.email, password: OWNER.password });

    const res = await first.post("/api/auth/password", { currentPassword: OWNER.password, newPassword: "a whole new passphrase" });
    expect(res.body.otherSessionsSignedOut).toBe(1);
    expect((await second.get("/api/emails")).status).toBe(401);
    expect((await first.get("/api/emails")).status).toBe(200);
  });

  it("never returns internals in an error", async () => {
    const session = await signedIn(t.app);
    const res = await session.get("/api/emails/not-a-number");
    expect(res.status).toBe(400);
    expect(JSON.stringify(res.body)).not.toMatch(/at \w+ \(|node_modules|SELECT/);
  });

  it("sends security headers", async () => {
    const res = await request(t.app).get("/api/health");
    expect(res.headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(res.headers["x-content-type-options"]).toBe("nosniff");
    expect(res.headers["x-powered-by"]).toBeUndefined();
  });
});
