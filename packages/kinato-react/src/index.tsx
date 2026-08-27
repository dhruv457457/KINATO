/**
 * @kinato/react - real-time checkout/cart tracking for AI-powered revenue
 * recovery, as a first-class React/Next.js integration (not just a plain
 * <script> tag - see static/sdk/kinato.js in the backend for the
 * vanilla-JS equivalent, same wire protocol against POST /api/events).
 *
 * This SDK never decides when a checkout is "abandoned" - that is
 * determined server-side by a durable sweeper, not a client-side timer that
 * a closed tab could simply never fire. All this does is tell Kinato a
 * checkout/cart exists and who the customer is; the server takes it from there.
 */
import React, { createContext, useContext, useEffect, useMemo, useRef, ReactNode } from "react";

const STORAGE_KEY = "kinato_retry_buffer_v1";

export interface KinatoConsent {
  voice?: boolean;
  email?: boolean;
  sms?: boolean;
}

export interface KinatoIdentifyInput {
  externalId: string;
  name?: string;
  email?: string;
  phone?: string;
  consent?: KinatoConsent;
}

export interface KinatoTrackInput {
  checkoutId?: string;
  cartId?: string;
  amount?: number;
  currency?: string;
  productIds?: string[];
}

export interface KinatoProviderProps {
  publishableKey: string;
  /** Defaults to the current page's origin - only override for a split
   * frontend/API domain setup. Never defaults to localhost. */
  endpoint?: string;
  children: ReactNode;
}

interface BufferedRequest {
  body: Record<string, unknown>;
  ts: number;
}

function readBuffer(): BufferedRequest[] {
  try {
    return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function writeBuffer(items: BufferedRequest[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(-50)));
  } catch {
    /* localStorage unavailable (private mode, quota) - buffering degrades silently */
  }
}

interface KinatoContextValue {
  identify: (input: KinatoIdentifyInput) => Promise<void>;
  track: (eventName: string, input?: KinatoTrackInput) => Promise<void>;
}

const KinatoContext = createContext<KinatoContextValue | null>(null);

export function KinatoProvider({ publishableKey, endpoint, children }: KinatoProviderProps) {
  const externalIdRef = useRef<string | null>(null);

  if (!publishableKey) {
    throw new Error("[Kinato] <KinatoProvider publishableKey> is required (a pk_... key from your Kinato dashboard).");
  }

  const resolvedEndpoint = useMemo(() => {
    if (endpoint) return endpoint;
    if (typeof window === "undefined") return "";
    return window.location.origin + "/api"; // set NEXT_PUBLIC_KINATO_API_URL and pass as `endpoint` if the API lives on a different domain
  }, [endpoint]);

  const send = useMemo(() => {
    return async (eventType: string, payload: Record<string, unknown>, customer?: Record<string, unknown>) => {
      const body: Record<string, unknown> = { event_type: eventType, payload };
      if (customer) body.customer = customer;

      try {
        const res = await fetch(`${resolvedEndpoint}/events`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Kinato-Key": publishableKey },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[Kinato] Send failed, buffering for retry:", err);
        const buffer = readBuffer();
        buffer.push({ body, ts: Date.now() });
        writeBuffer(buffer);
      }
    };
  }, [resolvedEndpoint, publishableKey]);

  useEffect(() => {
    const flush = () => {
      const buffer = readBuffer();
      if (!buffer.length) return;
      writeBuffer([]);
      buffer.forEach((item) => {
        fetch(`${resolvedEndpoint}/events`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Kinato-Key": publishableKey },
          body: JSON.stringify(item.body),
          keepalive: true,
        }).catch(() => {
          const current = readBuffer();
          current.push(item);
          writeBuffer(current);
        });
      });
    };

    flush();
    window.addEventListener("online", flush);

    // sendBeacon cannot carry custom headers, so the key travels in the body
    // instead - safe because pk_ keys are meant to be public (their security
    // is the server-side origin allowlist + restricted event scope, not
    // secrecy). See backend/app/api/events.py's `body_api_key` handling.
    const onPageHide = () => {
      const buffer = readBuffer();
      if (!buffer.length) return;
      buffer.forEach((item) => {
        navigator.sendBeacon(
          `${resolvedEndpoint}/events`,
          new Blob([JSON.stringify({ api_key: publishableKey, ...item.body })], { type: "application/json" })
        );
      });
      writeBuffer([]);
    };
    window.addEventListener("pagehide", onPageHide);

    return () => {
      window.removeEventListener("online", flush);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [resolvedEndpoint, publishableKey]);

  const value = useMemo<KinatoContextValue>(
    () => ({
      identify: async (input: KinatoIdentifyInput) => {
        externalIdRef.current = input.externalId;
        await send(
          "customer.identified",
          { consent: input.consent || {} },
          {
            external_id: input.externalId,
            name: input.name || "",
            email: input.email || "",
            phone: input.phone || "",
          }
        );
      },
      track: async (eventName: string, input: KinatoTrackInput = {}) => {
        await send(eventName, {
          checkout_id: input.checkoutId,
          cart_id: input.cartId,
          amount: input.amount,
          currency: input.currency || "INR",
          product_ids: input.productIds || [],
          customer_id: externalIdRef.current,
        });
      },
    }),
    [send]
  );

  return <KinatoContext.Provider value={value}>{children}</KinatoContext.Provider>;
}

/** Call inside a component wrapped by <KinatoProvider>. */
export function useKinato(): KinatoContextValue {
  const ctx = useContext(KinatoContext);
  if (!ctx) {
    throw new Error("[Kinato] useKinato() must be used inside a <KinatoProvider>.");
  }
  return ctx;
}
