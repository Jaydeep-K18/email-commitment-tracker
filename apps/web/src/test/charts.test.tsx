import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChartCard, Legend, stackTops } from "../components/charts";
import { deadline, duration, percent, wallClockDate } from "../lib/format";
import { renderWithProviders } from "./render";

const table = {
  caption: "Emails per day",
  columns: [
    { key: "date", label: "Day" },
    { key: "count", label: "Emails", align: "right" as const },
  ],
  rows: [
    { date: "Mon", count: 4 },
    { date: "Tue", count: 7 },
  ],
};

describe("charts", () => {
  it("has a legend only when there is more than one series", () => {
    const { rerender } = renderWithProviders(<Legend series={[{ key: "a", label: "Alpha", color: "red" }]} />);
    expect(screen.queryByRole("list", { name: "Legend" })).not.toBeInTheDocument();
    rerender(
      <Legend
        series={[
          { key: "a", label: "Alpha", color: "red" },
          { key: "b", label: "Beta", color: "blue" },
        ]}
      />,
    );
    expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("AlphaBeta");
  });

  it("offers every chart as a table", async () => {
    const { user } = renderWithProviders(
      <ChartCard title="Volume" table={table}>
        <div data-testid="plot" />
      </ChartCard>,
    );
    expect(screen.getByTestId("plot")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Table" }));
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
    const grid = screen.getByRole("table", { name: "Emails per day" });
    expect(within(grid).getAllByRole("row")).toHaveLength(3);
    expect(within(grid).getByText("7")).toBeInTheDocument();
  });

  it("rounds whichever segment is on top of each column", () => {
    const rows = [
      { a: 2, b: 1, c: 3 },
      { a: 2, b: 1, c: 0 },
      { a: 4, b: 0, c: 0 },
      { a: 0, b: 0, c: 0 },
    ];
    expect(stackTops(rows, ["a", "b", "c"])).toEqual(["c", "b", "a", undefined]);
  });

  it("keeps the last chart on screen, dimmed, while refetching", () => {
    renderWithProviders(
      <ChartCard title="Volume" table={table} stale>
        <div data-testid="plot" />
      </ChartCard>,
    );
    expect(screen.getByTestId("plot").closest(".opacity-60")).not.toBeNull();
  });
});

describe("formatting", () => {
  it("shows a deadline at the time the email said, never shifted by zone", () => {
    const date = wallClockDate("2026-10-09T17:00:00");
    expect(date.getHours()).toBe(17);
    expect(date.getDate()).toBe(9);
    expect(deadline("2026-10-09T17:00:00")).toMatch(/17:00|5:00/);
    expect(deadline(null)).toBe("No deadline");
  });

  it("formats durations and percentages for people", () => {
    expect(duration(850)).toBe("850 ms");
    expect(duration(4_200)).toBe("4.2 s");
    expect(duration(95_000)).toBe("1m 35s");
    expect(duration(null)).toBe("—");
    expect(percent(87.5)).toBe("87.5%");
    expect(percent(100)).toBe("100%");
  });
});
