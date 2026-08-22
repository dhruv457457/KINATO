"use client";

import React from "react";
import { BusinessProfileType } from "@/lib/types";
import { ShieldAlert, Sparkles, Ban, ArrowRight } from "lucide-react";

interface ScenariosQuickSelectProps {
  onSelectScenario: (profile: BusinessProfileType, targetSku?: string) => void;
  isLoading: boolean;
}

export const ScenariosQuickSelect: React.FC<ScenariosQuickSelectProps> = ({
  onSelectScenario,
  isLoading,
}) => {
  return (
    <div className="w-full flex flex-col gap-2.5">
      <span className="text-[11px] font-mono uppercase text-zinc-400 tracking-wider">
        Judge Verification Scenarios (1-Click Presets):
      </span>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        
        {/* Scenario 1: Success & FIFO Bundle */}
        <div
          onClick={() => onSelectScenario("CLOUD_KITCHEN", "SKU_CHEESE_MOZZ_1KG")}
          className="glass-card rounded-xl p-3.5 flex flex-col justify-between cursor-pointer border border-brand-500/20 hover:border-brand-500/60"
        >
          <div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 font-bold">
                SCENARIO 1: SUCCESS
              </span>
              <Sparkles className="w-3.5 h-3.5 text-accent-emerald" />
            </div>

            <h4 className="text-xs font-bold text-white font-sans mt-2">
              Cloud Kitchen Daily Restock
            </h4>
            <p className="text-[11px] text-zinc-400 mt-1">
              Restock Mozzarella. Wholesaler triggers aging chipotle sauce bundle (-₹160 discount) within ₹2,500 daily budget.
            </p>
          </div>

          <div className="mt-3 pt-2 border-t border-white/[0.04] flex items-center justify-between text-xs text-brand-300 font-mono">
            <span>Run Test</span>
            <ArrowRight className="w-3 h-3" />
          </div>
        </div>

        {/* Scenario 2: Floor Price Protection */}
        <div
          onClick={() => onSelectScenario("TECH_PANTRY", "SKU_COFFEE_BEANS_1KG")}
          className="glass-card rounded-xl p-3.5 flex flex-col justify-between cursor-pointer border border-amber-500/20 hover:border-amber-500/60"
        >
          <div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20 font-bold">
                SCENARIO 2: MARGIN GUARD
              </span>
              <ShieldAlert className="w-3.5 h-3.5 text-accent-amber" />
            </div>

            <h4 className="text-xs font-bold text-white font-sans mt-2">
              Tech Coworking Coffee Restock
            </h4>
            <p className="text-[11px] text-zinc-400 mt-1">
              Supplier bundles aging protein snack bars to hit exact ₹1,500 budget cap with Floor Price (SP ≥ CP×1.15) protection.
            </p>
          </div>

          <div className="mt-3 pt-2 border-t border-white/[0.04] flex items-center justify-between text-xs text-amber-300 font-mono">
            <span>Run Test</span>
            <ArrowRight className="w-3 h-3" />
          </div>
        </div>

        {/* Scenario 3: Cashflow Spend Cap Refusal */}
        <div
          onClick={() => onSelectScenario("RETAIL_STORE", "SKU_CORRUGATED_BOX_M")}
          className="glass-card rounded-xl p-3.5 flex flex-col justify-between cursor-pointer border border-rose-500/20 hover:border-rose-500/60"
        >
          <div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-300 border border-rose-500/20 font-bold">
                SCENARIO 3: POLICY REFUSAL
              </span>
              <Ban className="w-3.5 h-3.5 text-accent-rose" />
            </div>

            <h4 className="text-xs font-bold text-white font-sans mt-2">
              Retail Bulk Shipping Boxes
            </h4>
            <p className="text-[11px] text-zinc-400 mt-1">
              Bulk restock order evaluates against daily cashflow limit. Policy Engine deterministically flags overspend.
            </p>
          </div>

          <div className="mt-3 pt-2 border-t border-white/[0.04] flex items-center justify-between text-xs text-rose-300 font-mono">
            <span>Run Test</span>
            <ArrowRight className="w-3 h-3" />
          </div>
        </div>

      </div>
    </div>
  );
};
