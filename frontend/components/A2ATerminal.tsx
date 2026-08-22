"use client";

import React from "react";
import { A2A_Quote, TraceStep } from "@/lib/types";
import { Award, CheckCircle2, ChevronRight, Clock, MapPin, ShieldCheck, Sparkles, Terminal } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

interface A2ATerminalProps {
  rankedQuotes: A2A_Quote[];
  winningQuote: A2A_Quote | null;
  traceSteps: TraceStep[];
  isLoading: boolean;
}

export const A2ATerminal: React.FC<A2ATerminalProps> = ({
  rankedQuotes,
  winningQuote,
  traceSteps,
  isLoading,
}) => {
  return (
    <div className="glass-panel rounded-2xl p-5 flex flex-col gap-4">
      
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-accent-cyan" />
          <h2 className="text-sm font-bold text-white font-sans tracking-tight">
            Live A2A Protocol Stream & Bidding War
          </h2>
        </div>

        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-accent-emerald animate-ping" />
          <span className="text-[11px] font-mono text-zinc-400">Reverse RFQ Active</span>
        </div>
      </div>

      {/* Competing Supplier Cards Grid */}
      {rankedQuotes.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-[11px] font-mono uppercase text-zinc-400 tracking-wider">
            Competing Wholesaler Quotes (5-Factor Ranked):
          </span>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {rankedQuotes.map((q, idx) => {
              const isWinner = winningQuote?.quote_id === q.quote_id;
              const hasAgingBundle = q.items.length > 1;

              return (
                <motion.div
                  key={q.quote_id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.1 }}
                  className={`p-3.5 rounded-xl border relative flex flex-col justify-between transition-all ${
                    isWinner
                      ? "border-accent-emerald/80 bg-emerald-950/20 shadow-glow"
                      : "border-white/[0.06] bg-surface-100/60"
                  }`}
                >
                  {/* Winning Badge */}
                  {isWinner && (
                    <div className="absolute -top-2.5 right-3 bg-accent-emerald text-surface-300 font-mono text-[9px] font-bold px-2 py-0.5 rounded-full flex items-center gap-1 shadow-sm">
                      <Award className="w-3 h-3" />
                      <span>WINNING BID (Score: {q.utility_score})</span>
                    </div>
                  )}

                  <div>
                    <div className="flex items-start justify-between">
                      <h4 className="text-xs font-bold text-white font-sans">
                        {q.supplier_name}
                      </h4>
                    </div>

                    <div className="flex items-center gap-2 text-[11px] text-zinc-400 font-mono mt-1">
                      <span className="flex items-center gap-0.5">
                        <MapPin className="w-3 h-3 text-zinc-500" /> {q.distance_km}km
                      </span>
                      <span>·</span>
                      <span className="flex items-center gap-0.5">
                        <Clock className="w-3 h-3 text-zinc-500" /> {q.delivery_sla_hours}h SLA
                      </span>
                      <span>·</span>
                      <span className="flex items-center gap-0.5 text-accent-emerald">
                        <ShieldCheck className="w-3 h-3" /> {(q.trust_score * 100).toFixed(0)}%
                      </span>
                    </div>

                    {/* Items in quote */}
                    <div className="mt-2.5 pt-2 border-t border-white/[0.04] space-y-1">
                      {q.items.map((it) => (
                        <div key={it.sku} className="flex items-center justify-between text-[11px]">
                          <span className="text-zinc-300 truncate max-w-[140px]">
                            {it.quantity}x {it.name}
                          </span>
                          <span className="font-mono text-white">₹{it.total_price}</span>
                        </div>
                      ))}

                      {hasAgingBundle && (
                        <div className="mt-1.5 p-1.5 rounded-lg bg-brand-500/10 border border-brand-500/20 text-[10px] text-brand-300 flex items-center gap-1">
                          <Sparkles className="w-3 h-3 shrink-0" />
                          <span>FIFO bundle: -₹{q.total_discount} discount</span>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="mt-3 pt-2 border-t border-white/[0.06] flex items-center justify-between">
                    <span className="text-[11px] text-zinc-400 font-mono">Final Total:</span>
                    <span className={`text-sm font-bold font-mono ${isWinner ? "text-accent-emerald" : "text-white"}`}>
                      ₹{q.final_total.toLocaleString()}
                    </span>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      )}

      {/* Live Trace Steps Terminal */}
      <div className="flex flex-col gap-2">
        <span className="text-[11px] font-mono uppercase text-zinc-400 tracking-wider">
          Multi-Agent Reasoning & Negotiation Log:
        </span>

        <div className="bg-surface-300/90 border border-white/[0.06] rounded-xl p-3.5 max-h-56 overflow-y-auto space-y-2 font-mono text-xs">
          {traceSteps.length === 0 ? (
            <div className="text-zinc-500 text-center py-6">
              Click &quot;Auto-Restock All&quot; or select an item to watch Buyer &amp; Supplier agents negotiate live.
            </div>
          ) : (
            <AnimatePresence>
              {traceSteps.map((step, idx) => (
                <motion.div
                  key={idx}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex items-start gap-2 text-[11px]"
                >
                  <ChevronRight className="w-3.5 h-3.5 text-brand-400 mt-0.5 shrink-0" />
                  <div>
                    <span className="text-brand-300 font-semibold">[{step.actor}]</span>{" "}
                    <span className="text-zinc-300">{step.message}</span>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          )}

          {isLoading && (
            <div className="flex items-center gap-2 text-[11px] text-brand-400 animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-brand-400" />
              <span>Agents negotiating over A2A protocol...</span>
            </div>
          )}
        </div>
      </div>

    </div>
  );
};
