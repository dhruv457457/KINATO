"use client";

import React, { useState, useEffect } from "react";
import {
  BusinessProfileType,
  ExecutionMode,
  BuyerContext,
  A2A_Quote,
  A2A_FinalOffer,
  PolicyEvaluation,
  TraceStep,
  ProofReceipt,
} from "@/lib/types";
import {
  fetchInventoryStatus,
  executeNegotiation,
  fetchProofReceipts,
} from "@/lib/api";
import { Navbar } from "@/components/Navbar";
import { MetricsRibbon } from "@/components/MetricsRibbon";
import { ScenariosQuickSelect } from "@/components/ScenariosQuickSelect";
import { InventoryMonitor } from "@/components/InventoryMonitor";
import { A2ATerminal } from "@/components/A2ATerminal";
import { PolicyAndCheckout } from "@/components/PolicyAndCheckout";
import { ProofReceiptModal } from "@/components/ProofReceiptModal";

export default function DashboardPage() {
  // State
  const [currentProfile, setCurrentProfile] = useState<BusinessProfileType>("CLOUD_KITCHEN");
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("ONE_CLICK_APPROVAL");
  const [buyerContext, setBuyerContext] = useState<BuyerContext | null>(null);
  const [selectedSku, setSelectedSku] = useState<string | null>(null);

  // Negotiation & State
  const [rankedQuotes, setRankedQuotes] = useState<A2A_Quote[]>([]);
  const [winningQuote, setWinningQuote] = useState<A2A_Quote | null>(null);
  const [finalOffer, setFinalOffer] = useState<A2A_FinalOffer | null>(null);
  const [policyEvaluation, setPolicyEvaluation] = useState<PolicyEvaluation | null>(null);
  const [traceSteps, setTraceSteps] = useState<TraceStep[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Proofs Modal
  const [proofs, setProofs] = useState<ProofReceipt[]>([]);
  const [isProofsOpen, setIsProofsOpen] = useState(false);

  // Load Inventory Context upon profile change
  useEffect(() => {
    loadProfileData(currentProfile);
  }, [currentProfile]);

  const loadProfileData = async (profile: BusinessProfileType) => {
    try {
      const data = await fetchInventoryStatus(profile);
      setBuyerContext(data.buyer);
      setSelectedSku(null);
      // Reset active negotiation
      setRankedQuotes([]);
      setWinningQuote(null);
      setFinalOffer(null);
      setPolicyEvaluation(null);
      setTraceSteps([]);
    } catch (err) {
      console.error("Failed to load profile inventory:", err);
    }
  };

  const handleTriggerRestock = async (sku?: string) => {
    setIsLoading(true);
    setTraceSteps([]);
    setFinalOffer(null);
    setPolicyEvaluation(null);

    try {
      const targetSku = sku || selectedSku || undefined;
      const res = await executeNegotiation(currentProfile, executionMode, targetSku);

      setRankedQuotes(res.ranked_quotes);
      setWinningQuote(res.winning_quote);
      setFinalOffer(res.final_offer);
      setPolicyEvaluation(res.policy_evaluation);
      setTraceSteps(res.trace_steps);
    } catch (err) {
      console.error("Negotiation failed:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectScenario = async (profile: BusinessProfileType, targetSku?: string) => {
    if (profile !== currentProfile) {
      setCurrentProfile(profile);
      await loadProfileData(profile);
    }
    if (targetSku) setSelectedSku(targetSku);
    await handleTriggerRestock(targetSku);
  };

  const handleOpenProofs = async () => {
    try {
      const list = await fetchProofReceipts();
      setProofs(list);
      setIsProofsOpen(true);
    } catch (err) {
      console.error("Failed to load proof receipts:", err);
    }
  };

  const handlePaymentSuccess = async () => {
    // Refresh inventory and proofs
    await loadProfileData(currentProfile);
  };

  return (
    <div className="flex flex-col min-h-screen bg-background text-zinc-100 pb-12 font-sans">
      
      {/* Top Navbar */}
      <Navbar
        currentProfile={currentProfile}
        onProfileChange={setCurrentProfile}
        executionMode={executionMode}
        onModeChange={setExecutionMode}
        onOpenProofs={handleOpenProofs}
      />

      {/* Commercial Metrics Ribbon */}
      <MetricsRibbon />

      {/* Main Dashboard Container */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-6 flex flex-col gap-6 w-full">
        
        {/* Judge Scenarios Quick Select */}
        <ScenariosQuickSelect
          onSelectScenario={handleSelectScenario}
          isLoading={isLoading}
        />

        {/* 3-Column Core Command Center */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          
          {/* Left Column: Buyer Inventory Monitor (4 Cols) */}
          <div className="lg:col-span-4">
            <InventoryMonitor
              buyerContext={buyerContext}
              selectedSku={selectedSku}
              onSelectSku={setSelectedSku}
              onTriggerRestock={handleTriggerRestock}
              isLoading={isLoading}
            />
          </div>

          {/* Center Column: Live A2A Protocol Stream (4 Cols) */}
          <div className="lg:col-span-4">
            <A2ATerminal
              rankedQuotes={rankedQuotes}
              winningQuote={winningQuote}
              traceSteps={traceSteps}
              isLoading={isLoading}
            />
          </div>

          {/* Right Column: Deterministic Policy Gate & Checkout (4 Cols) */}
          <div className="lg:col-span-4">
            <PolicyAndCheckout
              finalOffer={finalOffer}
              policyEvaluation={policyEvaluation}
              executionMode={executionMode}
              buyerContext={buyerContext}
              onPaymentSuccess={handlePaymentSuccess}
            />
          </div>

        </div>

      </main>

      {/* Proof Receipts Audit Ledger Modal */}
      <ProofReceiptModal
        receipts={proofs}
        isOpen={isProofsOpen}
        onClose={() => setIsProofsOpen(false)}
      />

    </div>
  );
}
