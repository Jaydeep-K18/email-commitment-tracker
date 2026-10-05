/**
 * Server state, through TanStack Query: every read is cached, deduplicated and
 * refetched in the background, and every write invalidates exactly what it
 * changed. Query keys are built in one place so invalidation can't miss one.
 */
import type {
  ActivityEvent,
  Analytics,
  AnalyticsQuery,
  Commitment,
  CommitmentQuery,
  Contact,
  EmailBulk,
  EmailDetail,
  EmailPatch,
  InboxPage,
  InboxQuery,
  Job,
  JobStats,
  MetricWindow,
  Notification,
  Page,
  RelationshipPerson,
  SavedView,
  SessionInfo,
  Settings,
  SettingsSection,
  SetupStatus,
  SystemHealth,
  Tag,
} from "@commitmail/shared";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, setCsrfToken } from "./api";

export const keys = {
  session: ["session"] as const,
  emails: (query?: Partial<InboxQuery>) => (query ? (["emails", query] as const) : (["emails"] as const)),
  email: (id: number) => ["email", id] as const,
  tags: ["tags"] as const,
  views: ["views"] as const,
  commitments: (query?: Partial<CommitmentQuery>) => (query ? (["commitments", query] as const) : (["commitments"] as const)),
  calendar: (from?: string, to?: string) => (from ? (["calendar", from, to] as const) : (["calendar"] as const)),
  relationships: ["relationships"] as const,
  contacts: ["contacts"] as const,
  activity: (filters?: object) => (filters ? (["activity", filters] as const) : (["activity"] as const)),
  jobs: (filters?: object) => (filters ? (["jobs", filters] as const) : (["jobs"] as const)),
  job: (id: number) => ["job", id] as const,
  jobStats: ["jobStats"] as const,
  notifications: ["notifications"] as const,
  settings: ["settings"] as const,
  analytics: (query?: Partial<AnalyticsQuery>) => (query ? (["analytics", query] as const) : (["analytics"] as const)),
  health: ["health"] as const,
  metrics: (minutes: number) => ["metrics", minutes] as const,
  systemEvents: ["systemEvents"] as const,
  setup: ["setup"] as const,
};

// --- Session --------------------------------------------------------------------

export function useSession() {
  return useQuery({
    queryKey: keys.session,
    queryFn: async () => {
      const session = await api.get<SessionInfo>("/auth/session");
      setCsrfToken(session.csrfToken);
      return session;
    },
    staleTime: 60_000,
    retry: false,
  });
}

function useSessionMutation<TBody>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: TBody) => api.post<SessionInfo>(path, body),
    onSuccess: (session) => {
      setCsrfToken(session.csrfToken);
      client.setQueryData(keys.session, session);
    },
  });
}

export const useLogin = () => useSessionMutation<{ email: string; password: string }>("/auth/login");
export const useCreateOwner = () =>
  useSessionMutation<{ email: string; displayName: string; password: string }>("/auth/setup");

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/auth/logout"),
    onSettled: () => {
      setCsrfToken(null);
      client.clear();
      client.setQueryData(keys.session, { authenticated: false, setupRequired: false, user: null, csrfToken: null });
    },
  });
}

// --- Inbox ------------------------------------------------------------------------

export function useEmails(query: Partial<InboxQuery>) {
  return useQuery({
    queryKey: keys.emails(query),
    queryFn: () => api.get<InboxPage>("/emails", query as Record<string, never>),
    placeholderData: keepPreviousData,
  });
}

export function useEmail(id: number | null) {
  return useQuery({
    queryKey: keys.email(id ?? 0),
    queryFn: () => api.get<EmailDetail>(`/emails/${id}`),
    enabled: id != null,
  });
}

export function useUpdateEmail() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: EmailPatch }) => api.patch<EmailDetail>(`/emails/${id}`, patch),
    onSuccess: (email) => {
      client.setQueryData(keys.email(email.id), email);
      void client.invalidateQueries({ queryKey: keys.emails() });
    },
  });
}

export function useBulkEmails() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EmailBulk) => api.post<{ updated: number }>("/emails/bulk", body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.emails() });
      void client.invalidateQueries({ queryKey: ["email"] });
      void client.invalidateQueries({ queryKey: keys.tags });
    },
  });
}

export function useTags() {
  return useQuery({ queryKey: keys.tags, queryFn: () => api.get<Tag[]>("/tags") });
}

export function useCreateTag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; color: string }) => api.post<Tag>("/tags", body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.tags }),
  });
}

export function useDeleteTag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/tags/${id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.tags });
      void client.invalidateQueries({ queryKey: keys.emails() });
    },
  });
}

export function useViews() {
  return useQuery({ queryKey: keys.views, queryFn: () => api.get<SavedView[]>("/views") });
}

export function useSaveView() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Omit<SavedView, "id">) => api.post<SavedView>("/views", body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.views }),
  });
}

export function useDeleteView() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/views/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.views }),
  });
}

// --- Commitments and calendar ---------------------------------------------------------

export function useCommitments(query: Partial<CommitmentQuery>) {
  return useQuery({
    queryKey: keys.commitments(query),
    queryFn: () => api.get<Page<Commitment>>("/commitments", query as Record<string, never>),
    placeholderData: keepPreviousData,
  });
}

function invalidateCommitments(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: keys.commitments() });
  void client.invalidateQueries({ queryKey: keys.calendar() });
  void client.invalidateQueries({ queryKey: ["email"] });
  void client.invalidateQueries({ queryKey: keys.relationships });
}

export function useUpdateCommitment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { status?: string; approved?: boolean } }) =>
      api.patch<Commitment>(`/commitments/${id}`, patch),
    onSuccess: () => invalidateCommitments(client),
  });
}

export function useBulkCommitments() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { ids: number[]; action: string }) => api.post<{ updated: number }>("/commitments/bulk", body),
    onSuccess: () => invalidateCommitments(client),
  });
}

export function useCalendar(from: string, to: string) {
  return useQuery({
    queryKey: keys.calendar(from, to),
    queryFn: () => api.get<{ items: Commitment[] }>("/calendar", { from, to }),
    placeholderData: keepPreviousData,
  });
}

export function useRelationships() {
  return useQuery({
    queryKey: keys.relationships,
    queryFn: () => api.get<{ people: RelationshipPerson[] }>("/relationships"),
  });
}

// --- Contacts ------------------------------------------------------------------------

export function useContacts() {
  return useQuery({ queryKey: keys.contacts, queryFn: () => api.get<Contact[]>("/contacts") });
}

export function useSaveContact() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: Record<string, unknown> }) =>
      id ? api.patch<Contact>(`/contacts/${id}`, body) : api.post<Contact>("/contacts", body),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.contacts }),
  });
}

export function useDeleteContact() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/contacts/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.contacts }),
  });
}

// --- Activity, jobs, notifications ------------------------------------------------------

export function useActivity(filters: { type?: string[]; severity?: string[]; correlation?: string } = {}) {
  return useInfiniteQuery({
    queryKey: keys.activity(filters),
    queryFn: ({ pageParam }) =>
      api.get<{ items: ActivityEvent[]; nextBefore: number | null }>("/activity", { ...filters, before: pageParam, limit: 40 }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.nextBefore ?? undefined,
  });
}

export function useJobs(filters: { status?: string[]; type?: string[]; page?: number }) {
  return useQuery({
    queryKey: keys.jobs(filters),
    queryFn: () => api.get<Page<Job>>("/jobs", { ...filters, pageSize: 25 }),
    placeholderData: keepPreviousData,
  });
}

export function useJob(id: number | null) {
  return useQuery({ queryKey: keys.job(id ?? 0), queryFn: () => api.get<Job>(`/jobs/${id}`), enabled: id != null });
}

export function useJobStats() {
  return useQuery({ queryKey: keys.jobStats, queryFn: () => api.get<JobStats>("/jobs/stats"), refetchInterval: 15_000 });
}

export function useRetryJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<Job>(`/jobs/${id}/retry`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.jobs() });
      void client.invalidateQueries({ queryKey: ["job"] });
      void client.invalidateQueries({ queryKey: keys.jobStats });
    },
  });
}

export function useRetryAllFailed() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ retried: number }>("/jobs/retry-failed"),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.jobs() });
      void client.invalidateQueries({ queryKey: keys.jobStats });
    },
  });
}

export function useSyncNow() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ fetch: { created: boolean } }>("/sync"),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.jobs() }),
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: keys.notifications,
    queryFn: () => api.get<{ items: Notification[]; unread: number }>("/notifications", { limit: 30 }),
    refetchInterval: 60_000,
  });
}

export function useMarkNotificationsRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id?: number) => (id ? api.post<void>(`/notifications/${id}/read`) : api.post<void>("/notifications/read-all")),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.notifications }),
  });
}

// --- Settings, analytics, system, setup -----------------------------------------------

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get<Settings>("/settings"), staleTime: 5 * 60_000 });
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: <S extends SettingsSection>({ section, value }: { section: S; value: Settings[S] }) =>
      api.put<Settings>(`/settings/${section}`, value),
    onSuccess: (settings) => client.setQueryData(keys.settings, settings),
  });
}

export function useAnalytics(query: Partial<AnalyticsQuery>) {
  return useQuery({
    queryKey: keys.analytics(query),
    queryFn: () => api.get<Analytics>("/analytics", query as Record<string, never>),
    placeholderData: keepPreviousData,
  });
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: () => api.get<SystemHealth>("/system/health"), refetchInterval: 20_000 });
}

export function useMetrics(minutes = 60) {
  return useQuery({
    queryKey: keys.metrics(minutes),
    queryFn: () => api.get<{ source: "flink" | "fallback"; windows: MetricWindow[] }>("/system/metrics", { minutes }),
    refetchInterval: 30_000,
  });
}

export function useSystemEvents() {
  return useQuery({ queryKey: keys.systemEvents, queryFn: () => api.get<{ items: ActivityEvent[] }>("/system/events") });
}

export function useSetupStatus(enabled = true) {
  return useQuery({
    queryKey: keys.setup,
    queryFn: () => api.get<SetupStatus>("/setup/status"),
    enabled,
    retry: false,
  });
}
