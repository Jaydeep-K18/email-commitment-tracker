/**
 * Demo mode (`npm run demo`): the whole app with no server, database or model.
 * A service worker answers the API from generated data, so the dashboard can
 * be explored — or shown — anywhere.
 */
import { setupWorker } from "msw/browser";

import { handlers } from "./handlers";

export async function startDemo() {
  const worker = setupWorker(...handlers);
  await worker.start({
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
    // Assets, fonts and the dev server's own requests go straight through.
    onUnhandledRequest: "bypass",
    quiet: true,
  });
}
