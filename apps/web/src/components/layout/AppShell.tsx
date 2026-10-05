import * as DialogPrimitive from "@radix-ui/react-dialog";
import { motion } from "motion/react";
import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { cn } from "../../lib/cn";
import { DotBackground } from "../domain";
import { Brand, CollapseButton, SidebarNav } from "./Sidebar";
import { Topbar } from "./Topbar";

const COLLAPSE_KEY = "commitmail.sidebarCollapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

export function AppShell() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  const toggle = () => {
    setCollapsed((value) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, value ? "0" : "1");
      } catch {
        /* not persisted; still toggles */
      }
      return !value;
    });
  };

  return (
    <div className="relative min-h-dvh">
      <DotBackground />
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 hidden flex-col border-r border-border bg-[color-mix(in_oklch,var(--canvas)_88%,transparent)] backdrop-blur-md transition-[width] duration-200 md:flex",
          collapsed ? "w-[68px]" : "w-60",
        )}
      >
        <div className={cn("flex h-14 items-center border-b border-border", collapsed ? "justify-center px-2" : "px-4")}>
          <Brand collapsed={collapsed} />
        </div>
        <div className={cn("flex-1 overflow-y-auto py-4", collapsed ? "px-2" : "px-3")}>
          <SidebarNav collapsed={collapsed} />
        </div>
        <div className="border-t border-border p-2">
          <CollapseButton collapsed={collapsed} onToggle={toggle} />
        </div>
      </aside>

      {/* Mobile drawer */}
      <DialogPrimitive.Root open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 md:hidden" />
          <DialogPrimitive.Content className="fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r border-border bg-surface p-4 shadow-pop md:hidden">
            <DialogPrimitive.Title className="sr-only">Navigation</DialogPrimitive.Title>
            <Brand />
            <div className="mt-6 overflow-y-auto">
              <SidebarNav onNavigate={() => setMobileOpen(false)} />
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      <div className={cn("relative z-10 flex min-h-dvh flex-col transition-[padding] duration-200", collapsed ? "md:pl-[68px]" : "md:pl-60")}>
        <Topbar onOpenMenu={() => setMobileOpen(true)} />
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-8 md:py-8">
          {/* Enter-only: an exit animation around <Outlet> would re-render the
              outgoing copy with the new route and flash it. */}
          <motion.div
            key={location.pathname.split("/")[1]}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            <Outlet />
          </motion.div>
        </main>
      </div>
    </div>
  );
}
