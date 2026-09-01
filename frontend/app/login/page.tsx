"use client";

import { useState, useEffect, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { stepPath } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const RULE = "#D5D0BC";
const RULE_SOFT = "#EAE6D5";
const INK = "#1B1A17";
const INK_MUTED = "#8A8678";
const NEGATIVE = "#A3372A";

/** What came back on ?error=. Plain sentences, because the person is
 *  standing at a login form and "bad_state" tells them nothing. */
const GOOGLE_ERRORS: Record<string, string> = {
  bad_state: "That sign-in link expired or did not match. Please try again.",
  no_code: "Google did not send anything back. Please try again.",
  no_token: "Google would not complete the sign-in. Please try again.",
  no_email: "That Google account has no email address we can use.",
  email_not_verified: "That Google address is not verified, so we cannot use it to sign in.",
  google_unreachable: "We could not reach Google just now. Please try again.",
  google_not_configured: "Google sign-in is not set up on this server.",
};

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
  // Asked, not assumed. A sign-in button that cannot work is worse than no
  // button at all: the person clicks it, lands on a Google error page, and
  // has no way to know the missing piece is a key on our side.
  const [googleReady, setGoogleReady] = useState(false);

  useEffect(() => {
    // The callback sends people back here with ?error=... when something
    // went wrong on Google's side of the trip, so say what it was rather
    // than returning them to a blank form that looks like nothing happened.
    const params = new URLSearchParams(window.location.search);
    const failed = params.get("error");
    if (failed) setError(GOOGLE_ERRORS[failed] || "Google sign-in did not complete.");

    fetch(`${API_URL}/api/auth/google/status`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => setGoogleReady(Boolean(d.configured)))
      .catch(() => setGoogleReady(false));
  }, []);

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

        {googleReady && (
          <>
            <a
              href={`${API_URL}/api/auth/google/start`}
              className="w-full py-3 mb-6 text-[13px] font-semibold flex items-center justify-center gap-2.5 border transition-colors hover:bg-black/[0.03]"
              style={{ borderColor: RULE, color: INK, borderRadius: 0 }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
                <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.4a5.5 5.5 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.6-5.2 3.6-8.8z" />
                <path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3a7.2 7.2 0 0 1-10.7-3.8H1.4v3.1A12 12 0 0 0 12 24z" />
                <path fill="#FBBC05" d="M5.4 14.3a7.2 7.2 0 0 1 0-4.6V6.6H1.4a12 12 0 0 0 0 10.8l4-3.1z" />
                <path fill="#EA4335" d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.5-3.5A12 12 0 0 0 1.4 6.6l4 3.1A7.2 7.2 0 0 1 12 4.8z" />
              </svg>
              Continue with Google
            </a>
            <div className="flex items-center gap-3 mb-6">
              <span className="flex-1 h-px" style={{ background: RULE_SOFT }} />
              <span className="text-[11px] uppercase tracking-widest" style={{ color: INK_MUTED }}>
                or
              </span>
              <span className="flex-1 h-px" style={{ background: RULE_SOFT }} />
            </div>
          </>
        )}

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
