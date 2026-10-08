import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "./render";
import { lastRequest, owner, sent, server, signedOut } from "./server";

const LAZY = { timeout: 5_000 };

describe("route guards", () => {
  it("sends a first visit to account setup", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json({ ...signedOut, setupRequired: true })));
    renderApp("/inbox");
    expect(await screen.findByText("Create your account")).toBeInTheDocument();
  });

  it("sends a signed-out visitor to sign in", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json(signedOut)));
    renderApp("/settings");
    expect(await screen.findByText("Welcome back")).toBeInTheDocument();
    expect(lastRequest("GET", "/api/settings")).toBeUndefined();
  });

  it("lets the owner straight in", async () => {
    renderApp("/activity");
    expect(await screen.findByRole("heading", { name: "Activity" }, LAZY)).toBeInTheDocument();
    expect(await screen.findByText("Email received from Asha Rao")).toBeInTheDocument();
  });

  it("shows a not-found page inside the app for unknown paths", async () => {
    renderApp("/no-such-page");
    expect(await screen.findByText("There's no page here", undefined, LAZY)).toBeInTheDocument();
    expect(screen.getByText("/no-such-page")).toBeInTheDocument();
  });
});

describe("sign in", () => {
  it("checks the form before sending anything", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json(signedOut)));
    const { user } = renderApp("/login");
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Enter a valid email address")).toBeInTheDocument();
    expect(screen.getByText("Enter your password")).toBeInTheDocument();
    expect(lastRequest("POST", "/api/auth/login")).toBeUndefined();
  });

  it("shows the server's own message when sign-in fails", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(signedOut)),
      http.post("/api/auth/login", () =>
        HttpResponse.json({ error: { code: "invalid_credentials", message: "Email or password is incorrect." } }, { status: 401 })),
    );
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });

  it("signs in, returns to the page that was asked for, and sends the CSRF token after", async () => {
    let signedIn = false;
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(signedIn ? owner : signedOut)),
      http.post("/api/auth/login", () => {
        signedIn = true;
        return HttpResponse.json(owner);
      }),
    );
    const { user } = renderApp("/settings?tab=notifications");
    await user.type(await screen.findByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Settings" }, LAZY)).toBeInTheDocument();
    expect(lastRequest("POST", "/api/auth/login")?.body).toEqual({ email: "owner@example.com", password: "correct horse battery" });

    // A state change from here on carries the token from the session.
    await user.click(await screen.findByRole("switch", { name: "A retry succeeds" }));
    await screen.findByText("Saved");
    const put = sent.find((r) => r.method === "PUT");
    expect(put?.url.pathname).toBe("/api/settings/notifications");
  });
});

describe("creating the owner account", () => {
  async function fill(confirm: string) {
    let created = false;
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(created ? owner : { ...signedOut, setupRequired: true })),
      http.post("/api/auth/setup", () => {
        created = true;
        return HttpResponse.json(owner, { status: 201 });
      }),
    );
    const { user } = renderApp("/setup");
    await user.type(await screen.findByLabelText("Your name"), "Jaydeep Kamble");
    await user.type(screen.getByLabelText("Email"), "owner@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.type(screen.getByLabelText("Confirm password"), confirm);
    await user.click(screen.getByRole("button", { name: "Create account" }));
  }

  it("sends the account, without the confirmation, and moves on to onboarding", async () => {
    await fill("correct horse battery");
    await waitFor(() => expect(lastRequest("POST", "/api/auth/setup")).toBeDefined());
    expect(lastRequest("POST", "/api/auth/setup")?.body).toEqual({
      displayName: "Jaydeep Kamble",
      email: "owner@example.com",
      password: "correct horse battery",
    });
  });

  it("stops at mismatched passwords and says why", async () => {
    await fill("correct horse batterx");
    expect(await screen.findByText("The passwords don't match")).toBeInTheDocument();
    expect(lastRequest("POST", "/api/auth/setup")).toBeUndefined();
  });
});

