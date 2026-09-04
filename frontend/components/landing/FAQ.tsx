"use client";

import { useState } from "react";

const FAQS = [
  {
    q: "What can Kinato help me with?",
    a: "Recovering abandoned checkouts and failed payments. An AI agent phones the customer, works out what actually stopped them, and negotiates within limits you set — then sends a real Razorpay payment link if, and only if, your policy approves one.",
  },
  {
    q: "Do I need to change my checkout or Razorpay setup?",
    a: "No. Kinato sits alongside your existing store and Razorpay account. You keep your products, customers, and payment rails exactly as they are.",
  },
  {
    q: "Can the AI give away discounts on its own?",
    a: "No. The AI can request an offer during a conversation, but a deterministic policy engine — configured by you — is the only thing that approves, caps, or denies it.",
  },
  {
    q: "What happens if a customer says stop calling?",
    a: "Consent is revoked immediately and no further outreach happens on that channel. This is enforced in code, not left to the AI's judgment.",
  },
  {
    q: "How long does setup take?",
    a: "For this demo build, minutes — Kinato connects via a lightweight SDK snippet and your existing Razorpay test-mode keys.",
  },
];

export function FAQ() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section className="bg-background px-6 py-24 sm:py-32">
      <div className="mx-auto max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-wider text-brand-500">FAQ</p>
        <h2 className="mt-3 font-serif text-3xl sm:text-4xl text-dark">Good questions</h2>

        <div className="mt-10 divide-y divide-surface-300">
          {FAQS.map((item, i) => (
            <div key={item.q} className="py-5">
              <button
                onClick={() => setOpen(open === i ? null : i)}
                className="flex w-full items-center justify-between text-left"
              >
                <span className="font-medium text-dark">{item.q}</span>
                <span className="text-dark-200 text-xl leading-none">{open === i ? "–" : "+"}</span>
              </button>
              {open === i && (
                <p className="mt-3 text-sm leading-relaxed text-dark-200 max-w-2xl">{item.a}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
