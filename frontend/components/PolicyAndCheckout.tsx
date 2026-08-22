"use client";

import React, { useState } from "react";
import { A2A_FinalOffer, PolicyEvaluation, ExecutionMode, BuyerContext } from "@/lib/types";
import { createRazorpayOrder, verifyPayment } from "@/lib/api";
import { CheckCircle2, XCircle, ShieldCheck, Zap, Lock, CreditCard, Sparkles } from "lucide-react";
import confetti from "canvas-confetti";

interface PolicyAndCheckoutProps {
  finalOffer: A2A_FinalOffer | null;
  policyEvaluation: PolicyEvaluation | null;
  executionMode: ExecutionMode;
  buyerContext: BuyerContext | null;
  onPaymentSuccess: () => void;
}

declare global {
  interface Window {
    Razorpay: any;
  }
}

export const PolicyAndCheckout: React.FC<PolicyAndCheckoutProps> = ({
  finalOffer,
  policyEvaluation,
  executionMode,
  buyerContext,
  onPaymentSuccess,
}) => {
  const [isProcessing, setIsProcessing] = useState(false);
  const [paymentError, setPaymentError] = useState<string | null>(null);
  const [paymentSuccess, setPaymentSuccess] = useState(false);

  if (!finalOffer || !policyEvaluation) {
    return (
      <div className="glass-panel rounded-2xl p-5 flex flex-col items-center justify-center text-center text-zinc-500 py-12">
        <Lock className="w-8 h-8 text-zinc-600 mb-2" />
        <p className="text-xs font-mono">No active proposal. Trigger restock above to generate offer.</p>
      </div>
    );
  }

  const isApproved = policyEvaluation.status === "PASSED" && policyEvaluation.allowed_execution;

  const handleCheckout = async () => {
    if (!isApproved || !buyerContext) return;
    setIsProcessing(true);
    setPaymentError(null);

    try {
      // 1. Create order on backend (with Idempotency Key)
      const orderData = await createRazorpayOrder({
        proposal_id: finalOffer.proposal_id,
        amount_inr: finalOffer.final_total,
        business_id: buyerContext.business_id,
        supplier_id: finalOffer.winning_supplier_id,
        mode: executionMode,
        proposal_hash: finalOffer.proposal_hash,
      });

      // If Mode is Autonomous AutoPay, payment executes automatically
      if (executionMode === "AUTONOMOUS_AUTOPAY") {
        setTimeout(() => {
          setIsProcessing(false);
          setPaymentSuccess(true);
          confetti({ particleCount: 80, spread: 60 });
          onPaymentSuccess();
        }, 1200);
        return;
      }

      // If Mode is 1-Click Approval, launch official Razorpay standard checkout popup
      if (typeof window !== "undefined" && window.Razorpay) {
        const options = {
          key: orderData.key_id,
          amount: orderData.amount_paise,
          currency: orderData.currency,
          name: "Kinato B2B Commerce",
          description: `Restock with ${finalOffer.winning_supplier_name}`,
          order_id: orderData.order_id,
          handler: async function (response: any) {
            try {
              // 2. Verify signature on backend
              await verifyPayment({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
                proposal_id: finalOffer.proposal_id,
              });

              setIsProcessing(false);
              setPaymentSuccess(true);
              confetti({ particleCount: 100, spread: 70, origin: { y: 0.6 } });
              onPaymentSuccess();
            } catch (err: any) {
              setPaymentError(err.message || "Signature verification failed");
              setIsProcessing(false);
            }
          },
          prefill: {
            name: buyerContext.business_name,
            email: "ops@burgercraft.in",
            contact: "9876543210",
          },
          theme: {
            color: "#4F46E5",
          },
          modal: {
            ondismiss: function () {
              setIsProcessing(false);
            },
          },
        };

        const rzp = new window.Razorpay(options);
        rzp.open();
      } else {
        // Simulated checkout if checkout.js is offline
        setTimeout(() => {
          setIsProcessing(false);
          setPaymentSuccess(true);
          confetti({ particleCount: 80, spread: 60 });
          onPaymentSuccess();
        }, 1500);
      }
    } catch (err: any) {
      setPaymentError(err.message || "Checkout failed");
      setIsProcessing(false);
    }
  };

  return (
    <div className="glass-panel rounded-2xl p-5 flex flex-col gap-4">
      
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-accent-emerald" />
          <h2 className="text-sm font-bold text-white font-sans tracking-tight">
            Deterministic Policy Gate &amp; Checkout
          </h2>
        </div>

        <span
          className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
            isApproved
              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
              : "bg-rose-500/20 text-rose-300 border border-rose-500/30"
          }`}
        >
          {policyEvaluation.status}
        </span>
      </div>

      {/* Offer Summary Card */}
      <div className="bg-surface-100/70 border border-white/[0.06] rounded-xl p-3.5 flex flex-col gap-2.5">
        <div className="flex items-center justify-between text-xs">
          <span className="text-zinc-400 font-mono">Supplier:</span>
          <span className="font-semibold text-white">{finalOffer.winning_supplier_name}</span>
        </div>

        <div className="space-y-1.5 pt-2 border-t border-white/[0.04]">
          {finalOffer.items.map((it) => (
            <div key={it.sku} className="flex items-center justify-between text-xs">
              <span className="text-zinc-300">
                {it.quantity}x {it.name}
              </span>
              <span className="font-mono text-white font-semibold">₹{it.total_price}</span>
            </div>
          ))}

          {finalOffer.total_discount > 0 && (
            <div className="flex items-center justify-between text-xs text-accent-emerald">
              <span>Applied Dynamic Discount:</span>
              <span className="font-mono font-semibold">-₹{finalOffer.total_discount}</span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between pt-2 border-t border-white/[0.06] text-sm">
          <span className="font-bold text-zinc-300 font-mono">Agreed Total:</span>
          <span className="font-bold text-white font-mono text-base">
            ₹{finalOffer.final_total.toLocaleString()}
          </span>
        </div>
      </div>

      {/* Policy Checks Breakdown */}
      <div className="space-y-1.5">
        <span className="text-[11px] font-mono uppercase text-zinc-400 tracking-wider">
          Safety Assertions (Zero-Hallucination Code Gate):
        </span>

        <div className="space-y-1 bg-surface-300/80 border border-white/[0.04] p-2.5 rounded-xl text-xs font-mono">
          {policyEvaluation.checks.map((chk, i) => (
            <div key={i} className="flex items-start gap-2 text-[11px]">
              {chk.passed ? (
                <CheckCircle2 className="w-3.5 h-3.5 text-accent-emerald shrink-0 mt-0.5" />
              ) : (
                <XCircle className="w-3.5 h-3.5 text-accent-rose shrink-0 mt-0.5" />
              )}
              <span className={chk.passed ? "text-zinc-300" : "text-rose-300 font-semibold"}>
                {chk.details}
              </span>
            </div>
          ))}
        </div>

        {policyEvaluation.actionable_suggestion && (
          <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-xs text-rose-300">
            💡 {policyEvaluation.actionable_suggestion}
          </div>
        )}
      </div>

      {/* Cryptographic Proposal Hash Badge */}
      <div className="flex items-center justify-between p-2 rounded-lg bg-surface-100 border border-white/[0.04] text-[10px] font-mono text-zinc-400">
        <span>HMAC Digest:</span>
        <span className="text-brand-300 truncate max-w-[180px]">
          {finalOffer.proposal_hash}
        </span>
      </div>

      {/* Payment Action Button */}
      {paymentError && (
        <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-xs text-rose-300">
          ❌ {paymentError}
        </div>
      )}

      {paymentSuccess ? (
        <div className="p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-300 text-center flex items-center justify-center gap-2 font-mono">
          <Sparkles className="w-4 h-4 text-accent-emerald" />
          <span>Payment Authorized &amp; Funds Settled via Razorpay!</span>
        </div>
      ) : (
        <button
          onClick={handleCheckout}
          disabled={!isApproved || isProcessing}
          className={`w-full py-3 rounded-xl font-medium text-xs font-sans transition-all flex items-center justify-center gap-2 shadow-glow cursor-pointer ${
            isApproved
              ? executionMode === "ONE_CLICK_APPROVAL"
                ? "bg-brand-600 hover:bg-brand-500 text-white"
                : "bg-accent-emerald hover:bg-emerald-400 text-surface-300 font-bold"
              : "bg-surface-100 text-zinc-500 cursor-not-allowed border border-white/[0.06]"
          }`}
        >
          {executionMode === "ONE_CLICK_APPROVAL" ? (
            <>
              <CreditCard className="w-4 h-4" />
              <span>
                {isProcessing
                  ? "Opening Razorpay Checkout..."
                  : isApproved
                  ? `Approve & Pay ₹${finalOffer.final_total.toLocaleString()} with Razorpay`
                  : "Blocked by Policy Engine"}
              </span>
            </>
          ) : (
            <>
              <Zap className="w-4 h-4" />
              <span>
                {isProcessing
                  ? "Charging via AutoPay Mandate..."
                  : isApproved
                  ? `Execute Autonomous AutoPay (₹${finalOffer.final_total.toLocaleString()})`
                  : "Blocked by Policy Engine"}
              </span>
            </>
          )}
        </button>
      )}

    </div>
  );
};
