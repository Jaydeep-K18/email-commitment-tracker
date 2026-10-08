import { JOB_STATUSES, type Job, type JobStatus } from "@commitmail/shared";
import { CheckCircle2, CircleDashed, Loader2, RotateCcw, Workflow, X, XCircle } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "../components/domain";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Pagination, Segmented } from "../components/ui/controls";
import { Tooltip } from "../components/ui/overlay";
import { EmptyState, ErrorState, Skeleton, SkeletonRows } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { dateTime, duration, relative } from "../lib/format";
import { useJob, useJobStats, useJobs, useRetryAllFailed, useRetryJob } from "../lib/queries";

const STATUS: Record<JobStatus, { label: string; tone: BadgeTone }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  retrying: { label: "Retrying", tone: "warning" },
  succeeded: { label: "Succeeded", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = STATUS[status];
  return (
    <Badge tone={s.tone}>
      {status === "running" && <Loader2 className="size-3 animate-spin" />}
      {s.label}
    </Badge>
  );
}

export default function Jobs() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") as JobStatus | null;
  const page = Number(params.get("page") || 1);
  const jobs = useJobs({ status: status ? [status] : undefined, page });
  const stats = useJobStats();
  const retry = useRetryJob();
  const retryAll = useRetryAllFailed();
  const failed = stats.data?.byStatus.failed ?? 0;
  // With a job open beside the table, the secondary columns wait for a wide screen.
  const secondary = id ? "hidden 2xl:table-cell" : undefined;

  const set = (changes: Record<string, string | undefined>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) value ? next.set(key, value) : next.delete(key);
      if (!("page" in changes)) next.delete("page");
      return next;
    });

  const doRetry = (job: Job) =>
    retry.mutate(job.id, {
      onSuccess: () => toast.success("Queued for another try"),
      onError: (error) => toast.error(errorMessage(error)),
    });

  return (
    <>
      <PageHeader
        title="Background jobs"
        description="Every piece of background work — checking mail, analysing an email, publishing the calendar — with its retries."
        actions={
          failed > 0 ? (
            <Button
              variant="primary"
              loading={retryAll.isPending}
              onClick={() => retryAll.mutate(undefined, { onSuccess: (r) => toast.success(`Retrying ${r.retried} job${r.retried === 1 ? "" : "s"}`) })}
            >
              <RotateCcw className="size-4" /> Retry all failed
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented
          label="Status"
          value={status ?? "all"}
          onChange={(value) => set({ status: value === "all" ? undefined : value })}
          options={[
            { value: "all", label: "All" },
            ...(["failed", "retrying", "running", "queued", "succeeded"] as const).map((s) => ({
              value: s,
              label: STATUS[s].label,
              count: stats.data?.byStatus[s],
            })),
          ]}
        />
        {stats.data && (
          <span className="text-[12.5px] text-faint">
            Dispatch via {stats.data.dispatcher === "redis" ? "Redis" : "the database (Redis not connected)"}
            {stats.data.queue.ready != null && ` · ${stats.data.queue.ready} ready, ${stats.data.queue.delayed} waiting to retry`}
          </span>
        )}
      </div>

      <div className={cn("grid gap-5", id && "xl:grid-cols-[minmax(0,1fr)_420px]")}>
        <Card className="self-start overflow-hidden">
          {jobs.isPending ? (
            <div className="p-5"><SkeletonRows rows={6} /></div>
          ) : jobs.error ? (
            <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
          ) : !jobs.data?.items.length ? (
            <EmptyState
              icon={status === "failed" ? <CheckCircle2 /> : <Workflow />}
              title={status === "failed" ? "No failed jobs" : "No jobs here"}
              description={status === "failed" ? "Everything that failed has been retried or resolved." : "Jobs appear as the worker checks mail and analyses it."}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className={cn("w-full text-left text-[13px] transition-opacity", jobs.isPlaceholderData && "opacity-60")}>
                <thead className="border-b border-border bg-surface-2 text-[12px] text-muted">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Job</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                    <th className={cn("px-4 py-2.5 text-right font-medium", secondary)}>Attempts</th>
                    <th className={cn("px-4 py-2.5 font-medium", secondary)}>Updated</th>
                    <th className="px-4 py-2.5" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {jobs.data.items.map((job) => (
                    <tr
                      key={job.id}
                      onClick={() => navigate({ pathname: `/jobs/${job.id}`, search: params.toString() })}
                      className={cn("cursor-pointer transition-colors hover:bg-surface-2/70", String(job.id) === id && "bg-accent-soft/50")}
                    >
                      <td className="w-full max-w-0 px-4 py-3">
                        <p className="truncate font-medium text-text first-letter:uppercase">{job.description}</p>
                        {job.lastError && job.status !== "succeeded" && (
                          <p className="mt-0.5 truncate text-[12px] text-danger">{job.lastError}</p>
                        )}
                      </td>
                      <td className="px-4 py-3"><JobStatusBadge status={job.status} /></td>
                      <td className={cn("px-4 py-3 text-right text-muted tabular-nums", secondary)}>{job.attempts}/{job.maxAttempts}</td>
                      <td className={cn("px-4 py-3 whitespace-nowrap text-muted", secondary)}>
                        {job.status === "retrying" && job.nextAttemptAt ? (
                          <Tooltip content={`Next attempt ${dateTime(job.nextAttemptAt)}`}><span>retry {relative(job.nextAttemptAt)}</span></Tooltip>
                        ) : (
                          relative(job.updatedAt)
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {job.status === "failed" && (
                          <Button size="sm" onClick={(event) => { event.stopPropagation(); doRetry(job); }} loading={retry.isPending && retry.variables === job.id}>
                            <RotateCcw className="size-3.5" /> Retry
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {jobs.data && jobs.data.total > jobs.data.pageSize && (
            <div className="border-t border-border px-4 py-2.5">
              <Pagination page={jobs.data.page} pageSize={jobs.data.pageSize} total={jobs.data.total} onPage={(p) => set({ page: String(p) })} />
            </div>
          )}
        </Card>

        {id && <JobDetail id={Number(id)} onClose={() => navigate({ pathname: "/jobs", search: params.toString() })} onRetry={doRetry} />}
      </div>
    </>
  );
}

function JobDetail({ id, onClose, onRetry }: { id: number; onClose: () => void; onRetry: (job: Job) => void }) {
  const job = useJob(id);
  const emailLink = /^email:(\d+)$/.exec(job.data?.correlationId ?? "");
  return (
    <Card className="self-start xl:sticky xl:top-20">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-[12px] font-medium tracking-wide text-faint uppercase">Job #{id}</span>
        <Button size="icon-sm" variant="ghost" onClick={onClose} aria-label="Close job"><X className="size-4" /></Button>
      </div>
      {job.isPending ? (
        <div className="space-y-3 p-5"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-24" /></div>
      ) : job.error ? (
        <ErrorState error={job.error} />
      ) : job.data ? (
        <div className="space-y-5 p-5">
          <div>
            <p className="text-[15px] font-semibold text-text first-letter:uppercase">{job.data.description}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <JobStatusBadge status={job.data.status} />
              <span className="text-[12.5px] text-muted">Queued {dateTime(job.data.createdAt)}</span>
            </div>
            {emailLink && (
              <Link to={`/inbox/${emailLink[1]}`} className="mt-2 inline-block text-[13px] font-medium text-accent-text hover:underline">
                Open the email this is about
              </Link>
            )}
          </div>

          {job.data.lastError && job.data.status !== "succeeded" && (
            <div className="rounded-lg bg-danger-soft px-3 py-2.5 text-[12.5px] text-danger">
              <p className="font-medium">Last error</p>
              <p className="mt-0.5 font-mono break-words">{job.data.lastError}</p>
            </div>
          )}

          <section>
            <h3 className="mb-2 text-[13px] font-semibold">Attempts</h3>
            <ol className="space-y-2">
              {(job.data.history ?? []).map((attempt) => (
                <li key={attempt.attempt} className="flex gap-3 text-[12.5px]">
                  <span className="mt-0.5">
                    {attempt.status === "succeeded" ? <CheckCircle2 className="size-4 text-success" /> : attempt.status === "failed" ? <XCircle className="size-4 text-danger" /> : <CircleDashed className="size-4 text-info" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="text-text">Attempt {attempt.attempt}</span>
                    <span className="text-faint"> · {dateTime(attempt.startedAt)} · {duration(attempt.durationMs)}</span>
                    {attempt.error && <span className="mt-0.5 block font-mono break-words text-danger">{attempt.error}</span>}
                  </span>
                </li>
              ))}
              {!job.data.history?.length && <li className="text-[12.5px] text-faint">Not started yet.</li>}
            </ol>
            {job.data.status === "retrying" && job.data.nextAttemptAt && (
              <p className="mt-2 text-[12.5px] text-muted">Next attempt {relative(job.data.nextAttemptAt)}, with exponential backoff.</p>
            )}
          </section>

          {job.data.result && Object.keys(job.data.result).length > 0 && (
            <section>
              <h3 className="mb-2 text-[13px] font-semibold">Result</h3>
              <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-[12px] text-text">{JSON.stringify(job.data.result, null, 2)}</pre>
            </section>
          )}

          {job.data.status === "failed" && (
            <Button variant="primary" className="w-full" onClick={() => onRetry(job.data!)}>
              <RotateCcw className="size-4" /> Retry this job
            </Button>
          )}
        </div>
      ) : null}
    </Card>
  );
}

export { JOB_STATUSES };
