import {
  changePasswordSchema,
  passwordSchema,
  profilePatchSchema,
  RETENTION_OPTIONS,
  SETTINGS_SCHEMAS,
  TAG_COLORS,
  tagCreateSchema,
  type Settings,
  type SettingsSection,
} from "@commitmail/shared";
import { Download, Monitor, Moon, Plus, RefreshCw, Sun, Tag as TagIcon, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "../components/domain";
import { CalendarStep, ExtensionTokenCard, GoogleStep, MailboxStep, OllamaStep } from "../components/setup";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Segmented, Tabs } from "../components/ui/controls";
import { Field, Input, Select, Switch } from "../components/ui/form";
import { ConfirmDialog } from "../components/ui/overlay";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/states";
import { ApiError, errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { browserTimeZone } from "../lib/format";
import {
  useChangePassword,
  useCreateTag,
  useDeleteTag,
  useExportData,
  usePurge,
  useSaveSettings,
  useSession,
  useSettings,
  useSetupStatus,
  useSyncNow,
  useTags,
  useUpdateProfile,
  type PurgeScope,
  type SettingsUpdate,
} from "../lib/queries";

const TABS = [
  { value: "account", label: "Account" },
  { value: "notifications", label: "Notifications" },
  { value: "email", label: "Email" },
  { value: "calendar", label: "Calendar" },
  { value: "appearance", label: "Appearance" },
  { value: "integrations", label: "Integrations" },
  { value: "tags", label: "Tags" },
  { value: "privacy", label: "Privacy" },
] as const;

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.value === params.get("tab")) ? params.get("tab")! : "account";
  const content: Record<(typeof TABS)[number]["value"], ReactNode> = {
    account: <AccountTab />,
    notifications: <NotificationsTab />,
    email: <EmailTab />,
    calendar: <CalendarTab />,
    appearance: <AppearanceTab />,
    integrations: <IntegrationsTab />,
    tags: <TagsTab />,
    privacy: <PrivacyTab />,
  };
  return (
    <>
      <PageHeader title="Settings" description="Your account, how mail is checked, where events go, and what is kept." />
      <div className="max-w-3xl">
        <Tabs
          value={tab}
          onValueChange={(value) => setParams(value === "account" ? {} : { tab: value }, { replace: true })}
          tabs={TABS.map((t) => ({ value: t.value, label: t.label, content: <div className="space-y-6">{content[t.value]}</div> }))}
        />
      </div>
    </>
  );
}

// --- Settings sections ------------------------------------------------------------

/**
 * A section of settings being edited: the saved value, a local draft on top of
 * it, validation by the same schema the server uses, and save/discard.
 */
function useSection<S extends SettingsSection>(section: S) {
  const settings = useSettings();
  const save = useSaveSettings();
  const saved = settings.data?.[section] as Settings[S] | undefined;
  const [draft, setDraft] = useState<Settings[S] | null>(null);
  const value = draft ?? saved;
  const dirty = draft != null && JSON.stringify(draft) !== JSON.stringify(saved);

  const errors = useMemo(() => {
    if (!draft) return {} as Record<string, string>;
    const result = SETTINGS_SCHEMAS[section].safeParse(draft);
    if (result.success) return {} as Record<string, string>;
    return Object.fromEntries(result.error.issues.map((issue) => [issue.path.join(".") || "_", issue.message]));
  }, [draft, section]);

  const commit = (next: Settings[S] | null = draft, message = "Saved") => {
    if (!next) return;
    save.mutate({ section, value: next } as SettingsUpdate, {
      onSuccess: () => {
        setDraft(null);
        toast.success(message);
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  return {
    value,
    loading: settings.isPending,
    error: settings.error,
    retry: () => void settings.refetch(),
    dirty,
    valid: Object.keys(errors).length === 0,
    errors,
    saving: save.isPending,
    /** Edit the draft; saved with the Save button. */
    edit: (changes: Partial<Settings[S]>) => setDraft({ ...(value as Settings[S]), ...changes }),
    /** Change and save at once — for switches and choices, where a Save button is friction. */
    set: (changes: Partial<Settings[S]>, message?: string) => commit({ ...(value as Settings[S]), ...changes }, message),
    commit: () => commit(),
    discard: () => setDraft(null),
  };
}

function SectionState({ section, children }: { section: { loading: boolean; error: unknown; retry: () => void }; children: ReactNode }) {
  if (section.loading) return <Card className="p-5"><SkeletonRows rows={3} /></Card>;
  if (section.error) return <Card><ErrorState error={section.error} onRetry={section.retry} /></Card>;
  return <>{children}</>;
}

function SaveBar({ dirty, valid, saving, onSave, onDiscard }: { dirty: boolean; valid: boolean; saving: boolean; onSave: () => void; onDiscard: () => void }) {
  return (
    <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
      {dirty && <span className="mr-auto text-[12.5px] text-muted">Unsaved changes</span>}
      <Button size="sm" variant="ghost" disabled={!dirty || saving} onClick={onDiscard}>Discard</Button>
      <Button size="sm" variant="primary" disabled={!dirty || !valid} loading={saving} onClick={onSave}>Save changes</Button>
    </div>
  );
}

function NumberField({
  label,
  hint,
  value,
  onChange,
  error,
  min,
  max,
  unit,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (value: number) => void;
  error?: string;
  min: number;
  max: number;
  unit: string;
}) {
  return (
    <Field label={label} hint={hint ?? `${min}–${max} ${unit}`} error={error}>
      {(props) => (
        <div className="relative">
          <Input
            {...props}
            type="number"
            inputMode="numeric"
            min={min}
            max={max}
            value={Number.isNaN(value) ? "" : value}
            onChange={(e) => onChange(e.target.value === "" ? Number.NaN : Number(e.target.value))}
            className="pr-16"
          />
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-[12.5px] text-faint">{unit}</span>
        </div>
      )}
    </Field>
  );
}

const numberError = (message: string | undefined) => (message?.includes("nan") || message?.includes("NaN") ? "Enter a number" : message);

// --- Account ----------------------------------------------------------------------

function AccountTab() {
  return (
    <>
      <ProfileCard />
      <PasswordCard />
    </>
  );
}

function ProfileCard() {
  const session = useSession();
  const update = useUpdateProfile();
  const user = session.data?.user;
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const draft = { displayName: displayName ?? user?.displayName ?? "", email: email ?? user?.email ?? "" };
  const dirty = (displayName != null && displayName !== (user?.displayName ?? "")) || (email != null && email !== user?.email);
  const parsed = profilePatchSchema.safeParse(draft);
  const fieldError = (key: string) =>
    !parsed.success ? parsed.error.issues.find((issue) => issue.path[0] === key)?.message : undefined;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!parsed.success) return;
    update.mutate(parsed.data, {
      onSuccess: () => {
        setDisplayName(null);
        setEmail(null);
        toast.success("Profile saved");
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  return (
    <Card>
      <form onSubmit={submit}>
        <CardHeader title="Profile" description="Your name and the email you sign in with." />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" error={dirty ? fieldError("displayName") : undefined}>
            {(props) => <Input {...props} value={draft.displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="name" />}
          </Field>
          <Field label="Sign-in email" error={dirty ? fieldError("email") : undefined}>
            {(props) => <Input {...props} type="email" value={draft.email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />}
          </Field>
        </CardBody>
        <div className="flex justify-end border-t border-border px-5 py-3">
          <Button type="submit" size="sm" variant="primary" disabled={!dirty || !parsed.success} loading={update.isPending}>Save profile</Button>
        </div>
      </form>
    </Card>
  );
}

function PasswordCard() {
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [wrongCurrent, setWrongCurrent] = useState(false);
  const strength = next ? passwordSchema.safeParse(next) : null;
  const nextError = strength && !strength.success ? strength.error.issues[0]?.message : undefined;
  const mismatch = confirm.length > 0 && confirm !== next;
  const ready = changePasswordSchema.safeParse({ currentPassword: current, newPassword: next }).success && confirm === next;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    change.mutate(
      { currentPassword: current, newPassword: next },
      {
        onSuccess: (result) => {
          setCurrent("");
          setNext("");
          setConfirm("");
          toast.success(
            result.otherSessionsSignedOut
              ? `Password changed. ${result.otherSessionsSignedOut} other session${result.otherSessionsSignedOut === 1 ? " was" : "s were"} signed out.`
              : "Password changed",
          );
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === "wrong_password") setWrongCurrent(true);
          else toast.error(errorMessage(error));
        },
      },
    );
  };

  return (
    <Card>
      <form onSubmit={submit}>
        <CardHeader title="Password" description="Changing it signs out every other browser where you're signed in." />
        <CardBody className="space-y-4">
          <Field label="Current password" error={wrongCurrent ? "That isn't your current password." : undefined}>
            {(props) => (
              <Input {...props} type="password" value={current} autoComplete="current-password"
                onChange={(e) => { setCurrent(e.target.value); setWrongCurrent(false); }} />
            )}
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="New password" hint="At least 10 characters" error={nextError}>
              {(props) => <Input {...props} type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />}
            </Field>
            <Field label="Confirm new password" error={mismatch ? "The passwords don't match." : undefined}>
              {(props) => <Input {...props} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />}
            </Field>
          </div>
        </CardBody>
        <div className="flex justify-end border-t border-border px-5 py-3">
          <Button type="submit" size="sm" variant="primary" disabled={!ready} loading={change.isPending}>Change password</Button>
        </div>
      </form>
    </Card>
  );
}

// --- Notifications ------------------------------------------------------------------

const NOTIFICATION_OPTIONS: Array<{ key: Exclude<keyof Settings["notifications"], "browser">; label: string; description: string }> = [
  { key: "importantEmail", label: "Important email arrives", description: "Mail from a VIP contact, or classified as important or needing action." },
  { key: "jobFailed", label: "Background work fails for good", description: "After every retry has been used, so you can look into it." },
  { key: "retrySucceeded", label: "A retry succeeds", description: "When something that failed earlier goes through." },
  { key: "calendarFailed", label: "A calendar update fails", description: "When an event couldn't be added to or removed from your calendar." },
];

function NotificationsTab() {
  const section = useSection("notifications");
  const value = section.value;

  const toggleBrowser = async (on: boolean) => {
    if (on && "Notification" in window && Notification.permission !== "granted") {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        toast.error("Your browser blocked notifications for this site. Allow them in the site settings to turn this on.");
        return;
      }
    }
    section.set({ browser: on });
  };

  return (
    <SectionState section={section}>
      {value && (
        <>
          <Card>
            <CardHeader title="Tell me when" description="Shown in the bell at the top of every page, live." />
            <CardBody className="divide-y divide-border py-0">
              {NOTIFICATION_OPTIONS.map((option) => (
                <Switch
                  key={option.key}
                  label={option.label}
                  description={option.description}
                  checked={value[option.key]}
                  disabled={section.saving}
                  onCheckedChange={(on) => section.set({ [option.key]: on } as Partial<Settings["notifications"]>)}
                />
              ))}
            </CardBody>
          </Card>
          <Card>
            <CardBody className="py-0">
              <Switch
                label="Desktop notifications"
                description={"Notification" in window ? "Also pop them up as system notifications while the app is open." : "This browser doesn't support desktop notifications."}
                checked={value.browser}
                disabled={section.saving || !("Notification" in window)}
                onCheckedChange={(on) => void toggleBrowser(on)}
              />
            </CardBody>
          </Card>
        </>
      )}
    </SectionState>
  );
}

// --- Email --------------------------------------------------------------------------

function EmailTab() {
  const section = useSection("email");
  const setup = useSetupStatus();
  const sync = useSyncNow();
  const value = section.value;
  return (
    <>
      <SectionState section={section}>
        {value && (
          <Card>
            <CardHeader
              title="Checking for mail"
              description="The worker checks your inbox on this schedule. Mail is only ever read — never moved, marked or deleted."
              action={
                <Button
                  size="sm"
                  loading={sync.isPending}
                  onClick={() => sync.mutate(undefined, {
                    onSuccess: (r) => toast.success(r.fetch.created ? "Checking for mail now" : "A check is already running"),
                    onError: (error) => toast.error(errorMessage(error)),
                  })}
                >
                  <RefreshCw className="size-3.5" /> Check now
                </Button>
              }
            />
            <CardBody className="grid gap-4 sm:grid-cols-3">
              <NumberField label="Check every" unit="minutes" min={5} max={1440} value={value.fetchIntervalMinutes}
                error={numberError(section.errors.fetchIntervalMinutes)} onChange={(v) => section.edit({ fetchIntervalMinutes: v })} />
              <NumberField label="Look back" unit="days" min={1} max={90} value={value.lookbackDays}
                hint="How far back the first check reads" error={numberError(section.errors.lookbackDays)} onChange={(v) => section.edit({ lookbackDays: v })} />
              <NumberField label="At most" unit="emails" min={10} max={500} value={value.maxPerFetch}
                hint="Per check, newest first" error={numberError(section.errors.maxPerFetch)} onChange={(v) => section.edit({ maxPerFetch: v })} />
            </CardBody>
            <SaveBar dirty={section.dirty} valid={section.valid} saving={section.saving} onSave={section.commit} onDiscard={section.discard} />
          </Card>
        )}
      </SectionState>
      <SetupSection setup={setup}>{(status) => <MailboxStep status={status} />}</SetupSection>
    </>
  );
}

// --- Calendar -----------------------------------------------------------------------

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 0];
const weekdayName = (day: number, style: "short" | "long") =>
  new Intl.DateTimeFormat(undefined, { weekday: style }).format(new Date(2024, 0, 7 + day)); // 7 Jan 2024 was a Sunday

function CalendarTab() {
  const section = useSection("calendar");
  const setup = useSetupStatus();
  const value = section.value;
  const zones = useMemo(() => (typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : []), []);
  const hours = value?.workingHours;
  const editHours = (changes: Partial<Settings["calendar"]["workingHours"]>) => section.edit({ workingHours: { ...hours!, ...changes } });
  const toggleDay = (day: number) =>
    editHours({ days: hours!.days.includes(day) ? hours!.days.filter((d) => d !== day) : [...hours!.days, day].sort() });

  return (
    <>
      <SetupSection setup={setup}>{(status) => <CalendarStep status={status} />}</SetupSection>
      <SectionState section={section}>
        {value && hours && (
          <Card>
            <CardHeader title="Events" description="How commitments appear on your calendar." />
            <CardBody className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <NumberField label="Reminder" unit="min before" min={0} max={1440} value={value.reminderMinutes}
                  hint="0 for no reminder" error={numberError(section.errors.reminderMinutes)} onChange={(v) => section.edit({ reminderMinutes: v })} />
                <Field
                  label="Time zone"
                  error={section.errors.timeZone ? "Not a time zone this browser recognises" : undefined}
                  hint={value.timeZone !== browserTimeZone() ? (
                    <button type="button" className="text-accent-text hover:underline" onClick={() => section.edit({ timeZone: browserTimeZone() })}>
                      Use this device's: {browserTimeZone()}
                    </button>
                  ) : "Deadlines in emails are read in this zone"}
                >
                  {(props) => (
                    <>
                      <Input {...props} list="time-zones" value={value.timeZone} onChange={(e) => section.edit({ timeZone: e.target.value })} />
                      <datalist id="time-zones">{zones.map((zone) => <option key={zone} value={zone} />)}</datalist>
                    </>
                  )}
                </Field>
              </div>
              <fieldset>
                <legend className="text-[13px] font-medium text-text">Working hours</legend>
                <p className="mt-0.5 text-[12.5px] text-muted">Used to flag events outside your hours and to suggest free slots.</p>
                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <Field label="From" className="w-32">
                    {(props) => <Input {...props} type="time" value={hours.start} onChange={(e) => editHours({ start: e.target.value })} />}
                  </Field>
                  <Field label="To" className="w-32">
                    {(props) => <Input {...props} type="time" value={hours.end} onChange={(e) => editHours({ end: e.target.value })} />}
                  </Field>
                  <div role="group" aria-label="Working days" className="flex gap-1">
                    {WEEKDAYS.map((day) => {
                      const on = hours.days.includes(day);
                      return (
                        <button
                          key={day}
                          type="button"
                          aria-pressed={on}
                          aria-label={weekdayName(day, "long")}
                          onClick={() => toggleDay(day)}
                          className={cn(
                            "h-9 w-10 rounded-lg border text-[12.5px] font-medium transition-colors",
                            on ? "border-accent bg-accent-soft text-accent-text" : "border-border text-muted hover:bg-surface-2",
                          )}
                        >
                          {weekdayName(day, "short").slice(0, 2)}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {(section.errors.workingHours || section.errors["workingHours.days"] || section.errors["workingHours.start"] || section.errors["workingHours.end"]) && (
                  <p role="alert" className="mt-2 text-[12.5px] text-danger">
                    {section.errors["workingHours.days"] ? "Pick at least one working day." : "Working hours must end after they start."}
                  </p>
                )}
              </fieldset>
            </CardBody>
            <SaveBar dirty={section.dirty} valid={section.valid} saving={section.saving} onSave={section.commit} onDiscard={section.discard} />
          </Card>
        )}
      </SectionState>
    </>
  );
}

// --- Appearance ---------------------------------------------------------------------

const THEMES = [
  { value: "system", label: "System", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
] as const;

function AppearanceTab() {
  const section = useSection("appearance");
  const value = section.value;
  return (
    <SectionState section={section}>
      {value && (
        <Card>
          <CardHeader title="Appearance" description="Saved to your account, so it follows you to other browsers." />
          <CardBody className="space-y-6">
            <div>
              <p className="text-[13px] font-medium text-text">Theme</p>
              <div role="radiogroup" aria-label="Theme" className="mt-2 grid grid-cols-3 gap-3">
                {THEMES.map((theme) => {
                  const active = value.theme === theme.value;
                  return (
                    <button
                      key={theme.value}
                      role="radio"
                      aria-checked={active}
                      onClick={() => section.set({ theme: theme.value }, "Theme saved")}
                      className={cn(
                        "flex flex-col items-center gap-2 rounded-xl border p-4 text-[13px] font-medium transition-all",
                        active ? "border-accent bg-accent-soft/50 text-text ring-1 ring-accent" : "border-border text-muted hover:border-border-strong hover:bg-surface-2",
                      )}
                    >
                      <theme.icon className="size-5" />
                      {theme.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-[13px] font-medium text-text">Density</p>
                <p className="text-[12.5px] text-muted">Compact fits more rows on screen.</p>
              </div>
              <Segmented
                label="Density"
                value={value.density}
                onChange={(density) => section.set({ density }, "Density saved")}
                options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]}
              />
            </div>
            <div className="border-t border-border">
              <Switch
                label="Reduce motion"
                description="Turn off animations, whatever your system setting says."
                checked={value.reducedMotion}
                disabled={section.saving}
                onCheckedChange={(reducedMotion) => section.set({ reducedMotion })}
              />
            </div>
          </CardBody>
        </Card>
      )}
    </SectionState>
  );
}

// --- Integrations -------------------------------------------------------------------

/** The setup steps need the worker; say so plainly when it isn't running. */
function SetupSection({ setup, children }: { setup: ReturnType<typeof useSetupStatus>; children: (status: NonNullable<ReturnType<typeof useSetupStatus>["data"]>) => ReactNode }) {
  if (setup.isPending) return <Card className="p-5"><SkeletonRows rows={3} /></Card>;
  if (setup.error) {
    const workerDown = setup.error instanceof ApiError && setup.error.code === "worker_unavailable";
    return (
      <Card>
        {workerDown ? (
          <EmptyState
            title="The background worker isn't running"
            description={<>These settings are handled by the worker. Start it with <code className="font-mono text-text">python -m src.jobs.worker</code>, then try again.</>}
            action={<Button size="sm" onClick={() => void setup.refetch()}>Check again</Button>}
          />
        ) : (
          <ErrorState error={setup.error} onRetry={() => void setup.refetch()} />
        )}
      </Card>
    );
  }
  return <>{children(setup.data)}</>;
}

function IntegrationsTab() {
  const setup = useSetupStatus();
  return (
    <>
      <SetupSection setup={setup}>
        {(status) => (
          <div className="space-y-4">
            <OllamaStep status={status} onRecheck={() => void setup.refetch()} rechecking={setup.isFetching} />
            <GoogleStep status={status} />
          </div>
        )}
      </SetupSection>
      <ExtensionTokenCard />
    </>
  );
}

// --- Tags ---------------------------------------------------------------------------

const TAG_SWATCH: Record<(typeof TAG_COLORS)[number], string> = {
  slate: "oklch(0.6 0.03 260)",
  red: "oklch(0.6 0.2 25)",
  orange: "oklch(0.68 0.17 50)",
  amber: "oklch(0.75 0.15 80)",
  green: "oklch(0.62 0.15 150)",
  teal: "oklch(0.62 0.11 190)",
  blue: "oklch(0.6 0.15 250)",
  violet: "oklch(0.58 0.18 295)",
  pink: "oklch(0.65 0.18 350)",
};

function TagsTab() {
  const tags = useTags();
  const create = useCreateTag();
  const remove = useDeleteTag();
  const [name, setName] = useState("");
  const [color, setColor] = useState<(typeof TAG_COLORS)[number]>("slate");
  const [deleting, setDeleting] = useState<{ id: number; name: string; count: number } | null>(null);
  const parsed = tagCreateSchema.safeParse({ name, color });
  const duplicate = tags.data?.some((tag) => tag.name.toLowerCase() === name.trim().toLowerCase());

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!parsed.success || duplicate) return;
    create.mutate(parsed.data, {
      onSuccess: (tag) => {
        setName("");
        toast.success(`Created #${tag.name}`);
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  return (
    <Card>
      <CardHeader title="Tags" description="Your own labels for email, on top of the automatic categories. Add them from the inbox." />
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2 border-b border-border px-5 pb-4">
        <Field label="New tag" className="min-w-48 flex-1" error={duplicate ? "You already have a tag with that name." : undefined}>
          {(props) => <Input {...props} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. thesis" maxLength={64} />}
        </Field>
        <Field label="Colour" className="w-36">
          {(props) => (
            <Select {...props} value={color} onChange={(e) => setColor(e.target.value as typeof color)}>
              {TAG_COLORS.map((c) => <option key={c} value={c}>{c[0]!.toUpperCase() + c.slice(1)}</option>)}
            </Select>
          )}
        </Field>
        <Button type="submit" variant="primary" disabled={!parsed.success || duplicate} loading={create.isPending}>
          <Plus className="size-4" /> Add
        </Button>
      </form>
      <CardBody>
        {tags.isPending ? (
          <SkeletonRows rows={3} />
        ) : tags.error ? (
          <ErrorState error={tags.error} onRetry={() => void tags.refetch()} />
        ) : !tags.data?.length ? (
          <EmptyState icon={<TagIcon />} title="No tags yet" description="Create one above, then add it to emails from the inbox." />
        ) : (
          <ul className="divide-y divide-border">
            {tags.data.map((tag) => (
              <li key={tag.id} className="flex items-center gap-3 py-2.5">
                <span className="size-2.5 rounded-full" style={{ background: TAG_SWATCH[tag.color as keyof typeof TAG_SWATCH] ?? TAG_SWATCH.slate }} aria-hidden />
                <span className="text-[13.5px] font-medium text-text">#{tag.name}</span>
                <span className="text-[12.5px] text-faint">{tag.emailCount ?? 0} email{tag.emailCount === 1 ? "" : "s"}</span>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="ml-auto"
                  aria-label={`Delete #${tag.name}`}
                  onClick={() => setDeleting({ id: tag.id, name: tag.name, count: tag.emailCount ?? 0 })}
                >
                  <Trash2 className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={`Delete #${deleting?.name ?? ""}?`}
        description={deleting?.count ? `It will be removed from ${deleting.count} email${deleting.count === 1 ? "" : "s"}. The emails themselves stay.` : "No emails have this tag."}
        confirmLabel="Delete tag"
        loading={remove.isPending}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast.success(`Deleted #${deleting.name}`);
              setDeleting(null);
            },
            onError: (error) => toast.error(errorMessage(error)),
          })
        }
      />
    </Card>
  );
}

// --- Privacy ------------------------------------------------------------------------

const PURGES: Array<{ scope: PurgeScope; title: string; description: string }> = [
  {
    scope: "email_bodies",
    title: "Delete email bodies",
    description: "Blanks the text of every stored email. Subjects, senders, categories and commitments stay.",
  },
  {
    scope: "activity",
    title: "Clear the activity log",
    description: "Deletes the history of what happened. Mail, commitments and settings are untouched.",
  },
  {
    scope: "everything",
    title: "Delete all mail data",
    description: "Removes every email, commitment, job and event, and takes the events off Google Calendar. Your account, settings and contacts stay.",
  },
];

function PrivacyTab() {
  const section = useSection("privacy");
  const exportData = useExportData();
  const purge = usePurge();
  const [confirming, setConfirming] = useState<(typeof PURGES)[number] | null>(null);
  const value = section.value;

  return (
    <>
      <SectionState section={section}>
        {value && (
          <Card>
            <CardHeader title="What is kept" description="Everything is stored on this machine, in your own database." />
            <CardBody className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-3 py-2">
                <div>
                  <p className="text-sm font-medium text-text">Keep email for</p>
                  <p className="mt-0.5 text-[13px] text-muted">Older mail is deleted once a day. Commitments already on your calendar stay there.</p>
                </div>
                <Select
                  aria-label="Keep email for"
                  className="w-44"
                  value={value.retentionDays}
                  disabled={section.saving}
                  onChange={(e) => section.set({ retentionDays: Number(e.target.value) }, "Retention saved")}
                >
                  {RETENTION_OPTIONS.map((days) => (
                    <option key={days} value={days}>{days === 0 ? "Forever" : `${days} days`}</option>
                  ))}
                </Select>
              </div>
              <div className="border-t border-border">
                <Switch
                  label="Keep email text after analysis"
                  description="When off, each email's body is blanked as soon as it has been analysed. Subjects and commitments stay."
                  checked={value.keepEmailBodies}
                  disabled={section.saving}
                  onCheckedChange={(keepEmailBodies) => section.set({ keepEmailBodies })}
                />
              </div>
            </CardBody>
          </Card>
        )}
      </SectionState>

      <Card>
        <CardHeader
          title="Export your data"
          description="Every email, commitment, contact, tag and setting, plus recent activity, as one JSON file."
          action={
            <Button size="sm" loading={exportData.isPending} onClick={() => exportData.mutate(undefined, { onError: (error) => toast.error(errorMessage(error)) })}>
              <Download className="size-3.5" /> Export
            </Button>
          }
        />
      </Card>

      <Card className="border-[color-mix(in_oklch,var(--danger)_35%,var(--border))]">
        <CardHeader title="Delete data" description="These can't be undone. Each one asks you to type DELETE first." />
        <CardBody className="divide-y divide-border py-0">
          {PURGES.map((p) => (
            <div key={p.scope} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-text">{p.title}</p>
                <p className="mt-0.5 text-[13px] text-muted">{p.description}</p>
              </div>
              <Button size="sm" variant="danger" onClick={() => setConfirming(p)}>{p.title.split(" ")[0]}</Button>
            </div>
          ))}
        </CardBody>
      </Card>

      <ConfirmDialog
        open={!!confirming}
        onOpenChange={(open) => !open && setConfirming(null)}
        title={`${confirming?.title ?? ""}?`}
        description={confirming?.description}
        confirmLabel={confirming?.title ?? "Delete"}
        typeToConfirm="DELETE"
        loading={purge.isPending}
        onConfirm={() =>
          confirming &&
          purge.mutate(confirming.scope, {
            onSuccess: () => {
              toast.success("Done");
              setConfirming(null);
            },
            onError: (error) => toast.error(errorMessage(error)),
          })
        }
      />
    </>
  );
}
