"use client";

import React from "react";
import { BuyerContext, InventoryItem } from "@/lib/types";
import { AlertCircle, CheckCircle2, DollarSign, Package, RefreshCw } from "lucide-react";

interface InventoryMonitorProps {
  buyerContext: BuyerContext | null;
  selectedSku: string | null;
  onSelectSku: (sku: string) => void;
  onTriggerRestock: (sku?: string) => void;
  isLoading: boolean;
}

export const InventoryMonitor: React.FC<InventoryMonitorProps> = ({
  buyerContext,
  selectedSku,
  onSelectSku,
  onTriggerRestock,
  isLoading,
}) => {
  if (!buyerContext) {
    return (
      <div className="glass-panel rounded-2xl p-6 animate-pulse">
        <div className="h-6 w-1/3 bg-white/10 rounded mb-4" />
        <div className="h-20 w-full bg-white/5 rounded" />
      </div>
    );
  }

  const budgetUsedPct = Math.min(
    100,
    Math.round((buyerContext.weekly_spent_so_far / buyerContext.weekly_budget_limit) * 100)
  );

  return (
    <div className="glass-panel rounded-2xl p-5 flex flex-col gap-4">
      
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Package className="w-4 h-4 text-brand-400" />
            <h2 className="text-sm font-bold text-white font-sans tracking-tight">
              {buyerContext.business_name}
            </h2>
          </div>
          <p className="text-xs text-zinc-400 font-sans mt-0.5">
            Real-time Inventory & Days of Inventory Remaining (DIR)
          </p>
        </div>

        <button
          onClick={() => onTriggerRestock()}
          disabled={isLoading}
          className="px-3 py-1.5 rounded-xl bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white text-xs font-medium transition-all shadow-glow flex items-center gap-1.5 cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          <span>{isLoading ? "Negotiating..." : "Auto-Restock All"}</span>
        </button>
      </div>

      {/* Budget & Cashflow Capacity Bar */}
      <div className="bg-surface-100/70 border border-white/[0.06] rounded-xl p-3 flex flex-col gap-2">
        <div className="flex items-center justify-between text-xs">
          <span className="text-zinc-400 flex items-center gap-1">
            <DollarSign className="w-3.5 h-3.5 text-accent-emerald" /> Daily Budget Cap:
          </span>
          <span className="font-mono font-semibold text-white">
            ₹{buyerContext.daily_budget_limit.toLocaleString()} / day
          </span>
        </div>

        <div className="w-full bg-surface-300 h-2 rounded-full overflow-hidden border border-white/[0.04]">
          <div
            className={`h-full transition-all duration-500 rounded-full ${
              budgetUsedPct > 80 ? "bg-accent-rose" : "bg-gradient-to-r from-brand-500 to-accent-emerald"
            }`}
            style={{ width: `${budgetUsedPct}%` }}
          />
        </div>

        <div className="flex items-center justify-between text-[11px] font-mono text-zinc-500">
          <span>Weekly Spent: ₹{buyerContext.weekly_spent_so_far.toLocaleString()}</span>
          <span>Weekly Cap: ₹{buyerContext.weekly_budget_limit.toLocaleString()}</span>
        </div>
      </div>

      {/* Live Inventory List */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-mono uppercase text-zinc-400 tracking-wider">
          Tracked Stock on Hand:
        </span>

        <div className="space-y-2">
          {buyerContext.inventory.map((item: InventoryItem) => {
            const daysRemaining = item.daily_burn_rate > 0 
              ? roundToTwo(item.current_stock / item.daily_burn_rate)
              : 999;
            const isCritical = daysRemaining <= item.reorder_threshold_days;
            const isSelected = selectedSku === item.sku;

            return (
              <div
                key={item.sku}
                onClick={() => onSelectSku(item.sku)}
                className={`p-3 rounded-xl border transition-all cursor-pointer flex items-center justify-between ${
                  isSelected
                    ? "border-brand-500/80 bg-brand-500/10 shadow-sm"
                    : isCritical
                    ? "border-accent-rose/40 bg-accent-rose/5 hover:border-accent-rose/60"
                    : "border-white/[0.06] bg-surface-100/50 hover:border-white/[0.12]"
                }`}
              >
                <div className="flex items-start gap-2.5">
                  <div className={`mt-0.5 ${isCritical ? "text-accent-rose" : "text-accent-emerald"}`}>
                    {isCritical ? <AlertCircle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
                  </div>

                  <div>
                    <h3 className="text-xs font-semibold text-white font-sans">
                      {item.name}
                    </h3>
                    <p className="text-[11px] text-zinc-400 font-mono">
                      Stock: <span className="text-white font-semibold">{item.current_stock} {item.unit}</span> · Burn: {item.daily_burn_rate} {item.unit}/day
                    </p>
                  </div>
                </div>

                <div className="flex flex-col items-end gap-1">
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                      isCritical
                        ? "bg-accent-rose/20 text-rose-300 border border-accent-rose/30 animate-pulse"
                        : "bg-emerald-500/10 text-emerald-300 border border-emerald-500/20"
                    }`}
                  >
                    DIR: {daysRemaining}d {isCritical ? "⚠️ CRITICAL" : "✅ SAFE"}
                  </span>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onTriggerRestock(item.sku);
                    }}
                    disabled={isLoading}
                    className="text-[11px] font-mono text-brand-400 hover:text-brand-300 underline cursor-pointer"
                  >
                    Negotiate RFQ →
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

    </div>
  );
};

function roundToTwo(num: number): number {
  return Math.round((num + Number.EPSILON) * 100) / 100;
}
