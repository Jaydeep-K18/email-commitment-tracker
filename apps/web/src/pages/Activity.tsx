import type { ActivityEvent } from "@commitmail/shared";
import { History } from "lucide-react";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { ActivityList } from "../components/activity";
import { PageHeader } from "../components/domain";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Segmented } from "../components/ui/controls";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/states";
import { useActivity } from "../lib/queries";
import { useLive } from "../lib/realtime";

/** Event-type groups the timeline can be narrowed to. */
const GROUPS: Record<string, { label: string; types?: string[] }> = {
  all: { label: "Everything" },
  mail: { label: "Mail", types: ["email.received", "email.classified", "email.analyzed", "extraction.failed", "email.updated", "email.bulk_updated"] },
  calendar: { label: "Calendar", types: ["commitment.created", "commitment.updated", "commitment.superseded", "calendar.event_created", "calendar.event_updated", "calendar.event_failed", "calendar.event_removed", "calendar.synced"] },
  jobs: { label: "Jobs", types: ["job.queued", "job.started", "job.completed", "job.failed", "job.retrying", "job.retried"] },
  account: { label: "Account", types: ["auth.setup", "auth.login", "auth.login_failed", "auth.logout", "auth.password_changed", "settings.updated", "contact.created", "contact.updated", "contact.deleted", "data.exported", "data.purged"] },
};

function dayHeading(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(date);
}

export default function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const group = params.get("group") ?? "all";
  const problemsOnly = params.get("problems") === "true";
  const activity = useActivity({
    type: GROUPS[group]?.types,
    severity: problemsOnly ? ["warning", "error"] : undefined,
  });
  const live = useLive();

  const byDay = useMemo(() => {
    const events = activity.data?.pages.flatMap((p) => p.items) ?? [];
    const groups: Array<{ day: string; events: ActivityEvent[] }> = [];
    for (const event of events) {
      const day = dayHeading(event.createdAt);
      const last = groups[groups.length - 1];
      if (last?.day === day) last.events.push(event);
      else groups.push({ day, events: [event] });
    }
    return groups;
  }, [activity.data]);

  const set = (key: string, value: string | undefined) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });

  return (
    <>
      <PageHeader
        title="Activity"
        description="Everything that happened, in order — every email's journey from arrival to calendar, every job, and every change you made."
        actions={
          <span className="inline-flex items-center gap-2 text-[12.5px] text-muted">
            <span className={live.status === "live" ? "size-2 animate-pulse-dot rounded-full bg-success" : "size-2 rounded-full bg-faint"} />
            {live.status === "live" ? "Updating live" : "Reconnecting"}
          </span>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Show"
          value={group}
          onChange={(value) => set("group", value === "all" ? undefined : value)}
          options={Object.entries(GROUPS).map(([value, g]) => ({ value, label: g.label }))}
        />
        <Button size="sm" variant={problemsOnly ? "subtle" : "ghost"} onClick={() => set("problems", problemsOnly ? undefined : "true")}>
          {problemsOnly ? "Showing problems only" : "Problems only"}
        </Button>
      </div>

      <Card className="p-3">
        {activity.isPending ? (
          <div className="p-2"><SkeletonRows rows={8} /></div>
        ) : activity.error ? (
          <ErrorState error={activity.error} onRetry={() => void activity.refetch()} />
        ) : !byDay.length ? (
          <EmptyState icon={<History />} title="Nothing yet" description="As mail arrives and jobs run, each step is recorded here." />
        ) : (
          <div className="space-y-5">
            {byDay.map(({ day, events }) => (
              <section key={day} aria-label={day}>
                <h2 className="sticky top-14 z-10 mb-1 bg-surface/95 px-2 py-1.5 text-[12px] font-semibold tracking-wide text-faint uppercase backdrop-blur">
                  {day}
                </h2>
                <ActivityList events={events} />
              </section>
            ))}
            {activity.hasNextPage && (
              <div className="flex justify-center pb-2">
                <Button size="sm" loading={activity.isFetchingNextPage} onClick={() => void activity.fetchNextPage()}>
                  Load older
                </Button>
              </div>
            )}
          </div>
        )}
      </Card>
    </>
  );
}
