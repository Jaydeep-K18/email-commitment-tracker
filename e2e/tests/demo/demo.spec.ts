/**
 * The demo build, as published on GitHub Pages: every page renders from the
 * in-browser sample data, with no errors, on a laptop and on a phone.
 */
import { expect, test } from "@playwright/test";

const PAGES: Array<[string, RegExp]> = [
  ["/", /Good (morning|afternoon|evening)/],
  ["/inbox", /^Inbox$/],
  ["/commitments", /^Commitments$/],
  ["/calendar", /^Calendar$/],
  ["/relationships", /^Relationships$/],
  ["/contacts", /^Contacts$/],
  ["/analytics", /^Analytics$/],
  ["/activity", /^Activity$/],
  ["/jobs", /^Background jobs$/],
  ["/system", /^System/],
  ["/settings", /^Settings$/],
];

for (const [path, title] of PAGES) {
  test(`${path} renders without errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
    page.on("pageerror", (error) => errors.push(error.message));

    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByText("Demo — sample data, nothing is sent anywhere")).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("a clash can be settled in the demo", async ({ page }) => {
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Fine as it is" }).first().click();
  await expect(page.getByText("Left as it is")).toBeVisible();
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

  for (const path of ["/", "/inbox", "/calendar", "/analytics"]) {
    test(`${path} fits the screen without sideways scrolling`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBe(0);
    });
  }
});
