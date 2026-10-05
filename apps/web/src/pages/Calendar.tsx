import type { Commitment } from "@commitmail/shared";
import { CalendarCheck, ChevronLeft, ChevronRight, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "../components/domain";
import { CommitmentCard } from "../components/email";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Segmented } from "../components/ui/controls";
import { Dialog } from "../components/ui/overlay";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { cn } from "../lib/cn";
import { deadline, wallClockDate } from "../lib/format";
import { useCalendar } from "../lib/queries";

const pad = (n: number) => String(n).padStart(2, "0");
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** The 6×7 grid of days shown for a month, starting on Monday. */
function monthGrid(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
  return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
}

const TYPE_DOT: Record<string, string> = {
  deadline_on_you: "bg-cat-action",
  deadline_from_others: "bg-cat-update",
  meeting: "bg-cat-meeting",
  question_pending: "bg-cat-important",
};

export default function CalendarPage() {
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [mode, setMode] = useState<"month" | "agenda">("month");
  const [openDay, setOpenDay] = useState<string | null>(null);
  const days = useMemo(() => monthGrid(month), [month]);
  const calendar = useCalendar(isoDay(days[0]!), isoDay(days[41]!));

  const byDay = useMemo(() => {
    const map = new Map<string, Commitment[]>();
    for (const c of calendar.data?.items ?? []) {
      if (!c.deadline) continue;
      const key = c.deadline.slice(0, 10);
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return map;
  }, [calendar.data]);

  const today = isoDay(new Date());
  const shift = (months: number) => setMonth((m) => new Date(m.getFullYear(), m.getMonth() + months, 1));
  const label = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(month);
  const agenda = (calendar.data?.items ?? []).filter((c) => c.deadline && wallClockDate(c.deadline).getMonth() === month.getMonth());

  return (
    <>
      <PageHeader
        title="Calendar"
        description="Every dated commitment, and whether it's on your calendar. Times are shown exactly as the email wrote them."
        actions={
          <a href="/calendar.ics" download="email-commitments.ics">
            <Button><Download className="size-4" /> Download .ics</Button>
          </a>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <Button size="icon-sm" variant="ghost" onClick={() => shift(-1)} aria-label="Previous month"><ChevronLeft className="size-4" /></Button>
          <Button size="icon-sm" variant="ghost" onClick={() => shift(1)} aria-label="Next month"><ChevronRight className="size-4" /></Button>
          <h2 className="ml-1 text-[15px] font-semibold">{label}</h2>
          <Button size="sm" variant="ghost" onClick={() => setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))}>Today</Button>
          <div className="ml-auto">
            <Segmented label="Layout" value={mode} onChange={setMode} options={[{ value: "month", label: "Month" }, { value: "agenda", label: "Agenda" }]} />
          </div>
        </div>

        {calendar.error ? (
          <ErrorState error={calendar.error} onRetry={() => void calendar.refetch()} />
        ) : mode === "month" ? (
          <div className="grid grid-cols-7">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
              <div key={d} className="border-b border-border px-2 py-2 text-center text-[11.5px] font-semibold tracking-wide text-faint uppercase">{d}</div>
            ))}
            {days.map((day, index) => {
              const key = isoDay(day);
              const items = byDay.get(key) ?? [];
              const inMonth = day.getMonth() === month.getMonth();
              return (
                <button
                  key={key}
                  onClick={() => items.length && setOpenDay(key)}
                  className={cn(
                    "min-h-24 border-border p-1.5 text-left align-top transition-colors sm:min-h-28",
                    index % 7 !== 6 && "border-r",
                    index < 35 && "border-b",
                    inMonth ? "bg-surface" : "bg-surface-2/60",
                    items.length && "hover:bg-accent-soft/40",
                  )}
                  aria-label={`${deadline(key, true)}: ${items.length} commitment${items.length === 1 ? "" : "s"}`}
                >
                  <span className={cn("grid size-6 place-items-center rounded-full text-[12px] tabular-nums", key === today ? "bg-accent font-semibold text-accent-fg" : inMonth ? "text-text" : "text-faint")}>
                    {day.getDate()}
                  </span>
                  <div className="mt-1 space-y-0.5">
                    {calendar.isPending && inMonth && index % 5 === 0 && <Skeleton className="h-4" />}
                    {items.slice(0, 3).map((c) => (
                      <div key={c.id} className={cn("flex items-center gap-1 truncate rounded px-1 py-0.5 text-[11px]", c.decision.shouldSync ? "bg-surface-3 text-text" : "text-muted")}>
                        <span className={cn("size-1.5 shrink-0 rounded-full", TYPE_DOT[c.type])} />
                        <span className="truncate">{!c.allDay && c.deadline ? `${c.deadline.slice(11, 16)} ` : ""}{c.subject}</span>
                      </div>
                    ))}
                    {items.length > 3 && <p className="px-1 text-[11px] font-medium text-accent-text">+{items.length - 3} more</p>}
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <CardBody className="pt-4">
            {!agenda.length ? (
              <EmptyState icon={<CalendarCheck />} title="Nothing this month" description="No dated commitments fall in this month." />
            ) : (
              <ul className="space-y-2.5">
                {agenda.map((c) => <CommitmentCard key={c.id} commitment={c} showSource />)}
              </ul>
            )}
          </CardBody>
        )}
      </Card>

      <Legend />

      <Dialog open={!!openDay} onOpenChange={(open) => !open && setOpenDay(null)} title={openDay ? deadline(openDay, true) : ""} className="w-[min(94vw,560px)]">
        <ul className="max-h-[60vh] space-y-2.5 overflow-y-auto">
          {(openDay ? byDay.get(openDay) ?? [] : []).map((c) => <CommitmentCard key={c.id} commitment={c} showSource />)}
        </ul>
        <Link to="/commitments" className="mt-3 inline-block text-[13px] font-medium text-accent-text hover:underline">Manage commitments</Link>
      </Dialog>
    </>
  );
}

function Legend() {
  return (
    <Card className="mt-4">
      <CardHeader title="Reading the calendar" />
      <CardBody className="flex flex-wrap gap-x-6 gap-y-2 text-[12.5px] text-muted">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-cat-action" /> You owe</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-cat-update" /> Owed to you</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-cat-meeting" /> Meeting</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-5 rounded bg-surface-3" /> Shaded = on your calendar</span>
      </CardBody>
    </Card>
  );
}
