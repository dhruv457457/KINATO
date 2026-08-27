/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_RAZORPAY_KEY_ID: process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID || "rzp_test_TSk4KG18ZnfUX7",
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    NEXT_PUBLIC_DEMO_PHONE: process.env.NEXT_PUBLIC_DEMO_PHONE || "",
    NEXT_PUBLIC_DEMO_EMAIL: process.env.NEXT_PUBLIC_DEMO_EMAIL || ""
  }
};

export default nextConfig;
