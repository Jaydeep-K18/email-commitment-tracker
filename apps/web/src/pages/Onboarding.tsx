import { ArrowRight, ServerCrash } from "lucide-react";
import { Link } from "react-router-dom";

import { DotBackground } from "../components/domain";
import { CalendarStep, GoogleStep, MailboxStep, OllamaStep } from "../components/setup";
import { Button } from "../components/ui/button";
import { ErrorState, SkeletonRows } from "../components/ui/states";
import { ApiError } from "../lib/api";
import { useSetupStatus } from "../lib/queries";

/** After the account exists: connect the model, the mailbox and a calendar. */
export default function Onboarding() {
  const status = useSetupStatus();
  const workerDown = status.error instanceof ApiError && status.error.code === "worker_unavailable";

  return (
    <div className="relative min-h-dvh">
      <DotBackground />
      <div className="relative z-10 mx-auto max-w-2xl px-4 py-12">
        <div className="mb-8 flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="size-8" />
          <span className="text-lg font-semibold">CommitMail</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">Let's get you set up</h1>
        <p className="mt-1.5 text-sm text-muted">
          Four steps. You can change any of this later in Settings.
        </p>

        <div className="mt-8 space-y-4">
          {status.isPending && <SkeletonRows rows={4} />}
          {workerDown && (
            <div className="rounded-xl border border-border bg-surface p-6 text-center">
              <ServerCrash className="mx-auto size-8 text-warning" />
              <p className="mt-3 font-semibold">The background worker isn't running</p>
              <p className="mt-1 text-sm text-muted">It does the reading and the calendar work. Start it, then come back:</p>
              <code className="mt-3 inline-block rounded-lg bg-surface-2 px-3 py-1.5 font-mono text-[13px]">python -m src.jobs.worker</code>
              <div className="mt-4">
                <Button size="sm" onClick={() => void status.refetch()}>Check again</Button>
              </div>
            </div>
          )}
          {status.error && !workerDown && <ErrorState error={status.error} onRetry={() => void status.refetch()} />}
          {status.data && (
            <>
              <OllamaStep number={1} status={status.data} onRecheck={() => void status.refetch()} rechecking={status.isFetching} />
              <GoogleStep number={2} status={status.data} />
              <MailboxStep number={3} status={status.data} />
              <CalendarStep number={4} status={status.data} />
            </>
          )}
        </div>

        <div className="mt-8 flex items-center justify-between">
          <p className="text-[13px] text-muted">
            {status.data?.complete ? "All set — your first mail check has been queued." : status.data?.missing ? `Still needed: ${status.data.missing}.` : ""}
          </p>
          <Link to="/">
            <Button variant={status.data?.complete ? "primary" : "secondary"}>
              {status.data?.complete ? "Open the dashboard" : "Skip for now"} <ArrowRight className="size-4" />
            </Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
