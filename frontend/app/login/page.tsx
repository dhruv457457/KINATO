"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";

import { stepPath } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const body = mode === "login" ? { email, password } : { name, email, password };
      const res = await fetch(`${API_URL}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || "Something went wrong.");
        setLoading(false);
        return;
      }
      router.push(stepPath(data.merchant.onboarding_step));
    } catch {
      setError("Could not reach the server. Is the backend running?");
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen w-full flex items-center justify-center bg-background px-6">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="font-serif text-3xl text-dark">
            <span aria-hidden>🌿</span> Kinato
          </h1>
          <p className="mt-2 text-sm text-dark-200">
            {mode === "login" ? "Sign in to your merchant dashboard" : "Create your merchant account"}
          </p>
        </div>

        <div className="glass-card p-6">
          <div className="flex rounded-full bg-surface-100 p-1 mb-6 text-sm font-medium">
            <button
              type="button"
              onClick={() => setMode("login")}
              className={`flex-1 rounded-full py-2 transition-colors ${mode === "login" ? "bg-white shadow-card text-dark" : "text-dark-200"}`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => setMode("signup")}
              className={`flex-1 rounded-full py-2 transition-colors ${mode === "signup" ? "bg-white shadow-card text-dark" : "text-dark-200"}`}
            >
              Sign up
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "signup" && (
              <div>
                <label className="block text-xs font-medium text-dark-200 mb-1">Store / your name</label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded-xl border border-surface-300 px-3 py-2.5 text-sm outline-none focus:border-brand-500"
                  placeholder="Jiva Lifestyle"
                />
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-dark-200 mb-1">Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-xl border border-surface-300 px-3 py-2.5 text-sm outline-none focus:border-brand-500"
                placeholder="you@yourstore.com"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-dark-200 mb-1">Password</label>
              <input
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-xl border border-surface-300 px-3 py-2.5 text-sm outline-none focus:border-brand-500"
                placeholder="At least 8 characters"
              />
            </div>

            {error && <p className="text-xs text-rose-600">{error}</p>}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-semibold text-sm py-2.5 transition-colors disabled:opacity-60"
            >
              {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
