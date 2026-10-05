import { loginSchema } from "@commitmail/shared";
import { Eye, EyeOff } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { Button } from "../components/ui/button";
import { Field, Input } from "../components/ui/form";
import { FullPageSpinner } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { useLogin, useSession } from "../lib/queries";
import { AuthLayout } from "./AuthLayout";

export function Login() {
  const session = useSession();
  const login = useLogin();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});

  if (session.isPending) return <FullPageSpinner />;
  if (session.data?.setupRequired) return <Navigate to="/setup" replace />;
  if (session.data?.authenticated) return <Navigate to="/" replace />;

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = loginSchema.safeParse({ email, password });
    if (!parsed.success) {
      const fields = parsed.error.flatten().fieldErrors;
      setErrors({ email: fields.email?.[0] && "Enter a valid email address", password: fields.password?.[0] && "Enter your password" });
      return;
    }
    setErrors({});
    login.mutate(parsed.data, { onSuccess: () => navigate(from, { replace: true }) });
  };

  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to see what you've promised, and what you're owed.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Email" error={errors.email}>
          {(props) => (
            <Input {...props} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          )}
        </Field>
        <Field label="Password" error={errors.password}>
          {(props) => (
            <div className="relative">
              <Input
                {...props}
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-faint hover:text-text"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
          )}
        </Field>
        {login.error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {errorMessage(login.error)}
          </p>
        )}
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={login.isPending}>
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}
