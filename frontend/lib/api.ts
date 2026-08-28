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
  /** Real `recovery.blocked` counts, keyed by reason (e.g. "no_contact",
   *  "rail_degraded"). Money was on the table but Kinato deliberately
   *  stayed silent — this says why. */
  blocked_reasons: Record<string, number>;
  /** Outreach that happened DESPITE a hard stop (already paid, no consent,
   *  quiet hours, discount over ceiling). Not a metric to optimise — any
   *  non-zero value means a guarantee the merchant relies on was broken. */
  rule_breaks: number;
  /** Customers who committed to pay on a date. Outreach is paused for them —
   *  neither lost nor recovered, so they are reported separately rather than
   *  folded into either number. */
  promised_count: number;
  promised_paise: number;
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

export interface ProductRow {
  product_id: string;
  name: string;
  description: string;
  price_paise: number;
  cogs_paise: number | null;
  currency: string;
  inventory_count: number;
  image_url: string;
  visible_to_ai_buyers: boolean;
  active: boolean;
}

export function getCatalog(): Promise<{ products: ProductRow[] }> {
  return apiFetch("/api/dashboard/catalog");
}

/** The webhook URL to paste into Razorpay. Comes from the SERVER, not
 *  assembled here — a browser talking to localhost would otherwise show a
 *  localhost URL that Razorpay can never reach. */
export function getWebhookUrl(): Promise<{ url: string; public_base_configured: boolean }> {
  return apiFetch("/api/merchant/webhook-url");
}

/** Upload/replace catalog rows from a CSV. Same endpoint onboarding uses —
 *  re-uploading is an upsert by sku, so it doubles as "update my prices". */
export async function uploadCatalogCsv(file: File): Promise<{ imported: number; skipped: string[] }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/merchant/onboarding/catalog`, {
    method: "POST",
    credentials: "include",
    body: form, // no Content-Type: the browser must set the multipart boundary
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(data?.detail || `Upload failed (${res.status})`, res.status);
  return data;
}

export function setProductVisibility(productId: string, visible: boolean): Promise<{ product: ProductRow }> {
  return apiFetch(`/api/dashboard/catalog/${productId}/visibility`, {
    method: "POST",
    body: JSON.stringify({ visible }),
  });
}

export interface ApiKeyRow {
  key_id: string;
  key_type: "publishable" | "secret";
  key_prefix: string;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export function getApiKeys(): Promise<{ keys: ApiKeyRow[] }> {
  return apiFetch("/api/merchant/api-keys");
}

export function createApiKey(keyType: "publishable" | "secret"): Promise<{ key: string; key_id: string; key_prefix: string; warning: string }> {
  return apiFetch("/api/merchant/api-keys", { method: "POST", body: JSON.stringify({ key_type: keyType }) });
}

export function revokeApiKey(keyId: string): Promise<{ status: string }> {
  return apiFetch(`/api/merchant/api-keys/${keyId}/revoke`, { method: "POST" });
}

export function getRazorpayStatus(): Promise<{ connected: boolean; webhook_secret_set: boolean }> {
  return apiFetch("/api/merchant/razorpay/status");
}

/** Sets ONLY the webhook signing secret. Until it's set, Razorpay events are
 *  rejected unverified and recovery never starts — see
 *  backend/app/payments/webhooks.py. */
export function saveWebhookSecret(webhookSecret: string): Promise<{ status: string }> {
  return apiFetch("/api/merchant/razorpay/webhook-secret", {
    method: "PUT",
    body: JSON.stringify({ webhook_secret: webhookSecret }),
  });
}

export function getAllowedOrigins(): Promise<{ origins: string[] }> {
  return apiFetch("/api/merchant/allowed-origins");
}

export function setAllowedOrigins(origins: string[]): Promise<{ origins: string[] }> {
  return apiFetch("/api/merchant/allowed-origins", { method: "POST", body: JSON.stringify({ origins }) });
}

export interface IntelAnswer {
  question: string;
  answer: string;
  metrics: Record<string, any>;
  source?: "llm" | "heuristic";
  degraded?: boolean;
}

/** Ask a natural-language question about this merchant's own real data.
 *  Backed by /api/merchant-intel/chat, which is grounded strictly in this
 *  merchant's DB rows — see backend/app/services/merchant_intelligence.py. */
export function askMerchantIntel(question: string): Promise<IntelAnswer> {
  return apiFetch("/api/merchant-intel/chat", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

/** paise -> "₹1,234.56" */
export function formatInr(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}
