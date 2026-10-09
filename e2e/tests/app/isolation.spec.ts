/** Two people on one server, in real browsers: neither sees the other's mail, even by typing its address. */
import { expect, test } from "@playwright/test";

import { createUser, empty, seedMailbox } from "../../db";
import { signIn } from "./helpers";

let aliceMail: Record<string, number> = {};

test.beforeEach(async () => {
  await empty();
  const alice = await createUser("alice@example.com", "Alice");   // first, so the admin
  aliceMail = (await seedMailbox(alice, "alice")).emails;
  const bob = await createUser("bob@example.com", "Bob");
  await seedMailbox(bob, "bob");
});

test("each person's inbox holds only their own mail", async ({ page }) => {
  await signIn(page, "bob@example.com", "/inbox");
  await expect(page.getByText("Q3 report due Friday (bob)")).toBeVisible();
  await expect(page.getByText(/\(alice\)/)).toHaveCount(0);

  await page.getByPlaceholder("Search subject, sender, body…").fill("alice");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/\(alice\)/)).toHaveCount(0);
});

test("opening someone else's email by its address shows nothing of it", async ({ page }) => {
  await signIn(page, "bob@example.com", `/inbox/${aliceMail.report}`);
  const panel = page.getByRole("region", { name: "Email" });
  await expect(panel.getByRole("alert")).toContainText("Couldn't load this");
  await expect(page.getByText(/\(for alice\)/)).toHaveCount(0);
});

test("only the admin sees the deployment's health page", async ({ page }) => {
  await signIn(page, "bob@example.com", "/");
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("link", { name: "System health" })).toHaveCount(0);

  await page.context().clearCookies();
  await signIn(page, "alice@example.com", "/");
  await expect(page.getByRole("link", { name: "System health" })).toBeVisible();
});
