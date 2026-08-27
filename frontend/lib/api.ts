/**
 * Thin fetch wrapper shared by the onboarding funnel and dashboard.
 * Always sends the httpOnly session cookie; never a Bearer token from
 * client state (matching backend/app/core/auth.py's cookie-only model).
 */
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* empty body is fine for some endpoints */
  }
  if (!res.ok) {
    throw new ApiError(data?.detail || `Request failed (${res.status})`, res.status);
  }
  return data as T;
}

export interface Merchant {
  merchant_id: string;
  name: string;
  email: string;
  store_url: string;
  onboarding_step: "signup" | "connect" | "integrate" | "catalog" | "policy" | "done";
  rzp_connected: boolean;
  status: string;
}

export async function getCurrentMerchant(): Promise<Merchant | null> {
  try {
    const data = await apiFetch<{ merchant: Merchant }>("/api/auth/me");
    return data.merchant;
  } catch {
    return null;
  }
}

/** Maps an onboarding_step to the route that step lives on. */
export function stepPath(step: Merchant["onboarding_step"]): string {
  switch (step) {
    case "signup":
      return "/onboarding/connect"; // signup itself happens on /login
    case "connect":
      return "/onboarding/connect";
    case "integrate":
      return "/onboarding/integrate";
    case "catalog":
      return "/onboarding/catalog";
    case "policy":
      return "/onboarding/policy";
    case "done":
    default:
      return "/dashboard";
  }
}

// ---------------------------------------------------------------------------
// Dashboard - real, DB-backed data. See backend/app/api/dashboard.py: these
// numbers come from actual recovery_attempts/checkouts rows, never the
// in-memory event bus the old dashboard read (empty after every restart).
// ---------------------------------------------------------------------------

export interface DashboardOverview {
  revenue_at_risk_paise: number;
  revenue_recovered_paise: number;
  active_recoveries: number;
  recovered_count: number;
  total_attempts: number;
  opted_out_count: number;
  call_failed_count: number;
  abandoned_count: number;
  recovery_rate_pct: number | null;
}

export interface RecoveryRow {
  recovery_attempt_id: string;
  checkout_id: string;
  customer_id: string | null;
  state: string;
  channel: string | null;
  approved_discount_percent: number | null;
  final_amount_paise: number | null;
  attributed_revenue_paise: number | null;
  rzp_payment_link_id: string | null;
  created_at: string;
  updated_at: string;
  cart_amount_paise: number | null;
  currency: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  customer_email: string | null;
}

export interface AuditRow {
  audit_id: string;
  correlation_id?: string;
  actor: string;
  action: string;
  decision: string | null;
  degraded: boolean;
  latency_ms: number | null;
  args: any;
  result: any;
  created_at: string;
}

export function getOverview(): Promise<DashboardOverview> {
  return apiFetch("/api/dashboard/overview");
}

export function getRecoveries(): Promise<{ recoveries: RecoveryRow[] }> {
  return apiFetch("/api/dashboard/recoveries");
}

export function getRecoveryDetail(id: string): Promise<{ recovery_attempt: any; audit_trail: AuditRow[] }> {
  return apiFetch(`/api/dashboard/recoveries/${id}`);
}

export function getActivity(): Promise<{ activity: AuditRow[] }> {
  return apiFetch("/api/dashboard/activity");
}

export interface CustomerRow {
  customer_id: string;
  name: string | null;
  phone: string | null;
  email: string | null;
  created_at: string;
  voice_consent_status: "granted" | "revoked" | null;
}

export function getCustomers(): Promise<{ customers: CustomerRow[] }> {
  return apiFetch("/api/dashboard/customers");
}

export function revokeCustomerConsent(customerId: string): Promise<{ status: string }> {
  return apiFetch(`/api/dashboard/customers/${customerId}/revoke-consent`, { method: "POST" });
}

/** paise -> "₹1,234.56" */
export function formatInr(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}
