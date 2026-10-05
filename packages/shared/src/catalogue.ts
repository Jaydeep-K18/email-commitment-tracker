/**
 * Names the server, the app and the Python worker must spell identically.
 *
 * Python is the source of truth. These lists are checked against
 * `contracts/catalogue.json`, which `python -m scripts.export_contracts`
 * writes, so a mismatch fails a test on whichever side drifted.
 */

export const EVENT_TYPES = [
  "email.received",
  "email.classified",
  "email.analyzed",
  "extraction.failed",
  "commitment.created",
  "commitment.superseded",
  "calendar.event_created",
  "calendar.event_updated",
  "calendar.event_failed",
  "calendar.event_removed",
  "calendar.synced",
  "job.queued",
  "job.started",
  "job.completed",
  "job.failed",
  "job.retrying",
  "job.retried",
  "system.worker_started",
  "system.worker_stopped",
  "system.error",
  "email.updated",
  "email.bulk_updated",
  "commitment.updated",
  "contact.created",
  "contact.updated",
  "contact.deleted",
  "settings.updated",
  "auth.setup",
  "auth.login",
  "auth.login_failed",
  "auth.logout",
  "auth.password_changed",
  "data.exported",
  "data.purged",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const SEVERITIES = ["info", "success", "warning", "error"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const CATEGORIES = [
  "important",
  "action_required",
  "meeting",
  "update",
  "low_priority",
] as const;
export type Category = (typeof CATEGORIES)[number];

/** Sort order for "by priority": what needs the user soonest comes first. */
export const CATEGORY_RANK: Record<Category, number> = {
  action_required: 0,
  meeting: 1,
  important: 2,
  update: 3,
  low_priority: 4,
};

export const CATEGORY_LABELS: Record<Category, string> = {
  important: "Important",
  action_required: "Action Required",
  meeting: "Meetings",
  update: "Updates",
  low_priority: "Low Priority",
};

export const TIERS = ["CRITICAL", "IMPORTANT", "MONITOR", "SKIP"] as const;
export type Tier = (typeof TIERS)[number];

export const MATCH_TYPES = ["exact_email", "domain", "name_pattern"] as const;
export type MatchType = (typeof MATCH_TYPES)[number];

export const COMMITMENT_TYPES = [
  "deadline_on_you",
  "deadline_from_others",
  "question_pending",
  "meeting",
] as const;
export type CommitmentType = (typeof COMMITMENT_TYPES)[number];

export const COMMITMENT_STATUSES = [
  "pending",
  "fulfilled",
  "overdue",
  "dismissed",
  "superseded",
] as const;
export type CommitmentStatus = (typeof COMMITMENT_STATUSES)[number];

export const JOB_TYPES = [
  "fetch_mailbox",
  "process_email",
  "publish_calendar",
  "push_google_event",
  "remove_google_event",
  "apply_vip_rules",
  "enforce_retention",
] as const;
export type JobType = (typeof JOB_TYPES)[number];

export const JOB_STATUSES = [
  "queued",
  "running",
  "retrying",
  "succeeded",
  "failed",
  "cancelled",
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

/** Redis keys the worker reads job ids from; `{prefix}` is REDIS_KEY_PREFIX. */
export const REDIS_KEYS = {
  ready: "{prefix}:jobs:ready",
  processing: "{prefix}:jobs:processing",
  delayed: "{prefix}:jobs:delayed",
} as const;

export function redisKey(name: keyof typeof REDIS_KEYS, prefix: string): string {
  return REDIS_KEYS[name].replace("{prefix}", prefix);
}
