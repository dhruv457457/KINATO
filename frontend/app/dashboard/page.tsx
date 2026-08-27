"use client";
import React, { useEffect, useState, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const DEMO_PHONE = process.env.NEXT_PUBLIC_DEMO_PHONE || '+91XXXXXXXXXX';
const DEMO_EMAIL = process.env.NEXT_PUBLIC_DEMO_EMAIL || 'demo@example.com';

export default function MerchantCommandCenter() {
  const [activeTab, setActiveTab] = useState<'overview' | 'policy' | 'transcripts' | 'mcp' | 'chat' | 'sdk'>('overview');
  const [state, setState] = useState<any>(null);
  const [unauthenticated, setUnauthenticated] = useState(false);
  const [calling, setCalling] = useState(false);
  const [callStatusMsg, setCallStatusMsg] = useState("");
  
  // Policy State
  const [policy, setPolicy] = useState({
    max_discount_percent: 10.0,
    minimum_margin_percent: 15.0,
    calling_start_hour: 10,
    calling_end_hour: 20,
    voice_persona: "Sarah — Warm & Consultative",
    bundle_discount_percent: 20.0
  });
  const [policySaved, setPolicySaved] = useState(false);

  // Chat State
  const [chatQuestion, setChatQuestion] = useState("");
  const [chatMessages, setChatMessages] = useState<Array<{ role: string; content: string }>>([
    {
      role: "assistant",
      content: "Hi! I'm your Merchant Revenue Intelligence co-pilot. Ask me about recoveries, abandoned checkouts, or objections your customers are raising."
    }
  ]);
  const [chatLoading, setChatLoading] = useState(false);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fetchState = () => {
      fetch(`${API_URL}/api/dashboard/state`, { credentials: 'include' })
        .then(res => {
          if (res.status === 401) {
            setUnauthenticated(true);
            return null;
          }
          return res.json();
        })
        .then(data => {
          if (!data) return;
          setUnauthenticated(false);
          setState(data);
          if (data.policy) {
            setPolicy(prev => ({ ...prev, ...data.policy }));
          }
        })
        .catch(console.error);
    };

    fetchState();
    const interval = setInterval(fetchState, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (activeTab === 'chat') {
      chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, activeTab]);

  const triggerRealCall = async () => {
    setCalling(true);
    setCallStatusMsg(`Dialing ${DEMO_PHONE} via Twilio...`);
    try {
      const res = await fetch(`${API_URL}/api/trigger/test-call`, { method: 'POST', credentials: 'include' });
      const data = await res.json();
      setCallStatusMsg(`Call Live! SID: ${data.call_sid || 'Connected'}`);
    } catch (e) {
      setCallStatusMsg("Call trigger failed");
    }
    setTimeout(() => {
      setCalling(false);
      setCallStatusMsg("");
    }, 6000);
  };

  const savePolicy = async () => {
    try {
      await fetch(`${API_URL}/api/merchant/policy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(policy)
      });
      setPolicySaved(true);
      setTimeout(() => setPolicySaved(false), 2500);
    } catch (e) {
      console.error(e);
    }
  };

  const handleAskQuestion = async (queryText: string) => {
    if (!queryText.trim() || chatLoading) return;

    setChatQuestion("");
    setChatMessages(prev => [...prev, { role: "user", content: queryText }]);
    setChatLoading(true);

    try {
      const res = await fetch(`${API_URL}/api/merchant-intel/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ question: queryText })
      });
      const data = await res.json();
      setChatMessages(prev => [...prev, { role: "assistant", content: data.answer }]);
    } catch (e) {
      setChatMessages(prev => [...prev, { role: "assistant", content: "Unable to connect to Merchant Intelligence service." }]);
    }
    setChatLoading(false);
  };

  const submitChat = (e: React.FormEvent) => {
    e.preventDefault();
    handleAskQuestion(chatQuestion);
  };

  if (unauthenticated) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center space-y-4 font-sans text-slate-700 px-6 text-center">
        <div className="text-lg font-bold text-slate-900">You're not signed in</div>
        <p className="text-sm text-slate-500 max-w-sm">Sign in to your Kinato merchant account to view your dashboard.</p>
        <a href="/login" className="px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm rounded-xl shadow-sm">
          Go to login
        </a>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center space-y-4 font-sans text-slate-700">
        <div className="w-10 h-10 border-4 border-emerald-600 border-t-transparent rounded-full animate-spin"></div>
        <div className="text-sm font-semibold tracking-wide text-slate-900">Connecting to Kinato Core Server...</div>
        <p className="text-xs text-slate-400">Verifying event bus state at {API_URL}</p>
      </div>
    );
  }

  const hasEvent = (type: string) => state.events.some((e: any) => e.event_type === type);

  return (
    <div className="min-h-screen bg-[#f8fafc] text-slate-900 font-sans selection:bg-emerald-100 selection:text-emerald-900">
      
      {/* TOP HEADER */}
      <header className="sticky top-0 z-50 bg-white/95 backdrop-blur-md border-b border-slate-200/80 px-6 py-4">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4">
          
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-emerald-600 flex items-center justify-center text-white font-extrabold text-lg shadow-md shadow-emerald-600/20">
              K
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h1 className="text-lg font-bold tracking-tight text-slate-900">Kinato</h1>
                <span className="text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                  Live • Jiva Lifestyle Store
                </span>
              </div>
              <p className="text-xs text-slate-500 font-normal">Autonomous Revenue Infrastructure for Custom Web Stores</p>
            </div>
          </div>

          {/* ACTION BUTTONS */}
          <div className="flex flex-wrap items-center gap-2.5">
            <button 
              onClick={triggerRealCall} 
              disabled={calling}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs rounded-xl shadow-sm transition-all flex items-center space-x-1.5 disabled:opacity-60"
            >
              <span>📞</span>
              <span>{calling ? (callStatusMsg || "Calling...") : `Call Live (${DEMO_PHONE})`}</span>
            </button>

            <a 
              href={`${API_URL}/pay/chk_demo`}
              target="_blank" 
              rel="noreferrer"
              className="px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 font-semibold text-xs rounded-xl border border-slate-200 shadow-sm transition-all flex items-center space-x-1.5"
            >
              <span>💳</span>
              <span>Open Razorpay Checkout ↗</span>
            </a>
          </div>

        </div>

        {/* TAB NAVIGATION */}
        <div className="max-w-7xl mx-auto mt-4 flex space-x-1 border-t border-slate-100 pt-2 overflow-x-auto text-xs font-medium">
          {[
            { id: 'overview', label: '📊 Overview & Pipeline' },
            { id: 'policy', label: '⚙️ AI Policy & Jobs' },
            { id: 'transcripts', label: '🎧 Recovery Transcripts' },
            { id: 'mcp', label: '🤖 AI Commerce (FastMCP)' },
            { id: 'chat', label: '💬 Merchant Intelligence' },
            { id: 'sdk', label: '🔌 Custom Store SDK' }
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`px-3.5 py-2 rounded-lg transition-all whitespace-nowrap ${activeTab === tab.id ? 'bg-slate-100 text-slate-900 font-bold shadow-xs' : 'text-slate-500 hover:text-slate-800 hover:bg-slate-50'}`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </header>

      {/* MAIN CONTAINER */}
      <main className="max-w-7xl mx-auto p-6 space-y-6">

        {/* ========================================================================= */}
        {/* TAB 1: OVERVIEW & PIPELINE */}
        {/* ========================================================================= */}
        {activeTab === 'overview' && (
          <div className="space-y-6">
            
            {/* HERO KPI CARDS */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              
              <div className="bg-white border border-slate-200/80 rounded-2xl p-5 shadow-xs">
                <div className="flex items-center justify-between text-slate-500 text-xs font-medium mb-1">
                  <span>Recovered Revenue</span>
                  <span className="text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full font-semibold text-[10px]">100% Attributed</span>
                </div>
                <div className="text-3xl font-extrabold text-emerald-600 tracking-tight">
                  ₹{state.hero.kinato_attributed_revenue.toLocaleString()}
                </div>
                <div className="text-xs text-slate-500 mt-2 flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                  <span>{state.hero.completed_recoveries} successful recoveries today</span>
                </div>
              </div>

              <div className="bg-white border border-slate-200/80 rounded-2xl p-5 shadow-xs">
                <div className="flex items-center justify-between text-slate-500 text-xs font-medium mb-1">
                  <span>Revenue at Risk</span>
                  <span className="text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full font-semibold text-[10px]">Unrecovered</span>
                </div>
                <div className="text-3xl font-extrabold text-slate-900 tracking-tight">
                  ₹{state.hero.revenue_at_risk.toLocaleString()}
                </div>
                <div className="text-xs text-slate-500 mt-2">
                  {state.hero.active_recoveries} recovery opportunities detected
                </div>
              </div>

              <div className="bg-white border border-slate-200/80 rounded-2xl p-5 shadow-xs">
                <div className="flex items-center justify-between text-slate-500 text-xs font-medium mb-1">
                  <span>Recovery Win Rate</span>
                </div>
                <div className="text-3xl font-extrabold text-slate-900 tracking-tight">
                  {state.hero.win_rate_percent != null ? `${state.hero.win_rate_percent}%` : '—'}
                </div>
                <div className="text-xs text-slate-500 mt-2">
                  {state.hero.win_rate_percent != null
                    ? `${state.hero.completed_recoveries} of ${state.hero.active_recoveries} recoveries closed`
                    : 'No recoveries attempted yet'}
                </div>
              </div>

              <div className="bg-white border border-slate-200/80 rounded-2xl p-5 shadow-xs">
                <div className="flex items-center justify-between text-slate-500 text-xs font-medium mb-1">
                  <span>AI Agent Latency</span>
                  <span className="text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full font-semibold text-[10px]">ElevenLabs Flash</span>
                </div>
                <div className="text-3xl font-extrabold text-slate-900 tracking-tight">
                  ~580ms
                </div>
                <div className="text-xs text-slate-500 mt-2">
                  Parallel turn-taking active
                </div>
              </div>

            </div>

            {/* MAIN 2-COLUMN VIEW */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              
              {/* LEFT 2 COLS: LIVE RECOVERY PIPELINE */}
              <div className="lg:col-span-2 bg-white border border-slate-200/80 rounded-2xl p-6 shadow-xs space-y-6">
                <div className="flex items-center justify-between border-b border-slate-100 pb-4">
                  <div>
                    <h2 className="text-sm font-bold text-slate-900">Live Recovery Pipeline</h2>
                    <p className="text-xs text-slate-500">Autonomous voice negotiation & instant email delivery</p>
                  </div>
                </div>

                <div className="space-y-4 text-xs font-medium">
                  
                  <div className={`p-3.5 rounded-xl border flex items-center justify-between transition-all ${hasEvent('checkout.abandoned') ? 'bg-emerald-50/70 border-emerald-200 text-emerald-900' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                    <div className="flex items-center space-x-3">
                      <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${hasEvent('checkout.abandoned') ? 'bg-emerald-600 text-white' : 'bg-slate-200 text-slate-500'}`}>1</span>
                      <div>
                        <div className="font-semibold text-slate-900">Checkout Abandonment Detected</div>
                        <div className="text-[11px] text-slate-500">Handcrafted Bamboo Lamp (₹3,499) • Customer: Dhruv</div>
                      </div>
                    </div>
                    <span className="text-[11px] font-semibold">{hasEvent('checkout.abandoned') ? 'Completed' : 'Pending'}</span>
                  </div>

                  <div className={`p-3.5 rounded-xl border flex items-center justify-between transition-all ${hasEvent('recovery.opportunity.created') ? 'bg-emerald-50/70 border-emerald-200 text-emerald-900' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                    <div className="flex items-center space-x-3">
                      <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${hasEvent('recovery.opportunity.created') ? 'bg-emerald-600 text-white' : 'bg-slate-200 text-slate-500'}`}>2</span>
                      <div>
                        <div className="font-semibold text-slate-900">Identity & Consent Gate Verified</div>
                        <div className="text-[11px] text-slate-500">Opt-in confirmed • Discovery Agent synthesized Call Brief</div>
                      </div>
                    </div>
                    <span className="text-[11px] font-semibold">{hasEvent('recovery.opportunity.created') ? 'Verified' : 'Pending'}</span>
                  </div>

                  <div className={`p-3.5 rounded-xl border flex items-center justify-between transition-all ${hasEvent('call.started') ? 'bg-blue-50 border-blue-200 text-blue-900' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                    <div className="flex items-center space-x-3">
                      <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${hasEvent('call.started') ? 'bg-blue-600 text-white animate-pulse' : 'bg-slate-200 text-slate-500'}`}>3</span>
                      <div>
                        <div className="font-semibold text-slate-900">Live Voice Concierge (Twilio + ElevenLabs)</div>
                        <div className="text-[11px] text-slate-500">Sarah negotiating stepped margin ladder (3% ➔ 7% ➔ 10% ➔ Bundle)</div>
                      </div>
                    </div>
                    <span className="text-[11px] font-semibold">{hasEvent('call.started') ? 'In Progress' : 'Pending'}</span>
                  </div>

                  <div className={`p-3.5 rounded-xl border flex items-center justify-between transition-all ${hasEvent('payment_link.created') || hasEvent('email.sent') ? 'bg-emerald-50/70 border-emerald-200 text-emerald-900' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                    <div className="flex items-center space-x-3">
                      <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${hasEvent('payment_link.created') ? 'bg-emerald-600 text-white' : 'bg-slate-200 text-slate-500'}`}>4</span>
                      <div>
                        <div className="font-semibold text-slate-900">Policy Approved & VIP Checkout Email Sent</div>
                        <div className="text-[11px] text-slate-500">Resend delivered 10% discount link (₹3,149) to {DEMO_EMAIL}</div>
                      </div>
                    </div>
                    <span className="text-[11px] font-semibold">{hasEvent('payment_link.created') ? 'Dispatched' : 'Pending'}</span>
                  </div>

                  <div className={`p-3.5 rounded-xl border flex items-center justify-between transition-all ${hasEvent('payment.succeeded') ? 'bg-emerald-100 border-emerald-300 text-emerald-950 font-bold' : 'bg-slate-50 border-slate-200 text-slate-400'}`}>
                    <div className="flex items-center space-x-3">
                      <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${hasEvent('payment.succeeded') ? 'bg-emerald-700 text-white' : 'bg-slate-200 text-slate-500'}`}>5</span>
                      <div>
                        <div className="font-semibold text-slate-900">Razorpay Payment Settled & Attributed</div>
                        <div className="text-[11px] text-slate-600">₹3,149 settled to merchant • 100% matched to recovery_attempt_id</div>
                      </div>
                    </div>
                    <span className="text-[11px] font-semibold">{hasEvent('payment.succeeded') ? '✓ Settled' : 'Pending'}</span>
                  </div>

                </div>
              </div>

              {/* RIGHT 1 COL: LIVE CUSTOMER INTELLIGENCE & EVENT LOG */}
              <div className="space-y-6">
                
                {/* Customer Intel Card */}
                <div className="bg-white border border-slate-200/80 rounded-2xl p-5 shadow-xs">
                  <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-3">
                    <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">Customer Intelligence</h3>
                    <span className="text-[10px] bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full font-semibold">Real-Time</span>
                  </div>
                  
                  {state.latest_intel ? (
                    <div className="space-y-2.5 text-xs">
                      <div className="flex justify-between text-slate-600">
                        <span>Buyer Temperature:</span>
                        <span className="font-bold text-amber-700 bg-amber-50 px-2 py-0.5 rounded">{state.latest_intel.temperature || 'WARM'}</span>
                      </div>
                      <div className="flex justify-between text-slate-600">
                        <span>Identified Barrier:</span>
                        <span className="font-bold text-red-700 bg-red-50 px-2 py-0.5 rounded">{state.latest_intel.barrier || 'PRICE'}</span>
                      </div>
                      <div className="flex justify-between text-slate-600">
                        <span>Next Action:</span>
                        <span className="font-semibold text-blue-700 bg-blue-50 px-2 py-0.5 rounded">{state.latest_intel.next_action || 'request_offer'}</span>
                      </div>
                      <div className="mt-3 p-3 bg-slate-50 rounded-xl border border-slate-200 text-slate-700 text-xs">
                        <span className="text-[10px] text-slate-400 block font-bold uppercase mb-1">Immutable Customer Words:</span>
                        <span className="italic font-sans">"{state.latest_intel.customer_words || state.latest_intel.transcript || 'Too expensive yaar'}"</span>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-6 text-xs text-slate-400 italic">Waiting for customer conversation turn...</div>
                  )}
                </div>

                {/* Event Timeline */}
                <div className="bg-white border border-slate-200/80 rounded-2xl p-5 shadow-xs flex flex-col h-[280px]">
                  <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-2">
                    <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">Event Timeline</h3>
                    <span className="text-[10px] text-slate-400">{state.events.length} events</span>
                  </div>
                  <div className="overflow-y-auto space-y-1.5 flex-grow pr-1 text-xs">
                    {state.events.slice().reverse().map((e: any, i: number) => (
                      <div key={i} className="flex items-center justify-between py-1 border-b border-slate-50 text-[11px]">
                        <span className={`font-mono ${e.event_type.includes('payment') || e.event_type.includes('revenue') ? 'text-emerald-700 font-bold' : e.event_type.includes('rejected') ? 'text-red-600' : 'text-slate-600'}`}>
                          {e.event_type}
                        </span>
                        <span className="text-slate-400 text-[10px] tabular-nums">
                          {new Date(e.timestamp || Date.now()).toLocaleTimeString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

              </div>

            </div>

          </div>
        )}

        {/* ========================================================================= */}
        {/* TAB 2: AI POLICY & JOBS CONFIGURATION */}
        {/* ========================================================================= */}
        {activeTab === 'policy' && (
          <div className="bg-white border border-slate-200/80 rounded-2xl p-8 shadow-xs max-w-4xl mx-auto space-y-8">
            <div className="border-b border-slate-100 pb-4">
              <h2 className="text-lg font-bold text-slate-900">Deterministic Policy & AI Rules</h2>
              <p className="text-xs text-slate-500">Configure allowable discount caps, margin safety thresholds, and AI sales persona.</p>
            </div>

            <div className="space-y-6">
              
              {/* Max Discount Slider */}
              <div className="space-y-2">
                <div className="flex justify-between text-sm font-semibold text-slate-800">
                  <span>Maximum Allowed Single-Item Discount</span>
                  <span className="text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-lg border border-emerald-200">{policy.max_discount_percent}%</span>
                </div>
                <input 
                  type="range" 
                  min="0" 
                  max="25" 
                  step="1"
                  value={policy.max_discount_percent}
                  onChange={e => setPolicy({ ...policy, max_discount_percent: parseFloat(e.target.value) })}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-emerald-600"
                />
                <p className="text-xs text-slate-500">The Call Agent can never exceed this ceiling on a single product.</p>
              </div>

              {/* Minimum Margin Slider */}
              <div className="space-y-2">
                <div className="flex justify-between text-sm font-semibold text-slate-800">
                  <span>Minimum Gross Margin Protection</span>
                  <span className="text-slate-800 bg-slate-100 px-2.5 py-0.5 rounded-lg">{policy.minimum_margin_percent}%</span>
                </div>
                <input 
                  type="range" 
                  min="5" 
                  max="40" 
                  step="1"
                  value={policy.minimum_margin_percent}
                  onChange={e => setPolicy({ ...policy, minimum_margin_percent: parseFloat(e.target.value) })}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-slate-700"
                />
                <p className="text-xs text-slate-500">Policy Engine automatically blocks discounts if gross margin drops below this floor.</p>
              </div>

              {/* Bundle Pivot Discount */}
              <div className="space-y-2">
                <div className="flex justify-between text-sm font-semibold text-slate-800">
                  <span>Artisan Combo Bundle Discount (Lamp + Coasters)</span>
                  <span className="text-blue-700 bg-blue-50 px-2.5 py-0.5 rounded-lg border border-blue-200">{policy.bundle_discount_percent}%</span>
                </div>
                <input 
                  type="range" 
                  min="10" 
                  max="35" 
                  step="1"
                  value={policy.bundle_discount_percent}
                  onChange={e => setPolicy({ ...policy, bundle_discount_percent: parseFloat(e.target.value) })}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
                />
                <p className="text-xs text-slate-500">Unlocked only when customer asks for more than maximum single-item discount.</p>
              </div>

              {/* Voice Persona Selector */}
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-800">Voice Concierge Persona</label>
                <select 
                  value={policy.voice_persona}
                  onChange={e => setPolicy({ ...policy, voice_persona: e.target.value })}
                  className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-sm font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                >
                  <option value="Sarah — Warm & Consultative">Sarah — Charismatic, Warm & Consultative (Assam Craft Expert)</option>
                  <option value="Arjun — Professional & Direct">Arjun — Professional & Direct</option>
                  <option value="Maya — Premium Luxury Concierge">Maya — Premium Luxury Lifestyle Concierge</option>
                </select>
              </div>

              <div className="pt-4 flex items-center justify-between border-t border-slate-100">
                <button
                  onClick={savePolicy}
                  className="px-6 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-xl shadow-sm transition-all"
                >
                  Save Policy Configuration
                </button>
                {policySaved && (
                  <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 px-3 py-1 rounded-full">
                    ✓ Policy Saved & Active on Event Bus
                  </span>
                )}
              </div>

            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* TAB 3: RECOVERY FEED & TRANSCRIPTS */}
        {/* ========================================================================= */}
        {activeTab === 'transcripts' && (
          <div className="bg-white border border-slate-200/80 rounded-2xl p-6 shadow-xs space-y-6">
            <div className="border-b border-slate-100 pb-4 flex justify-between items-center">
              <div>
                <h2 className="text-base font-bold text-slate-900">Customer Recovery Session Feed</h2>
                <p className="text-xs text-slate-500">Auditable turn-by-turn transcripts, sentiment badges, and email verification.</p>
              </div>
              <span className="text-xs font-semibold bg-slate-100 px-3 py-1 rounded-full text-slate-700">1 Session Logged</span>
            </div>

            <div className="border border-slate-200 rounded-xl overflow-hidden">
              <div className="p-4 bg-slate-50 border-b border-slate-200 flex justify-between items-center text-xs">
                <div>
                  <span className="font-bold text-slate-900">Session ID: live_demo</span> • 
                  <span className="text-slate-500 ml-1">Customer: Dhruv ({DEMO_PHONE} / {DEMO_EMAIL})</span>
                </div>
                <span className="bg-emerald-50 text-emerald-700 font-semibold px-2.5 py-0.5 rounded-full text-[11px]">
                  Recovered ₹3,149 via Resend Email
                </span>
              </div>
              
              <div className="p-5 space-y-3.5 text-xs font-sans">
                <div className="flex space-x-3">
                  <span className="font-bold text-emerald-700 shrink-0">Sarah (AI):</span>
                  <p className="text-slate-700">"Hey! Am I speaking with Dhruv?"</p>
                </div>
                <div className="flex space-x-3 bg-slate-50/80 p-2.5 rounded-lg">
                  <span className="font-bold text-slate-900 shrink-0">Dhruv (Customer):</span>
                  <p className="text-slate-800 italic">"Yes speaking, but yaar your lamp is too expensive for me."</p>
                </div>
                <div className="flex space-x-3">
                  <span className="font-bold text-emerald-700 shrink-0">Sarah (AI):</span>
                  <p className="text-slate-700">"I completely hear you, Dhruv! Look, each lamp is hand-woven in Assam for 3 days, but let me do free express shipping and a 3% courtesy discount for ₹3,394."</p>
                </div>
                <div className="flex space-x-3 bg-slate-50/80 p-2.5 rounded-lg">
                  <span className="font-bold text-slate-900 shrink-0">Dhruv (Customer):</span>
                  <p className="text-slate-800 italic">"What is your best final price?"</p>
                </div>
                <div className="flex space-x-3">
                  <span className="font-bold text-emerald-700 shrink-0">Sarah (AI):</span>
                  <p className="text-slate-700">"Between you and me, ₹3,149 is our store manager's 10% bottom floor. If that works, I can lock that in for you right now!"</p>
                </div>
                <div className="flex space-x-3 bg-slate-50/80 p-2.5 rounded-lg">
                  <span className="font-bold text-slate-900 shrink-0">Dhruv (Customer):</span>
                  <p className="text-slate-800 italic">"Okay send me the link on email."</p>
                </div>
                <div className="flex space-x-3 bg-emerald-50 p-2.5 rounded-lg border border-emerald-200 text-emerald-950 font-medium">
                  <span className="font-bold text-emerald-800 shrink-0">Outcome:</span>
                  <p>Policy Engine approved 10% (₹3,149). Resend Email dispatched to {DEMO_EMAIL}. Razorpay payment settled.</p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* TAB 4: AI COMMERCE (FASTMCP) */}
        {/* ========================================================================= */}
        {activeTab === 'mcp' && (
          <div className="space-y-6">
            <div className="bg-white border border-slate-200/80 rounded-2xl p-6 shadow-xs">
              <h2 className="text-base font-bold text-slate-900">External AI Buyer Commerce</h2>
              <p className="text-xs text-slate-500">Machine-readable catalog and purchase-intent tools for AI shopping agents. In development.</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              
              <div className="bg-white border border-slate-200/80 rounded-2xl p-6 shadow-xs space-y-4">
                <h3 className="text-xs font-bold text-purple-900 uppercase tracking-wider">FastMCP Tool Protocol</h3>
                <div className="space-y-2 text-xs text-slate-700">
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 font-mono">
                    <span className="text-purple-700 font-bold">1. search_products(query, max_price)</span>
                    <p className="text-slate-500 text-[11px] mt-1 font-sans">Returns normalized in-stock products without exposing internal DB schemas.</p>
                  </div>
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 font-mono">
                    <span className="text-purple-700 font-bold">2. quote(product_id, quantity)</span>
                    <p className="text-slate-500 text-[11px] mt-1 font-sans">Issues an immutable 10-minute price snapshot with cryptographic quote_id.</p>
                  </div>
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 font-mono">
                    <span className="text-purple-700 font-bold">3. create_purchase_intent(quote_id)</span>
                    <p className="text-slate-500 text-[11px] mt-1 font-sans">Strict 5-point revalidation before generating merchant Razorpay link.</p>
                  </div>
                </div>
              </div>

              <div className="bg-white border border-slate-200/80 rounded-2xl p-6 shadow-xs space-y-4">
                <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">Deterministic Safety Invariants</h3>
                <div className="space-y-2.5 text-xs">
                  <div className="p-3 bg-red-50/70 border border-red-200 rounded-xl">
                    <div className="font-bold text-red-800">🛡️ Price Tamper Protection</div>
                    <p className="text-red-700 text-[11px] mt-0.5">If an AI tries to buy at ₹2,499 after a price shift to ₹3,499, Kinato rejects the intent with reason <code>quote_price_mismatch</code>.</p>
                  </div>
                  <div className="p-3 bg-slate-50 border border-slate-200 rounded-xl">
                    <div className="font-bold text-slate-800">⏳ Quote TTL Expiry Gate</div>
                    <p className="text-slate-600 text-[11px] mt-0.5">Quotes older than 600s are automatically invalidated to prevent stale inventory arbitrage.</p>
                  </div>
                  <div className="p-3 bg-slate-50 border border-slate-200 rounded-xl">
                    <div className="font-bold text-slate-800">📦 Live Stock Verification</div>
                    <p className="text-slate-600 text-[11px] mt-0.5">Ensures inventory count &gt; requested quantity before creating payment orders.</p>
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* TAB 5: MERCHANT INTELLIGENCE CHAT (REFINED CONVERSATIONAL CO-PILOT) */}
        {/* ========================================================================= */}
        {activeTab === 'chat' && (
          <div className="bg-white border border-slate-200/80 rounded-2xl p-6 shadow-xs max-w-3xl mx-auto space-y-5">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center space-x-3">
                <div className="w-8 h-8 rounded-lg bg-emerald-600 flex items-center justify-center text-white font-bold text-sm shadow-xs">
                  K
                </div>
                <div>
                  <h2 className="text-sm font-bold text-slate-900">Merchant Revenue Intelligence Co-Pilot</h2>
                  <p className="text-[11px] text-slate-500">Real-time revenue, objection, and conversion analytics</p>
                </div>
              </div>
              <span className="text-[10px] font-semibold bg-emerald-50 text-emerald-700 px-2.5 py-0.5 rounded-full border border-emerald-200 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                Connected to Event Bus
              </span>
            </div>

            {/* Quick Suggestion Chips */}
            <div className="space-y-1.5">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Suggested Questions:</span>
              <div className="flex flex-wrap gap-1.5">
                {[
                  "Hey",
                  "What are the top 3 customer objections?",
                  "How much revenue did Kinato recover today?",
                  "Why are customers hesitating on the Bamboo Lamp?",
                  "What is our current recovery win rate?"
                ].map((prompt, idx) => (
                  <button
                    key={idx}
                    onClick={() => handleAskQuestion(prompt)}
                    className="px-3 py-1 bg-slate-50 hover:bg-emerald-50 hover:border-emerald-200 border border-slate-200 rounded-full text-slate-700 hover:text-emerald-800 text-[11px] transition-all font-medium"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>

            {/* Message Thread */}
            <div className="border border-slate-200/80 rounded-xl p-4 h-[380px] overflow-y-auto space-y-4 bg-slate-50/40">
              {chatMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[88%] p-4 rounded-2xl shadow-xs ${
                    msg.role === 'user' 
                      ? 'bg-emerald-600 text-white rounded-br-xs font-medium text-xs' 
                      : 'bg-white border border-slate-200 text-slate-800 rounded-bl-xs text-xs space-y-1.5'
                  }`}>
                    {msg.role === 'assistant' ? (
                      <ReactMarkdown 
                        remarkPlugins={[remarkGfm]}
                        components={{
                          ul: ({ node, ...props }) => <ul className="list-disc pl-4 space-y-1 my-1.5 text-slate-700" {...props} />,
                          ol: ({ node, ...props }) => <ol className="list-decimal pl-4 space-y-1 my-1.5 text-slate-700" {...props} />,
                          li: ({ node, ...props }) => <li className="text-xs text-slate-700 leading-normal" {...props} />,
                          strong: ({ node, ...props }) => <strong className="font-bold text-slate-900" {...props} />,
                          p: ({ node, ...props }) => <p className="text-xs leading-relaxed my-1 text-slate-700" {...props} />,
                          h3: ({ node, ...props }) => <h3 className="text-xs font-bold text-slate-900 mt-2 mb-1 uppercase tracking-wider" {...props} />,
                          code: ({ node, ...props }) => <code className="bg-slate-100 text-slate-800 px-1 py-0.5 rounded text-[11px] font-mono" {...props} />
                        }}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    ) : (
                      <span>{msg.content}</span>
                    )}
                  </div>
                </div>
              ))}
              
              {chatLoading && (
                <div className="flex justify-start">
                  <div className="bg-white border border-slate-200 text-slate-500 p-3.5 rounded-2xl text-xs flex items-center space-x-2 shadow-xs">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-bounce"></span>
                    <span className="text-xs font-medium text-slate-600">Analyzing live store metrics & customer transcripts...</span>
                  </div>
                </div>
              )}
              <div ref={chatBottomRef} />
            </div>

            {/* Chat Input */}
            <form onSubmit={submitChat} className="flex gap-2">
              <input 
                type="text"
                value={chatQuestion}
                onChange={e => setChatQuestion(e.target.value)}
                placeholder="Ask about revenue, customer objections, or conversion trends..."
                className="flex-1 p-3 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
              />
              <button
                type="submit"
                disabled={chatLoading || !chatQuestion.trim()}
                className="px-5 py-3 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-xl transition-all disabled:opacity-50 shadow-xs"
              >
                Ask AI
              </button>
            </form>
          </div>
        )}

        {/* ========================================================================= */}
        {/* TAB 6: CUSTOM STORE SDK INTEGRATION */}
        {/* ========================================================================= */}
        {activeTab === 'sdk' && (
          <div className="bg-white border border-slate-200/80 rounded-2xl p-8 shadow-xs max-w-4xl mx-auto space-y-6">
            <div className="border-b border-slate-100 pb-4">
              <h2 className="text-lg font-bold text-slate-900">Custom Web Store Integration SDK</h2>
              <p className="text-xs text-slate-500">Integrate Kinato into any custom Next.js, React, or HTML e-commerce checkout in 2 minutes.</p>
            </div>

            <div className="space-y-4">
              <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">1. Add the Kinato SDK Script</h3>
              <div className="p-4 bg-slate-900 text-slate-100 rounded-xl text-xs font-mono overflow-x-auto">
                <code>{`<!-- Kinato Autonomous Revenue SDK -->
<script src="${API_URL}/sdk/kinato.js" async></script>`}</code>
              </div>

              <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider mt-6">2. Initialize and Track Checkout Lifecycle</h3>
              <div className="p-4 bg-slate-900 text-slate-100 rounded-xl text-xs font-mono overflow-x-auto">
                <code>{`<script>
  // 1. Initialize for your store
  Kinato.init({
    merchantId: "jiva_store",
    apiKey: "pk_live_kinato_89a7f293"
  });

  // 2. Identify the shopper when they enter phone / email
  Kinato.identify({
    customerId: "cust_9981",
    name: "Dhruv",
    phone: "${DEMO_PHONE}",
    email: "${DEMO_EMAIL}"
  });

  // 3. Track checkout start
  Kinato.track("checkout.started", {
    cartId: "cart_8829",
    amount: 3499,
    currency: "INR",
    items: [
      { id: "sku_lamp_01", name: "Handcrafted Bamboo Lamp", price: 3499 }
    ]
  });

  // If the user closes the tab or remains inactive for 10 minutes,
  // Kinato automatically triggers the autonomous recovery pipeline!
</script>`}</code>
              </div>
            </div>
          </div>
        )}

      </main>

    </div>
  );
}
