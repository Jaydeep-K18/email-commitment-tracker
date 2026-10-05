/** Rendering events: one line each, an icon per kind, a link to what it is about. */
import type { ActivityEvent } from "@commitmail/shared";
import {
  AlertTriangle,
  Bot,
  CalendarCheck,
  CalendarX,
  CheckCircle2,
  CircleDot,
  Inbox,
  KeyRound,
  ListChecks,
  RotateCcw,
  Settings2,
  Shuffle,
  Tag,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { Link } from "react-router-dom";

import { cn } from "../lib/cn";
import { relative } from "../lib/format";

const ICONS: Array<[RegExp, LucideIcon]> = [
  [/^email\.received$/, Inbox],
  [/^email\.classified$/, Shuffle],
  [/^email\.analyzed$/, Bot],
  [/^email\.(updated|bulk_updated)$/, Tag],
  [/^extraction\.failed$/, AlertTriangle],
  [/^commitment\./, ListChecks],
  [/^calendar\.event_created$/, CalendarCheck],
  [/^calendar\.event_(failed|removed)$/, CalendarX],
  [/^calendar\.synced$/, CalendarCheck],
  [/^job\.retried$/, RotateCcw],
  [/^job\.completed$/, CheckCircle2],
  [/^job\.(failed)$/, AlertTriangle],
  [/^auth\./, KeyRound],
  [/^(settings|contact)\./, Settings2],
  [/^data\./, Trash2],
];

export function iconFor(type: string): LucideIcon {
  return ICONS.find(([pattern]) => pattern.test(type))?.[1] ?? CircleDot;
}

const SEVERITY_RING: Record<string, string> = {
  info: "bg-surface-3 text-muted",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-[color-mix(in_oklch,var(--warning)_70%,var(--text))]",
  error: "bg-danger-soft text-danger",
};

/** Where clicking an event takes you. */
export function linkFor(event: ActivityEvent): string | null {
  const email = /^email:(\d+)$/.exec(event.correlationId ?? "");
  if (email) return `/inbox/${email[1]}`;
  if (event.entityType === "job" && event.entityId) return `/jobs/${event.entityId}`;
  if (event.entityType === "contact") return "/contacts";
  if (event.entityType === "settings") return "/settings";
  return null;
}

export function ActivityItem({ event, compact = false }: { event: ActivityEvent; compact?: boolean }) {
  const Icon = iconFor(event.type);
  const href = linkFor(event);
  const body = (
    <>
      <span className={cn("mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg", SEVERITY_RING[event.severity])}>
        <Icon className="size-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn("block text-[13px] text-text", compact ? "line-clamp-1" : "line-clamp-2")}>{event.message}</span>
        <span className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-faint">
          <span className="font-mono">{event.type}</span>
          <span aria-hidden>·</span>
          <time dateTime={event.createdAt} title={new Date(event.createdAt).toLocaleString()}>
            {relative(event.createdAt)}
          </time>
        </span>
      </span>
    </>
  );
  const className = "flex gap-3 rounded-lg px-2 py-2 transition-colors";
  return href ? (
    <Link to={href} className={cn(className, "hover:bg-surface-2")}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

/** A list that animates new arrivals in at the top. */
export function ActivityList({ events, compact }: { events: ActivityEvent[]; compact?: boolean }) {
  return (
    <ul className="space-y-0.5">
      <AnimatePresence initial={false}>
        {events.map((event) => (
          <motion.li
            key={event.id}
            layout="position"
            initial={{ opacity: 0, y: -6, backgroundColor: "var(--accent-soft)" }}
            animate={{ opacity: 1, y: 0, backgroundColor: "rgba(0,0,0,0)" }}
            transition={{ duration: 0.4, backgroundColor: { duration: 1.6 } }}
            className="rounded-lg"
          >
            <ActivityItem event={event} compact={compact} />
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  );
}
