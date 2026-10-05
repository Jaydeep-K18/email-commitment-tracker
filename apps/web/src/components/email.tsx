/** One email, opened: its content, what was found in it, and its whole story. */
import { CATEGORIES, CATEGORY_LABELS, type Commitment, type EmailDetail } from "@commitmail/shared";
import {
  Archive,
  ArchiveRestore,
  CalendarCheck,
  CalendarX,
  ChevronDown,
  Clock3,
  Mail,
  Plus,
  Quote,
  Reply,
  Star,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { dateTime, deadline, deadlineRelative, isOverdue, minutes } from "../lib/format";
import { useBulkEmails, useEmail, useTags, useUpdateCommitment, useUpdateEmail } from "../lib/queries";
import { ActivityItem } from "./activity";
import { Avatar, CategoryBadge, TierBadge } from "./domain";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { DropdownContent, DropdownItem, DropdownLabel, DropdownMenu, DropdownSeparator, DropdownTrigger, Tooltip } from "./ui/overlay";
import { ErrorState, Skeleton } from "./ui/states";

export function EmailDetailPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const email = useEmail(id);
  return (
    <Card className="flex max-h-[calc(100dvh-8rem)] min-w-0 flex-col overflow-hidden lg:sticky lg:top-20">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <span className="text-[12px] font-medium tracking-wide text-faint uppercase">Email</span>
        <Button size="icon-sm" variant="ghost" onClick={onClose} aria-label="Close email">
          <X className="size-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {email.isPending ? (
          <div className="space-y-3 p-5">
            <Skeleton className="h-6 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-40" />
          </div>
        ) : email.error ? (
          <ErrorState error={email.error} onRetry={() => void email.refetch()} />
        ) : email.data ? (
          <EmailContent email={email.data} />
        ) : null}
      </div>
    </Card>
  );
}

function EmailContent({ email }: { email: EmailDetail }) {
  const update = useUpdateEmail();
  const patch = (body: Parameters<typeof update.mutate>[0]["patch"], message: string) =>
    update.mutate({ id: email.id, patch: body }, { onSuccess: () => toast.success(message), onError: (e) => toast.error(errorMessage(e)) });

  return (
    <article>
      <header className="space-y-3 border-b border-border p-5">
        <h2 className="text-lg leading-snug font-semibold text-text">{email.subject || "(no subject)"}</h2>
        <div className="flex items-center gap-3">
          <Avatar name={email.senderName} email={email.senderEmail} size={36} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-text">{email.senderName || email.senderEmail}</p>
            <p className="truncate text-[12.5px] text-muted">
              {email.senderEmail} · {dateTime(email.receivedAt)}
            </p>
          </div>
          <TierBadge tier={email.vipTier} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <CategoryMenu email={email} />
          <TagsEditor email={email} />
        </div>
        {email.categoryReason && <p className="text-[12.5px] text-faint">Why: {email.categoryReason}</p>}
        <div className="flex flex-wrap gap-1.5 pt-1">
          <Button size="sm" variant="ghost" onClick={() => patch({ isStarred: !email.isStarred }, email.isStarred ? "Unstarred" : "Starred")}>
            <Star className={cn("size-3.5", email.isStarred && "text-warning")} fill={email.isStarred ? "currentColor" : "none"} /> {email.isStarred ? "Starred" : "Star"}
          </Button>
          {email.archivedAt ? (
            <Button size="sm" variant="ghost" onClick={() => patch({ archived: false }, "Moved to inbox")}><ArchiveRestore className="size-3.5" /> Unarchive</Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => patch({ archived: true }, "Archived")}><Archive className="size-3.5" /> Archive</Button>
          )}
          {email.deletedAt ? (
            <Button size="sm" variant="ghost" onClick={() => patch({ deleted: false }, "Restored")}><Undo2 className="size-3.5" /> Restore</Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => patch({ deleted: true }, "Moved to trash")}><Trash2 className="size-3.5" /> Delete</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => patch({ isRead: false }, "Marked unread")}><Mail className="size-3.5" /> Mark unread</Button>
        </div>
        <p className="text-[11.5px] text-faint">These change CommitMail's view only — your real mailbox is never modified.</p>
      </header>

      {email.commitments.length > 0 && (
        <section className="border-b border-border p-5">
          <h3 className="mb-3 text-[13px] font-semibold text-text">Found in this email</h3>
          <ul className="space-y-2.5">
            {email.commitments.map((c) => <CommitmentCard key={c.id} commitment={c} />)}
          </ul>
        </section>
      )}

      {email.repliedAt && (
        <section className="flex items-center gap-2 border-b border-border px-5 py-3 text-[13px] text-muted">
          <Reply className="size-4 text-success" /> You replied {minutes(email.responseMinutes)} after it arrived ({dateTime(email.repliedAt)}).
        </section>
      )}

      <section className="p-5">
        <div className="text-[14px] leading-relaxed whitespace-pre-wrap text-text">{email.body || <span className="text-faint">The body has been removed under your privacy settings.</span>}</div>
      </section>

      {email.timeline.length > 0 && (
        <section className="border-t border-border p-5">
          <h3 className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-text"><Clock3 className="size-4 text-faint" /> What happened</h3>
          <ol className="relative ml-3.5 border-l border-border pl-3">
            {email.timeline.map((event) => (
              <li key={event.id} className="-ml-[26px]">
                <ActivityItem event={event} />
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

export function CommitmentCard({ commitment: c, showSource = false }: { commitment: Commitment; showSource?: boolean }) {
  const update = useUpdateCommitment();
  const act = (patch: { status?: string; approved?: boolean }, message: string) =>
    update.mutate({ id: c.id, patch }, { onSuccess: () => toast.success(message), onError: (e) => toast.error(errorMessage(e)) });
  const overdue = c.status === "pending" && isOverdue(c.deadline);
  return (
    <li className="rounded-xl border border-border bg-surface-2/60 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-text">{c.subject}</p>
          <p className={cn("mt-0.5 text-[12.5px]", overdue ? "text-danger" : "text-muted")}>
            {deadline(c.deadline, c.allDay)}{c.deadline && ` · ${deadlineRelative(c.deadline)}`}
            {c.counterpartyName && ` · ${c.counterpartyName}`}
          </p>
          {showSource && c.source.subject && <p className="mt-0.5 truncate text-[12px] text-faint">From “{c.source.subject}”</p>}
        </div>
        <Tooltip content={c.decision.reason}>
          <span>
            {c.decision.shouldSync ? (
              <Badge tone="success"><CalendarCheck className="size-3" /> On calendar</Badge>
            ) : c.decision.awaitingApproval ? (
              <Badge tone="warning">Needs approval</Badge>
            ) : (
              <Badge tone="neutral"><CalendarX className="size-3" /> Not on calendar</Badge>
            )}
          </span>
        </Tooltip>
      </div>
      {c.evidenceQuote && (
        <blockquote className="mt-2.5 flex gap-2 rounded-lg bg-surface px-3 py-2 text-[12.5px] text-muted italic">
          <Quote className="mt-0.5 size-3.5 shrink-0 text-faint" /> {c.evidenceQuote}
        </blockquote>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {c.decision.awaitingApproval && (
          <Button size="sm" variant="primary" loading={update.isPending} onClick={() => act({ approved: true }, "Approved — it will appear on your calendar")}>
            Approve for calendar
          </Button>
        )}
        {c.status === "pending" || c.status === "overdue" ? (
          <>
            <Button size="sm" onClick={() => act({ status: "fulfilled" }, "Marked done")}>Mark done</Button>
            <Button size="sm" variant="ghost" onClick={() => act({ status: "dismissed" }, "Dismissed")}>Dismiss</Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => act({ status: "pending" }, "Reopened")}>Reopen</Button>
        )}
      </div>
    </li>
  );
}

function CategoryMenu({ email }: { email: EmailDetail }) {
  const update = useUpdateEmail();
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <button className="inline-flex items-center gap-1 rounded-full transition-opacity hover:opacity-80" aria-label="Change category">
          {email.category ? <CategoryBadge category={email.category} /> : <Badge>Uncategorised</Badge>}
          <ChevronDown className="size-3.5 text-faint" />
        </button>
      </DropdownTrigger>
      <DropdownContent align="start">
        <DropdownLabel>Move to</DropdownLabel>
        {CATEGORIES.map((category) => (
          <DropdownItem key={category} onSelect={() => update.mutate({ id: email.id, patch: { category } })}>
            {CATEGORY_LABELS[category]}
          </DropdownItem>
        ))}
        {email.categorySource === "user" && (
          <>
            <DropdownSeparator />
            <DropdownItem onSelect={() => update.mutate({ id: email.id, patch: { category: null } })}>Let CommitMail decide</DropdownItem>
          </>
        )}
      </DropdownContent>
    </DropdownMenu>
  );
}

function TagsEditor({ email }: { email: EmailDetail }) {
  const tags = useTags();
  const bulk = useBulkEmails();
  const [, force] = useState(0);
  const has = new Set(email.tags.map((t) => t.id));
  const toggle = (tagId: number) =>
    bulk.mutate(
      { action: has.has(tagId) ? "removeTag" : "addTag", ids: [email.id], tagId },
      { onSuccess: () => force((n) => n + 1) },
    );
  return (
    <>
      {email.tags.map((tag) => (
        <Badge key={tag.id} tone="neutral" className="pr-1">
          #{tag.name}
          <button onClick={() => toggle(tag.id)} aria-label={`Remove tag ${tag.name}`} className="rounded-full p-0.5 hover:bg-surface"><X className="size-3" /></button>
        </Badge>
      ))}
      {!!tags.data?.length && (
        <DropdownMenu>
          <DropdownTrigger asChild>
            <button className="inline-flex items-center gap-1 rounded-full border border-dashed border-border-strong px-2 py-0.5 text-[11.5px] text-muted hover:text-text">
              <Plus className="size-3" /> Tag
            </button>
          </DropdownTrigger>
          <DropdownContent align="start">
            {tags.data.map((tag) => (
              <DropdownItem key={tag.id} onSelect={() => toggle(tag.id)}>
                {has.has(tag.id) ? "✓ " : ""}#{tag.name}
              </DropdownItem>
            ))}
          </DropdownContent>
        </DropdownMenu>
      )}
    </>
  );
}
