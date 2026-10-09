import type { ActivityEvent, Category, Commitment } from "@commitmail/shared";
import {
  AlarmClock,
  ArrowRight,
  Bot,
  CalendarClock,
  CheckCheck,
  ClipboardCheck,
  Inbox,
  Radio,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as RechartsTooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import { ActivityList } from "../components/activity";
import { AXIS, ChartCard, ChartTooltip, GRID, SEGMENT, stackedShapes } from "../components/charts";
import { CATEGORY_SERIES, PageHeader, StatCard, StatusDot } from "../components/domain";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { browserTimeZone, deadline, deadlineRelative, isOverdue, percent, wallClockDate } from "../lib/format";
import {
  useActivity,
  useAnalytics,
  useCommitments,
  useEmails,
  useHealth,
  useJobStats,
  useSession,
  useUpdateCommitment,
} from "../lib/queries";
import { useLive } from "../lib/realtime";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export default function Overview() {
  const session = useSession();
  const inbox = useEmails({ folder: "inbox", pageSize: 1 });
  const upcoming = useCommitments({ view: "upcoming", pageSize: 6 });
  const review = useCommitments({ view: "review", pageSize: 1 });
  const analytics = useAnalytics({ range: "30d", tz: browserTimeZone() });
  const jobs = useJobStats();

  const firstName = session.data?.user?.displayName?.split(" ")[0];
  const counts = inbox.data?.counts;

  return (
    <>
      <PageHeader
        title={`${greeting()}${firstName ? `, ${firstName}` : ""}`}
        description={new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(new Date())}
        actions={
          <Link to="/inbox?category=action_required">
            <Button variant="primary">
              <Sparkles className="size-4" /> What needs me
            </Button>
          </Link>
        }
      />

      <section aria-label="Key numbers" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Needs action"
          icon={TriangleAlert}
          tone="danger"
          value={counts?.byCategory.action_required}
          loading={inbox.isPending}
          hint={<span>{counts?.byCategory.meeting ?? 0} meetings</span>}
        />
        <StatCard
          label="Unread"
          icon={Inbox}
          tone="accent"
          value={counts?.unread}
          loading={inbox.isPending}
          hint={<span>in your inbox</span>}
        />
        <StatCard
          label="Awaiting your review"
          icon={ClipboardCheck}
          tone="warning"
          value={review.data?.total}
          loading={review.isPending}
          hint={<span>before they reach the calendar</span>}
        />
        <StatCard
          label="Email → calendar"
          icon={CalendarClock}
          tone="success"
          value={analytics.data?.conversion.rate ?? null}
          format={(n) => percent(n)}
          loading={analytics.isPending}
          change={analytics.data?.trends.conversionChange}
          hint={<span>of analysed mail, 30 days</span>}
        />
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <div className="min-w-0 space-y-6 xl:col-span-2">
          <UpcomingCard query={upcoming} />
          <VolumeCard analytics={analytics} />
        </div>
        <div className="min-w-0 space-y-6">
          <LiveFeedCard />
          <AttentionCard failed={jobs.data?.byStatus.failed ?? 0} review={review.data?.total ?? 0} />
          <HealthCard />
        </div>
      </div>
    </>
  );
}

function UpcomingCard({ query }: { query: ReturnType<typeof useCommitments> }) {
  const update = useUpdateCommitment();
  return (
    <Card>
      <CardHeader
        title="Coming up"
        description="Deadlines and meetings from your email, soonest first."
        action={<Link to="/commitments" className="text-[13px] font-medium text-accent-text hover:underline">All commitments</Link>}
      />
      <CardBody className="px-2 pb-2">
        {query.isPending ? (
          <div className="px-3 pb-3"><SkeletonRows rows={4} /></div>
        ) : query.error ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : !query.data?.items.length ? (
          <EmptyState icon={<CheckCheck />} title="Nothing due" description="No open commitments with a deadline ahead. Enjoy it." />
        ) : (
          <ul>
            {query.data.items.map((c) => (
              <UpcomingRow
                key={c.id}
                commitment={c}
                onDone={() =>
                  update.mutate(
                    { id: c.id, patch: { status: "fulfilled" } },
                    { onSuccess: () => toast.success("Marked done"), onError: (e) => toast.error(errorMessage(e)) },
                  )
                }
              />
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function UpcomingRow({ commitment: c, onDone }: { commitment: Commitment; onDone: () => void }) {
  const soon = !!c.deadline && wallClockDate(c.deadline).getTime() - Date.now() < 48 * 3600_000;
  return (
    <li className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-surface-2">
      <div
        className={cn(
          "grid w-12 shrink-0 place-items-center rounded-lg border py-1 text-center leading-tight",
          soon ? "border-[color-mix(in_oklch,var(--danger)_35%,var(--border))] bg-danger-soft" : "border-border bg-surface-2",
        )}
      >
        {c.deadline ? (
          <>
            <span className="text-[10px] font-semibold text-muted uppercase">
              {new Intl.DateTimeFormat(undefined, { month: "short" }).format(wallClockDate(c.deadline))}
            </span>
            <span className={cn("text-base font-semibold", soon ? "text-danger" : "text-text")}>{wallClockDate(c.deadline).getDate()}</span>
          </>
        ) : (
          <AlarmClock className="size-4 text-faint" />
        )}
      </div>
      <Link to={`/inbox/${c.emailId}`} className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-text">{c.subject}</p>
        <p className="truncate text-[12.5px] text-muted">
          {deadline(c.deadline, c.allDay)} · <span className={cn(isOverdue(c.deadline) && "text-danger")}>{deadlineRelative(c.deadline)}</span>
          {c.counterpartyName && <> · {c.counterpartyName}</>}
        </p>
      </Link>
      {c.decision.shouldSync && <Badge tone="success" className="hidden sm:inline-flex">On calendar</Badge>}
      <Button size="sm" variant="ghost" className="opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100" onClick={onDone}>
        Done
      </Button>
    </li>
  );
}

function VolumeCard({ analytics }: { analytics: ReturnType<typeof useAnalytics> }) {
  const data = useMemo(() => analytics.data?.volume.slice(-14) ?? [], [analytics.data]);
  const segment = stackedShapes(data, CATEGORY_SERIES.map((s) => s.key));
  const dayLabel = (date: unknown) =>
    new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" }).format(wallClockDate(String(date)));
  return (
    <ChartCard
      title="Incoming mail"
      description="The last two weeks, by category."
      action={<Link to="/analytics" className="text-[13px] font-medium text-accent-text hover:underline">Analytics</Link>}
      series={[...CATEGORY_SERIES]}
      loading={analytics.isPending}
      error={analytics.error}
      onRetry={() => void analytics.refetch()}
      stale={analytics.isPlaceholderData}
      height={220}
      table={{
        caption: "Emails received per day by category, last 14 days",
        columns: [
          { key: "date", label: "Day" },
          ...CATEGORY_SERIES.map((s) => ({ key: s.key, label: s.label, align: "right" as const })),
          { key: "total", label: "Total", align: "right" as const },
        ],
        rows: data.map((day) => ({
          date: dayLabel(day.date),
          total: day.total,
          ...Object.fromEntries(CATEGORY_SERIES.map((s) => [s.key, day[s.key as Category] ?? 0])),
        })),
      }}
    >
      <ResponsiveContainer>
        <BarChart data={data} margin={{ left: -20, right: 4, top: 4, bottom: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="date" tickFormatter={(d: string) => String(Number(d.slice(8)))} {...AXIS} />
          <YAxis allowDecimals={false} {...AXIS} />
          <RechartsTooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip labelFormat={dayLabel} />} />
          {CATEGORY_SERIES.map((s) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stackId="volume"
              fill={s.color}
              {...SEGMENT}
              shape={segment(s.key)}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function LiveFeedCard() {
  const live = useLive();
  const history = useActivity();
  const events = useMemo(() => {
    const seen = new Set<number>();
    const merged: ActivityEvent[] = [];
    for (const event of [...live.events, ...(history.data?.pages[0]?.items ?? [])]) {
      if (seen.has(event.id)) continue;
      seen.add(event.id);
      merged.push(event);
    }
    return merged.slice(0, 9);
  }, [live.events, history.data]);

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Live activity
            <Radio className={cn("size-3.5", live.status === "live" ? "text-success" : "text-faint")} />
          </span>
        }
        action={<Link to="/activity" className="text-[13px] font-medium text-accent-text hover:underline">Timeline</Link>}
      />
      <CardBody className="px-3 pb-3">
        {history.isPending && !live.events.length ? (
          <SkeletonRows rows={5} />
        ) : events.length ? (
          <ActivityList events={events} compact />
        ) : (
          <EmptyState icon={<Bot />} title="Quiet so far" description="New mail, analyses and calendar changes appear here as they happen." />
        )}
      </CardBody>
    </Card>
  );
}

function AttentionCard({ failed, review }: { failed: number; review: number }) {
  if (!failed && !review) return null;
  return (
    <Card className="border-[color-mix(in_oklch,var(--warning)_40%,var(--border))]">
      <CardHeader title="Needs your attention" />
      <CardBody className="space-y-2">
        {review > 0 && (
          <Link to="/commitments?view=review" className="flex items-center justify-between rounded-lg bg-warning-soft px-3 py-2.5 text-sm transition-colors hover:brightness-[0.98]">
            <span>{review} commitment{review === 1 ? "" : "s"} waiting for approval</span>
            <ArrowRight className="size-4" />
          </Link>
        )}
        {failed > 0 && (
          <Link to="/jobs?status=failed" className="flex items-center justify-between rounded-lg bg-danger-soft px-3 py-2.5 text-sm text-danger transition-colors hover:brightness-[0.98]">
            <span>{failed} failed background job{failed === 1 ? "" : "s"}</span>
            <ArrowRight className="size-4" />
          </Link>
        )}
      </CardBody>
    </Card>
  );
}

function HealthCard() {
  const health = useHealth();
  const parts = health.data
    ? (["worker", "ollama", "database", "redis", "kafka", "flink"] as const).map((key) => [key, health.data.components[key]] as const)
    : [];
  return (
    <Card>
      <CardHeader
        title="System"
        action={<Link to="/system" className="text-[13px] font-medium text-accent-text hover:underline">Details</Link>}
      />
      <CardBody>
        {health.isPending ? (
          <SkeletonRows rows={3} />
        ) : (
          <ul className="grid grid-cols-2 gap-2">
            {parts.map(([name, component]) => (
              <li key={name} className="flex items-center gap-2 rounded-lg bg-surface-2 px-2.5 py-2 text-[12.5px] capitalize">
                <StatusDot state={component.state} pulse />
                <span className="text-text">{name}</span>
                <span className="ml-auto text-faint">{component.state === "unconfigured" ? "off" : component.state}</span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
