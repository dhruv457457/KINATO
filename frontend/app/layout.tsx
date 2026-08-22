import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kinato — Autonomous B2B Restock & A2A Commerce Protocol",
  description: "Agent-to-Agent reverse bidding with deterministic safety guardrails and Razorpay settlement rails.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-background text-zinc-100 min-h-screen antialiased selection:bg-brand-500/30 selection:text-brand-100">
        {/* Subtle Ambient Background Mesh Glows */}
        <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
          <div className="absolute -top-40 left-1/4 w-[600px] h-[600px] bg-brand-600/10 rounded-full blur-[140px] animate-pulse-slow" />
          <div className="absolute top-1/2 -right-40 w-[500px] h-[500px] bg-accent-emerald/8 rounded-full blur-[160px]" />
          <div className="absolute -bottom-40 left-1/3 w-[500px] h-[500px] bg-accent-cyan/8 rounded-full blur-[140px]" />
        </div>

        {/* Official Razorpay Standard Web Checkout SDK */}
        <Script
          src="https://checkout.razorpay.com/v1/checkout.js"
          strategy="beforeInteractive"
        />

        <div className="relative z-10 flex flex-col min-h-screen">
          {children}
        </div>
      </body>
    </html>
  );
}
