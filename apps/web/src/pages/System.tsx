import type { ComponentHealth, HealthState, MetricWindow, SystemHealth } from "@commitmail/shared";
import { Activity as ActivityIcon, AlertTriangle, Gauge, ShieldCheck, Timer, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ActivityList } from "../components/activity";
import { AXIS, ChartCard, ChartTooltip, GRID, TOP_RADIUS, type Series } from "../components/charts";
import { PageHeader, StatCard, StatusDot } from "../components/domain";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Segmented } from "../components/ui/controls";
import { EmptyState, ErrorState, Skeleton, SkeletonRows } from "../components/ui/states";
import { dateTime, duration, number, percent, relative } from "../lib/format";
import { useHealth, useJobStats, useMetrics, useSystemEvents } from "../lib/queries";

const STATE: Record<HealthState, { label: string; tone: BadgeTone }> = {
  ok: { label: "Healthy", tone: "success" },
  degraded: { label: "Degraded", tone: "warning" },
  down: { label: "Down", tone: "danger" },
  unconfigured: { label: "Not set up", tone: "neutral" },
};

/** Every moving part, in the order a request flows through them. */
const COMPONENTS: Array<{ key: keyof SystemHealth["components"]; label: string; role: string }> = [
  { key: "api", label: "API server", role: "Express — serves the app, the API and the live socket" },
  { key: "database", label: "PostgreSQL", role: "Source of truth: mail, commitments, jobs and the event log" },
  { key: "redis", label: "Redis", role: "Hands background jobs to the worker" },
  { key: "worker", label: "Python worker", role: "Fetches mail, runs the analysis, writes the calendar" },
  { key: "ollama", label: "Ollama", role: "The local language model that reads each email" },
  { key: "kafka", label: "Kafka", role: "Streams events to the dashboard and to Flink" },
  { key: "flink", label: "Apache Flink", role: "Rolling throughput, latency and error metrics" },
];

const RANGES = [
  { value: "30", label: "30 min" },
  { value: "60", label: "1 hour" },
  { value: "240", label: "4 hours" },
] as const;

const LATENCY_SERIES: Series[] = [
  { key: "p95", label: "95th percentile", color: "var(--accent)", kind: "line" },
  { key: "avg", label: "Average", color: "var(--text-faint)", kind: "line" },
];

const clock = (iso: unknown) =>
  new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(String(iso)));

interface Point {
  t: string;
  processed: number;
  events: number;
  failures: number;
  avg: number | null;
  p95: number | null;
  successRate: number | null;
  errorSpike: boolean;
  volumeAnomaly: boolean;
}

function toPoints(windows: MetricWindow[]): Point[] {
  return windows.map((w) => ({
    t: w.windowStart,
    processed: w.metrics.emailsProcessed,
    events: w.metrics.events,
    failures: w.metrics.failures,
    avg: w.metrics.avgLatencyMs,
    p95: w.metrics.p95LatencyMs,
    successRate: w.metrics.successRate,
    errorSpike: w.metrics.errorSpike,
    volumeAnomaly: w.metrics.volumeAnomaly,
  }));
}

export default function SystemPage() {
  const [range, setRange] = useState<(typeof RANGES)[number]["value"]>("60");
  const health = useHealth();
  const metrics = useMetrics(Number(range));
  const stats = useJobStats();
  const points = useMemo(() => toPoints(metrics.data?.windows ?? []), [metrics.data]);

  const totals = useMemo(() => {
    const processed = points.reduce((sum, p) => sum + p.processed, 0);
    const failures = points.reduce((sum, p) => sum + p.failures, 0);
    const p95s = points.map((p) => p.p95).filter((v): v is number => v != null);
    return {
      processed,
      failures,
      perMinute: points.length ? Math.round((processed / points.length) * 10) / 10 : null,
      worstP95: p95s.length ? Math.max(...p95s) : null,
      spikes: points.filter((p) => p.errorSpike).length,
      anomalies: points.filter((p) => p.errorSpike || p.volumeAnomaly),
    };
  }, [points]);

  const overall = health.data?.status;

  return (
    <>
      <PageHeader
        title="System"
        description="Whether every part is up, and how fast mail is moving through them."
        actions={
          overall ? (
            <span className="inline-flex items-center gap-2 text-[13px] text-muted">
              <StatusDot state={overall} pulse />
              <span className="font-medium text-text">{overall === "ok" ? "All systems normal" : STATE[overall].label}</span>
              <span className="text-faint">· checked {relative(health.data!.checkedAt)}</span>
            </span>
          ) : undefined
        }
      />

      <section aria-label="Components" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {health.isPending
          ? COMPONENTS.map((c) => <Skeleton key={c.key} className="h-[104px] rounded-xl" />)
          : health.error
            ? <Card className="sm:col-span-2 xl:col-span-4"><ErrorState error={health.error} onRetry={() => void health.refetch()} /></Card>
            : COMPONENTS.map((c) => <ComponentCard key={c.key} label={c.label} role={c.role} health={health.data!.components[c.key]} />)}
      </section>

      <div className="mt-8 mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-[17px] font-semibold tracking-tight">Live metrics</h2>
        <Segmented label="Metrics window" value={range} onChange={setRange} options={[...RANGES]} />
        {metrics.data && (
          <span className="text-[12.5px] text-faint">
            {metrics.data.source === "flink"
              ? "One-minute windows computed by Apache Flink"
              : "One-minute windows computed from the database — Flink isn't running"}
          </span>
        )}
      </div>

      <section aria-label="Metric summary" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatCard label="Emails analysed" icon={Workflow} value={totals.processed} loading={metrics.isPending}
          hint={totals.perMinute != null ? <span>{totals.perMinute} per minute</span> : undefined} />
        <StatCard label="Slowest p95" icon={Timer} tone="warning" value={totals.worstP95} format={duration} loading={metrics.isPending}
          hint={<span>worst one-minute window</span>} />
        <StatCard label="Failed attempts" icon={AlertTriangle} tone="danger" value={totals.failures} loading={metrics.isPending}
          hint={<span>{totals.spikes ? `${totals.spikes} error spike${totals.spikes === 1 ? "" : "s"}` : "no error spikes"} · each retried</span>} />
        <StatCard label="Jobs ready to run" icon={Gauge} tone="info" value={stats.data?.queue.ready ?? stats.data?.byStatus.queued ?? null} loading={stats.isPending}
          hint={stats.data ? <Link to="/jobs" className="hover:underline">{stats.data.queue.delayed ?? stats.data.byStatus.retrying} waiting to retry</Link> : undefined} />
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <ThroughputChart points={points} loading={metrics.isPending} error={metrics.error} onRetry={() => void metrics.refetch()} />
        <LatencyChart points={points} loading={metrics.isPending} error={metrics.error} onRetry={() => void metrics.refetch()} />
        <FailuresChart points={points} loading={metrics.isPending} error={metrics.error} onRetry={() => void metrics.refetch()} />
        <AnomalyCard anomalies={totals.anomalies} loading={metrics.isPending} />
      </div>

      <SystemEvents />
    </>
  );
}

function ComponentCard({ label, role, health }: { label: string; role: string; health: ComponentHealth }) {
  const state = STATE[health.state];
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[14px] font-semibold text-text">
          <StatusDot state={health.state} />
          {label}
        </span>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      <p className="mt-1 text-[12.5px] text-muted">{role}</p>
      <p className="mt-2 truncate text-[12px] text-faint" title={health.detail}>
        {health.latencyMs != null && <span className="text-muted">{duration(health.latencyMs)} · </span>}
        {health.detail ?? (health.state === "ok" ? "Responding" : "")}
      </p>
    </Card>
  );
}

interface ChartProps {
  points: Point[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}

const TIME_AXIS = <XAxis dataKey="t" {...AXIS} tickFormatter={clock} minTickGap={32} interval="preserveStartEnd" />;

function ThroughputChart({ points, loading, error, onRetry }: ChartProps) {
  return (
    <ChartCard
      title="Throughput"
      description="Emails analysed each minute"
      series={[{ key: "processed", label: "Emails analysed", color: "var(--accent)" }]}
      loading={loading}
      error={error}
      onRetry={onRetry}
      table={{
        caption: "Emails analysed and events recorded per minute",
        columns: [
          { key: "t", label: "Minute" },
          { key: "processed", label: "Emails analysed", align: "right" },
          { key: "events", label: "All events", align: "right" },
        ],
        rows: points.filter((p) => p.processed || p.events).reverse().map((p) => ({ t: clock(p.t), processed: p.processed, events: p.events })),
      }}
    >
      <ResponsiveContainer>
        <BarChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
          <CartesianGrid {...GRID} />
          {TIME_AXIS}
          <YAxis {...AXIS} allowDecimals={false} />
          <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip labelFormat={clock} hideZero={false} />} />
          <Bar dataKey="processed" name="Emails analysed" fill="var(--accent)" radius={TOP_RADIUS} maxBarSize={24} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function LatencyChart({ points, loading, error, onRetry }: ChartProps) {
  const measured = points.filter((p) => p.p95 != null).length;
  // Sparse windows would be invisible as a bare line, so give each point a dot.
  const dot = (color: string) => (measured < 20 ? { r: 2.5, strokeWidth: 0, fill: color } : false);
  return (
    <ChartCard
      title="Analysis time"
      description="How long each email took to analyse, per minute"
      series={LATENCY_SERIES}
      loading={loading}
      error={error}
      onRetry={onRetry}
      table={{
        caption: "Average and 95th-percentile analysis time per minute",
        columns: [
          { key: "t", label: "Minute" },
          { key: "avg", label: "Average", align: "right" },
          { key: "p95", label: "95th percentile", align: "right" },
        ],
        rows: points.filter((p) => p.p95 != null).reverse().map((p) => ({ t: clock(p.t), avg: duration(p.avg), p95: duration(p.p95) })),
      }}
    >
      {measured === 0 ? (
        <EmptyState icon={<Timer />} title="Nothing analysed in this window" description="Times appear as soon as the worker finishes an email." />
      ) : (
        <ResponsiveContainer>
          <LineChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
            <CartesianGrid {...GRID} />
            {TIME_AXIS}
            <YAxis {...AXIS} tickFormatter={(v: number) => duration(v)} width={56} />
            <RechartsTooltip cursor={{ stroke: "var(--border-strong)" }} content={<ChartTooltip labelFormat={clock} valueFormat={(v) => duration(Number(v))} />} />
            <Line dataKey="avg" name="Average" stroke="var(--text-faint)" strokeWidth={2} dot={dot("var(--text-faint)")} connectNulls isAnimationActive={false} />
            <Line dataKey="p95" name="95th percentile" stroke="var(--accent)" strokeWidth={2} dot={dot("var(--accent)")} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

function FailuresChart({ points, loading, error, onRetry }: ChartProps) {
  return (
    <ChartCard
      title="Failed attempts"
      description="Job attempts that failed each minute — each is retried with backoff"
      series={[{ key: "failures", label: "Failed attempts", color: "var(--danger)" }]}
      loading={loading}
      error={error}
      onRetry={onRetry}
      table={{
        caption: "Failed job attempts and success rate per minute",
        columns: [
          { key: "t", label: "Minute" },
          { key: "failures", label: "Failed attempts", align: "right" },
          { key: "rate", label: "Success rate", align: "right" },
          { key: "flag", label: "" },
        ],
        rows: points
          .filter((p) => p.failures || p.successRate != null)
          .reverse()
          .map((p) => ({ t: clock(p.t), failures: p.failures, rate: percent(p.successRate), flag: p.errorSpike ? "Error spike" : "" })),
      }}
    >
      <ResponsiveContainer>
        <BarChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
          <CartesianGrid {...GRID} />
          {TIME_AXIS}
          <YAxis {...AXIS} allowDecimals={false} />
          <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip labelFormat={clock} hideZero={false} />} />
          <Bar dataKey="failures" name="Failed attempts" fill="var(--danger)" radius={TOP_RADIUS} maxBarSize={24} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Flagged windows as a list — a handful of moments reads better as text than as marks. */
function AnomalyCard({ anomalies, loading }: { anomalies: Point[]; loading: boolean }) {
  return (
    <Card>
      <CardHeader title="Anomalies" description="Minutes with an error spike or unusual volume" />
      <CardBody>
        {loading ? (
          <SkeletonRows rows={3} />
        ) : !anomalies.length ? (
          <EmptyState icon={<ShieldCheck />} title="Nothing unusual" description="A minute is flagged when failures jump to three times the average, or volume is three deviations above it." />
        ) : (
          <ul className="divide-y divide-border">
            {anomalies.slice().reverse().map((p) => (
              <li key={p.t} className="flex items-center gap-3 py-2.5 text-[13px]">
                <AlertTriangle className="size-4 shrink-0 text-warning" />
                <span className="text-text">{dateTime(p.t)}</span>
                <span className="text-muted">
                  {p.errorSpike && `${number(p.failures)} failed attempts`}
                  {p.errorSpike && p.volumeAnomaly && " · "}
                  {p.volumeAnomaly && `${number(p.events)} events`}
                </span>
                <span className="ml-auto flex gap-1.5">
                  {p.errorSpike && <Badge tone="danger">Error spike</Badge>}
                  {p.volumeAnomaly && <Badge tone="warning">Volume</Badge>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function SystemEvents() {
  const events = useSystemEvents();
  return (
    <Card className="mt-6">
      <CardHeader
        title="Warnings and errors"
        description="The most recent problems anywhere in the system"
        action={<Link to="/activity?problems=true" className="text-[13px] font-medium text-accent-text hover:underline">All activity</Link>}
      />
      <CardBody>
        {events.isPending ? (
          <SkeletonRows rows={4} />
        ) : events.error ? (
          <ErrorState error={events.error} onRetry={() => void events.refetch()} />
        ) : !events.data?.items.length ? (
          <EmptyState icon={<ActivityIcon />} title="No problems recorded" description="Failures, retries and outages show up here as they happen." />
        ) : (
          <ActivityList events={events.data.items.slice(0, 12)} compact />
        )}
      </CardBody>
    </Card>
  );
}
