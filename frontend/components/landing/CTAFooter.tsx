"use client";

import Link from "next/link";
import { useState, FormEvent } from "react";

export function CTAFooter() {
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
    <footer className="bg-brand-500 text-background px-6 py-24 sm:py-32">
      <div className="mx-auto max-w-3xl text-center">
        <h2 className="font-serif text-3xl sm:text-5xl leading-tight">
          The sale is still there.
          <br />
          <span className="italic">Somebody just has to ask.</span>
        </h2>

        <form
          onSubmit={handleSubmit}
          className="mt-10 mx-auto flex w-full max-w-md items-center gap-2 rounded-full bg-white/95 p-1.5 shadow-floating"
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

        <div className="mt-16 flex items-center justify-center gap-8 text-sm text-background/70">
          <Link href="/dashboard" className="hover:text-background transition-colors">
            Live Demo
          </Link>
          <a href="#how-it-works" className="hover:text-background transition-colors">
            How it works
          </a>
          <a
            href="https://razorpay.com/buildathon/"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-background transition-colors"
          >
            Razorpay AI Buildathon
          </a>
        </div>
        <p className="mt-8 text-xs text-background/50">© 2026 Kinato. Built for the Razorpay AI Buildathon.</p>
      </div>
    </footer>
  );
}
