import "./index.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";

import { App } from "./App";
import { TooltipProvider } from "./components/ui/overlay";
import { createQueryClient } from "./lib/queryClient";

const DEMO = import.meta.env.MODE === "demo";

/** Says plainly that nothing on screen is real mail. */
function DemoBadge() {
  return (
    <div className="pointer-events-none fixed bottom-3 left-3 z-50 rounded-full border border-border bg-surface px-3 py-1 text-[12px] font-medium text-muted shadow-pop">
      Demo — sample data, nothing is sent anywhere
    </div>
  );
}

async function boot() {
  if (DEMO) {
    const { startDemo } = await import("./demo/start");
    await startDemo();
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={createQueryClient()}>
        <BrowserRouter>
          <MotionConfig reducedMotion="user">
            <TooltipProvider delayDuration={300}>
              <App />
              {DEMO && <DemoBadge />}
              <Toaster
                position="bottom-right"
                toastOptions={{
                  classNames: {
                    toast: "!bg-surface !border-border !text-text !shadow-pop !rounded-xl",
                    description: "!text-muted",
                  },
                }}
              />
            </TooltipProvider>
          </MotionConfig>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

void boot();
