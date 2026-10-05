import { Loader2 } from "lucide-react";
import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";
import { FullPageSpinner } from "./components/ui/states";
import { useSession, useSettings } from "./lib/queries";
import { LiveProvider } from "./lib/realtime";
import { useThemeSync } from "./lib/theme";
import { Login } from "./pages/Login";
import { Setup } from "./pages/Setup";

// Pages load on first visit, so the sign-in screen does not ship the charting
// and graph libraries the dashboard needs.
const Overview = lazy(() => import("./pages/Overview"));
const InboxPage = lazy(() => import("./pages/Inbox"));
const Commitments = lazy(() => import("./pages/Commitments"));
const CalendarPage = lazy(() => import("./pages/Calendar"));
const Relationships = lazy(() => import("./pages/Relationships"));
const Contacts = lazy(() => import("./pages/Contacts"));
const Analytics = lazy(() => import("./pages/Analytics"));
const ActivityPage = lazy(() => import("./pages/Activity"));
const Jobs = lazy(() => import("./pages/Jobs"));
const SystemPage = lazy(() => import("./pages/System"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const Onboarding = lazy(() => import("./pages/Onboarding"));
const NotFound = lazy(() => import("./pages/NotFound"));

function PageFallback() {
  return (
    <div className="grid min-h-[40vh] place-items-center" role="status" aria-label="Loading">
      <Loader2 className="size-5 animate-spin text-faint" />
    </div>
  );
}

/** Settings-driven theme, once signed in (the settings API needs a session). */
function ThemeFromSettings({ children }: { children: ReactNode }) {
  const settings = useSettings();
  useThemeSync(settings.data?.appearance.theme, settings.data?.appearance.reducedMotion);
  return <>{children}</>;
}

/** Protected routes: set up first, then signed in, then the app. */
function RequireOwner() {
  const session = useSession();
  const location = useLocation();
  if (session.isPending) return <FullPageSpinner />;
  if (session.data?.setupRequired) return <Navigate to="/setup" replace />;
  if (!session.data?.authenticated) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return (
    <ThemeFromSettings>
      <LiveProvider enabled>
        <Outlet />
      </LiveProvider>
    </ThemeFromSettings>
  );
}

export function App() {
  return (
    <Suspense fallback={<FullPageSpinner />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/setup" element={<Setup />} />
        <Route element={<RequireOwner />}>
          <Route path="/onboarding" element={<Suspense fallback={<FullPageSpinner />}><Onboarding /></Suspense>} />
          <Route element={<AppShell />}>
            <Route
              element={
                <Suspense fallback={<PageFallback />}>
                  <Outlet />
                </Suspense>
              }
            >
              <Route index element={<Overview />} />
              <Route path="inbox" element={<InboxPage />} />
              <Route path="inbox/:id" element={<InboxPage />} />
              <Route path="commitments" element={<Commitments />} />
              <Route path="calendar" element={<CalendarPage />} />
              <Route path="relationships" element={<Relationships />} />
              <Route path="contacts" element={<Contacts />} />
              <Route path="analytics" element={<Analytics />} />
              <Route path="activity" element={<ActivityPage />} />
              <Route path="jobs" element={<Jobs />} />
              <Route path="jobs/:id" element={<Jobs />} />
              <Route path="system" element={<SystemPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Route>
        </Route>
      </Routes>
    </Suspense>
  );
}
