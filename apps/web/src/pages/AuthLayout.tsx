import { motion } from "motion/react";
import { CalendarCheck2, Lock, Sparkles } from "lucide-react";
import type { ReactNode } from "react";

import { DotBackground } from "../components/domain";

const POINTS = [
  { icon: Sparkles, title: "Finds the promises in your mail", body: "“Can you send the report by Friday?” becomes a dated commitment." },
  { icon: CalendarCheck2, title: "Puts them on your calendar", body: "Google Calendar, Outlook, Apple Calendar — whichever you use." },
  { icon: Lock, title: "Your email never leaves this machine", body: "The AI runs locally. Nothing is sent to an AI service." },
];

/** The frame around sign-in and first-run setup. */
export function AuthLayout({ title, subtitle, children }: { title: string; subtitle: ReactNode; children: ReactNode }) {
  return (
    <div className="relative grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <DotBackground />
      <aside className="relative z-10 hidden flex-col justify-between border-r border-border bg-[color-mix(in_oklch,var(--accent)_6%,var(--canvas))] p-12 lg:flex">
        <div className="flex items-center gap-2.5">
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="size-8" />
          <span className="text-lg font-semibold tracking-tight">CommitMail</span>
        </div>
        <div>
          <h2 className="max-w-md text-[34px] leading-[1.15] font-semibold tracking-tight text-text">
            Every promise in your inbox, <span className="text-accent">on your calendar</span>.
          </h2>
          <ul className="mt-10 space-y-6">
            {POINTS.map((point, index) => (
              <motion.li
                key={point.title}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.1 + index * 0.08, duration: 0.35 }}
                className="flex gap-4"
              >
                <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-surface text-accent shadow-card">
                  <point.icon className="size-5" />
                </span>
                <span>
                  <span className="block text-[15px] font-medium text-text">{point.title}</span>
                  <span className="mt-0.5 block text-sm text-muted">{point.body}</span>
                </span>
              </motion.li>
            ))}
          </ul>
        </div>
        <p className="text-[12.5px] text-faint">Runs on your own machine. Single owner. No tracking.</p>
      </aside>
      <main className="relative z-10 flex items-center justify-center p-6">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
          className="w-full max-w-[400px]"
        >
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="size-8" />
            <span className="text-lg font-semibold">CommitMail</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-text">{title}</h1>
          <p className="mt-1.5 text-sm text-muted">{subtitle}</p>
          <div className="mt-8">{children}</div>
        </motion.div>
      </main>
    </div>
  );
}
