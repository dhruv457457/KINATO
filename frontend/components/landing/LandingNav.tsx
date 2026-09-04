"use client";

import Link from "next/link";

export function LandingNav() {
  return (
    <nav className="fixed top-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2">
      <Link
        href="/"
        className="flex items-center gap-2 rounded-full bg-white/95 backdrop-blur px-5 py-2.5 text-sm font-semibold text-dark shadow-floating"
      >
        <span aria-hidden>🌿</span> Kinato
      </Link>
      <Link
        href="/#how-it-works"
        className="rounded-full bg-white/90 backdrop-blur px-5 py-2.5 text-sm font-medium text-dark-200 hover:text-dark transition-colors shadow-card"
      >
        How it works
      </Link>
      <Link
        href="/dashboard"
        className="rounded-full bg-white/90 backdrop-blur px-5 py-2.5 text-sm font-medium text-dark-200 hover:text-dark transition-colors shadow-card"
      >
        Demo
      </Link>
      <Link
        href="/login"
        className="rounded-full bg-dark text-background px-5 py-2.5 text-sm font-medium hover:bg-dark-100 transition-colors shadow-card"
      >
        Login
      </Link>
    </nav>
  );
}
