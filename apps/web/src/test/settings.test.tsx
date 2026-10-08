import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "./render";
import { lastRequest, server } from "./server";

const LAZY = { timeout: 5_000 };

describe("settings", () => {
  it("saves the theme to the account and applies it", async () => {
    const { user } = renderApp("/settings?tab=appearance");
    await user.click(await screen.findByRole("radio", { name: "Dark" }, LAZY));
    await waitFor(() => expect(lastRequest("PUT", "/api/settings/appearance")?.body).toMatchObject({ theme: "dark" }));
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  it("validates numbers with the server's own rules before saving", async () => {
    const { user } = renderApp("/settings?tab=email");
    const interval = await screen.findByLabelText("Check every", undefined, LAZY);
    await user.clear(interval);
    await user.type(interval, "2");
    expect(screen.getByRole("alert")).toHaveTextContent(/greater than or equal to 5/);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();

    await user.clear(interval);
    await user.type(interval, "30");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(lastRequest("PUT", "/api/settings/email")?.body).toMatchObject({ fetchIntervalMinutes: 30 }));
  });

  it("won't change the password until the two new ones match", async () => {
    const { user } = renderApp("/settings");
    await user.type(await screen.findByLabelText("Current password", undefined, LAZY), "old password 1");
    await user.type(screen.getByLabelText("New password"), "a much better one");
    await user.type(screen.getByLabelText("Confirm new password"), "a much better on");
    expect(screen.getByText("The passwords don't match.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
  });

  it("points at the current-password field when it is wrong", async () => {
    server.use(
      http.post("/api/auth/password", () =>
        HttpResponse.json({ error: { code: "wrong_password", message: "Your current password is incorrect." } }, { status: 400 })),
    );
    const { user } = renderApp("/settings");
    await user.type(await screen.findByLabelText("Current password", undefined, LAZY), "not it");
    await user.type(screen.getByLabelText("New password"), "a much better one");
    await user.type(screen.getByLabelText("Confirm new password"), "a much better one");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("That isn't your current password.")).toBeInTheDocument();
  });

  it("asks for DELETE to be typed before purging", async () => {
    server.use(http.post("/api/privacy/purge", () => HttpResponse.json({ scope: "activity", events: 12 })));
    const { user } = renderApp("/settings?tab=privacy");
    await user.click(await screen.findByRole("button", { name: "Clear" }, LAZY));

    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Clear the activity log" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "delete");
    expect(confirm).toBeDisabled();
    await user.clear(within(dialog).getByRole("textbox"));
    await user.type(within(dialog).getByRole("textbox"), "DELETE");
    await user.click(confirm);

    await waitFor(() => expect(lastRequest("POST", "/api/privacy/purge")?.body).toEqual({ scope: "activity", confirm: "DELETE" }));
  });
});
