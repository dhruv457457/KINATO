"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getCurrentMerchant, Merchant, stepPath } from "@/lib/api";

const STEPS: { key: Merchant["onboarding_step"]; num: string; label: string; path: string }[] = [
  { key: "signup", num: "01", label: "Create account", path: "/login" },
  { key: "connect", num: "02", label: "Connect Razorpay", path: "/onboarding/connect" },
  { key: "integrate", num: "03", label: "Integrate", path: "/onboarding/integrate" },
  { key: "catalog", num: "04", label: "Catalog", path: "/onboarding/catalog" },
  { key: "policy", num: "05", label: "Policy", path: "/onboarding/policy" },
];

const STEP_ORDER: Merchant["onboarding_step"][] = ["signup", "connect", "integrate", "catalog", "policy", "done"];

export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [merchant, setMerchant] = useState<Merchant | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setChecked(false);
    getCurrentMerchant().then((m) => {
      if (cancelled) return;
      if (!m) {
        router.replace("/login");
        return;
      }
      if (m.onboarding_step === "done") {
        router.replace("/dashboard");
        return;
      }
      const currentPageIndex = STEPS.findIndex((s) => s.path === pathname);
      // The page the merchant's current step should land them on - derived
      // from the same stepPath() used for the initial post-login redirect,
      // so "signup" (meaning "account created, do Connect next") maps to
      // the Connect page's index, not signup's own index. Comparing raw
      // STEP_ORDER indices instead double-counts that offset and produces
      // an infinite self-redirect on the Connect page.
      const allowedIndex = STEPS.findIndex((s) => s.path === stepPath(m.onboarding_step));
      // Resumable, not skippable: a merchant can't jump ahead to a step
      // they haven't reached yet (e.g. pasting /onboarding/policy before
      // connecting Razorpay), but can freely revisit a completed one.
      if (currentPageIndex > -1 && currentPageIndex > allowedIndex) {
        router.replace(stepPath(m.onboarding_step));
        return;
      }
      setMerchant(m);
      setChecked(true);
    });
    return () => {
      cancelled = true;
    };
  }, [router, pathname]);

  if (!checked || !merchant) {
    return (
      <main className="min-h-screen w-full flex items-center justify-center bg-background">
        <p className="text-sm text-dark-200">Loading your workspace…</p>
      </main>
    );
  }

  const currentIndex = STEPS.findIndex((s) => s.path === pathname);
  const merchantStepIndex = STEP_ORDER.indexOf(merchant.onboarding_step);

  return (
    <div className="grid min-h-screen" style={{ gridTemplateColumns: "272px 1fr" }}>
      {/* RAIL */}
      <aside
        className="border-r flex flex-col justify-between px-8 py-10 sticky top-0 h-screen"
        style={{ borderColor: "#D5D0BC" }}
      >
        <div>
          <div className="font-serif text-[19px] font-semibold text-dark">Kinato</div>
          <nav className="mt-14">
            {STEPS.map((step, i) => {
              const stepIndex = STEP_ORDER.indexOf(step.key);
              const done = stepIndex < merchantStepIndex;
              const active = i === currentIndex;
              return (
                <div
                  key={step.key}
                  className="flex items-baseline gap-3.5 py-[15px] border-t"
                  style={{
                    borderColor: "#EAE6D5",
                    color: active ? "#1B1A17" : done ? "#44433C" : "#D5D0BC",
                  }}
                >
                  <span className="text-xs font-semibold tabular-nums w-4 flex-shrink-0">{step.num}</span>
                  <span className="text-[13px] font-medium">{step.label}</span>
                </div>
              );
            })}
            <div className="border-b" style={{ borderColor: "#EAE6D5" }} />
          </nav>
          <div className="mt-3.5 h-px relative" style={{ background: "#EAE6D5" }}>
            <div
              className="absolute left-0 top-0 h-px transition-all duration-500"
              style={{
                background: "#1B1A17",
                width: `${(Math.max(currentIndex, 0) / (STEPS.length - 1)) * 100}%`,
              }}
            />
          </div>
        </div>
        <div className="text-[11px] uppercase tracking-wider text-dark-200 tabular-nums">
          Step {Math.max(currentIndex, 0) + 1} / {STEPS.length}
        </div>
      </aside>

      {/* MAIN */}
      <div className="flex flex-col min-w-0">{children}</div>
    </div>
  );
}
