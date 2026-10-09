/**
 * Guessing passwords is cut off after five failures. Its own project, run
 * last: every test signs in from 127.0.0.1, and once this one trips the limit
 * that address stays locked out for fifteen minutes.
 */
import { expect, test } from "@playwright/test";

import { createUser, empty, PASSWORD } from "../../db";

test("five wrong passwords lock sign-in, even for the right one", async ({ request }) => {
  await empty();
  await createUser("alice@example.com", "Alice");
  const attempt = (password: string) =>
    request.post("/api/auth/login", {
      data: { email: "alice@example.com", password },
      headers: { Origin: "http://127.0.0.1:4400" },
    });

  for (let i = 0; i < 5; i++) expect((await attempt(`wrong guess number ${i}`)).status()).toBe(401);
  expect((await attempt("wrong guess number 5")).status()).toBe(429);
  expect((await attempt(PASSWORD)).status()).toBe(429);
});
