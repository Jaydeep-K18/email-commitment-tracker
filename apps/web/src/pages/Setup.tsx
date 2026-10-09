import { passwordSchema, setupSchema } from "@commitmail/shared";
import { Check } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { Button } from "../components/ui/button";
import { Field, Input } from "../components/ui/form";
import { FullPageSpinner } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { useCreateOwner, useSession } from "../lib/queries";
import { AuthLayout } from "./AuthLayout";

/** The password rules, shown live so the user is never surprised by a rejection. */
function PasswordChecks({ password }: { password: string }) {
  const checks = [
    { label: "At least 10 characters", ok: password.length >= 10 },
    { label: "Not just a repeated character", ok: new Set(password).size >= 4 },
  ];
  return (
    <ul className="mt-2 space-y-1">
      {checks.map((check) => (
        <li key={check.label} className={cn("flex items-center gap-1.5 text-[12.5px]", check.ok ? "text-success" : "text-faint")}>
          <Check className={cn("size-3.5", !check.ok && "opacity-40")} /> {check.label}
        </li>
      ))}
    </ul>
  );
}

/** First run: create the one owner account. Never shown again afterwards. */
export function Setup() {
  const session = useSession();
  const createOwner = useCreateOwner();
  const navigate = useNavigate();
  const [form, setForm] = useState({ displayName: "", email: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});

  if (session.isPending) return <FullPageSpinner />;
  // Creating the account updates the session before the mutation's own
  // callback runs, so this redirect is what fires: send a new owner on to
  // onboarding, not past it.
  if (!session.data?.setupRequired) {
    const next = createOwner.isSuccess ? "/onboarding" : session.data?.authenticated ? "/" : "/login";
    return <Navigate to={next} replace />;
  }

  const set = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    // "Confirm" is checked here and never sent: the account schema is strict,
    // so an extra field would fail it.
    const { confirm, ...account } = form;
    const parsed = setupSchema.safeParse(account);
    const next: Record<string, string> = {};
    if (!parsed.success) {
      for (const [key, messages] of Object.entries(parsed.error.flatten().fieldErrors)) {
        if (messages?.[0]) next[key] = key === "email" ? "Enter a valid email address" : messages[0];
      }
      // A failure no field claims must still say something, never just do nothing.
      if (!Object.keys(next).length) next.form = "Something in the form isn't right. Check each field and try again.";
    }
    if (account.password !== confirm) next.confirm = "The passwords don't match";
    setErrors(next);
    if (Object.keys(next).length || !parsed.success) return;
    createOwner.mutate(parsed.data, { onSuccess: () => navigate("/onboarding", { replace: true }) });
  };

  return (
    <AuthLayout title="Create your account" subtitle="This app has one owner — you. Choose the password that protects your mail.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Your name" error={errors.displayName}>
          {(props) => <Input {...props} autoComplete="name" value={form.displayName} onChange={set("displayName")} autoFocus />}
        </Field>
        <Field label="Email" hint="Used to sign in. Doesn't have to be the mailbox you'll connect." error={errors.email}>
          {(props) => <Input {...props} type="email" autoComplete="username" value={form.email} onChange={set("email")} />}
        </Field>
        <Field label="Password" error={errors.password}>
          {(props) => (
            <>
              <Input {...props} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
              <PasswordChecks password={form.password} />
            </>
          )}
        </Field>
        <Field label="Confirm password" error={errors.confirm}>
          {(props) => <Input {...props} type="password" autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />}
        </Field>
        {errors.form && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {errors.form}
          </p>
        )}
        {createOwner.error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {errorMessage(createOwner.error)}
          </p>
        )}
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          loading={createOwner.isPending}
          disabled={!passwordSchema.safeParse(form.password).success}
        >
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
