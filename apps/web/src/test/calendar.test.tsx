import type { CalendarFlag, Commitment } from "@commitmail/shared";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "./render";
import { lastRequest, server } from "./server";

const LAZY = { timeout: 5_000 };

function commitment(id: number, subject: string, deadline: string, extra: Partial<Commitment> = {}): Commitment {
  return {
    id, emailId: 10 + id, type: "meeting", subject, deadline, allDay: false,
    counterpartyName: null, counterpartyEmail: null, direction: null, evidenceQuote: "q", confidence: 0.9,
    vipTier: "CRITICAL", status: "pending", manuallyAdded: false, syncApproved: false, calendarSynced: true,
    googleEventId: null, createdAt: "2026-10-08T09:00:00Z",
    decision: { shouldSync: true, reason: "CRITICAL sender", needsReview: false, awaitingApproval: false },
    source: { emailId: 10 + id, subject: `Re: ${subject}`, senderName: null, senderEmail: null },
    ...extra,
  };
}

function flag(overrides: Partial<CalendarFlag>): CalendarFlag {
  return {
    id: 7, kind: "duplicate", status: "open",
    commitment: commitment(2, "Send the Q3 budget report", "2026-10-15T17:00:00", { type: "deadline_on_you" }),
    other: null, external: null, similarity: null, overlap: null, suggestions: [],
    createdAt: "2026-10-08T09:00:00Z", resolvedAt: null,
    ...overrides,
  };
}

function withFlags(...items: CalendarFlag[]) {
  server.use(http.get("/api/calendar/flags", () => HttpResponse.json({ items })));
}

describe("calendar clashes and duplicates", () => {
  it("shows a clash with free times, and copies them for a reply", async () => {
    withFlags(flag({
      kind: "conflict",
      commitment: commitment(2, "Vendor call", "2026-10-13T10:30:00"),
      other: commitment(1, "Budget sync", "2026-10-13T10:00:00"),
      overlap: { start: "2026-10-13T10:30:00", end: "2026-10-13T11:00:00" },
      suggestions: [
        { start: "2026-10-13T09:00:00", end: "2026-10-13T10:00:00" },
        { start: "2026-10-14T10:30:00", end: "2026-10-14T11:30:00" },
      ],
    }));
    const { user } = renderApp("/calendar");

    expect(await screen.findByText("These overlap", {}, LAZY)).toBeInTheDocument();
    expect(screen.getByText("Budget sync")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Re: Vendor call/ })).toHaveAttribute("href", "/inbox/12");

    await user.click(screen.getByRole("button", { name: "Copy times for a reply" }));
    expect(await screen.findByText(/paste it into your reply/)).toBeInTheDocument();
    // user-event stands in for the system clipboard.
    const text = await navigator.clipboard.readText();
    expect(text.split("\n")).toHaveLength(3);
    expect(text).toMatch(/^Would one of these times work instead\?\n• /);
  });

  it("settles a possible duplicate by keeping one copy", async () => {
    withFlags(flag({ other: commitment(1, "Q3 budget report", "2026-10-15T12:00:00", { type: "deadline_on_you" }), similarity: 0.75 }));
    const { user } = renderApp("/calendar");

    expect(await screen.findByText("Possibly the same thing twice", {}, LAZY)).toBeInTheDocument();
    expect(screen.getByText("75% alike")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep the earlier one" }));
    await waitFor(() => expect(lastRequest("POST", "/api/calendar/flags/7/resolve")?.body).toEqual({ keep: 1 }));
  });

  it("offers to remove ours when the event is already on the calendar", async () => {
    withFlags(flag({
      commitment: commitment(3, "Design review with Priya", "2026-10-14T14:00:00"),
      external: { id: "inv", title: "Design review", start: "2026-10-14T14:00:00", end: "2026-10-14T15:00:00", allDay: false, link: "https://calendar.google.com/event?eid=inv" },
    }));
    const { user } = renderApp("/calendar");

    expect(await screen.findByText("Already on your calendar?", {}, LAZY)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open in Google Calendar/ })).toHaveAttribute("href", "https://calendar.google.com/event?eid=inv");
    await user.click(screen.getByRole("button", { name: "Remove ours" }));
    await waitFor(() => expect(lastRequest("POST", "/api/calendar/flags/7/resolve")?.body).toEqual({ keep: "external" }));
  });

  it("stays out of the way when nothing needs attention, and can be asked to look again", async () => {
    const { user } = renderApp("/calendar");

    expect(await screen.findByText("No clashes or duplicates ahead.", {}, LAZY)).toBeInTheDocument();
    expect(screen.queryByText("Needs your attention")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check for clashes" }));
    await waitFor(() => expect(lastRequest("POST", "/api/calendar/scan")).toBeDefined());
  });
});
