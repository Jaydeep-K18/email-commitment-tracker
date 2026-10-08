/**
 * The pieces every chart is built from, so all of them follow the same rules:
 *
 * - A legend whenever there are two or more series. Identity is never colour
 *   alone; the legend text is in text ink, the colour sits in a swatch beside it.
 * - A table view for every chart — the accessible twin, and the relief for any
 *   series colour that is below 3:1 on the surface.
 * - Hairline, solid gridlines one step off the surface; quiet axes.
 * - Thin marks: columns capped at 24px with a 4px rounded end, 2px surface gaps
 *   between stacked segments, 2px lines.
 * - On refetch the previous chart stays, dimmed, instead of flashing a skeleton.
 *
 * Category colours were validated with the palette checker (see index.css).
 */
import { useState, type ReactNode } from "react";
import { Rectangle, type RectangleProps, type TooltipProps } from "recharts";

import { cn } from "../lib/cn";
import { Card, CardBody, CardHeader } from "./ui/card";
import { Segmented } from "./ui/controls";
import { ErrorState, Skeleton } from "./ui/states";

export interface Series {
  key: string;
  label: string;
  color: string;
  /** How the swatch is drawn: a block for bars/areas, a line for lines. */
  kind?: "block" | "line";
}

export const AXIS = {
  stroke: "var(--text-faint)",
  tick: { fill: "var(--text-faint)", fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

export const GRID = { vertical: false, stroke: "var(--border)" } as const;

/** Separates stacked segments and touching bars with the surface colour. */
export const SEGMENT = { stroke: "var(--surface)", strokeWidth: 2, maxBarSize: 24 } as const;

/** Only the top segment of a stack gets the rounded data-end. */
export const TOP_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

/** For each row, the series whose segment is on top of its column (the last non-zero one). */
export function stackTops(rows: ReadonlyArray<object>, keys: readonly string[]): Array<string | undefined> {
  return rows.map((row) => [...keys].reverse().find((key) => Number((row as Record<string, unknown>)[key]) > 0));
}

/**
 * Shapes for a stacked column chart. The rounded end belongs on whichever
 * segment is on top of each column — a day with no low-priority mail still
 * gets a rounded top — so it is decided per column, not per series.
 */
export function stackedShapes(rows: ReadonlyArray<object>, keys: readonly string[]) {
  const tops = stackTops(rows, keys);
  return (key: string) =>
    function Segment(props: unknown) {
      const p = props as RectangleProps & { index: number };
      return <Rectangle {...p} radius={tops[p.index] === key ? TOP_RADIUS : 0} />;
    };
}

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5" aria-label="Legend">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5 text-[12px] text-muted">
          {s.kind === "line" ? (
            <span className="h-0.5 w-3.5 rounded-full" style={{ background: s.color }} aria-hidden />
          ) : (
            <span className="size-2.5 rounded-[3px]" style={{ background: s.color }} aria-hidden />
          )}
          {s.label}
        </li>
      ))}
    </ul>
  );
}

/** Tooltip content: values in text ink, identity in the swatch. */
export function ChartTooltip({
  active,
  payload,
  label,
  labelFormat = (v) => String(v),
  valueFormat = (v) => (typeof v === "number" ? v.toLocaleString() : String(v)),
  hideZero = true,
}: TooltipProps<number, string> & {
  labelFormat?: (value: unknown) => string;
  valueFormat?: (value: unknown) => string;
  hideZero?: boolean;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((p) => !hideZero || Number(p.value) !== 0);
  const total = payload.reduce((sum, p) => sum + (Number(p.value) || 0), 0);
  return (
    <div className="min-w-40 rounded-lg border border-border bg-surface px-3 py-2 shadow-pop">
      <p className="mb-1.5 text-[12px] font-medium text-text">{labelFormat(label)}</p>
      {rows.length === 0 ? (
        <p className="text-[12px] text-faint">Nothing</p>
      ) : (
        <ul className="space-y-1">
          {rows
            .slice()
            .reverse()
            .map((p) => (
              <li key={String(p.dataKey)} className="flex items-center gap-2 text-[12px]">
                <span className="size-2 rounded-[2px]" style={{ background: p.color }} aria-hidden />
                <span className="text-muted">{p.name}</span>
                <span className="ml-auto pl-3 font-medium text-text tabular-nums">{valueFormat(p.value)}</span>
              </li>
            ))}
        </ul>
      )}
      {payload.length > 1 && rows.length > 1 && (
        <p className="mt-1.5 flex justify-between border-t border-border pt-1.5 text-[12px] text-muted">
          Total <span className="font-medium text-text tabular-nums">{valueFormat(total)}</span>
        </p>
      )}
    </div>
  );
}

export interface TableView {
  columns: Array<{ key: string; label: string; align?: "left" | "right" }>;
  rows: Array<Record<string, ReactNode>>;
  caption: string;
}

function DataTable({ table }: { table: TableView }) {
  return (
    <div className="max-h-72 overflow-auto rounded-lg border border-border">
      <table className="w-full text-[12.5px]">
        <caption className="sr-only">{table.caption}</caption>
        <thead className="sticky top-0 bg-surface-2 text-muted">
          <tr>
            {table.columns.map((c) => (
              <th key={c.key} scope="col" className={cn("px-3 py-2 font-medium", c.align === "right" ? "text-right" : "text-left")}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((c) => (
                <td key={c.key} className={cn("px-3 py-1.5 text-text", c.align === "right" && "text-right tabular-nums")}>
                  {row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartCard({
  title,
  description,
  action,
  series = [],
  table,
  loading,
  error,
  onRetry,
  stale,
  height = 220,
  children,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  series?: Series[];
  table: TableView;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** True while a refetch is showing the previous data. */
  stale?: boolean;
  /** Height of the plot including its axis band, so nothing is cropped. */
  height?: number;
  children: ReactNode;
  className?: string;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Card className={className}>
      <CardHeader
        title={title}
        description={description}
        action={
          <>
            {action}
            <Segmented
              label={`${title} view`}
              value={view}
              onChange={setView}
              options={[{ value: "chart", label: "Chart" }, { value: "table", label: "Table" }]}
            />
          </>
        }
      />
      <CardBody>
        {loading ? (
          <Skeleton className="w-full" style={{ height }} />
        ) : error ? (
          <ErrorState error={error} onRetry={onRetry} />
        ) : view === "table" ? (
          <DataTable table={table} />
        ) : (
          <div className={cn("space-y-3 transition-opacity", stale && "opacity-60")}>
            <Legend series={series} />
            <div style={{ height }} role="img" aria-label={`${title} chart. Switch to the table view for the values.`}>
              {children}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
