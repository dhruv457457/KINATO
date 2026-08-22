"use client";

import React from "react";
import { TrendingUp, Recycle, Clock, ShieldAlert } from "lucide-react";

export const MetricsRibbon: React.FC = () => {
  return (
    <div className="w-full bg-surface-200/60 border-y border-white/[0.06] backdrop-blur-md py-2.5 px-4 sm:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-4 text-xs font-sans">
        
        {/* Metric 1: AOV Lift */}
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-brand-500/10 border border-brand-500/20 text-brand-400">
            <TrendingUp className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm">+17.8%</span>
              <span className="text-[10px] text-accent-emerald font-mono bg-emerald-500/10 px-1 rounded">AOV Lift</span>
            </div>
            <p className="text-[11px] text-zinc-400">Merchant dynamic bundle yield</p>
          </div>
        </div>

        {/* Metric 2: Waste Recovery Rate */}
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-accent-emerald">
            <Recycle className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm">76.5%</span>
              <span className="text-[10px] text-emerald-400 font-mono bg-emerald-500/10 px-1 rounded">Recovered</span>
            </div>
            <p className="text-[11px] text-zinc-400">FIFO aging batches cleared</p>
          </div>
        </div>

        {/* Metric 3: Restock Velocity */}
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-accent-cyan/10 border border-accent-cyan/20 text-accent-cyan">
            <Clock className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm">6.2s</span>
              <span className="text-[10px] text-cyan-400 font-mono bg-cyan-500/10 px-1 rounded">435x Faster</span>
            </div>
            <p className="text-[11px] text-zinc-400">Vs 45m manual phone calls</p>
          </div>
        </div>

        {/* Metric 4: Floor Price Shield Invariant */}
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-accent-amber/10 border border-accent-amber/20 text-accent-amber">
            <ShieldAlert className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm font-mono">SP ≥ CP×1.15</span>
              <span className="text-[10px] text-amber-400 font-mono bg-amber-500/10 px-1 rounded">Locked</span>
            </div>
            <p className="text-[11px] text-zinc-400">Zero merchant loss guarantee</p>
          </div>
        </div>

      </div>
    </div>
  );
};
