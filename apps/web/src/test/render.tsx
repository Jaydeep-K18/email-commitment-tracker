import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";

import { App } from "../App";
import { TooltipProvider } from "../components/ui/overlay";
import { setCsrfToken } from "../lib/api";

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
}

/** The whole app — routes, guards, shell — starting at `path`. */
export function renderApp(path = "/") {
  setCsrfToken(null);
  const user = userEvent.setup();
  const result = render(
    <QueryClientProvider client={client()}>
      <MemoryRouter initialEntries={[path]}>
        <TooltipProvider>
          <App />
          <Toaster />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user, ...result };
}

/** One component with the providers it needs. */
export function renderWithProviders(ui: ReactElement) {
  const user = userEvent.setup();
  const result = render(
    <QueryClientProvider client={client()}>
      <MemoryRouter>
        <TooltipProvider>{ui}</TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user, ...result };
}
