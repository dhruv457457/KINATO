"use client";

/**
 * Dashboard error boundary.
 *
 * Replaces the previous components/ErrorBoundary.tsx, which was never
 * imported by anything (dead code) and was the sole consumer of the
 * lucide-react dependency - a whole icon library pulled in for three glyphs
 * in a file that never rendered. This version has no icon dependency and is
 * actually mounted, in app/dashboard/layout.tsx.
 *
 * Why it matters in production: without a boundary, one unhandled render
 * error in a single page (a malformed audit payload, an unexpected null)
 * unmounts the entire React tree and the merchant gets a blank white page
 * with no way back. This keeps the shell and offers a real recovery.
 */

import { Component, ReactNode } from "react";
import { RULE, INK_MUTED, NEGATIVE, WASH } from "./primitives";

interface Props {
  children: ReactNode;
  /** Lets the boundary reset when the user navigates to another page. */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

export class DashboardErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    // Navigating away from the broken page should clear the error, rather
    // than pinning the whole dashboard to it until a full reload.
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="px-14 py-14 anim-rise">
        <div className="text-[11px] font-semibold uppercase tracking-widest mb-2" style={{ color: NEGATIVE }}>
          This page hit an error
        </div>
        <h2 className="font-serif font-semibold text-[24px] mb-4">Something on this screen failed to render.</h2>
        <p className="text-sm mb-6 max-w-lg" style={{ color: INK_MUTED }}>
          Your data is unaffected — this is a display problem, not a data problem. The rest of the dashboard still
          works; you can switch pages in the sidebar, or try this one again.
        </p>

        <button className="onb-btn-secondary" onClick={() => this.setState({ error: null })}>
          Try again
        </button>

        <pre
          className="mt-8 text-[11px] p-3 overflow-x-auto max-w-2xl"
          style={{ background: WASH, border: `1px solid ${RULE}`, fontFamily: "var(--font-geist-mono)" }}
        >
          {error.message || String(error)}
        </pre>
      </div>
    );
  }
}
