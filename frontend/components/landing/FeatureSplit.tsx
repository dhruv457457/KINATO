export function FeatureSplit() {
  return (
    <section className="bg-dark px-6 py-24 sm:py-32 text-background">
      <div className="mx-auto max-w-5xl grid gap-16 sm:grid-cols-2 items-center">
        <div>
          <h2 className="font-serif text-3xl sm:text-4xl leading-tight">
            AI that grows your revenue
            <br />
            <span className="italic">and answers your questions</span>
          </h2>
          <p className="mt-6 text-dark-200/90 text-[15px] leading-relaxed max-w-md">
            Ask Kinato anything about your sales — what&apos;s causing abandonment, how much was
            recovered this week, which products customers hesitate on. It answers from your real,
            live event stream, not a stale report.
          </p>
        </div>
        <div className="glass-card !bg-dark-100/60 !border-white/10 p-6 font-mono text-sm text-background/90">
          <p className="text-brand-100/70">Merchant:</p>
          <p className="mt-1">&ldquo;Recover today&apos;s failed payments above ₹1,000.&rdquo;</p>
          <p className="mt-4 text-brand-100/70">Kinato:</p>
          <p className="mt-1">
            &ldquo;6 eligible customers. 5 have valid contact consent. Beginning recovery now.&rdquo;
          </p>
        </div>
      </div>

      <div className="mx-auto max-w-5xl grid gap-16 sm:grid-cols-2 items-center mt-24">
        <div className="glass-card !bg-dark-100/60 !border-white/10 p-6 order-2 sm:order-1">
          <div className="flex items-center justify-between text-xs text-background/60 font-mono">
            <span>RAHUL SHARMA — ₹3,499</span>
            <span className="text-emerald-300">RECOVERED</span>
          </div>
          <div className="mt-4 space-y-2 text-sm text-background/90 font-mono">
            <p>Checkout abandoned → Consent verified</p>
            <p>Calling → &ldquo;It&apos;s too expensive yaar&rdquo;</p>
            <p>Barrier: PRICE · Budget: ₹3,000</p>
            <p>Policy approved: 8% → Email sent</p>
            <p className="text-emerald-300">₹3,219 recovered</p>
          </div>
        </div>
        <div className="order-1 sm:order-2">
          <h2 className="font-serif text-3xl sm:text-4xl leading-tight">
            Every recovery,
            <br />
            <span className="italic">fully explainable</span>
          </h2>
          <p className="mt-6 text-dark-200/90 text-[15px] leading-relaxed max-w-md">
            Open any recovery and see exactly what the customer said, what Kinato understood, and
            which policy approved the outcome. Nothing about a money decision is a black box.
          </p>
        </div>
      </div>
    </section>
  );
}
