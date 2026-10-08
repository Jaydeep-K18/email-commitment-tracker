import {
  CATEGORIES,
  CATEGORY_LABELS,
  INBOX_FOLDERS,
  TIERS,
  type Category,
  type EmailBulk,
  type EmailListItem,
  type InboxQuery,
} from "@commitmail/shared";
import {
  Archive,
  ArchiveRestore,
  BookmarkPlus,
  CalendarDays,
  Filter,
  ListChecks,
  MailOpen,
  Paperclip,
  Search,
  Star,
  Tag as TagIcon,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { CategoryBadge, PageHeader } from "../components/domain";
import { EmailDetailPanel } from "../components/email";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Kbd, Pagination, Segmented } from "../components/ui/controls";
import { Checkbox, Input, Select, Switch } from "../components/ui/form";
import {
  Dialog,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownMenu,
  DropdownSeparator,
  DropdownTrigger,
  Popover,
} from "../components/ui/overlay";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { shortDate } from "../lib/format";
import { useBulkEmails, useEmails, useSaveView, useTags, useUpdateEmail, useViews } from "../lib/queries";

const SORTS = [
  { value: "received:desc", label: "Newest first" },
  { value: "received:asc", label: "Oldest first" },
  { value: "priority:desc", label: "Priority" },
  { value: "sender:asc", label: "Sender A–Z" },
  { value: "subject:asc", label: "Subject A–Z" },
  { value: "relevance:desc", label: "Best match" },
] as const;

const FOLDER_LABELS: Record<(typeof INBOX_FOLDERS)[number], string> = {
  inbox: "Inbox",
  archived: "Archived",
  trash: "Trash",
  all: "All mail",
};

/** Filters live in the URL: shareable, bookmarkable, and the back button works. */
function useInboxQuery() {
  const [params, setParams] = useSearchParams();
  const query = useMemo<Partial<InboxQuery>>(() => {
    const list = (key: string) => params.get(key)?.split(",").filter(Boolean);
    const flag = (key: string) => (params.get(key) === "true" ? true : undefined);
    return {
      q: params.get("q") || undefined,
      folder: (params.get("folder") as InboxQuery["folder"]) || "inbox",
      category: list("category") as Category[] | undefined,
      tier: list("tier") as InboxQuery["tier"],
      tag: list("tag")?.map(Number),
      unread: flag("unread"),
      starred: flag("starred"),
      hasCommitments: flag("hasCommitments"),
      sort: (params.get("sort") as InboxQuery["sort"]) || (params.get("q") ? "relevance" : "received"),
      dir: (params.get("dir") as InboxQuery["dir"]) || "desc",
      page: Number(params.get("page") || 1),
      pageSize: 25,
    };
  }, [params]);

  const update = useCallback(
    (changes: Record<string, string | number | boolean | Array<string | number> | undefined | null>, resetPage = true) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(changes)) {
            const text = Array.isArray(value) ? value.join(",") : value == null || value === false ? "" : String(value);
            if (text) next.set(key, text);
            else next.delete(key);
          }
          if (resetPage && !("page" in changes)) next.delete("page");
          return next;
        },
        { replace: false },
      );
    },
    [setParams],
  );
  return { query, update, params };
}

export default function InboxPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { query, update, params } = useInboxQuery();
  const emails = useEmails(query);
  const bulk = useBulkEmails();
  const updateEmail = useUpdateEmail();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [cursor, setCursor] = useState(0);
  const [searchText, setSearchText] = useState(query.q ?? "");
  const openId = id ? Number(id) : null;
  const items = emails.data?.items ?? [];

  useEffect(() => setSearchText(query.q ?? ""), [query.q]);
  // A new page or filter is a new selection.
  useEffect(() => setSelected(new Set()), [params]);

  const runBulk = (body: EmailBulk, label: string) =>
    bulk.mutate(body, {
      onSuccess: (result) => {
        toast.success(`${label}: ${result.updated} email${result.updated === 1 ? "" : "s"}`);
        setSelected(new Set());
      },
      onError: (error) => toast.error(errorMessage(error)),
    });

  const open = (email: EmailListItem) => {
    navigate({ pathname: `/inbox/${email.id}`, search: params.toString() });
    if (!email.isRead) updateEmail.mutate({ id: email.id, patch: { isRead: true } });
  };

  const toggleSelect = (emailId: number) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(emailId)) next.delete(emailId);
      else next.add(emailId);
      return next;
    });

  // Keyboard: j/k move, Enter opens, x selects, e archives, s stars.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable || event.metaKey || event.ctrlKey) return;
      const current = items[cursor];
      if (event.key === "j") setCursor((c) => Math.min(items.length - 1, c + 1));
      else if (event.key === "k") setCursor((c) => Math.max(0, c - 1));
      else if (event.key === "Enter" && current) open(current);
      else if (event.key === "x" && current) toggleSelect(current.id);
      else if (event.key === "e" && current) runBulk({ action: current.archivedAt ? "unarchive" : "archive", ids: [current.id] }, "Archived");
      else if (event.key === "s" && current) updateEmail.mutate({ id: current.id, patch: { isStarred: !current.isStarred } });
      else if (event.key === "Escape" && openId) navigate({ pathname: "/inbox", search: params.toString() });
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const allSelected = items.length > 0 && items.every((e) => selected.has(e.id));
  const counts = emails.data?.counts;
  const activeCategory = query.category?.length === 1 ? query.category[0]! : "all";

  return (
    <>
      <PageHeader
        title={FOLDER_LABELS[query.folder ?? "inbox"]}
        description="Sorted for you by the rules and the model — change any category and it stays changed."
        actions={<SavedViews />}
      />

      <div className={cn("grid gap-5", openId && "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]")}>
        <div className="min-w-0 space-y-3">
          <Segmented
            label="Category"
            value={activeCategory}
            onChange={(value) => update({ category: value === "all" ? undefined : [value] })}
            className="max-w-full overflow-x-auto"
            options={[
              { value: "all", label: "All" },
              ...CATEGORIES.map((c) => ({ value: c, label: CATEGORY_LABELS[c], count: counts?.byCategory[c] })),
            ]}
          />

          <div className="flex flex-wrap items-center gap-2">
            <form
              className="relative min-w-48 flex-1"
              onSubmit={(event) => {
                event.preventDefault();
                update({ q: searchText.trim() || undefined, sort: searchText.trim() ? "relevance" : undefined });
              }}
            >
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint" />
              <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="Search subject, sender, body…" className="pl-9" aria-label="Search" />
              {query.q && (
                <button type="button" className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-faint hover:text-text" aria-label="Clear search" onClick={() => update({ q: undefined, sort: undefined })}>
                  <X className="size-3.5" />
                </button>
              )}
            </form>
            <Select aria-label="Folder" value={query.folder} onChange={(e) => update({ folder: e.target.value === "inbox" ? undefined : e.target.value })} className="w-auto">
              {INBOX_FOLDERS.map((f) => <option key={f} value={f}>{FOLDER_LABELS[f]}</option>)}
            </Select>
            <Select
              aria-label="Sort"
              value={`${query.sort}:${query.dir}`}
              onChange={(e) => {
                const [sort, dir] = e.target.value.split(":");
                update({ sort, dir });
              }}
              className="w-auto"
            >
              {SORTS.filter((s) => s.value !== "relevance:desc" || query.q).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </Select>
            <FiltersPopover query={query} update={update} />
          </div>

          <Card className="overflow-hidden">
            <div className="flex h-11 items-center gap-3 border-b border-border px-4">
              <Checkbox
                label="Select all on this page"
                checked={allSelected ? true : selected.size ? "indeterminate" : false}
                onCheckedChange={(checked) => setSelected(checked ? new Set(items.map((e) => e.id)) : new Set())}
              />
              <AnimatePresence mode="wait" initial={false}>
                {selected.size ? (
                  <motion.div key="bulk" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="flex flex-1 items-center gap-1 overflow-x-auto">
                    <span className="mr-1 text-[13px] font-medium whitespace-nowrap text-text">{selected.size} selected</span>
                    <BulkActions ids={[...selected]} folder={query.folder ?? "inbox"} run={runBulk} pending={bulk.isPending} />
                  </motion.div>
                ) : (
                  <motion.p key="count" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex-1 text-[13px] text-muted">
                    {emails.data ? `${emails.data.total} email${emails.data.total === 1 ? "" : "s"}` : " "}
                    <span className="ml-3 hidden text-faint xl:inline">
                      <Kbd>j</Kbd> <Kbd>k</Kbd> move · <Kbd>x</Kbd> select · <Kbd>e</Kbd> archive · <Kbd>s</Kbd> star
                    </span>
                  </motion.p>
                )}
              </AnimatePresence>
            </div>

            {emails.isPending ? (
              <div className="p-4"><SkeletonRows rows={8} /></div>
            ) : emails.error ? (
              <ErrorState error={emails.error} onRetry={() => void emails.refetch()} />
            ) : !items.length ? (
              <EmptyState
                icon={query.q ? <Search /> : <MailOpen />}
                title={query.q ? "No matches" : query.folder === "trash" ? "Trash is empty" : "Nothing here"}
                description={query.q ? `Nothing matches “${query.q}” with these filters.` : "When mail arrives it lands here, already sorted."}
              />
            ) : (
              <ul className={cn("divide-y divide-border transition-opacity", emails.isPlaceholderData && "opacity-60")}>
                {items.map((email, index) => (
                  <EmailRow
                    key={email.id}
                    email={email}
                    active={email.id === openId}
                    focused={index === cursor}
                    selected={selected.has(email.id)}
                    onSelect={() => toggleSelect(email.id)}
                    onOpen={() => {
                      setCursor(index);
                      open(email);
                    }}
                    onStar={() => updateEmail.mutate({ id: email.id, patch: { isStarred: !email.isStarred } })}
                  />
                ))}
              </ul>
            )}
            {emails.data && emails.data.total > emails.data.pageSize && (
              <div className="border-t border-border px-4 py-2.5">
                <Pagination page={emails.data.page} pageSize={emails.data.pageSize} total={emails.data.total} onPage={(page) => update({ page }, false)} />
              </div>
            )}
          </Card>
        </div>

        {openId && (
          <EmailDetailPanel
            id={openId}
            onClose={() => navigate({ pathname: "/inbox", search: params.toString() })}
          />
        )}
      </div>
    </>
  );
}

function EmailRow({
  email,
  active,
  focused,
  selected,
  onSelect,
  onOpen,
  onStar,
}: {
  email: EmailListItem;
  active: boolean;
  focused: boolean;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onStar: () => void;
}) {
  return (
    <li
      onClick={onOpen}
      aria-current={active ? "true" : undefined}
      className={cn(
        "group relative flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors",
        active ? "bg-accent-soft/60" : selected ? "bg-surface-2" : "hover:bg-surface-2",
        focused && !active && "ring-1 ring-inset ring-border-strong",
      )}
    >
      {!email.isRead && <span className="absolute top-1/2 left-1.5 size-1.5 -translate-y-1/2 rounded-full bg-accent" aria-label="Unread" />}
      <div className="flex items-center gap-2 pt-0.5">
        <Checkbox label={`Select ${email.subject ?? "email"}`} checked={selected} onCheckedChange={onSelect} />
        <button
          onClick={(event) => {
            event.stopPropagation();
            onStar();
          }}
          aria-label={email.isStarred ? "Unstar" : "Star"}
          aria-pressed={email.isStarred}
          className={cn("rounded p-0.5 transition-colors", email.isStarred ? "text-warning" : "text-faint opacity-0 group-hover:opacity-100 hover:text-warning")}
        >
          <Star className="size-4" fill={email.isStarred ? "currentColor" : "none"} />
        </button>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className={cn("truncate text-[13.5px]", email.isRead ? "text-muted" : "font-semibold text-text")}>
            {email.senderName || email.senderEmail || "Unknown sender"}
          </span>
          <span className="ml-auto shrink-0 text-[12px] text-faint tabular-nums">{shortDate(email.receivedAt)}</span>
        </div>
        <p className={cn("truncate text-[13.5px]", email.isRead ? "text-text" : "font-medium text-text")}>{email.subject || "(no subject)"}</p>
        <p className="truncate text-[12.5px] text-faint">{email.snippet}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <CategoryBadge category={email.category} />
          {email.tags.map((tag) => <Badge key={tag.id} tone="neutral">#{tag.name}</Badge>)}
          {email.commitmentCount > 0 && (
            <Badge tone="accent"><ListChecks className="size-3" /> {email.commitmentCount}</Badge>
          )}
          {email.hasInvite && <CalendarDays className="size-3.5 text-cat-meeting" aria-label="Calendar invitation" />}
          {email.hasAttachments && <Paperclip className="size-3.5 text-faint" aria-label="Has attachments" />}
        </div>
      </div>
    </li>
  );
}

function BulkActions({
  ids,
  folder,
  run,
  pending,
}: {
  ids: number[];
  folder: string;
  run: (body: EmailBulk, label: string) => void;
  pending: boolean;
}) {
  const tags = useTags();
  return (
    <>
      <Button size="sm" variant="ghost" disabled={pending} onClick={() => run({ action: "markRead", ids }, "Marked read")}><MailOpen className="size-3.5" /> Read</Button>
      {folder === "archived" ? (
        <Button size="sm" variant="ghost" disabled={pending} onClick={() => run({ action: "unarchive", ids }, "Moved to inbox")}><ArchiveRestore className="size-3.5" /> Unarchive</Button>
      ) : (
        <Button size="sm" variant="ghost" disabled={pending} onClick={() => run({ action: "archive", ids }, "Archived")}><Archive className="size-3.5" /> Archive</Button>
      )}
      {folder === "trash" ? (
        <Button size="sm" variant="ghost" disabled={pending} onClick={() => run({ action: "restore", ids }, "Restored")}><Undo2 className="size-3.5" /> Restore</Button>
      ) : (
        <Button size="sm" variant="ghost" disabled={pending} onClick={() => run({ action: "delete", ids }, "Moved to trash")}><Trash2 className="size-3.5" /> Delete</Button>
      )}
      <Button size="sm" variant="ghost" disabled={pending} onClick={() => run({ action: "star", ids }, "Starred")}><Star className="size-3.5" /> Star</Button>
      <DropdownMenu>
        <DropdownTrigger asChild>
          <Button size="sm" variant="ghost" disabled={pending}>Category</Button>
        </DropdownTrigger>
        <DropdownContent align="start">
          <DropdownLabel>File under</DropdownLabel>
          {CATEGORIES.map((c) => (
            <DropdownItem key={c} onSelect={() => run({ action: "setCategory", ids, category: c }, CATEGORY_LABELS[c])}>
              {CATEGORY_LABELS[c]}
            </DropdownItem>
          ))}
          <DropdownSeparator />
          <DropdownItem onSelect={() => run({ action: "setCategory", ids, category: null }, "Back to automatic")}>Let CommitMail decide</DropdownItem>
        </DropdownContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownTrigger asChild>
          <Button size="sm" variant="ghost" disabled={pending}><TagIcon className="size-3.5" /> Tag</Button>
        </DropdownTrigger>
        <DropdownContent align="start">
          {!tags.data?.length && <p className="px-2.5 py-2 text-[13px] text-muted">No tags yet — create them in Settings.</p>}
          {tags.data?.map((tag) => (
            <DropdownItem key={tag.id} onSelect={() => run({ action: "addTag", ids, tagId: tag.id }, `Tagged #${tag.name}`)}>#{tag.name}</DropdownItem>
          ))}
        </DropdownContent>
      </DropdownMenu>
    </>
  );
}

type UpdateQuery = ReturnType<typeof useInboxQuery>["update"];

function FiltersPopover({ query, update }: { query: Partial<InboxQuery>; update: UpdateQuery }) {
  const tags = useTags();
  const active = [query.unread, query.starred, query.hasCommitments, query.tier?.length, query.tag?.length].filter(Boolean).length;
  const toggleIn = (list: Array<string | number> | undefined, value: string | number) =>
    list?.includes(value) ? list.filter((v) => v !== value) : [...(list ?? []), value];
  return (
    <Popover
      className="w-72 p-4"
      trigger={
        <Button variant={active ? "subtle" : "secondary"}>
          <Filter className="size-4" /> Filters {active ? <span className="rounded-full bg-accent px-1.5 text-[11px] text-accent-fg">{active}</span> : null}
        </Button>
      }
    >
      <div className="divide-y divide-border">
        <Switch label="Unread only" checked={!!query.unread} onCheckedChange={(v) => update({ unread: v || undefined })} />
        <Switch label="Starred only" checked={!!query.starred} onCheckedChange={(v) => update({ starred: v || undefined })} />
        <Switch label="With commitments" checked={!!query.hasCommitments} onCheckedChange={(v) => update({ hasCommitments: v || undefined })} />
        <div className="py-3">
          <p className="mb-2 text-[13px] font-medium">Sender tier</p>
          <div className="flex flex-wrap gap-1.5">
            {TIERS.map((tier) => (
              <button
                key={tier}
                onClick={() => update({ tier: toggleIn(query.tier, tier) })}
                className={cn("rounded-full border px-2.5 py-0.5 text-[12px] transition-colors", query.tier?.includes(tier) ? "border-accent bg-accent-soft text-accent-text" : "border-border text-muted hover:text-text")}
              >
                {tier.toLowerCase()}
              </button>
            ))}
          </div>
        </div>
        {!!tags.data?.length && (
          <div className="py-3">
            <p className="mb-2 text-[13px] font-medium">Tags</p>
            <div className="flex flex-wrap gap-1.5">
              {tags.data.map((tag) => (
                <button
                  key={tag.id}
                  onClick={() => update({ tag: toggleIn(query.tag, tag.id) })}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[12px] transition-colors", query.tag?.includes(tag.id) ? "border-accent bg-accent-soft text-accent-text" : "border-border text-muted hover:text-text")}
                >
                  #{tag.name}
                </button>
              ))}
            </div>
          </div>
        )}
        {active > 0 && (
          <div className="pt-3">
            <Button size="sm" variant="ghost" className="w-full" onClick={() => update({ unread: undefined, starred: undefined, hasCommitments: undefined, tier: undefined, tag: undefined })}>
              Clear filters
            </Button>
          </div>
        )}
      </div>
    </Popover>
  );
}

function SavedViews() {
  const views = useViews();
  const save = useSaveView();
  const [params, setParams] = useSearchParams();
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);

  const current = () => {
    const filters: Record<string, unknown> = {};
    for (const key of ["q", "folder", "category", "tier", "tag", "unread", "starred", "hasCommitments"]) {
      const value = params.get(key);
      if (!value) continue;
      filters[key] = ["category", "tier"].includes(key) ? value.split(",") : key === "tag" ? value.split(",").map(Number) : ["unread", "starred", "hasCommitments"].includes(key) ? value === "true" : value;
    }
    return { filters, sort: { sort: params.get("sort") ?? "received", dir: (params.get("dir") as "asc" | "desc") ?? "desc" } };
  };

  return (
    <>
      <DropdownMenu>
        <DropdownTrigger asChild>
          <Button>Views</Button>
        </DropdownTrigger>
        <DropdownContent>
          <DropdownLabel>Saved views</DropdownLabel>
          {!views.data?.length && <p className="px-2.5 py-1.5 text-[13px] text-muted">None yet.</p>}
          {views.data?.map((view) => (
            <DropdownItem
              key={view.id}
              icon={view.isPinned ? <Star /> : undefined}
              onSelect={() => {
                const next = new URLSearchParams();
                for (const [key, value] of Object.entries(view.filters)) {
                  if (value === undefined || value === null || value === false) continue;
                  next.set(key, Array.isArray(value) ? value.join(",") : String(value));
                }
                next.set("sort", view.sort.sort);
                next.set("dir", view.sort.dir);
                setParams(next);
              }}
            >
              {view.name}
            </DropdownItem>
          ))}
          <DropdownSeparator />
          <DropdownItem icon={<BookmarkPlus />} onSelect={() => setNaming(true)}>Save current view…</DropdownItem>
        </DropdownContent>
      </DropdownMenu>
      <Dialog
        open={naming}
        onOpenChange={setNaming}
        title="Save this view"
        description="The current filters and sort, one click away."
        footer={
          <>
            <Button onClick={() => setNaming(false)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={!name.trim()}
              loading={save.isPending}
              onClick={() =>
                save.mutate(
                  { name: name.trim(), ...current(), isPinned: false } as never,
                  {
                    onSuccess: () => {
                      toast.success("View saved");
                      setNaming(false);
                      setName("");
                    },
                    onError: (error) => toast.error(errorMessage(error)),
                  },
                )
              }
            >
              Save view
            </Button>
          </>
        }
      >
        <Input ref={nameInput} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Unread from clients" autoFocus />
      </Dialog>
    </>
  );
}
