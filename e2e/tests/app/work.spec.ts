/** A day's work, signed in: triage mail, approve a commitment, settle the calendar, change a setting. */
import { expect, test } from "@playwright/test";

import { createUser, empty, seedMailbox, sql } from "../../db";
import { consoleErrors, signIn } from "./helpers";

let alice = 0;

test.beforeEach(async () => {
  await empty();
  alice = await createUser("alice@example.com", "Alice");
  await seedMailbox(alice, "alice");
});

test("triage: open an email, star it, archive it", async ({ page }) => {
  const errors = consoleErrors(page);
  await signIn(page, "alice@example.com", "/inbox");

  await page.getByText("Q3 report due Friday (alice)").click();
  const email = page.getByRole("region", { name: "Email" });
  await expect(email.getByText("Could you send me the Q3 report by Friday 5pm? (for alice)")).toBeVisible();

  await email.getByRole("button", { name: "Star" }).click();
  await expect(email.getByRole("button", { name: "Starred" })).toBeVisible();
  await email.getByRole("button", { name: "Archive" }).click();
  await expect(email.getByRole("button", { name: "Unarchive" })).toBeVisible();

  const [row] = await sql<{ is_starred: boolean; archived: boolean }>(
    "SELECT is_starred, archived_at IS NOT NULL AS archived FROM raw_emails WHERE subject LIKE 'Q3 report%'",
  );
  expect(row).toEqual({ is_starred: true, archived: true });
  expect(errors).toEqual([]);
});

test("approve a commitment from the review queue", async ({ page }) => {
  await signIn(page, "alice@example.com", "/commitments?view=review");

  await expect(page.getByText("Lena sends the logo files")).toBeVisible();
  await page.getByRole("button", { name: "Approve for calendar" }).click();
  await expect(page.getByText(/Approved/)).toBeVisible();

  const [row] = await sql<{ sync_approved: boolean }>("SELECT sync_approved FROM commitments WHERE subject = 'Lena sends the logo files'");
  expect(row!.sync_approved).toBe(true);
});

test("settle a possible duplicate and a clash on the calendar", async ({ page }) => {
  await signIn(page, "alice@example.com", "/calendar");

  await expect(page.getByText("Needs your attention")).toBeVisible();
  await page.getByRole("button", { name: "Keep the earlier one" }).click();
  await expect(page.getByText("Kept the earlier one")).toBeVisible();
  await page.getByRole("button", { name: "Fine as it is" }).click();
  await expect(page.getByText("Needs your attention")).toBeHidden();

  const flags = await sql<{ kind: string; status: string }>("SELECT kind, status FROM calendar_flags ORDER BY kind");
  expect(flags).toEqual([{ kind: "conflict", status: "dismissed" }, { kind: "duplicate", status: "resolved" }]);
  const [copy] = await sql<{ status: string }>("SELECT status FROM commitments WHERE subject = 'Send the Q3 report again'");
  expect(copy!.status).toBe("dismissed");
});

test("a setting sticks across a reload", async ({ page }) => {
  await signIn(page, "alice@example.com", "/settings?tab=appearance");

  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
});
