"use client";

import { useState, FormEvent } from "react";

export function Hero() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setStatus("sending");
    try {
      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      setStatus(res.ok ? "sent" : "error");
    } catch {
      setStatus("error");
    }
  }

  return (
    <section className="relative min-h-screen w-full overflow-hidden bg-dark flex flex-col justify-end">
      {/* Background video. Drop your clip at frontend/public/videos/hero.mp4 (muted, looping, ~10s+). */}
      <video
        className="absolute inset-0 w-full h-full object-cover"
        autoPlay
        muted
        loop
        playsInline
      >
        <source src="/videos/hero.mp4" type="video/mp4" />
      </video>
      <div className="absolute inset-0 bg-gradient-to-b from-dark/60 via-dark/30 to-dark/80" />

      <div className="relative z-10 flex flex-col items-center text-center px-6 pb-20 pt-40">
        <h1 className="font-serif text-4xl sm:text-6xl md:text-7xl text-white leading-[1.05] max-w-4xl">
          You&apos;re a merchant.
          <br />
          <span className="italic font-medium">Not an ad budget.</span>
        </h1>

        <div className="mt-8 flex flex-col items-center gap-3 text-white/90">
          <p className="text-base sm:text-lg font-medium">Let Kinato recover the sale</p>
          <div className="flex items-center gap-2 rounded-full bg-white/10 backdrop-blur px-4 py-2 text-sm border border-white/15 animate-fade-in-up">
            <span aria-hidden>📧</span>
            Emailed Rahul an 8% recovery offer &mdash; ₹3,219 recovered
            <span className="text-[10px] uppercase tracking-wider text-white/45 ml-1">example</span>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="mt-10 flex w-full max-w-md items-center gap-2 rounded-full bg-white/95 backdrop-blur p-1.5 shadow-floating"
        >
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Your email"
            className="flex-1 bg-transparent px-4 py-2.5 text-sm text-dark placeholder:text-dark-200/60 outline-none"
          />
          <button
            type="submit"
            disabled={status === "sending"}
            className="rounded-full bg-dark text-background px-5 py-2.5 text-sm font-semibold hover:bg-dark-100 transition-colors disabled:opacity-60"
          >
            {status === "sent" ? "Thanks!" : status === "sending" ? "Sending…" : "Get started"}
          </button>
        </form>
        {status === "error" && (
          <p className="mt-2 text-xs text-rose-200">Something went wrong — try again in a moment.</p>
        )}
      </div>
    </section>
  );
}
