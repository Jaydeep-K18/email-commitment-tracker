/**
 * Request schemas. The server validates every request body and query string
 * against these; the React app builds its forms and filters from the same ones,
 * so a value the UI lets you submit is a value the API accepts.
 */
import { z } from "zod";

import {
  CATEGORIES,
  COMMITMENT_STATUSES,
  COMMITMENT_TYPES,
  EVENT_TYPES,
  JOB_STATUSES,
  JOB_TYPES,
  MATCH_TYPES,
  SEVERITIES,
  TIERS,
} from "./catalogue";

// --- Building blocks ---------------------------------------------------------

/** A query-string list: accepts `a,b` or repeated `?x=a&x=b`. */
export function csv<T extends z.ZodTypeAny>(item: T) {
  return z.preprocess((value) => {
    if (value === undefined || value === "") return undefined;
    const parts = Array.isArray(value) ? value : String(value).split(",");
    return parts.map((part) => (typeof part === "string" ? part.trim() : part)).filter((p) => p !== "");
  }, z.array(item).max(50).optional());
}

/** Query-string booleans arrive as text. */
export const boolish = z.preprocess((value) => {
  if (value === "true" || value === "1") return true;
  if (value === "false" || value === "0") return false;
  return value;
}, z.boolean());

export const idParam = z.object({ id: z.coerce.number().int().positive() });
export const ids = z.array(z.number().int().positive()).min(1).max(500);

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "expected YYYY-MM-DD");
const hhmm = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "expected HH:MM");

export function isValidTimeZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}
export const timeZone = z.string().min(1).max(64).refine(isValidTimeZone, "unknown time zone");

const page = z.coerce.number().int().min(1).max(10_000).default(1);
const pageSize = z.coerce.number().int().min(1).max(100).default(25);

// --- Inbox -------------------------------------------------------------------

export const INBOX_FOLDERS = ["inbox", "archived", "trash", "all"] as const;
export const INBOX_SORTS = ["received", "sender", "priority", "category", "subject", "relevance"] as const;
export const SORT_DIRECTIONS = ["asc", "desc"] as const;

export const inboxFiltersSchema = z.object({
  q: z.string().trim().max(200).optional(),
  folder: z.enum(INBOX_FOLDERS).default("inbox"),
  category: csv(z.enum(CATEGORIES)),
  tier: csv(z.enum(TIERS)),
  tag: csv(z.coerce.number().int().positive()),
  unread: boolish.optional(),
  starred: boolish.optional(),
  hasCommitments: boolish.optional(),
  sender: z.string().trim().max(254).optional(),
  from: isoDate.optional(),
  to: isoDate.optional(),
});
export type InboxFilters = z.infer<typeof inboxFiltersSchema>;

export const inboxSortSchema = z.object({
  sort: z.enum(INBOX_SORTS).default("received"),
  dir: z.enum(SORT_DIRECTIONS).default("desc"),
});
export type InboxSort = z.infer<typeof inboxSortSchema>;

export const inboxQuerySchema = inboxFiltersSchema.merge(inboxSortSchema).extend({ page, pageSize });
export type InboxQuery = z.infer<typeof inboxQuerySchema>;

export const emailPatchSchema = z
  .object({
    isRead: z.boolean(),
    isStarred: z.boolean(),
    archived: z.boolean(),
    deleted: z.boolean(),
    /** null hands the category back to the automatic classifier. */
    category: z.enum(CATEGORIES).nullable(),
  })
  .partial()
  .strict()
  .refine((patch) => Object.keys(patch).length > 0, "nothing to update");
export type EmailPatch = z.infer<typeof emailPatchSchema>;

export const SIMPLE_BULK_ACTIONS = [
  "markRead",
  "markUnread",
  "star",
  "unstar",
  "archive",
  "unarchive",
  "delete",
  "restore",
] as const;

export const emailBulkSchema = z.discriminatedUnion("action", [
  z.object({ action: z.enum(SIMPLE_BULK_ACTIONS), ids }).strict(),
  z.object({ action: z.literal("setCategory"), ids, category: z.enum(CATEGORIES).nullable() }).strict(),
  z.object({ action: z.literal("addTag"), ids, tagId: z.number().int().positive() }).strict(),
  z.object({ action: z.literal("removeTag"), ids, tagId: z.number().int().positive() }).strict(),
]);
export type EmailBulk = z.infer<typeof emailBulkSchema>;

// --- Tags and saved views ----------------------------------------------------

export const TAG_COLORS = ["slate", "red", "orange", "amber", "green", "teal", "blue", "violet", "pink"] as const;

export const tagCreateSchema = z
  .object({
    name: z.string().trim().min(1).max(64),
    color: z.enum(TAG_COLORS).default("slate"),
  })
  .strict();
export const tagPatchSchema = tagCreateSchema.partial().strict();

export const savedViewSchema = z
  .object({
    name: z.string().trim().min(1).max(80),
    filters: inboxFiltersSchema,
    sort: inboxSortSchema,
    isPinned: z.boolean().default(false),
  })
  .strict();
export const savedViewPatchSchema = savedViewSchema.partial().strict();

// --- Commitments -------------------------------------------------------------

export const COMMITMENT_VIEWS = ["all", "upcoming", "review", "overdue", "calendar"] as const;

export const commitmentQuerySchema = z.object({
  view: z.enum(COMMITMENT_VIEWS).default("all"),
  status: csv(z.enum(COMMITMENT_STATUSES)),
  tier: csv(z.enum(TIERS)),
  type: csv(z.enum(COMMITMENT_TYPES)),
  q: z.string().trim().max(200).optional(),
  page,
  pageSize,
});
export type CommitmentQuery = z.infer<typeof commitmentQuerySchema>;

export const commitmentPatchSchema = z
  .object({
    status: z.enum(["pending", "fulfilled", "dismissed"]),
    approved: z.boolean(),
  })
  .partial()
  .strict()
  .refine((patch) => Object.keys(patch).length > 0, "nothing to update");

export const commitmentBulkSchema = z
  .object({ ids, action: z.enum(["approve", "unapprove", "dismiss", "fulfil", "reopen"]) })
  .strict();

export const calendarRangeSchema = z.object({ from: isoDate, to: isoDate });

export const calendarFlagQuerySchema = z.object({ status: z.enum(["open", "all"]).default("open") });

/** Settle a possible duplicate: keep one side, dismiss the other. "external" keeps the user's own event. */
export const calendarFlagResolveSchema = z
  .object({ keep: z.union([z.number().int().positive(), z.literal("external")]) })
  .strict();

export const calendarInsightsQuerySchema = z.object({ days: z.coerce.number().int().min(1).max(60).default(14) });

// --- Contacts (VIP rules) ----------------------------------------------------

const looksLikeEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const looksLikeDomain = /^@?([a-z0-9-]+\.)+[a-z]{2,}$/i;

export const contactCreateSchema = z
  .object({
    matchType: z.enum(MATCH_TYPES),
    matchValue: z.string().trim().min(1).max(254),
    tier: z.enum(TIERS),
    displayName: z.string().trim().max(80).optional(),
  })
  .strict()
  .superRefine((value, ctx) => {
    if (value.matchType === "exact_email" && !looksLikeEmail.test(value.matchValue)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["matchValue"], message: "not an email address" });
    }
    if (value.matchType === "domain" && !looksLikeDomain.test(value.matchValue)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["matchValue"], message: "not a domain" });
    }
  });
export type ContactCreate = z.infer<typeof contactCreateSchema>;

export const contactPatchSchema = z
  .object({ tier: z.enum(TIERS), displayName: z.string().trim().max(80).nullable() })
  .partial()
  .strict();

/** Mirrors Python's vip_filter.normalize_match_value. */
export function normalizeMatchValue(value: string, matchType: (typeof MATCH_TYPES)[number]): string {
  const cleaned = value.trim();
  if (matchType === "exact_email") return cleaned.toLowerCase();
  if (matchType === "domain") return cleaned.toLowerCase().replace(/^@+/, "");
  return cleaned;
}

// --- Activity, jobs, analytics -----------------------------------------------

export const activityQuerySchema = z.object({
  /** Keyset cursor: events with an id below this one. */
  before: z.coerce.number().int().positive().optional(),
  limit: z.coerce.number().int().min(1).max(200).default(50),
  type: csv(z.enum(EVENT_TYPES)),
  severity: csv(z.enum(SEVERITIES)),
  correlation: z.string().max(64).optional(),
});

export const jobQuerySchema = z.object({
  status: csv(z.enum(JOB_STATUSES)),
  type: csv(z.enum(JOB_TYPES)),
  page,
  pageSize,
});

export const ANALYTICS_RANGES = ["7d", "30d", "90d"] as const;
export const analyticsQuerySchema = z.object({
  range: z.enum(ANALYTICS_RANGES).default("30d"),
  tz: timeZone.default("UTC"),
});
export type AnalyticsQuery = z.infer<typeof analyticsQuerySchema>;

// --- Settings ----------------------------------------------------------------

export const notificationSettingsSchema = z
  .object({
    importantEmail: z.boolean().default(true),
    jobFailed: z.boolean().default(true),
    retrySucceeded: z.boolean().default(true),
    calendarFailed: z.boolean().default(true),
    calendarIssues: z.boolean().default(true),
    /** Also show the operating system's own notification, when permitted. */
    browser: z.boolean().default(false),
  })
  .strict();

export const emailSettingsSchema = z
  .object({
    fetchIntervalMinutes: z.number().int().min(5).max(1440).default(15),
    lookbackDays: z.number().int().min(1).max(90).default(7),
    maxPerFetch: z.number().int().min(10).max(500).default(50),
  })
  .strict();

export const calendarSettingsSchema = z
  .object({
    /** auto: Google when its calendar access has been granted, otherwise .ics only. */
    target: z.enum(["auto", "ics", "google"]).default("auto"),
    reminderMinutes: z.number().int().min(0).max(1440).default(30),
    timeZone: timeZone.default("UTC"),
    workingHours: z
      .object({
        start: hhmm.default("09:00"),
        end: hhmm.default("18:00"),
        days: z.array(z.number().int().min(0).max(6)).min(1).max(7).default([1, 2, 3, 4, 5]),
      })
      .strict()
      .refine((hours) => hours.start < hours.end, "working hours must end after they start")
      .default({}),
  })
  .strict();

export const appearanceSettingsSchema = z
  .object({
    theme: z.enum(["system", "light", "dark"]).default("system"),
    density: z.enum(["comfortable", "compact"]).default("comfortable"),
    reducedMotion: z.boolean().default(false),
  })
  .strict();

export const RETENTION_OPTIONS = [0, 30, 90, 180, 365] as const;
export const privacySettingsSchema = z
  .object({
    /** Days to keep email; 0 keeps everything. */
    retentionDays: z
      .number()
      .int()
      .refine((days) => (RETENTION_OPTIONS as readonly number[]).includes(days), "not a retention option")
      .default(0),
    /** When off, email bodies are blanked once analysed; metadata stays. */
    keepEmailBodies: z.boolean().default(true),
  })
  .strict();

export const SETTINGS_SCHEMAS = {
  notifications: notificationSettingsSchema,
  email: emailSettingsSchema,
  calendar: calendarSettingsSchema,
  appearance: appearanceSettingsSchema,
  privacy: privacySettingsSchema,
} as const;
export type SettingsSection = keyof typeof SETTINGS_SCHEMAS;
export const SETTINGS_SECTIONS = Object.keys(SETTINGS_SCHEMAS) as SettingsSection[];
export type Settings = { [K in SettingsSection]: z.infer<(typeof SETTINGS_SCHEMAS)[K]> };

export function defaultSettings(): Settings {
  return Object.fromEntries(
    SETTINGS_SECTIONS.map((section) => [section, SETTINGS_SCHEMAS[section].parse({})]),
  ) as Settings;
}

// --- Auth --------------------------------------------------------------------

export const passwordSchema = z
  .string()
  .min(10, "use at least 10 characters")
  .max(200)
  .refine((value) => new Set(value).size >= 4, "too repetitive");

export const setupSchema = z
  .object({
    email: z.string().trim().toLowerCase().email().max(254),
    displayName: z.string().trim().min(1).max(80),
    password: passwordSchema,
  })
  .strict();

export const loginSchema = z
  .object({
    email: z.string().trim().toLowerCase().email().max(254),
    password: z.string().min(1).max(200),
  })
  .strict();

export const changePasswordSchema = z
  .object({ currentPassword: z.string().min(1).max(200), newPassword: passwordSchema })
  .strict();

export const profilePatchSchema = z
  .object({
    displayName: z.string().trim().min(1).max(80),
    email: z.string().trim().toLowerCase().email().max(254),
  })
  .partial()
  .strict();

// --- Privacy and setup -------------------------------------------------------

export const purgeSchema = z
  .object({
    scope: z.enum(["email_bodies", "activity", "everything"]),
    /** Typed by the user, so a purge can never be one stray click. */
    confirm: z.literal("DELETE"),
  })
  .strict();

export const mailboxSchema = z
  .object({
    address: z.string().trim().toLowerCase().email().max(254),
    password: z.string().min(1).max(200),
  })
  .strict();
