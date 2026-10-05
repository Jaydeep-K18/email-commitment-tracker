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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <BrowserRouter>
        <MotionConfig reducedMotion="user">
          <TooltipProvider delayDuration={300}>
            <App />
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
