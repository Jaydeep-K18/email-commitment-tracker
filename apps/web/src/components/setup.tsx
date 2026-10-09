/**
 * The setup steps, shared by first-run onboarding and Settings → Integrations.
 * Each action is forwarded by the server to the Python worker, which owns the
 * keyring, the local model and Google's sign-in.
 */
import { mailboxSchema, type SetupStatus } from "@commitmail/shared";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays,
  CheckCircle2,
  Circle,
  Copy,
  Cpu,
  Download,
  ExternalLink,
  Key,
  Mail,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { api, errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { keys, useSaveSettings, useSettings } from "../lib/queries";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Field, Input } from "./ui/form";

export function StepCard({
  number,
  title,
  description,
  done,
  icon,
  children,
}: {
  number?: number;
  title: string;
  description: ReactNode;
  done: boolean;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className={cn("p-5 transition-colors", done && "border-[color-mix(in_oklch,var(--success)_35%,var(--border))]")}>
      <div className="flex gap-4">
        <div className={cn("grid size-10 shrink-0 place-items-center rounded-xl [&>svg]:size-5", done ? "bg-success-soft text-success" : "bg-accent-soft text-accent-text")}>
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-[15px] font-semibold text-text">
              {number != null && <span className="mr-1.5 text-faint">{number}.</span>}
              {title}
            </h3>
            <span className={cn("inline-flex items-center gap-1 text-[12.5px] font-medium", done ? "text-success" : "text-faint")}>
              {done ? <CheckCircle2 className="size-4" /> : <Circle className="size-4" />}
              {done ? "Done" : "Not yet"}
            </span>
          </div>
          <p className="mt-1 text-[13px] text-muted">{description}</p>
          <div className="mt-4">{children}</div>
        </div>
      </div>
    </Card>
  );
}

function CodeLine({ children }: { children: string }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-[12.5px] text-text">
      <code className="truncate">{children}</code>
      <button
        className="shrink-0 text-faint hover:text-text"
        aria-label="Copy"
        onClick={() => void navigator.clipboard?.writeText(children).then(() => toast.success("Copied"))}
      >
        <Copy className="size-3.5" />
      </button>
    </div>
  );
}

function useSetupAction<T = unknown>(method: "post" | "del", path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: unknown) => (method === "post" ? api.post<T>(path, body) : api.del<T>(path)),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.setup }),
  });
}

export function OllamaStep({ status, number, onRecheck, rechecking }: { status: SetupStatus; number?: number; onRecheck: () => void; rechecking: boolean }) {
  const { ollama } = status;
  const done = ollama.running && ollama.modelPresent;
  return (
    <StepCard
      number={number}
      icon={<Cpu />}
      done={done}
      title="Local AI model"
      description="Ollama runs the model that reads your email — on this machine, so nothing is sent to an AI service."
    >
      {done ? (
        <p className="text-[13px] text-muted">
          Running <span className="font-mono text-text">{ollama.model}</span>.
        </p>
      ) : (
        <div className="space-y-3">
          <p className="text-[13px] text-text">{ollama.problem}</p>
          {!ollama.running && (
            <a href="https://ollama.com/download" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-[13px] font-medium text-accent-text hover:underline">
              Download Ollama <ExternalLink className="size-3.5" />
            </a>
          )}
          <CodeLine>{`ollama pull ${ollama.model}`}</CodeLine>
          <Button size="sm" onClick={onRecheck} loading={rechecking}>
            <RefreshCw className="size-3.5" /> Check again
          </Button>
        </div>
      )}
    </StepCard>
  );
}

export function GoogleStep({ status, number }: { status: SetupStatus; number?: number }) {
  const signIn = useSetupAction<{ email: string; resumed: boolean }>("post", "/setup/google/sign-in");
  const disconnect = useSetupAction("del", "/setup/google");
  const { google } = status;
  const signInButton = (label: string) => (
    <div className="space-y-2">
      <Button
        variant="primary"
        size="sm"
        loading={signIn.isPending}
        onClick={() =>
          signIn.mutate(undefined, {
            onSuccess: (result) =>
              toast.success(
                result.resumed ? `Signed in again as ${result.email} — catching up on mail and your calendar` : `Signed in as ${result.email}`,
              ),
            onError: (error) => toast.error(errorMessage(error)),
          })
        }
      >
        {label}
      </Button>
      {signIn.isPending && <p className="text-[12.5px] text-muted">Finish signing in from the browser tab that just opened…</p>}
    </div>
  );
  return (
    <StepCard
      number={number}
      icon={<ShieldCheck />}
      done={google.signedIn && !google.expired}
      title="Sign in with Google"
      description="Optional. Identifies you to Google — it does not read your mail. Needed only for the Google Calendar option."
    >
      {google.signedIn && google.expired ? (
        <div className="space-y-3">
          <p role="alert" className="rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-text">
            Google ended the sign-in for <span className="font-medium">{google.email ?? "your account"}</span>, so mail checks and
            Google Calendar updates are paused. Sign in again to pick up where they left off.
          </p>
          {signInButton("Sign in again")}
        </div>
      ) : google.signedIn ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[13px] text-muted">
            Signed in as <span className="font-medium text-text">{google.email ?? "your Google account"}</span>
            {google.calendar && " · calendar access granted"}
          </p>
          <Button size="sm" variant="ghost" loading={disconnect.isPending} onClick={() => disconnect.mutate(undefined)}>
            Disconnect
          </Button>
        </div>
      ) : !google.clientConfigured ? (
        <p className="text-[13px] text-muted">
          No Google client is configured for this install. Follow <span className="font-mono">docs/google-setup.md</span> to add one, or skip this step.
        </p>
      ) : (
        signInButton("Sign in with Google")
      )}
    </StepCard>
  );
}

export function MailboxStep({ status, number }: { status: SetupStatus; number?: number }) {
  const save = useSetupAction<{ ok: boolean; message: string }>("post", "/setup/mailbox");
  const test = useSetupAction<{ ok: boolean; message: string }>("post", "/setup/mailbox/test");
  const [address, setAddress] = useState(status.mailbox.address ?? "");
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const valid = mailboxSchema.safeParse({ address, password }).success;
  const done = status.mailbox.connected;

  const run = (action: typeof save) =>
    action.mutate(
      { address, password },
      {
        onSuccess: (outcome) => {
          setResult(outcome);
          if (outcome.ok && action === save) setPassword("");
        },
        onError: (error) => setResult({ ok: false, message: errorMessage(error) }),
      },
    );

  return (
    <StepCard
      number={number}
      icon={<Mail />}
      done={done}
      title="Connect your mailbox"
      description={
        <>
          Read-only, with an <strong className="font-medium text-text">app password</strong> — not your normal one. Works with Gmail, Outlook, Yahoo and university mail.
        </>
      }
    >
      {done && status.mailbox.address && !result && (
        <p className="mb-3 text-[13px] text-muted">
          Connected to <span className="font-medium text-text">{status.mailbox.address}</span>. Enter a new app password below to change it.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Email address">
          {(props) => <Input {...props} type="email" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="you@gmail.com" />}
        </Field>
        <Field label="App password" hint="Stored in your OS keychain, never in a file.">
          {(props) => <Input {...props} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="xxxx xxxx xxxx xxxx" autoComplete="off" />}
        </Field>
      </div>
      <details className="mt-2 text-[12.5px] text-muted">
        <summary className="cursor-pointer select-none hover:text-text">How do I make an app password?</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>Turn on 2-Step Verification in your Google Account.</li>
          <li>Open Google Account → Security → App passwords.</li>
          <li>Create one for “Mail” and paste the 16 characters here.</li>
        </ol>
      </details>
      {result && (
        <p role="status" className={cn("mt-3 rounded-lg px-3 py-2 text-[13px]", result.ok ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>
          {result.message}
        </p>
      )}
      <div className="mt-4 flex gap-2">
        <Button size="sm" disabled={!valid} loading={test.isPending} onClick={() => run(test)}>
          Test connection
        </Button>
        <Button size="sm" variant="primary" disabled={!valid} loading={save.isPending} onClick={() => run(save)}>
          Save
        </Button>
      </div>
    </StepCard>
  );
}

export function CalendarStep({ status, number }: { status: SetupStatus; number?: number }) {
  const settings = useSettings();
  const saveSettings = useSaveSettings();
  const connect = useSetupAction<{ calendar: boolean }>("post", "/setup/google/calendar");
  const target = settings.data?.calendar.target ?? "auto";
  const feedUrl = `${window.location.origin}/calendar.ics`;

  const choose = (value: "auto" | "ics" | "google") => {
    if (!settings.data) return;
    saveSettings.mutate(
      { section: "calendar", value: { ...settings.data.calendar, target: value } },
      { onSuccess: () => toast.success("Calendar choice saved") },
    );
  };

  const options = [
    {
      value: "ics" as const,
      title: "Calendar file",
      body: "Download an .ics file, or subscribe to the live feed from Outlook, Apple Calendar or Thunderbird.",
    },
    {
      value: "google" as const,
      title: "Google Calendar",
      body: "Events go straight into your Google Calendar and reach your phone. Needs calendar permission.",
    },
  ];

  return (
    <StepCard
      number={number}
      icon={<CalendarDays />}
      done
      title="Where should events go?"
      description="Not tied to one calendar app — the calendar file is always written either way."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {options.map((option) => {
          const active = target === option.value || (target === "auto" && option.value === (status.google.calendar ? "google" : "ics"));
          return (
            <button
              key={option.value}
              onClick={() => choose(option.value)}
              className={cn(
                "rounded-xl border p-4 text-left transition-all",
                active ? "border-accent bg-accent-soft/50 ring-1 ring-accent" : "border-border hover:border-border-strong hover:bg-surface-2",
              )}
            >
              <p className="text-sm font-semibold text-text">{option.title}</p>
              <p className="mt-1 text-[12.5px] text-muted">{option.body}</p>
            </button>
          );
        })}
      </div>

      <div className="mt-4 space-y-3">
        <div className="flex flex-wrap gap-2">
          <a href={`${import.meta.env.BASE_URL}calendar.ics`} download="email-commitments.ics">
            <Button size="sm">
              <Download className="size-3.5" /> Download .ics
            </Button>
          </a>
          {!status.google.calendar && (
            <Button
              size="sm"
              variant="subtle"
              disabled={!status.google.signedIn}
              loading={connect.isPending}
              onClick={() =>
                connect.mutate(undefined, {
                  onSuccess: () => {
                    toast.success("Google Calendar connected");
                    choose("google");
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                })
              }
            >
              Connect Google Calendar
            </Button>
          )}
        </div>
        {!status.google.signedIn && <p className="text-[12.5px] text-faint">Sign in with Google first to use Google Calendar.</p>}
        <div>
          <p className="mb-1.5 text-[12.5px] text-muted">Subscription URL — keeps itself up to date:</p>
          <CodeLine>{feedUrl}</CodeLine>
          <p className="mt-1.5 text-[12px] text-faint">Google Calendar can't subscribe to a local address; use the Google option for that.</p>
        </div>
      </div>
    </StepCard>
  );
}

export function ExtensionTokenCard() {
  const client = useQueryClient();
  const [token, setToken] = useState<string | null>(null);
  const reveal = useMutation({ mutationFn: () => api.get<{ token: string }>("/setup/extension-token"), onSuccess: (r) => setToken(r.token) });
  const rotate = useMutation({
    mutationFn: () => api.post<{ token: string }>("/setup/extension-token/rotate"),
    onSuccess: (r) => {
      setToken(r.token);
      toast.success("New token generated — the old one no longer works");
      void client.invalidateQueries({ queryKey: keys.setup });
    },
  });
  return (
    <StepCard
      icon={<Key />}
      done={!!token}
      title="Gmail side panel"
      description="The browser extension adds an “Add to calendar” panel inside Gmail. Paste this token into its options page."
    >
      {token ? <CodeLine>{token}</CodeLine> : null}
      <div className="mt-3 flex gap-2">
        {!token && (
          <Button size="sm" loading={reveal.isPending} onClick={() => reveal.mutate()}>
            Show token
          </Button>
        )}
        <Button size="sm" variant="ghost" loading={rotate.isPending} onClick={() => rotate.mutate()}>
          Generate a new token
        </Button>
      </div>
      <p className="mt-3 text-[12px] text-faint">Treat it like a password: it's what stops other websites from talking to your tracker.</p>
    </StepCard>
  );
}
