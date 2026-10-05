/**
 * The live connection to the server.
 *
 * One WebSocket for the whole app. Each event that arrives does two things:
 * it is added to the live feed, and it refreshes exactly the data it affects —
 * a new email refreshes the inbox, a finished job the job list — so screens
 * stay current without polling and without refetching everything.
 *
 * The socket reconnects on its own with exponential backoff and jitter, and a
 * close with code 4001 means the session ended, which signs the app out.
 */
import type { ActivityEvent, Notification, ServerMessage } from "@commitmail/shared";
import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { keys } from "./queries";

export type LiveStatus = "connecting" | "live" | "offline";

interface Live {
  status: LiveStatus;
  /** The newest live events, newest first. History comes from the API. */
  events: ActivityEvent[];
}

const LiveContext = createContext<Live>({ status: "offline", events: [] });
const FEED_LIMIT = 60;
export const CLOSE_SESSION_ENDED = 4001;

/** Which cached data an event makes stale. */
function affected(event: ActivityEvent): QueryKey[] {
  const [area] = event.type.split(".");
  switch (area) {
    case "email":
    case "extraction":
      return [keys.emails(), ["email"], keys.analytics()];
    case "commitment":
    case "calendar":
      return [keys.commitments(), keys.calendar(), ["email"], keys.relationships, keys.analytics()];
    case "job":
      return [keys.jobs(), ["job"], keys.jobStats];
    case "contact":
      return [keys.contacts];
    case "settings":
      return [keys.settings];
    default:
      return [];
  }
}

/** Batch invalidations: a burst of 30 events refreshes each query once. */
function invalidator(client: QueryClient) {
  const pending = new Map<string, QueryKey>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (queryKeys: QueryKey[]) => {
    for (const key of queryKeys) pending.set(JSON.stringify(key), key);
    timer ??= setTimeout(() => {
      for (const key of pending.values()) void client.invalidateQueries({ queryKey: key });
      pending.clear();
      timer = null;
    }, 400);
  };
}

export function socketUrl(location: Location = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}

export function LiveProvider({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const client = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const invalidate = useMemo(() => invalidator(client), [client]);
  const attempt = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const onNotification = (notification: Notification) => {
      void client.invalidateQueries({ queryKey: keys.notifications });
      const show = notification.severity === "error" ? toast.error : notification.severity === "success" ? toast.success : toast;
      show(notification.title, { description: notification.body ?? undefined });
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        new Notification(notification.title, { body: notification.body ?? undefined, tag: `commitmail-${notification.id}` });
      }
    };

    const connect = () => {
      setStatus("connecting");
      socket = new WebSocket(socketUrl());
      socket.onopen = () => {
        attempt.current = 0;
        setStatus("live");
        // Anything that happened while disconnected is in the database; refetch.
        invalidate([keys.emails(), keys.jobs(), keys.notifications, keys.activity()]);
      };
      socket.onmessage = (message) => {
        let data: ServerMessage;
        try {
          data = JSON.parse(String(message.data));
        } catch {
          return;
        }
        if (data.type === "event") {
          setEvents((current) => [data.event, ...current].slice(0, FEED_LIMIT));
          invalidate([...affected(data.event), keys.activity()]);
        } else if (data.type === "notification") {
          onNotification(data.notification);
        } else if (data.type === "metrics") {
          void client.invalidateQueries({ queryKey: ["metrics"] });
        }
      };
      socket.onclose = (event) => {
        setStatus("offline");
        if (stopped) return;
        if (event.code === CLOSE_SESSION_ENDED) {
          void client.invalidateQueries({ queryKey: keys.session });
          return;
        }
        // 1s, 2s, 4s ... capped at 30s, with jitter so many tabs don't reconnect in lockstep.
        const base = Math.min(30_000, 1_000 * 2 ** attempt.current++);
        retry = setTimeout(connect, base / 2 + Math.random() * (base / 2));
      };
    };

    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [enabled, client, invalidate]);

  const value = useMemo(() => ({ status, events }), [status, events]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): Live {
  return useContext(LiveContext);
}
