"use client";

import React from "react";
import { ProofReceipt } from "@/lib/types";
import { X, CheckCircle2, Copy, ShieldCheck, FileCode2 } from "lucide-react";

interface ProofReceiptModalProps {
  receipts: ProofReceipt[];
  isOpen: boolean;
  onClose: () => void;
}

export const ProofReceiptModal: React.FC<ProofReceiptModalProps> = ({
  receipts,
  isOpen,
  onClose,
}) => {
  if (!isOpen) return null;

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
      <div className="glass-panel w-full max-w-3xl rounded-2xl p-6 flex flex-col gap-4 max-h-[85vh] overflow-hidden">
        
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/[0.08] pb-4">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-brand-500/10 text-brand-400 border border-brand-500/20">
              <FileCode2 className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white font-sans">
                Cryptographic Proof of Intent &amp; Settlement Ledger
              </h2>
              <p className="text-xs text-zinc-400 font-sans">
                Immutable JSON audit trail verified against Razorpay rails
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-xl text-zinc-400 hover:text-white hover:bg-surface-100 transition-all cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Ledger List */}
        <div className="overflow-y-auto space-y-4 pr-1">
          {receipts.length === 0 ? (
            <div className="text-center py-12 text-zinc-500 text-xs font-mono">
              No proof receipts recorded yet. Complete a transaction above to mint a receipt.
            </div>
          ) : (
            receipts.map((rcpt) => (
              <div
                key={rcpt.receipt_id}
                className="bg-surface-100/90 border border-white/[0.08] rounded-xl p-4 flex flex-col gap-3 font-mono text-xs"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4 text-accent-emerald" />
                    <span className="font-bold text-white text-sm">{rcpt.receipt_id}</span>
                    <span className="text-[10px] text-zinc-400">({rcpt.timestamp})</span>
                  </div>

                  <button
                    onClick={() => copyToClipboard(JSON.stringify(rcpt, null, 2))}
                    className="p-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-zinc-300 hover:text-white transition-all flex items-center gap-1 text-[11px] cursor-pointer"
                  >
                    <Copy className="w-3 h-3" />
                    <span>Copy JSON</span>
                  </button>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-white/[0.04] text-[11px]">
                  <div>
                    <span className="text-zinc-500">Buyer:</span>
                    <p className="text-zinc-200 truncate">{rcpt.business_name}</p>
                  </div>
                  <div>
                    <span className="text-zinc-500">Supplier:</span>
                    <p className="text-zinc-200 truncate">{rcpt.supplier_name}</p>
                  </div>
                  <div>
                    <span className="text-zinc-500">Amount:</span>
                    <p className="text-accent-emerald font-bold">₹{rcpt.total_amount_inr}</p>
                  </div>
                  <div>
                    <span className="text-zinc-500">Razorpay Order:</span>
                    <p className="text-brand-300 truncate">{rcpt.razorpay_order_id}</p>
                  </div>
                </div>

                {/* Raw JSON viewer */}
                <div className="bg-surface-300/90 p-3 rounded-lg border border-white/[0.04] text-[10px] text-zinc-300 overflow-x-auto">
                  <pre>{JSON.stringify(rcpt, null, 2)}</pre>
                </div>
              </div>
            ))
          )}
        </div>

      </div>
    </div>
  );
};
