import {
  Bell,
  CheckCheck,
  LogOut,
  Menu,
  Monitor,
  Moon,
  RefreshCw,
  Search,
  Sun,
  User,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { errorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { relative } from "../../lib/format";
import {
  useLogout,
  useMarkNotificationsRead,
  useNotifications,
  useSaveSettings,
  useSession,
  useSettings,
  useSyncNow,
} from "../../lib/queries";
import { useLive } from "../../lib/realtime";
import { applyTheme, type ThemePreference } from "../../lib/theme";
import { Avatar, SEVERITY_TONE } from "../domain";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Kbd } from "../ui/controls";
import {
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownMenu,
  DropdownSeparator,
  DropdownTrigger,
  Popover,
  Tooltip,
} from "../ui/overlay";

export function Topbar({ onOpenMenu }: { onOpenMenu: () => void }) {
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-[color-mix(in_oklch,var(--canvas)_82%,transparent)] px-4 backdrop-blur-md md:px-6">
      <Button variant="ghost" size="icon" className="md:hidden" onClick={onOpenMenu} aria-label="Open menu">
        <Menu className="size-5" />
      </Button>
      <GlobalSearch />
      <div className="ml-auto flex items-center gap-1.5">
        <LiveIndicator />
        <SyncButton />
        <ThemeMenu />
        <NotificationBell />
        <UserMenu />
      </div>
    </header>
  );
}

/** Search the inbox from anywhere. "/" focuses it, as in most mail clients. */
function GlobalSearch() {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && !target.isContentEditable) {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <form
      role="search"
      className="relative hidden w-full max-w-sm sm:block"
      onSubmit={(event) => {
        event.preventDefault();
        navigate(`/inbox?q=${encodeURIComponent(value.trim())}`);
      }}
    >
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint" />
      <input
        ref={input}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Search mail…"
        aria-label="Search mail"
        className="h-9 w-full rounded-lg border border-border bg-surface pr-10 pl-9 text-sm text-text shadow-card placeholder:text-faint focus:border-accent focus:outline-none"
      />
      <span className="absolute top-1/2 right-2 -translate-y-1/2">
        <Kbd>/</Kbd>
      </span>
    </form>
  );
}

function LiveIndicator() {
  const { status } = useLive();
  const label = { live: "Live", connecting: "Connecting…", offline: "Offline" }[status];
  return (
    <Tooltip content={status === "live" ? "Updates arrive as they happen" : "Reconnecting to live updates"}>
      <span className="hidden items-center gap-2 rounded-full border border-border bg-surface px-2.5 py-1 text-[12px] font-medium text-muted lg:inline-flex" aria-live="polite">
        <span
          className={cn(
            "size-2 rounded-full",
            status === "live" ? "animate-pulse-dot bg-success" : status === "connecting" ? "bg-warning" : "bg-faint",
          )}
        />
        {label}
      </span>
    </Tooltip>
  );
}

function SyncButton() {
  const sync = useSyncNow();
  return (
    <Tooltip content="Check the mailbox and republish the calendar now">
      <Button
        size="sm"
        variant="secondary"
        loading={sync.isPending}
        onClick={() =>
          sync.mutate(undefined, {
            onSuccess: (result) => toast.success(result.fetch.created ? "Sync started" : "A sync is already running"),
            onError: (error) => toast.error(errorMessage(error)),
          })
        }
      >
        {!sync.isPending && <RefreshCw className="size-3.5" />}
        <span className="hidden sm:inline">Sync now</span>
      </Button>
    </Tooltip>
  );
}

function ThemeMenu() {
  const settings = useSettings();
  const save = useSaveSettings();
  const current = settings.data?.appearance.theme ?? "system";
  const choose = (theme: ThemePreference) => {
    applyTheme(theme);
    if (settings.data) save.mutate({ section: "appearance", value: { ...settings.data.appearance, theme } });
  };
  const Icon = current === "dark" ? Moon : current === "light" ? Sun : Monitor;
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          <Icon className="size-[18px]" />
        </Button>
      </DropdownTrigger>
      <DropdownContent>
        <DropdownLabel>Theme</DropdownLabel>
        <DropdownItem icon={<Sun />} onSelect={() => choose("light")}>Light</DropdownItem>
        <DropdownItem icon={<Moon />} onSelect={() => choose("dark")}>Dark</DropdownItem>
        <DropdownItem icon={<Monitor />} onSelect={() => choose("system")}>Match system</DropdownItem>
      </DropdownContent>
    </DropdownMenu>
  );
}

function NotificationBell() {
  const navigate = useNavigate();
  const notifications = useNotifications();
  const markRead = useMarkNotificationsRead();
  const [open, setOpen] = useState(false);
  const unread = notifications.data?.unread ?? 0;
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      className="w-[min(92vw,380px)] p-0"
      trigger={
        <Button variant="ghost" size="icon" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} className="relative">
          <Bell className="size-[18px]" />
          {unread > 0 && (
            <span className="absolute top-1.5 right-1.5 grid min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] leading-4 font-bold text-white tabular-nums">
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </Button>
      }
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <p className="text-sm font-semibold">Notifications</p>
        <Button size="sm" variant="ghost" disabled={!unread} onClick={() => markRead.mutate(undefined)}>
          <CheckCheck className="size-3.5" /> Mark all read
        </Button>
      </div>
      <ul className="max-h-[420px] overflow-y-auto py-1">
        {!notifications.data?.items.length && (
          <li className="px-4 py-10 text-center text-[13px] text-muted">You're all caught up.</li>
        )}
        {notifications.data?.items.map((n) => (
          <li key={n.id}>
            <button
              className={cn("flex w-full gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2", !n.readAt && "bg-accent-soft/40")}
              onClick={() => {
                if (!n.readAt) markRead.mutate(n.id);
                setOpen(false);
                if (n.link) navigate(n.link);
              }}
            >
              <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.readAt ? "bg-transparent" : "bg-accent")} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-medium text-text">{n.title}</span>
                  <Badge tone={SEVERITY_TONE[n.severity]} className="shrink-0">{n.severity === "error" ? "Failed" : n.severity === "success" ? "Resolved" : "New"}</Badge>
                </span>
                {n.body && <span className="mt-0.5 line-clamp-2 block text-[12.5px] text-muted">{n.body}</span>}
                <span className="mt-1 block text-[11.5px] text-faint">{relative(n.createdAt)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Popover>
  );
}

function UserMenu() {
  const session = useSession();
  const logout = useLogout();
  const navigate = useNavigate();
  const user = session.data?.user;
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <button className="ml-1 rounded-full ring-offset-2 ring-offset-canvas focus-visible:ring-2 focus-visible:ring-accent" aria-label="Account menu">
          <Avatar name={user?.displayName} email={user?.email} size={32} />
        </button>
      </DropdownTrigger>
      <DropdownContent>
        <div className="px-2.5 py-2">
          <p className="truncate text-sm font-medium">{user?.displayName}</p>
          <p className="truncate text-[12.5px] text-muted">{user?.email}</p>
        </div>
        <DropdownSeparator />
        <DropdownItem icon={<User />} onSelect={() => navigate("/settings?tab=profile")}>Profile & security</DropdownItem>
        <DropdownItem icon={<Bell />} onSelect={() => navigate("/settings?tab=notifications")}>Notification settings</DropdownItem>
        <DropdownSeparator />
        <DropdownItem icon={<LogOut />} onSelect={() => logout.mutate(undefined, { onSuccess: () => navigate("/login") })} danger>
          Sign out
        </DropdownItem>
      </DropdownContent>
    </DropdownMenu>
  );
}

