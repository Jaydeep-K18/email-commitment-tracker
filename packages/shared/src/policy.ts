/**
 * The calendar tier policy, mirrored from Python's `sync_engine.decide()`.
 *
 * The server needs it to build the review queue and to explain on screen why
 * a commitment is or is not on the calendar; the worker uses the Python copy
 * to decide what to publish. Two copies of a policy are a liability unless
 * something forces them to agree, so `contracts/sync-decisions.json` holds
 * Python's answer for every combination of inputs, and the tests check this
 * function gives the same answer — same booleans, same reason, word for word.
 */
import type { CommitmentStatus } from "./catalogue";

export interface PolicyInput {
  type: string;
  hasDeadline: boolean;
  status: CommitmentStatus | string;
  vipTier: string | null;
  manuallyAdded: boolean;
  syncApproved: boolean;
}

export interface SyncDecision {
  shouldSync: boolean;
  reason: string;
  needsReview: boolean;
  awaitingApproval: boolean;
}

const NON_CALENDAR_TYPES = new Set(["question_pending"]);
const AUTO_SYNC_TIERS = new Set(["CRITICAL", "IMPORTANT"]);
const FLAGGED_TIERS = new Set(["IMPORTANT"]);
const APPROVAL_TIERS = new Set(["MONITOR"]);

function decision(
  shouldSync: boolean,
  reason: string,
  extra: Partial<Pick<SyncDecision, "needsReview" | "awaitingApproval">> = {},
): SyncDecision {
  return {
    shouldSync,
    reason,
    needsReview: extra.needsReview ?? false,
    awaitingApproval: extra.awaitingApproval ?? false,
  };
}

/** Ordered most-disqualifying first, exactly as in Python. */
export function decide(c: PolicyInput): SyncDecision {
  if (NON_CALENDAR_TYPES.has(c.type)) return decision(false, "questions do not go on the calendar");
  if (!c.hasDeadline) return decision(false, "no deadline to put on the calendar");
  if (c.status === "superseded") return decision(false, "replaced by a newer email");
  if (c.status === "dismissed") return decision(false, "dismissed by the user");
  if (c.status === "fulfilled") return decision(false, "already fulfilled");

  const tier = (c.vipTier ?? "").toUpperCase();

  if (tier === "SKIP" && !c.manuallyAdded) return decision(false, "sender is on the skip list");

  if (AUTO_SYNC_TIERS.has(tier)) {
    const flagged = FLAGGED_TIERS.has(tier);
    return decision(
      true,
      flagged ? `${tier} tier syncs automatically (flagged for review)` : `${tier} tier syncs automatically`,
      { needsReview: flagged },
    );
  }

  if (c.syncApproved) {
    if (tier === "SKIP") return decision(true, "you added this from the Gmail panel");
    return decision(true, `${tier || "untiered"} commitment approved by you`);
  }

  if (APPROVAL_TIERS.has(tier)) {
    return decision(false, `${tier} tier waiting for your approval`, { awaitingApproval: true });
  }

  return decision(false, "no VIP tier — waiting for your approval", { awaitingApproval: true });
}
