import { LandingNav } from "@/components/landing/LandingNav";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { FeatureSplit } from "@/components/landing/FeatureSplit";
import { FAQ } from "@/components/landing/FAQ";
import { CTAFooter } from "@/components/landing/CTAFooter";

export default function LandingPage() {
  return (
    <main className="w-full">
      <LandingNav />
      <Hero />
      <HowItWorks />
      <FeatureSplit />
      <FAQ />
      <CTAFooter />
    </main>
  );
}
