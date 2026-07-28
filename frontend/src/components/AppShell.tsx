"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { wakeApi, startKeepAlivePinger } from "@/lib/api";
import { clearSession, getFullName, getRole, getToken } from "@/lib/auth";

const NAV_LINKS = [
  { href: "/", label: "Chat" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/network", label: "Network" },
];

/** Wraps every authenticated page: redirects to /login if there's no session,
 * otherwise renders a shared nav bar around the page content. */
export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login/");
      return;
    }
    setAuthChecked(true);
    // Keep Render + Neon warm while the officer navigates.
    void wakeApi();
    // Ping every 5 min so the free-tier API does not sleep mid-session.
    return startKeepAlivePinger();
  }, [router]);

  if (!authChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--sand)] text-sm text-[var(--muted)]">
        Loading…
      </div>
    );
  }

  function handleLogout() {
    clearSession();
    router.replace("/login/");
  }

  return (
    <div className="flex h-screen flex-col bg-[var(--sand)] text-[var(--navy)]">
      <header className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--navy)] px-3 py-2 text-white">
        <div className="flex items-center gap-4">
          <Link href="/" className="font-display text-base tracking-tight">
            Crime AI <span className="text-[var(--saffron)]">·</span> KSP
          </Link>
          <nav className="flex gap-0.5 text-sm">
            {NAV_LINKS.map((link) => {
              const active =
                link.href === "/"
                  ? pathname === "/" || pathname === ""
                  : pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  prefetch
                  className={`rounded-md px-2.5 py-1 transition ${
                    active
                      ? "bg-white/15 text-white"
                      : "text-white/70 hover:bg-white/10 hover:text-white"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="hidden sm:inline text-white/80">{getFullName()}</span>
          <span className="rounded-full bg-[var(--saffron)] px-2 py-0.5 text-xs font-medium text-[var(--navy)]">
            {getRole()}
          </span>
          <button
            onClick={handleLogout}
            className="text-white/70 underline decoration-white/30 underline-offset-2 hover:text-white"
          >
            Log out
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
