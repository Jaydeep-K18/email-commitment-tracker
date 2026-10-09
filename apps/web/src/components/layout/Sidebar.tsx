import {
  Activity,
  BarChart3,
  CalendarDays,
  ChevronsLeft,
  ChevronsRight,
  HeartPulse,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Network,
  Settings,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { NavLink } from "react-router-dom";

import { cn } from "../../lib/cn";
import { useCommitments, useEmails, useJobStats, useSession } from "../../lib/queries";
import { Tooltip } from "../ui/overlay";

interface Item {
  to: string;
  label: string;
  icon: LucideIcon;
  badge?: number;
  badgeTone?: "accent" | "danger";
}

export function useNavItems(): Array<{ section: string; items: Item[] }> {
  // Small, cached queries; the WebSocket keeps them fresh.
  const inbox = useEmails({ folder: "inbox", pageSize: 1 });
  const review = useCommitments({ view: "review", pageSize: 1 });
  const jobs = useJobStats();
  const isAdmin = useSession().data?.user?.isAdmin ?? false;

  return [
    {
      section: "Work",
      items: [
        { to: "/", label: "Overview", icon: LayoutDashboard },
        { to: "/inbox", label: "Inbox", icon: Inbox, badge: inbox.data?.counts.unread },
        { to: "/commitments", label: "Commitments", icon: ListChecks, badge: review.data?.total },
        { to: "/calendar", label: "Calendar", icon: CalendarDays },
        { to: "/relationships", label: "Relationships", icon: Network },
        { to: "/contacts", label: "Contacts", icon: Users },
      ],
    },
    {
      section: "Insight",
      items: [
        { to: "/analytics", label: "Analytics", icon: BarChart3 },
        { to: "/activity", label: "Activity", icon: Activity },
      ],
    },
    {
      section: "System",
      items: [
        { to: "/jobs", label: "Jobs", icon: Workflow, badge: jobs.data?.byStatus.failed, badgeTone: "danger" },
        ...(isAdmin ? [{ to: "/system", label: "System health", icon: HeartPulse }] : []),
        { to: "/settings", label: "Settings", icon: Settings },
      ],
    },
  ];
}

export function SidebarNav({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const sections = useNavItems();
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {sections.map((section) => (
        <div key={section.section}>
          {!collapsed && (
            <p className="mb-1.5 px-3 text-[11px] font-semibold tracking-wider text-faint uppercase">{section.section}</p>
          )}
          <ul className="space-y-0.5">
            {section.items.map((item) => {
              const link = (
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      "group relative flex h-9 items-center gap-3 rounded-lg px-3 text-[13.5px] font-medium transition-colors",
                      collapsed && "justify-center px-0",
                      isActive
                        ? "bg-surface text-text shadow-card ring-1 ring-border"
                        : "text-muted hover:bg-surface-2 hover:text-text",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive && <span className="absolute top-2 bottom-2 left-0 w-0.5 rounded-full bg-accent" aria-hidden />}
                      <item.icon className={cn("size-[18px] shrink-0", isActive ? "text-accent" : "text-faint group-hover:text-muted")} />
                      {!collapsed && <span className="truncate">{item.label}</span>}
                      {!!item.badge && (
                        <span
                          className={cn(
                            "ml-auto rounded-full px-1.5 text-[11px] leading-[18px] font-semibold tabular-nums",
                            item.badgeTone === "danger" ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent-text",
                            collapsed && "absolute -top-1 -right-1 ml-0 min-w-[18px] text-center",
                          )}
                        >
                          {item.badge > 99 ? "99+" : item.badge}
                        </span>
                      )}
                    </>
                  )}
                </NavLink>
              );
              return (
                <li key={item.to}>
                  {collapsed ? (
                    <Tooltip content={item.label} side="right">
                      {link}
                    </Tooltip>
                  ) : (
                    link
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function Brand({ collapsed }: { collapsed?: boolean }) {
  return (
    <div className={cn("flex items-center gap-2.5", collapsed && "justify-center")}>
      <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="size-7" />
      {!collapsed && (
        <div className="leading-tight">
          <p className="text-[15px] font-semibold tracking-tight text-text">CommitMail</p>
          <p className="text-[11px] text-faint">Promises, kept</p>
        </div>
      )}
    </div>
  );
}

export function CollapseButton({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      className="flex h-8 w-full items-center justify-center gap-2 rounded-lg text-[12.5px] text-faint transition-colors hover:bg-surface-2 hover:text-text"
    >
      {collapsed ? <ChevronsRight className="size-4" /> : <><ChevronsLeft className="size-4" /> Collapse</>}
    </button>
  );
}
