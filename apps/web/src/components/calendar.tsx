import type { CalendarFlag, CalendarInsights, CalendarSpan, Commitment } from "@commitmail/shared";
import { AlertTriangle, CalendarClock, Copy, CopyCheck, ExternalLink, Mail, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { deadline, wallClockDate } from "../lib/format";
import { useCalendarFlags, useCalendarInsights, useDismissFlag, useResolveFlag, useScanCalendar } from "../lib/queries";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardBody, CardHeader } from "./ui/card";
import { ErrorState, SkeletonRows } from "./ui/states";

const clock = (value: string) =>
  new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(wallClockDate(value));

/** "Tue 13 Oct, 09:00–10:00" */
export function span({ start, end }: CalendarSpan): string {
  return `${deadline(start)}–${clock(end)}`;
}

/** What to paste into a reply when proposing another time. */
export function proposal(suggestions: CalendarSpan[]): string {
  return ["Would one of these times work instead?", ...suggestions.map((s) => `• ${span(s)}`)].join("\n");
}

function Side({ commitment, label }: { commitment: Commitment; label?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-surface px-3 py-2">
      {label && <p className="text-[11px] font-semibold tracking-wide text-faint uppercase">{label}</p>}
      <p className="truncate text-[13.5px] font-medium text-text">{commitment.subject}</p>
      <p className="text-[12px] text-muted">
        {deadline(commitment.deadline, commitment.allDay)}
        {commitment.counterpartyName && ` · ${commitment.counterpartyName}`}
      </p>
      <Link to={`/inbox/${commitment.emailId}`} className="mt-0.5 inline-flex max-w-full items-center gap-1 truncate text-[12px] text-accent-text hover:underline">
        <Mail className="size-3 shrink-0" /> <span className="truncate">{commitment.source.subject ?? "Source email"}</span>
      </Link>
    </div>
  );
}

function ExternalSide({ flag }: { flag: CalendarFlag }) {
  const event = flag.external!;
  return (
    <div className="min-w-0 rounded-lg border border-dashed border-border-strong bg-surface-2/60 px-3 py-2">
      <p className="text-[11px] font-semibold tracking-wide text-faint uppercase">On your Google Calendar</p>
      <p className="truncate text-[13.5px] font-medium text-text">{event.title}</p>
      <p className="text-[12px] text-muted">{event.allDay ? deadline(event.start, true) : span(event)}</p>
      {event.link && (
        <a href={event.link} target="_blank" rel="noreferrer" className="mt-0.5 inline-flex items-center gap-1 text-[12px] text-accent-text hover:underline">
          <ExternalLink className="size-3" /> Open in Google Calendar
        </a>
      )}
    </div>
  );
}

export function FlagCard({ flag }: { flag: CalendarFlag }) {
  const dismiss = useDismissFlag();
  const resolve = useResolveFlag();
  const busy = dismiss.isPending || resolve.isPending;
  const settle = (keep: number | "external", message: string) =>
    resolve.mutate({ id: flag.id, keep }, { onSuccess: () => toast.success(message), onError: (e) => toast.error(errorMessage(e)) });
  const leave = (message: string) =>
    dismiss.mutate(flag.id, { onSuccess: () => toast.success(message), onError: (e) => toast.error(errorMessage(e)) });
  const copy = () =>
    void navigator.clipboard?.writeText(proposal(flag.suggestions)).then(() => toast.success("Copied — paste it into your reply"));

  const conflict = flag.kind === "conflict";
  const title = conflict
    ? "These overlap"
    : flag.external
      ? "Already on your calendar?"
      : "Possibly the same thing twice";

  return (
    <li className="rounded-xl border border-border bg-surface-2/60 p-3.5" aria-label={`${title}: ${flag.commitment.subject}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={conflict ? "danger" : "warning"}>
          {conflict ? <CalendarClock className="size-3" /> : <CopyCheck className="size-3" />}
          {conflict ? "Clash" : "Possible duplicate"}
        </Badge>
        <p className="text-[13.5px] font-medium text-text">{title}</p>
        {flag.similarity != null && <span className="text-[12px] text-faint">{Math.round(flag.similarity * 100)}% alike</span>}
        {flag.overlap && <span className="text-[12px] text-faint">overlap {span(flag.overlap)}</span>}
      </div>

      <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
        <Side commitment={flag.commitment} label={conflict ? (flag.other ? "From the later email" : "From your email") : undefined} />
        {flag.other ? <Side commitment={flag.other} /> : flag.external && <ExternalSide flag={flag} />}
      </div>

      {conflict && flag.suggestions.length > 0 && (
        <div className="mt-2.5">
          <p className="text-[12px] text-muted">Free in your working hours instead:</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {flag.suggestions.map((s) => (
              <li key={s.start} className="rounded-md bg-accent-soft px-2 py-0.5 text-[12px] font-medium text-accent-text tabular-nums">{span(s)}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-1.5">
        {conflict ? (
          <>
            {flag.suggestions.length > 0 && (
              <Button size="sm" variant="primary" onClick={copy}><Copy className="size-3.5" /> Copy times for a reply</Button>
            )}
            <Button size="sm" variant="ghost" loading={dismiss.isPending} disabled={busy} onClick={() => leave("Left as it is")}>Fine as it is</Button>
          </>
        ) : flag.external ? (
          <>
            <Button size="sm" loading={resolve.isPending} disabled={busy} onClick={() => settle("external", "Removed ours — the event on your calendar stays")}>Remove ours</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => leave("Kept both")}>Keep both</Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy} onClick={() => settle(flag.commitment.id, "Kept the later one")}>Keep the later one</Button>
            <Button size="sm" disabled={busy} onClick={() => settle(flag.other!.id, "Kept the earlier one")}>Keep the earlier one</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => leave("Kept both")}>They're different</Button>
          </>
        )}
      </div>
    </li>
  );
}

/** Ask the worker to look again now, rather than after the next mail check. */
export function ScanButton() {
  const scan = useScanCalendar();
  return (
    <Button
      loading={scan.isPending}
      onClick={() =>
        scan.mutate(undefined, {
          onSuccess: () => toast.success("Checking your calendar — anything it finds will appear here"),
          onError: (e) => toast.error(errorMessage(e)),
        })
      }
    >
      <RefreshCw className="size-4" /> Check for clashes
    </Button>
  );
}

/** Open flags, soonest first. Hidden when there is nothing to settle. */
export function FlagsPanel() {
  const flags = useCalendarFlags();
  const items = flags.data?.items ?? [];
  if (!flags.error && !items.length) return null;
  return (
    <Card className="mb-4 border-warning/40">
      <CardHeader
        title={<span className="flex items-center gap-2">Needs your attention <Badge tone="warning">{items.length}</Badge></span>}
        description="Clashes and possible duplicates in the next 60 days, including events already on your Google Calendar."
      />
      <CardBody>
        {flags.error ? (
          <ErrorState error={flags.error} onRetry={() => void flags.refetch()} />
        ) : (
          <ul className="space-y-2.5">
            {items.map((flag) => <FlagCard key={flag.id} flag={flag} />)}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function DayBars({ days }: { days: CalendarInsights["days"] }) {
  const most = Math.max(1, ...days.map((d) => d.deadlines + d.meetings));
  return (
    <div className="flex h-20 items-end gap-1" role="img" aria-label="Deadlines and meetings per day, next two weeks">
      {days.map((d) => {
        const date = wallClockDate(d.date);
        const label = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric" }).format(date);
        return (
          <div key={d.date} className="flex flex-1 flex-col items-center gap-1" title={`${label}: ${d.deadlines} deadline(s), ${d.meetings} meeting(s)`}>
            <div className="flex w-full max-w-5 flex-col-reverse overflow-hidden rounded-sm bg-surface-3" style={{ height: 64 }}>
              <div className="bg-cat-action" style={{ height: `${(d.deadlines / most) * 100}%` }} />
              <div className="bg-cat-meeting" style={{ height: `${(d.meetings / most) * 100}%` }} />
            </div>
            <span className={cn("text-[10px] tabular-nums", [0, 6].includes(date.getDay()) ? "text-faint" : "text-muted")}>{date.getDate()}</span>
          </div>
        );
      })}
    </div>
  );
}

export function InsightsPanel() {
  const insights = useCalendarInsights(14);
  const data = insights.data;
  return (
    <Card className="mb-4">
      <CardHeader title="The next two weeks" description="What's due and when you're meeting, from the commitments headed for your calendar." />
      <CardBody>
        {insights.error ? (
          <ErrorState error={insights.error} onRetry={() => void insights.refetch()} />
        ) : !data ? (
          <SkeletonRows rows={2} />
        ) : (
          <div className="grid gap-5 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div>
              <DayBars days={data.days} />
              <p className="mt-2 flex gap-4 text-[12px] text-muted">
                <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-cat-action" /> Deadlines</span>
                <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-cat-meeting" /> Meetings</span>
              </p>
            </div>
            <ul className="space-y-2 text-[13px]">
              <li className="flex gap-2">
                <CalendarClock className="mt-0.5 size-4 shrink-0 text-faint" />
                {data.busiestDay ? (
                  <span><strong className="font-semibold">{deadline(data.busiestDay.date, true)}</strong> is your busiest day: {data.busiestDay.count} things due or scheduled.</span>
                ) : (
                  <span className="text-muted">No day has more than two things on it.</span>
                )}
              </li>
              <li className="flex gap-2">
                <CopyCheck className={cn("mt-0.5 size-4 shrink-0", data.openFlags.conflicts + data.openFlags.duplicates ? "text-warning" : "text-faint")} />
                {data.openFlags.conflicts + data.openFlags.duplicates ? (
                  <span>
                    {[
                      data.openFlags.conflicts && `${data.openFlags.conflicts} clash${data.openFlags.conflicts === 1 ? "" : "es"}`,
                      data.openFlags.duplicates && `${data.openFlags.duplicates} possible duplicate${data.openFlags.duplicates === 1 ? "" : "s"}`,
                    ].filter(Boolean).join(" and ")}{" "}
                    to settle, above.
                  </span>
                ) : (
                  <span className="text-muted">No clashes or duplicates ahead.</span>
                )}
              </li>
              <li className="flex gap-2">
                <AlertTriangle className={cn("mt-0.5 size-4 shrink-0", data.outsideHours.length ? "text-warning" : "text-faint")} />
                {data.outsideHours.length ? (
                  <span>
                    {data.outsideHours.length} outside your <Link to="/settings?tab=calendar" className="text-accent-text hover:underline">working hours</Link>:{" "}
                    {data.outsideHours.slice(0, 3).map((o) => `${o.subject} (${deadline(o.deadline)})`).join("; ")}
                    {data.outsideHours.length > 3 && ` and ${data.outsideHours.length - 3} more`}.
                  </span>
                ) : (
                  <span className="text-muted">Everything falls inside your working hours.</span>
                )}
              </li>
            </ul>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
