/** Small components that know about CommitMail's own concepts. */
import { CATEGORY_LABELS, type Category, type Severity } from "@commitmail/shared";
import { animate, useInView, useReducedMotion } from "motion/react";
import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "../lib/cn";
import { initials } from "../lib/format";
import { Badge, type BadgeTone } from "./ui/badge";
import { Card } from "./ui/card";

export const CATEGORY_STYLE: Record<Category, { dot: string; text: string; soft: string }> = {
  action_required: { dot: "bg-cat-action", text: "text-cat-action", soft: "bg-[color-mix(in_oklch,var(--cat-action)_12%,transparent)]" },
  meeting: { dot: "bg-cat-meeting", text: "text-cat-meeting", soft: "bg-[color-mix(in_oklch,var(--cat-meeting)_12%,transparent)]" },
  important: { dot: "bg-cat-important", text: "text-[color-mix(in_oklch,var(--cat-important)_75%,var(--text))]", soft: "bg-[color-mix(in_oklch,var(--cat-important)_16%,transparent)]" },
  update: { dot: "bg-cat-update", text: "text-cat-update", soft: "bg-[color-mix(in_oklch,var(--cat-update)_12%,transparent)]" },
  low_priority: { dot: "bg-cat-low", text: "text-muted", soft: "bg-surface-3" },
};

/** The same colours as CSS variables, for charts (which need real values). */
export const CATEGORY_CHART_COLOR: Record<Category, string> = {
  action_required: "var(--cat-action)",
  meeting: "var(--cat-meeting)",
  important: "var(--cat-important)",
  update: "var(--cat-update)",
  low_priority: "var(--cat-low)",
};

export function CategoryBadge({ category, className }: { category: Category | null; className?: string }) {
  if (!category) return null;
  const style = CATEGORY_STYLE[category];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-medium whitespace-nowrap", style.soft, style.text, className)}>
      <span className={cn("size-1.5 rounded-full", style.dot)} aria-hidden />
      {CATEGORY_LABELS[category]}
    </span>
  );
}

const TIER_TONE: Record<string, BadgeTone> = { CRITICAL: "danger", IMPORTANT: "warning", MONITOR: "info", SKIP: "neutral" };

export function TierBadge({ tier }: { tier: string | null | undefined }) {
  if (!tier) return null;
  return <Badge tone={TIER_TONE[tier] ?? "neutral"}>{tier.charAt(0) + tier.slice(1).toLowerCase()}</Badge>;
}

export const SEVERITY_TONE: Record<Severity, BadgeTone> = { info: "info", success: "success", warning: "warning", error: "danger" };

const AVATAR_HUES = [277, 22, 155, 235, 300, 75, 190, 340];

/** Initials on a colour derived from the name, so a person keeps their colour. */
export function Avatar({ name, email, size = 32 }: { name?: string | null; email?: string | null; size?: number }) {
  const seed = (email || name || "?").split("").reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const hue = AVATAR_HUES[seed % AVATAR_HUES.length];
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-full font-semibold"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.38,
        background: `oklch(0.9 0.05 ${hue} / 0.9)`,
        color: `oklch(0.4 0.12 ${hue})`,
      }}
    >
      {initials(name, email)}
    </span>
  );
}

/** Counts up to the value once it scrolls into view; instant with reduced motion. */
export function CountUp({ value, format = (n: number) => Math.round(n).toLocaleString() }: { value: number; format?: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);
  useEffect(() => {
    if (reduced || !inView) {
      if (reduced) setShown(value);
      return;
    }
    const controls = animate(0, value, { duration: 0.9, ease: [0.16, 1, 0.3, 1], onUpdate: setShown });
    return () => controls.stop();
  }, [value, inView, reduced]);
  return <span ref={ref} className="tabular-nums">{format(shown)}</span>;
}

export function StatCard({
  label,
  value,
  format,
  icon: Icon,
  hint,
  change,
  tone = "accent",
  loading,
}: {
  label: string;
  value: number | null | undefined;
  format?: (n: number) => string;
  icon: LucideIcon;
  hint?: ReactNode;
  /** Percentage change versus the previous period. */
  change?: number | null;
  tone?: "accent" | "success" | "warning" | "danger" | "info";
  loading?: boolean;
}) {
  const toneClass = {
    accent: "bg-accent-soft text-accent-text",
    success: "bg-success-soft text-success",
    warning: "bg-warning-soft text-[color-mix(in_oklch,var(--warning)_70%,var(--text))]",
    danger: "bg-danger-soft text-danger",
    info: "bg-info-soft text-info",
  }[tone];
  return (
    <Card className="group overflow-hidden p-4 transition-shadow hover:shadow-pop">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-muted">{label}</span>
        <span className={cn("grid size-8 place-items-center rounded-lg transition-transform group-hover:scale-105", toneClass)}>
          <Icon className="size-4" />
        </span>
      </div>
      <div className="mt-2 text-[26px] leading-none font-semibold tracking-tight text-text">
        {loading || value == null ? <span className="text-faint">—</span> : <CountUp value={value} format={format} />}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[12.5px] text-muted">
        {change != null && (
          <span className={cn("inline-flex items-center gap-0.5 font-medium", change >= 0 ? "text-success" : "text-danger")}>
            {change >= 0 ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
            {Math.abs(change).toFixed(change % 1 === 0 ? 0 : 1)}%
          </span>
        )}
        {hint}
      </div>
    </Card>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold tracking-tight text-text">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A coloured dot for health states and live indicators. */
export function StatusDot({ state, pulse }: { state: "ok" | "degraded" | "down" | "unconfigured"; pulse?: boolean }) {
  const color = { ok: "bg-success", degraded: "bg-warning", down: "bg-danger", unconfigured: "bg-faint" }[state];
  return <span className={cn("inline-block size-2 rounded-full", color, pulse && state === "ok" && "animate-pulse-dot")} aria-hidden />;
}

/** The background: a dot grid that brightens around the pointer. */
export function DotBackground() {
  const glow = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    if (reduced) return;
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        glow.current?.style.setProperty("--mx", `${event.clientX}px`);
        glow.current?.style.setProperty("--my", `${event.clientY}px`);
      });
    };
    const onLeave = () => glow.current?.style.setProperty("--mx", "-999px");
    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      cancelAnimationFrame(frame);
    };
  }, [reduced]);
  return (
    <>
      <div className="dot-field" aria-hidden />
      <div ref={glow} className="dot-field-glow" aria-hidden />
      <div className="dot-field-fade" aria-hidden />
    </>
  );
}
