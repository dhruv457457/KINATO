"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { stepPath } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const RULE = "#D5D0BC";
const RULE_SOFT = "#EAE6D5";
const INK = "#1B1A17";
const INK_MUTED = "#8A8678";
const NEGATIVE = "#A3372A";

/** A text field in the same editorial language as the rest of the product:
 *  a hairline rule, no radius, the label above rather than floating inside
 *  it. The password variant carries its own reveal toggle - typing a
 *  password you cannot see, twice, on a phone, is where sign-ups are lost,
 *  and a masked field is protecting against a shoulder that usually isn't
 *  there. */
function Field({
  label,
  hint,
  type = "text",
  value,
  onChange,
  placeholder,
  autoComplete,
  required = true,
  minLength,
  autoFocus = false,
  revealable = false,
}: {
  label: string;
  hint?: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoComplete?: string;
  required?: boolean;
  minLength?: number;
  autoFocus?: boolean;
  revealable?: boolean;
}) {
  const [revealed, setRevealed] = useState(false);
  const inputType = revealable && revealed ? "text" : type;

  return (
    <div className="mb-5">
      <label className="block text-[11px] font-semibold uppercase tracking-wider mb-2" style={{ color: INK_MUTED }}>
        {label}
      </label>
      <div className="flex items-stretch border" style={{ borderColor: RULE }}>
        <input
          type={inputType}
          required={required}
          minLength={minLength}
          value={value}
          autoFocus={autoFocus}
          autoComplete={autoComplete}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="flex-1 min-w-0 px-3 py-3 text-[14px] outline-none bg-transparent"
          style={{ color: INK }}
        />
        {revealable && (
          <button
            type="button"
            onClick={() => setRevealed((v) => !v)}
            // Not in the tab order: someone tabbing email -> password ->
            // submit should not land on a reveal button on the way.
            tabIndex={-1}
            aria-label={revealed ? "Hide password" : "Show password"}
            className="px-3 text-[11px] font-semibold uppercase tracking-wider border-l shrink-0 transition-colors"
            style={{ borderColor: RULE, color: INK_MUTED }}
          >
            {revealed ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {hint && (
        <div className="text-[11px] mt-1.5" style={{ color: INK_MUTED }}>
          {hint}
        </div>
      )}
    </div>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function switchMode(next: "login" | "signup") {
    setMode(next);
    // A stale "incorrect password" sitting above a fresh sign-up form is
    // an error message about a form that no longer exists.
    setError("");
  }

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
    <main className="min-h-screen w-full flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-[420px]">
        <div className="mb-9">
          <Link href="/" className="font-serif text-[22px] font-semibold" style={{ color: INK }}>
            Kinato
          </Link>
          <h1 className="font-serif font-semibold text-[30px] leading-tight mt-6" style={{ color: INK }}>
            {mode === "login" ? "Sign in" : "Create your account"}
          </h1>
          <p className="text-[13px] mt-2 leading-relaxed" style={{ color: INK_MUTED }}>
            {mode === "login"
              ? "Your recovery dashboard, your policies, your customers."
              : "Takes a minute. You'll connect Razorpay next."}
          </p>
        </div>

        {/* Two modes, one form. A tab pair rather than two routes, because
            the fields are nearly identical and a full page navigation to
            add one field is a navigation nobody asked for. */}
        <div className="flex border-b mb-7" style={{ borderColor: RULE }}>
          {(["login", "signup"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => switchMode(m)}
              className="px-1 mr-7 pb-2.5 text-[13px] font-medium transition-colors"
              style={{
                color: mode === m ? INK : INK_MUTED,
                borderBottom: `2px solid ${mode === m ? INK : "transparent"}`,
                marginBottom: -1,
              }}
            >
              {m === "login" ? "Sign in" : "Sign up"}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit}>
          {mode === "signup" && (
            <Field
              label="Store name"
              value={name}
              onChange={setName}
              placeholder="Loomwork"
              autoComplete="organization"
              autoFocus
            />
          )}
          <Field
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            placeholder="you@yourstore.com"
            autoComplete="email"
            autoFocus={mode === "login"}
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            placeholder={mode === "signup" ? "At least 8 characters" : ""}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={8}
            hint={mode === "signup" ? "At least 8 characters." : undefined}
            revealable
          />

          {error && (
            <p className="text-[12px] mb-4 leading-relaxed anim-fade" style={{ color: NEGATIVE }}>
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 text-[13px] font-semibold uppercase tracking-wider transition-opacity disabled:opacity-50"
            style={{ background: INK, color: "#FFFFFF", borderRadius: 0 }}
          >
            {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <div className="mt-7 pt-5 border-t text-[12px]" style={{ borderColor: RULE_SOFT, color: INK_MUTED }}>
          {mode === "login" ? (
            <>
              No account yet?{" "}
              <button type="button" onClick={() => switchMode("signup")} className="underline" style={{ color: INK }}>
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button type="button" onClick={() => switchMode("login")} className="underline" style={{ color: INK }}>
                Sign in
              </button>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
