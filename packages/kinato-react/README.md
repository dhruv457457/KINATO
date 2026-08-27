# @kinato/react

React/Next.js SDK for Kinato. Same wire protocol as the vanilla `<script>`
SDK (`backend/static/sdk/kinato.js`) — pick whichever fits your stack.

## Install

```bash
npm install @kinato/react
# or, until this is published: npm install /path/to/razaorpay/packages/kinato-react
```

## Usage

```tsx
// app/layout.tsx
import { KinatoProvider } from "@kinato/react";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html>
      <body>
        <KinatoProvider publishableKey={process.env.NEXT_PUBLIC_KINATO_KEY!}>
          {children}
        </KinatoProvider>
      </body>
    </html>
  );
}
```

```tsx
// app/checkout/page.tsx
import { useKinato } from "@kinato/react";

export default function CheckoutPage() {
  const { identify, track } = useKinato();

  useEffect(() => {
    identify({
      externalId: currentUser.id,
      email: currentUser.email,
      phone: currentUser.phone,
      consent: { voice: true, email: true }, // only what the customer actually opted into
    });
  }, []);

  function onCheckoutStart(cart: Cart) {
    track("checkout.started", {
      checkoutId: cart.id,
      amount: cart.total,
      currency: "INR",
      productIds: cart.items.map((i) => i.sku),
    });
  }

  // ...
}
```

## What this does — and doesn't — decide

This SDK reports that a checkout/cart exists. It does **not** decide when a
checkout counts as abandoned — that's a server-side, restart-safe sweeper
(never a client timer a closed tab could just never fire), and it does
**not** grant consent on your behalf — `identify()`'s `consent` object must
reflect what the customer actually agreed to; nothing is assumed opted-in by
default.

## Endpoint

Defaults to `window.location.origin + "/api"`. If your Kinato backend runs
on a different domain than your storefront, pass `endpoint` explicitly:

```tsx
<KinatoProvider publishableKey={key} endpoint="https://api.yourkinato.com/api">
```
