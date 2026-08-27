import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: "Kinato — AI Revenue Recovery for Your Store",
  description: "Kinato detects checkout abandonment and failed payments on your existing store, recovers the sale through policy-bounded AI outreach, and settles through your own Razorpay account.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=Playfair+Display:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-background text-dark min-h-screen antialiased selection:bg-brand-100 selection:text-brand-700 font-sans">
        {/* Official Razorpay Standard Web Checkout SDK */}
        <Script
          src="https://checkout.razorpay.com/v1/checkout.js"
          strategy="beforeInteractive"
        />

        <Providers>
          <div className="relative z-10 flex flex-col min-h-screen">
            {children}
          </div>
        </Providers>
      </body>
    </html>
  );
}
