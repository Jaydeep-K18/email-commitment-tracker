/** Loading, empty and error states — every list and page uses these three. */
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

import { errorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { Button } from "./button";

export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return <div className={cn("skeleton rounded-md", className)} style={style} aria-hidden />;
}

export function SkeletonRows({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2.5", className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-8 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5" />
            <Skeleton className={cn("h-3", i % 2 ? "w-2/3" : "w-1/2")} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-14 text-center", className)}>
      {icon && (
        <div className="mb-3 grid size-11 place-items-center rounded-xl border border-border bg-surface-2 text-muted [&>svg]:size-5">
          {icon}
        </div>
      )}
      <p className="text-sm font-semibold text-text">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <div className="mb-3 grid size-11 place-items-center rounded-xl bg-danger-soft text-danger">
        <AlertTriangle className="size-5" />
      </div>
      <p className="text-sm font-semibold text-text">Couldn't load this</p>
      <p className="mt-1 max-w-sm text-[13px] text-muted">{errorMessage(error)}</p>
      {onRetry && (
        <Button className="mt-4" size="sm" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Try again
        </Button>
      )}
    </div>
  );
}

export function FullPageSpinner() {
  return (
    <div className="grid min-h-dvh place-items-center" role="status" aria-label="Loading">
      <Loader2 className="size-6 animate-spin text-faint" />
    </div>
  );
}
