import type { Analytics as AnalyticsData, AnalyticsQuery, Category } from "@commitmail/shared";
import { CalendarCheck, Gauge, Inbox, MessageSquareReply, ShieldCheck, Timer } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AXIS, ChartCard, ChartTooltip, GRID, SEGMENT, stackedShapes, type Series } from "../components/charts";
import { CATEGORY_SERIES, PageHeader, StatCard, TierBadge } from "../components/domain";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Segmented } from "../components/ui/controls";
import { ErrorState } from "../components/ui/states";
import { browserTimeZone, duration, minutes, number, percent, wallClockDate } from "../lib/format";
import { useAnalytics } from "../lib/queries";

const RANGES: Array<{ value: AnalyticsQuery["range"]; label: string }> = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
];

/** Calendar outcomes mean good or bad, so they wear status colours, not categorical ones. */
const CALENDAR_SERIES: Series[] = [
  { key: "created", label: "Added", color: "var(--success)" },
  { key: "removed", label: "Removed", color: "var(--text-faint)" },
  { key: "failed", label: "Failed", color: "var(--danger)" },
];

const dayLabel = (date: unknown) =>
  new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" }).format(wallClockDate(String(date)));

export default function Analytics() {
  const [range, setRange] = useState<AnalyticsQuery["range"]>("30d");
  const tz = browserTimeZone();
  const analytics = useAnalytics({ range, tz });
  const data = analytics.data;

  return (
    <>
      <PageHeader
        title="Analytics"
        description="How much mail arrives, what kind, how fast you answer, and how much of it becomes a calendar commitment."
      />

      {/* One filter row, scoping every figure and chart below it. */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Segmented label="Time range" value={range} onChange={setRange} options={RANGES} />
        <span className="text-[12.5px] text-faint">Days counted in your time zone ({tz})</span>
      </div>

      {analytics.error && !data ? (
        <Card><ErrorState error={analytics.error} onRetry={() => void analytics.refetch()} /></Card>
      ) : (
        <>
          <section aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
            <StatCard
              label="Emails received"
              icon={Inbox}
              value={sum(data?.volume.map((d) => d.total))}
              change={data?.trends.volumeChange}
              loading={analytics.isPending}
              hint={<span>vs previous {range}</span>}
            />
            <StatCard
              label="Needs action"
              icon={Gauge}
              tone="danger"
              value={data?.actionVsInformational.action}
              loading={analytics.isPending}
              hint={data ? <span>{share(data.actionVsInformational.action, data)} of mail</span> : undefined}
            />
            <StatCard
              label="Median reply"
              icon={MessageSquareReply}
              tone="info"
              value={data?.responseTime.medianMinutes ?? null}
              format={minutes}
              loading={analytics.isPending}
              hint={data ? <span>{data.responseTime.replied} replies · p90 {minutes(data.responseTime.p90Minutes)}</span> : undefined}
            />
            <StatCard
              label="Email → calendar"
              icon={CalendarCheck}
              tone="success"
              value={data?.conversion.rate ?? null}
              format={percent}
              change={data?.trends.conversionChange}
              loading={analytics.isPending}
              hint={data ? <span>{data.conversion.onCalendar} of {data.conversion.analyzed} analysed</span> : undefined}
            />
            <StatCard
              label="Analysis success"
              icon={ShieldCheck}
              tone="success"
              value={data?.processing.successRate ?? null}
              format={percent}
              loading={analytics.isPending}
              hint={data ? <span>{data.processing.failed} failed attempt{data.processing.failed === 1 ? "" : "s"}</span> : undefined}
            />
            <StatCard
              label="Analysis time, p95"
              icon={Timer}
              tone="warning"
              value={data?.processing.p95LatencyMs ?? null}
              format={duration}
              loading={analytics.isPending}
              hint={data ? <span>average {duration(data.processing.avgLatencyMs)}</span> : undefined}
            />
          </section>

          <div className="mt-6 grid gap-6 xl:grid-cols-3">
            <VolumeChart data={data} loading={analytics.isPending} stale={analytics.isPlaceholderData} className="xl:col-span-2" />
            <CategoryBreakdown data={data} loading={analytics.isPending} stale={analytics.isPlaceholderData} />
            <TopSenders data={data} loading={analytics.isPending} stale={analytics.isPlaceholderData} />
            <CalendarActivity data={data} loading={analytics.isPending} stale={analytics.isPlaceholderData} className="xl:col-span-2" />
            <TierList data={data} />
            <SplitCard data={data} />
          </div>
        </>
      )}
    </>
  );
}

function sum(values: number[] | undefined): number | undefined {
  return values?.reduce((a, b) => a + b, 0);
}

function share(part: number, data: AnalyticsData): string {
  const total = data.actionVsInformational.action + data.actionVsInformational.informational;
  return total ? percent(Math.round((part / total) * 1000) / 10) : "—";
}

interface ChartProps {
  data: AnalyticsData | undefined;
  loading: boolean;
  stale?: boolean;
  className?: string;
}

function VolumeChart({ data, loading, stale, className }: ChartProps) {
  const rows = data?.volume ?? [];
  const segment = stackedShapes(rows, CATEGORY_SERIES.map((s) => s.key));
  return (
    <ChartCard
      className={className}
      title="Volume over time"
      description="Emails received each day, by category."
      series={[...CATEGORY_SERIES]}
      loading={loading}
      stale={stale}
      height={260}
      table={{
        caption: "Emails received per day by category",
        columns: [
          { key: "date", label: "Day" },
          ...CATEGORY_SERIES.map((s) => ({ key: s.key, label: s.label, align: "right" as const })),
          { key: "total", label: "Total", align: "right" as const },
        ],
        rows: rows.map((d) => ({
          date: dayLabel(d.date),
          total: d.total,
          ...Object.fromEntries(CATEGORY_SERIES.map((s) => [s.key, d[s.key as Category] ?? 0])),
        })),
      }}
    >
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ left: -20, right: 4, top: 4, bottom: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis
            dataKey="date"
            {...AXIS}
            minTickGap={16}
            tickFormatter={(d: string) => new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(wallClockDate(d))}
          />
          <YAxis allowDecimals={false} {...AXIS} />
          <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip labelFormat={dayLabel} />} />
          {CATEGORY_SERIES.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} stackId="v" fill={s.color} {...SEGMENT} shape={segment(s.key)} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** One bar per category: the axis names each bar, so no legend box is needed. */
function CategoryBreakdown({ data, loading, stale, className }: ChartProps) {
  const rows = useMemo(
    () => CATEGORY_SERIES.map((s) => ({ key: s.key, label: s.label, color: s.color, count: data?.byCategory[s.key as Category] ?? 0 })),
    [data],
  );
  return (
    <ChartCard
      className={className}
      title="By category"
      description="Where your mail was sorted."
      loading={loading}
      stale={stale}
      height={220}
      table={{
        caption: "Emails by category",
        columns: [{ key: "label", label: "Category" }, { key: "count", label: "Emails", align: "right" }],
        rows: rows.map((r) => ({ label: r.label, count: r.count })),
      }}
    >
      <ResponsiveContainer>
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 36, top: 0, bottom: 0 }}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis type="category" dataKey="label" width={96} {...AXIS} tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip hideZero={false} />} />
          <Bar dataKey="count" name="Emails" radius={[0, 4, 4, 0]} maxBarSize={20} isAnimationActive={false}>
            {rows.map((r) => <Cell key={r.key} fill={r.color} />)}
            <LabelList dataKey="count" position="right" className="fill-text text-[12px]" formatter={(v: number) => number(v)} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** A single series, so a single colour — senders are not ranked by hue. */
function TopSenders({ data, loading, stale, className }: ChartProps) {
  const rows = (data?.topSenders ?? []).map((s) => ({ ...s, label: s.name || s.email }));
  return (
    <ChartCard
      className={className}
      title="Most frequent senders"
      description="Who fills your inbox."
      loading={loading}
      stale={stale}
      height={Math.max(160, rows.length * 30)}
      table={{
        caption: "Most frequent senders",
        columns: [{ key: "label", label: "Sender" }, { key: "email", label: "Address" }, { key: "count", label: "Emails", align: "right" }],
        rows: rows.map((r) => ({ label: r.label, email: r.email, count: r.count })),
      }}
    >
      {rows.length === 0 ? (
        <p className="grid h-full place-items-center text-[13px] text-faint">No mail in this range.</p>
      ) : (
        <ResponsiveContainer>
          <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 36, top: 0, bottom: 0 }}>
            <XAxis type="number" hide allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="label"
              width={128}
              {...AXIS}
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              tickFormatter={(v: string) => (v.length > 20 ? `${v.slice(0, 19)}…` : v)}
            />
            <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip />} />
            <Bar dataKey="count" name="Emails" fill="var(--accent)" radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false}>
              <LabelList dataKey="count" position="right" className="fill-text text-[12px]" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

function CalendarActivity({ data, loading, stale, className }: ChartProps) {
  const rows = data?.calendarActivity ?? [];
  const segment = stackedShapes(rows, CALENDAR_SERIES.map((s) => s.key));
  return (
    <ChartCard
      className={className}
      title="Calendar activity"
      description="Events added to, removed from, or rejected by your calendar each day."
      series={CALENDAR_SERIES}
      loading={loading}
      stale={stale}
      height={220}
      table={{
        caption: "Calendar events per day",
        columns: [
          { key: "date", label: "Day" },
          ...CALENDAR_SERIES.map((s) => ({ key: s.key, label: s.label, align: "right" as const })),
        ],
        rows: rows.map((d) => ({ date: dayLabel(d.date), created: d.created, removed: d.removed, failed: d.failed })),
      }}
    >
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ left: -20, right: 4, top: 4, bottom: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis
            dataKey="date"
            {...AXIS}
            minTickGap={16}
            tickFormatter={(d: string) => new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(wallClockDate(d))}
          />
          <YAxis allowDecimals={false} {...AXIS} />
          <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip labelFormat={dayLabel} />} />
          {CALENDAR_SERIES.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} stackId="c" fill={s.color} {...SEGMENT} shape={segment(s.key)} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Four numbers do not need a chart. */
function TierList({ data }: { data: AnalyticsData | undefined }) {
  const order = ["CRITICAL", "IMPORTANT", "MONITOR", "SKIP", "UNTIERED"];
  const total = Object.values(data?.byTier ?? {}).reduce((a, b) => a + b, 0);
  return (
    <Card>
      <CardHeader title="By sender tier" description="How your contact rules split the mail." />
      <CardBody>
        <ul className="divide-y divide-border">
          {order.map((tier) => {
            const count = data?.byTier[tier] ?? 0;
            return (
              <li key={tier} className="flex items-center justify-between py-2 text-[13px]">
                {tier === "UNTIERED" ? <span className="text-muted">No rule</span> : <TierBadge tier={tier} />}
                <span className="text-text tabular-nums">
                  {number(count)} <span className="text-faint">{total ? `(${Math.round((count / total) * 100)}%)` : ""}</span>
                </span>
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}

/** Two parts of a whole: a single split meter with both labels, not a two-slice pie. */
function SplitCard({ data }: { data: AnalyticsData | undefined }) {
  const action = data?.actionVsInformational.action ?? 0;
  const info = data?.actionVsInformational.informational ?? 0;
  const total = action + info;
  const actionShare = total ? (action / total) * 100 : 0;
  return (
    <Card className="xl:col-span-2">
      <CardHeader title="Action required vs informational" description="Action covers tasks and meetings; everything else is for your information." />
      <CardBody>
        <div className="flex h-3 overflow-hidden rounded-full bg-surface-3" role="img" aria-label={`${action} need action, ${info} informational`}>
          <div className="h-full rounded-l-full bg-cat-action" style={{ width: `${actionShare}%` }} />
          {actionShare > 0 && actionShare < 100 && <div className="h-full w-0.5 bg-surface" />}
        </div>
        <div className="mt-3 flex justify-between text-[13px]">
          <span className="flex items-center gap-2 text-muted">
            <span className="size-2.5 rounded-[3px] bg-cat-action" /> Needs action <span className="font-medium text-text">{number(action)}</span>
          </span>
          <span className="flex items-center gap-2 text-muted">
            <span className="size-2.5 rounded-[3px] bg-surface-3 ring-1 ring-border" /> Informational <span className="font-medium text-text">{number(info)}</span>
          </span>
        </div>
      </CardBody>
    </Card>
  );
}
