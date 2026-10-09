import { expect, type Page } from "@playwright/test";

import { PASSWORD } from "../../db";

/** The app server's origin (playwright.config.ts): state-changing requests must come from it. */
export const APP = "http://127.0.0.1:4400";

/** Sign in through the API (fast), sharing the browser's cookies; then open `path`. */
export async function signIn(page: Page, email: string, path = "/"): Promise<void> {
  const res = await page.request.post("/api/auth/login", { data: { email, password: PASSWORD }, headers: { Origin: APP } });
  expect(res.status(), await res.text()).toBe(200);
  await page.goto(path);
}

/** Errors the page logged, so a test can insist there were none. */
export function consoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}
