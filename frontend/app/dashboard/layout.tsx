"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getCurrentMerchant, Merchant, stepPath } from "@/lib/api";

const NAV_ITEMS: { label: string; path: string; enabled: boolean }[] = [
  { label: "Overview", path: "/dashboard", enabled: true },
  { label: "Recoveries", path: "/dashboard/recoveries", enabled: true },
  { label: "Customers", path: "/dashboard/customers", enabled: false },
  { label: "Catalog", path: "/dashboard/catalog", enabled: false },
  { label: "AI Commerce", path: "/dashboard/ai-commerce", enabled: false },
  { label: "Policies", path: "/dashboard/policies", enabled: false },
  { label: "Activity", path: "/dashboard/activity", enabled: false },
  { label: "Settings", path: "/dashboard/settings", enabled: false },
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
    <div className="grid min-h-screen" style={{ gridTemplateColumns: "240px 1fr" }}>
      {/* RAIL - same signage language as onboarding, permanent instead of linear */}
      <aside
        className="border-r flex flex-col justify-between px-7 py-9 sticky top-0 h-screen"
        style={{ borderColor: "#D5D0BC" }}
      >
        <div>
          <div className="font-serif text-[19px] font-semibold text-dark mb-10">Kinato</div>
          <nav>
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.path;
              const content = (
                <div
                  className="flex items-baseline gap-3 py-2.5 text-[13px] font-medium transition-colors"
                  style={{
                    color: !item.enabled ? "#D5D0BC" : active ? "#1B1A17" : "#6B6862",
                    cursor: item.enabled ? "pointer" : "default",
                    borderLeft: active ? "2px solid #1B1A17" : "2px solid transparent",
                    paddingLeft: "10px",
                    marginLeft: "-12px",
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
        <div className="text-[11px] text-dark-200">
          <div className="font-medium text-dark">{merchant.name}</div>
          <div className="truncate">{merchant.email}</div>
        </div>
      </aside>

      {/* MAIN */}
      <div className="flex flex-col min-w-0 overflow-x-auto">{children}</div>
    </div>
  );
}
