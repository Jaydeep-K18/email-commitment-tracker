import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderApp } from "./render";
import { lastRequest, sent } from "./server";

const LAZY = { timeout: 5_000 };

describe("inbox", () => {
  it("lists mail with its category, and counts it", async () => {
    renderApp("/inbox");
    expect(await screen.findByText("Project review on Friday", undefined, LAZY)).toBeInTheDocument();
    expect(screen.getByText("Standup moved to 10")).toBeInTheDocument();
    expect(screen.getByText("2 emails")).toBeInTheDocument();
  });

  it("filters by category through the API", async () => {
    const { user } = renderApp("/inbox");
    await screen.findByText("Project review on Friday", undefined, LAZY);
    const tabs = screen.getByRole("tablist", { name: "Category" });
    await user.click(within(tabs).getByRole("tab", { name: /Meetings/ }));
    await waitFor(() => expect(lastRequest("GET", "/api/emails")?.url.searchParams.get("category")).toBe("meeting"));
  });

  it("searches when the search form is submitted", async () => {
    const { user } = renderApp("/inbox");
    await screen.findByText("Project review on Friday", undefined, LAZY);
    await user.type(screen.getByRole("textbox", { name: "Search" }), "slides{Enter}");
    await waitFor(() => {
      const params = lastRequest("GET", "/api/emails")?.url.searchParams;
      expect(params?.get("q")).toBe("slides");
      expect(params?.get("sort")).toBe("relevance");
    });
  });

  it("opens an unread email and marks it read", async () => {
    const { user } = renderApp("/inbox");
    await user.click(await screen.findByText("Project review on Friday", undefined, LAZY));
    await waitFor(() => expect(lastRequest("PATCH", "/api/emails/1")?.body).toEqual({ isRead: true }));
    expect(await screen.findByRole("button", { name: "Close email" })).toBeInTheDocument();
  });

  it("does not mark an already-read email again", async () => {
    const { user } = renderApp("/inbox");
    await user.click(await screen.findByText("Standup moved to 10", undefined, LAZY));
    await screen.findByRole("button", { name: "Close email" });
    expect(sent.some((r) => r.method === "PATCH" && r.url.pathname === "/api/emails/2")).toBe(false);
  });

  it("stars without opening", async () => {
    const { user } = renderApp("/inbox");
    await screen.findByText("Project review on Friday", undefined, LAZY);
    await user.click(screen.getAllByRole("button", { name: "Star" })[0]!);
    await waitFor(() => expect(lastRequest("PATCH", "/api/emails/1")?.body).toEqual({ isStarred: true }));
    expect(screen.queryByRole("button", { name: "Close email" })).not.toBeInTheDocument();
  });
});
