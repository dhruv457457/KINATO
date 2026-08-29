"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getCurrentMerchant, Merchant, stepPath } from "@/lib/api";
import { DashboardErrorBoundary } from "@/components/dashboard/ErrorBoundary";

const NAV_ITEMS: { label: string; path: string; enabled: boolean }[] = [
  { label: "Overview", path: "/dashboard", enabled: true },
  { label: "Ask", path: "/dashboard/ask", enabled: true },
  { label: "Recoveries", path: "/dashboard/recoveries", enabled: true },
  { label: "Customers", path: "/dashboard/customers", enabled: true },
  { label: "Catalog", path: "/dashboard/catalog", enabled: true },
  { label: "AI Commerce", path: "/dashboard/ai-commerce", enabled: false },
  { label: "Policies", path: "/dashboard/policies", enabled: true },
  { label: "Activity", path: "/dashboard/activity", enabled: true },
  { label: "Settings", path: "/dashboard/settings", enabled: true },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [merchant, setMerchant] = useState<Merchant | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCurrentMerchant().then((m) => {
      if (cancelled) return;
      if (!m) {
        router.replace("/login");
        return;
      }
      if (m.onboarding_step !== "done") {
        router.replace(stepPath(m.onboarding_step));
        return;
      }
      setMerchant(m);
      setChecked(true);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!checked || !merchant) {
    return (
      <main className="min-h-screen w-full flex items-center justify-center bg-background">
        <p className="text-sm text-dark-200">Loading your workspace…</p>
      </main>
    );
  }

  return (
    // Below lg the rail becomes a horizontal strip above the content
    // rather than a drawer. A hamburger would introduce an interaction
    // language this dashboard uses nowhere else, for eight destinations
    // that fit on one line; the rail is signage, and signage should stay
    // visible.
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[240px_1fr]">
      {/* RAIL - same signage language as onboarding, permanent instead of linear */}
      <aside
        className="border-b lg:border-b-0 lg:border-r flex flex-col justify-between px-6 lg:px-7 py-5 lg:py-9 lg:sticky lg:top-0 lg:h-screen"
        style={{ borderColor: "#D5D0BC" }}
      >
        <div className="flex items-center gap-6 lg:block">
          <div className="font-serif text-[19px] font-semibold text-dark mb-0 lg:mb-10 shrink-0">Kinato</div>
          <nav className="flex gap-5 overflow-x-auto lg:block lg:gap-0 lg:overflow-visible">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.path;
              const content = (
                <div
                  className="flex items-baseline gap-2 lg:gap-3 py-1 lg:py-2.5 text-[13px] font-medium transition-colors whitespace-nowrap nav-item"
                  style={{
                    color: !item.enabled ? "#D5D0BC" : active ? "#1B1A17" : "#6B6862",
                    cursor: item.enabled ? "pointer" : "default",
                    // The active marker rotates with the rail: a left rule
                    // when the nav is vertical, an underline when it lies
                    // down. Same 2px hairline either way.
                    ["--active-rule" as string]: active ? "#1B1A17" : "transparent",
                  }}
                >
                  {item.label}
                  {!item.enabled && <span className="text-[10px] uppercase tracking-wider">soon</span>}
                </div>
              );
              return item.enabled ? (
                <button key={item.path} onClick={() => router.push(item.path)} className="w-full text-left">
                  {content}
                </button>
              ) : (
                <div key={item.path}>{content}</div>
              );
            })}
          </nav>
        </div>
        <div className="text-[11px] text-dark-200 hidden lg:block">
          <div className="font-medium text-dark">{merchant.name}</div>
          <div className="truncate">{merchant.email}</div>
        </div>
      </aside>

      {/* MAIN */}
      <div className="flex flex-col min-w-0 overflow-x-auto">
        {/* Keyed on pathname so navigating away from a page that threw
            clears the error instead of pinning the dashboard to it. */}
        <DashboardErrorBoundary resetKey={pathname}>{children}</DashboardErrorBoundary>
      </div>
    </div>
  );
}
