import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router";
import { api, errorText, unwrap } from "../../api/client";
import { useAuthState } from "../../api/queries";
import { Button } from "../../components/Button";
import { Logo } from "../../components/Logo";
import { Loading } from "../../components/ui";
import { useTitle } from "../../lib/useTitle";

function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function Login() {
  useTitle("Sign in");
  const { data, isPending } = useAuthState();
  const [params] = useSearchParams();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = safeNext(params.get("next"));

  if (isPending) return <Loading text="Connecting…" />;
  if (data?.signed_in) return <Navigate to={next} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const state = await unwrap(api.POST("/api/auth/login", { body: { password } }));
      queryClient.setQueryData(["auth"], state);
    } catch (problem) {
      setError(errorText(problem));
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-gray-900 via-gray-900 to-indigo-950 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <Logo className="h-14 w-14" />
          <h1 className="text-2xl font-bold text-white">Programarr</h1>
        </div>
        <form onSubmit={submit} className="rounded-lg bg-gray-800/90 p-6 shadow-xl ring-1 ring-gray-700">
          <label htmlFor="password" className="mb-1 block text-sm font-semibold text-gray-300">
            Password
          </label>
          <input
            id="password"
            type="password"
            className="field-input"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && (
            <p className="mt-3 text-sm text-red-300" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" className="mt-5 w-full" disabled={busy || !password}>
            Sign in
          </Button>
        </form>
      </div>
    </div>
  );
}
