"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { login } from "@/lib/api";
import { saveSession } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await login(username.trim(), password);
      saveSession(
        result.access_token,
        result.role,
        result.full_name,
        result.username || username.trim(),
      );
      router.replace("/");
    } catch {
      setError("Invalid username or password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--navy)] px-4">
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "radial-gradient(ellipse 80% 50% at 20% 20%, #c45c2640, transparent), radial-gradient(ellipse 60% 40% at 80% 80%, #2f6f4e33, transparent)",
        }}
      />
      <div className="relative w-full max-w-md rounded-xl border border-white/10 bg-[var(--surface)] p-8 shadow-xl">
        <p className="font-display text-sm tracking-wide text-[var(--saffron)]">Karnataka State Police</p>
        <h1 className="mt-1 font-display text-3xl text-[var(--navy)]">Crime AI</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">
          Sign in to query FIRs, explore criminal networks, and review crime trends.
        </p>

        <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-3">
          <label className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-white px-3 py-2 text-sm text-[var(--navy)]"
            />
          </label>
          <label className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-white px-3 py-2 text-sm text-[var(--navy)]"
            />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="mt-1 rounded-md bg-[var(--navy)] px-4 py-2.5 text-sm font-medium text-white hover:bg-[var(--navy-deep)] disabled:opacity-40"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="mt-6 rounded-md bg-[var(--sand)] p-3 text-xs text-[var(--muted)]">
          <p className="font-medium text-[var(--navy)]">Demo accounts</p>
          <ul className="mt-1 space-y-0.5">
            <li>investigator / investigator123</li>
            <li>sho / sho123456 · dsp / dsp1234567</li>
            <li>analyst / analyst12345 · admin / admin1234567</li>
          </ul>
        </div>
      </div>
    </main>
  );
}
