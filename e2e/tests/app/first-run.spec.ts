/** The first visit: no account yet, so the app asks for one, and that account runs it. */
import { expect, test } from "@playwright/test";

import { empty, PASSWORD, sql } from "../../db";

test.beforeEach(empty);

test("a first visit creates the admin account; signing out and back in works", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup$/);

  await page.getByLabel("Your name").fill("Jaydeep");
  await page.getByLabel("Email").fill("me@example.com");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/onboarding$/);
  await page.getByText("Skip for now").click();
  await expect(page.getByText("Coming up")).toBeVisible();

  const [account] = await sql<{ email: string; is_admin: boolean; password_hash: string }>(
    "SELECT email, is_admin, password_hash FROM users",
  );
  expect(account).toMatchObject({ email: "me@example.com", is_admin: true });
  expect(account!.password_hash).toMatch(/^\$argon2id\$/);   // never the password itself

  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill("me@example.com");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Coming up")).toBeVisible();
});

test("a second visitor cannot create another admin once one exists", async ({ page, request }) => {
  await page.goto("/");
  await page.getByLabel("Your name").fill("First");
  await page.getByLabel("Email").fill("first@example.com");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding$/);

  // `request` is another client entirely: no cookie, as a stranger would be.
  const res = await request.post("/api/auth/setup", {
    data: { displayName: "Second", email: "second@example.com", password: PASSWORD },
    headers: { Origin: "http://127.0.0.1:4400" },
  });
  expect(res.status()).toBe(409);
});
