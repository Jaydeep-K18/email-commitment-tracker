import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityEvent } from "@commitmail/shared";

import { withLiveEvent } from "../lib/realtime";
import { renderApp } from "./render";
import { lastRequest, liveClients } from "./server";

const LAZY = { timeout: 5_000 };

describe("notifications", () => {
  it("shows the unread count and marks everything read", async () => {
    const { user } = renderApp("/activity");
    const bell = await screen.findByRole("button", { name: "Notifications, 2 unread" }, LAZY);
    await user.click(bell);
    expect(await screen.findByText("Couldn't analyse an email")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Mark all read/ }));
    await waitFor(() => expect(lastRequest("POST", "/api/notifications/read-all")).toBeDefined());
  });

  it("opens a notification's link and marks just that one read", async () => {
    const { user } = renderApp("/activity");
    await user.click(await screen.findByRole("button", { name: "Notifications, 2 unread" }, LAZY));
    await user.click(await screen.findByText("Important: Project review"));
    await waitFor(() => expect(lastRequest("POST", "/api/notifications/1/read")).toBeDefined());
    expect(await screen.findByRole("button", { name: "Close email" }, LAZY)).toBeInTheDocument();
  });
});

describe("live updates", () => {
  it("connects once signed in and shows a pushed notification as a toast", async () => {
    renderApp("/activity");
    await screen.findByRole("heading", { name: "Activity" }, LAZY);
    expect(await screen.findByText("Updating live")).toBeInTheDocument();
    await waitFor(() => expect(liveClients.length).toBeGreaterThan(0));
    expect(liveClients.at(-1)!.url.pathname).toBe("/ws");

    act(() =>
      liveClients.at(-1)!.send(
        JSON.stringify({
          type: "notification",
          notification: { id: 3, kind: "retry_succeeded", title: "Retry worked", body: "The email was analysed", severity: "success", link: null, readAt: null, createdAt: new Date().toISOString() },
        }),
      ),
    );
    expect(await screen.findByText("Retry worked")).toBeInTheDocument();
  });
});

describe("the live event list", () => {
  const event = (id: number): ActivityEvent => ({
    id, type: "email.received", entityType: "email", entityId: String(id), correlationId: `email:${id}`,
    severity: "info", message: `event ${id}`, payload: {}, source: "worker", createdAt: "2026-10-08T09:30:00Z",
  });

  it("puts the newest event first and keeps a bounded list", () => {
    const list = [event(3), event(2), event(1)].reduceRight<ActivityEvent[]>((acc, e) => withLiveEvent(acc, e, 2), []);
    expect(list.map((e) => e.id)).toEqual([3, 2]);
  });

  it("ignores an event it already has, as Kafka can deliver one twice", () => {
    const once = withLiveEvent([], event(1));
    expect(withLiveEvent(once, event(1))).toBe(once);
  });
});
