/** The defences, checked against the running server rather than assumed. */
import { expect, test } from "@playwright/test";

import { createUser, empty, PASSWORD } from "../../db";

const ORIGIN = "http://127.0.0.1:4400";

test.beforeEach(async () => {
  await empty();
  await createUser("alice@example.com", "Alice");
});

test("pages are served with strict security headers", async ({ request }) => {
  const res = await request.get("/");
  const headers = res.headers();
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["content-security-policy"]).toContain("script-src 'self'");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("the API answers nobody who is not signed in", async ({ request }) => {
  for (const path of ["/api/emails", "/api/commitments", "/api/settings", "/api/privacy/export"]) {
    expect((await request.get(path)).status(), path).toBe(401);
  }
});

test("the session cookie cannot be read by scripts or sent by other sites", async ({ request }) => {
  const res = await request.post("/api/auth/login", {
    data: { email: "alice@example.com", password: PASSWORD },
    headers: { Origin: ORIGIN },
  });
  expect(res.status()).toBe(200);
  const cookie = res.headers()["set-cookie"] ?? "";
  expect(cookie).toMatch(/cm_session=/);
  expect(cookie).toMatch(/HttpOnly/i);
  expect(cookie).toMatch(/SameSite=Strict/i);
});

test("a request from another site is refused, even with a valid password", async ({ request }) => {
  const res = await request.post("/api/auth/login", {
    data: { email: "alice@example.com", password: PASSWORD },
    headers: { Origin: "https://evil.example" },
  });
  expect(res.status()).toBe(403);
});

test("a signed-in change without the CSRF token is refused", async ({ request }) => {
  const login = await request.post("/api/auth/login", {
    data: { email: "alice@example.com", password: PASSWORD },
    headers: { Origin: ORIGIN },
  });
  expect(login.status()).toBe(200);
  const res = await request.put("/api/settings/appearance", {
    data: { theme: "dark", density: "compact", reducedMotion: false },
    headers: { Origin: ORIGIN },
  });
  expect(res.status()).toBe(403);
});
