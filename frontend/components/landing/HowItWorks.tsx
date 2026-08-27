const STEPS = [
  {
    n: "01",
    title: "Detects revenue at risk",
    body: "Kinato watches checkout events on your own site in real time and flags abandoned carts and failed payments the moment they happen — no dashboard-refreshing required.",
  },
  {
    n: "02",
    title: "Recovers with a real conversation",
    body: "A voice or email agent reaches out, understands the actual objection — price, timing, trust — and negotiates within limits you set. Nothing above your policy ever gets approved.",
  },
  {
    n: "03",
    title: "Reconciles automatically",
    body: "The moment Razorpay confirms payment, the sale is attributed back to the exact recovery attempt that closed it. Every rupee is explainable and audited.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="bg-background px-6 py-24 sm:py-32">
      <div className="mx-auto max-w-5xl">
        <p className="text-sm font-semibold uppercase tracking-wider text-brand-500">How Kinato works</p>
        <h2 className="mt-3 font-serif text-3xl sm:text-4xl text-dark max-w-xl">
          One AI layer on top of the store you already run.
        </h2>

        <div className="mt-16 grid gap-10 sm:grid-cols-3">
          {STEPS.map((step) => (
            <div key={step.n} className="glass-card p-6">
              <span className="font-serif text-3xl text-brand-500/40">{step.n}</span>
              <h3 className="mt-4 text-lg font-semibold text-dark">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-dark-200">{step.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
