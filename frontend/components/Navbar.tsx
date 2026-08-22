"use client";

import React from "react";
import { BusinessProfileType, ExecutionMode } from "@/lib/types";
import { ShieldCheck, Zap, Sparkles, Layers, History } from "lucide-react";

interface NavbarProps {
  currentProfile: BusinessProfileType;
  onProfileChange: (profile: BusinessProfileType) => void;
  executionMode: ExecutionMode;
  onModeChange: (mode: ExecutionMode) => void;
  onOpenProofs: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  currentProfile,
  onProfileChange,
  executionMode,
  onModeChange,
  onOpenProofs,
}) => {
  return (
    <header className="sticky top-0 z-50 w-full border-b border-white/[0.08] bg-surface-300/80 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        
        {/* Brand & Logo */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-brand-500 to-indigo-700 flex items-center justify-center shadow-glow">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-lg font-bold tracking-tight text-white font-sans">
                Kinato
              </span>
              <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-brand-500/20 text-brand-300 border border-brand-500/30">
                A2A Protocol v1.0
              </span>
            </div>
            <p className="text-[11px] text-zinc-400 font-sans hidden sm:block">
              Autonomous B2B Restock & Agentic Commerce on Razorpay
            </p>
          </div>
        </div>

        {/* Center: Profile Switcher */}
        <div className="flex items-center gap-1 bg-surface-100/90 border border-white/[0.06] rounded-xl p-1 shadow-inner">
          <button
            onClick={() => onProfileChange("CLOUD_KITCHEN")}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              currentProfile === "CLOUD_KITCHEN"
                ? "bg-brand-600 text-white shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            <span>🍔</span>
            <span className="hidden md:inline">Cloud Kitchen</span>
          </button>

          <button
            onClick={() => onProfileChange("TECH_PANTRY")}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              currentProfile === "TECH_PANTRY"
                ? "bg-brand-600 text-white shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            <span>💻</span>
            <span className="hidden md:inline">Tech Pantry</span>
          </button>

          <button
            onClick={() => onProfileChange("RETAIL_STORE")}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              currentProfile === "RETAIL_STORE"
                ? "bg-brand-600 text-white shadow-sm"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            <span>🏪</span>
            <span className="hidden md:inline">Retail Store</span>
          </button>
        </div>

        {/* Right Controls: Mode Switcher & Proofs */}
        <div className="flex items-center gap-3">
          {/* Execution Mode Toggle */}
          <button
            onClick={() =>
              onModeChange(
                executionMode === "ONE_CLICK_APPROVAL"
                  ? "AUTONOMOUS_AUTOPAY"
                  : "ONE_CLICK_APPROVAL"
              )
            }
            className={`px-3 py-1.5 rounded-xl border text-xs font-mono flex items-center gap-2 transition-all ${
              executionMode === "ONE_CLICK_APPROVAL"
                ? "border-brand-500/40 bg-brand-500/10 text-brand-200 hover:bg-brand-500/20"
                : "border-accent-emerald/40 bg-accent-emerald/10 text-emerald-200 hover:bg-accent-emerald/20"
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Mode:</span>
            <span className="font-semibold">
              {executionMode === "ONE_CLICK_APPROVAL" ? "1-Click Modal" : "AutoPay Mandate"}
            </span>
          </button>

          {/* Proof Receipts Audit Ledger Button */}
          <button
            onClick={onOpenProofs}
            className="p-2 rounded-xl border border-white/[0.08] bg-surface-100 hover:bg-surface-50 text-zinc-300 hover:text-white transition-all text-xs flex items-center gap-1.5"
            title="View Cryptographic Proof Receipts Ledger"
          >
            <History className="w-4 h-4 text-brand-400" />
            <span className="hidden lg:inline">Audit Log</span>
          </button>

          {/* Razorpay Test Badge */}
          <div className="hidden xl:flex items-center gap-1.5 text-[11px] font-mono text-zinc-400 bg-surface-100 px-2.5 py-1 rounded-lg border border-white/[0.06]">
            <ShieldCheck className="w-3.5 h-3.5 text-accent-emerald" />
            <span>Razorpay Sandbox</span>
          </div>
        </div>

      </div>
    </header>
  );
};
