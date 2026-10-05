import { COMMITMENT_TYPES, TIERS, type CommitmentQuery } from "@commitmail/shared";
import { CheckCheck, ClipboardCheck, Hourglass, ListChecks } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "../components/domain";
import { CommitmentCard } from "../components/email";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Pagination, Segmented } from "../components/ui/controls";
import { Select } from "../components/ui/form";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { useBulkCommitments, useCommitments } from "../lib/queries";

const TYPE_LABELS: Record<string, string> = {
  deadline_on_you: "You owe",
  deadline_from_others: "Owed to you",
  question_pending: "Questions",
  meeting: "Meetings",
};

const EMPTY: Record<string, { title: string; description: string; icon: JSX.Element }> = {
  upcoming: { title: "Nothing coming up", description: "No open commitments with a deadline ahead.", icon: <CheckCheck /> },
  review: { title: "Nothing to review", description: "Commitments from MONITOR contacts wait here for your OK before reaching the calendar.", icon: <ClipboardCheck /> },
  overdue: { title: "Nothing overdue", description: "Everything past its deadline has been dealt with.", icon: <Hourglass /> },
  all: { title: "No commitments yet", description: "They appear as the model reads mail from your contacts.", icon: <ListChecks /> },
};

export default function Commitments() {
  const [params, setParams] = useSearchParams();
  const view = (params.get("view") as CommitmentQuery["view"]) || "upcoming";
  const query: Partial<CommitmentQuery> = {
    view,
    tier: params.get("tier") ? [params.get("tier") as CommitmentQuery["tier"] extends Array<infer T> | undefined ? T : never] : undefined,
    type: params.get("type") ? [params.get("type") as never] : undefined,
    page: Number(params.get("page") || 1),
    pageSize: 20,
  };
  const commitments = useCommitments(query);
  const reviewCount = useCommitments({ view: "review", pageSize: 1 });
  const bulk = useBulkCommitments();

  const set = (changes: Record<string, string | undefined>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) value ? next.set(key, value) : next.delete(key);
      if (!("page" in changes)) next.delete("page");
      return next;
    });

  const items = commitments.data?.items ?? [];
  const empty = EMPTY[view ?? "all"]!;

  return (
    <>
      <PageHeader
        title="Commitments"
        description="What the model found in your email — deadlines you owe, things owed to you, meetings and open questions."
        actions={
          view === "review" && items.length > 0 ? (
            <Button
              variant="primary"
              loading={bulk.isPending}
              onClick={() =>
                bulk.mutate(
                  { ids: items.map((c) => c.id), action: "approve" },
                  { onSuccess: (r) => toast.success(`Approved ${r.updated}`), onError: (e) => toast.error(errorMessage(e)) },
                )
              }
            >
              Approve all on this page
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="View"
          value={view ?? "upcoming"}
          onChange={(value) => set({ view: value })}
          options={[
            { value: "upcoming", label: "Upcoming" },
            { value: "review", label: "Review", count: reviewCount.data?.total },
            { value: "overdue", label: "Overdue" },
            { value: "all", label: "All" },
          ]}
        />
        <Select aria-label="Type" className="w-auto" value={params.get("type") ?? ""} onChange={(e) => set({ type: e.target.value || undefined })}>
          <option value="">All types</option>
          {COMMITMENT_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
        </Select>
        <Select aria-label="Tier" className="w-auto" value={params.get("tier") ?? ""} onChange={(e) => set({ tier: e.target.value || undefined })}>
          <option value="">All tiers</option>
          {TIERS.map((t) => <option key={t} value={t}>{t.toLowerCase()}</option>)}
        </Select>
      </div>

      {commitments.isPending ? (
        <Card className="p-5"><SkeletonRows rows={5} /></Card>
      ) : commitments.error ? (
        <Card><ErrorState error={commitments.error} onRetry={() => void commitments.refetch()} /></Card>
      ) : !items.length ? (
        <Card><EmptyState icon={empty.icon} title={empty.title} description={empty.description} /></Card>
      ) : (
        <>
          <ul className="grid gap-3 lg:grid-cols-2">
            {items.map((c) => <CommitmentCard key={c.id} commitment={c} showSource />)}
          </ul>
          <div className="mt-4">
            <Pagination page={commitments.data!.page} pageSize={commitments.data!.pageSize} total={commitments.data!.total} onPage={(page) => set({ page: String(page) })} />
          </div>
        </>
      )}
    </>
  );
}
