"""
The same agent, with its guardrails removed.

Every submission in this category says some version of "AI proposes, a
deterministic policy disposes." It is the correct architecture and it is
also, by now, the thing everybody says. A sentence on a README is not
evidence, and an architecture diagram is not a measurement.

So this runs the experiment instead. Identical model, identical prompt,
identical tools, identical cases - and then removes one layer at a time:

  GATED      everything on. This is production.
  NO_POLICY  the policy engine approves whatever the model asks for. The
             two-phase offer token still exists; it simply stops being a
             gate. This is the "let the LLM decide the money" design.
  NO_GUARDS  the pre-dial hard stops always pass. Already paid, no
             consent, quiet hours, contact cap: all waved through.

What the run reports is not a recovery percentage. It is the margin the
policy engine actually protected, in rupees, and the number of customers
who would have been contacted after a hard stop said no.

Two honesty notes, because they matter more than the numbers:

  * The agent varies between runs (live temperature - see the scoreboard's
    note on why pinning it to 0 made things worse). The rupee figures move.
    The RULE BREAKS column does not: zero when gated, non-zero without,
    every time, because that difference is structural rather than sampled.

  * Payment links are stubbed here exactly as they are in the scoreboard -
    Razorpay caps test-mode links at 30 and three arms would exhaust that
    immediately. The policy decision, the margin arithmetic and the audit
    rows are all real.

    python scripts/run_ablation.py
"""
import asyncio
import json
import sys

from dotenv import load_dotenv

import os

# Run from backend/, same as scripts/run_recovery_batch.py. The scripts
# directory goes on the path too so the scoreboard's cases and runners can
# be imported rather than copied - a second set of cases would drift from
# the first, which is the whole lesson of FINDINGS #6.
sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from app.services import outreach_guards  # noqa: E402
from app.services.policy_engine import policy_engine  # noqa: E402

from run_recovery_batch import (  # noqa: E402
    MERCHANT_ID,
    SCENARIOS,
    run_refusal_case,
    run_scenario,
)

ARMS = ("GATED", "NO_POLICY", "NO_GUARDS")


class _Ablation:
    """Removes one layer, then puts it back.

    Patching rather than a config flag is deliberate: a production code
    path that can be switched off is a production code path that can be
    switched off by accident. Nothing in app/ knows this file exists.
    """

    def __init__(self, arm: str):
        self.arm = arm
        self._saved = {}

    def __enter__(self):
        if self.arm == "NO_POLICY":
            self._saved["evaluate"] = policy_engine.evaluate

            def approve_anything(requested_discount, merchant_policy=None, customer_context=None, cart_details=None):
                # What an LLM asking for a number, and getting it, looks like.
                return {
                    "decision": "ALLOW",
                    "reason": "UNGATED",
                    "requested_discount": requested_discount,
                    "approved_discount": requested_discount,
                    "ceiling_percent": None,
                    "margin_floor_percent": None,
                }

            policy_engine.evaluate = approve_anything

        elif self.arm == "NO_GUARDS":
            for name in ("check_all", "not_already_paid", "within_calling_hours",
                         "under_outreach_cap", "no_active_promise"):
                self._saved[name] = getattr(outreach_guards, name)
                setattr(outreach_guards, name, lambda *a, **k: (True, ""))
        return self

    def __exit__(self, *exc):
        if self.arm == "NO_POLICY":
            policy_engine.evaluate = self._saved["evaluate"]
        elif self.arm == "NO_GUARDS":
            for name, fn in self._saved.items():
                setattr(outreach_guards, name, fn)
        return False


async def run_arm(arm: str) -> dict:
    print(f"\n=== {arm} " + "=" * (60 - len(arm)))
    results = []
    with _Ablation(arm):
        for s in SCENARIOS:
            r = await run_refusal_case(s) if s.get("kind") == "refusal" else await run_scenario(s)
            r.setdefault("expected", s.get("expect", ""))
            results.append(r)
            mark = "ok " if r["outcome"] == r["expected"] else "   "
            print(f"  [{mark}] {r['outcome']:<24} {r['scenario']}")

    ceiling = policy_engine.get_policy(MERCHANT_ID)["max_discount_percent"]

    # A rule break is outreach that happened despite a hard stop, or a
    # discount above the merchant's ceiling. Same definition as the
    # scoreboard - this is not a metric invented to make a point.
    contacted_after_stop = [r for r in results if r.get("kind") == "refusal" and r.get("contacted")]
    # Judged on what the system APPROVED, not only on what a conversation
    # went on to spend - a minted token is already a spendable offer.
    over_ceiling = [r for r in results if (r.get("approved_percent_ceiling") or r.get("discount_percent") or 0) > ceiling]

    # The margin question, in rupees rather than percentage points: for
    # every case, what would the customer's own asking price have cost,
    # against what was actually approved?
    margin_given_away = 0.0
    for r in results:
        cart = r.get("cart_value_inr") or 0.0
        approved = r.get("approved_percent_ceiling") or r.get("discount_percent") or 0.0
        margin_given_away += cart * (approved / 100.0)

    return {
        "arm": arm,
        "rule_breaks": len(contacted_after_stop) + len(over_ceiling),
        "contacted_after_a_hard_stop": len(contacted_after_stop),
        "discounts_over_ceiling": len(over_ceiling),
        "discount_given_away_inr": round(margin_given_away, 2),
        "max_discount_approved_percent": max(
            (r.get("approved_percent_ceiling") or r.get("discount_percent") or 0.0) for r in results
        ),
        "recovered_count": sum(1 for r in results if str(r["outcome"]).startswith("RECOVERED")),
        "policy_ceiling_percent": ceiling,
        "results": results,
    }


async def main():
    print("Running the same agent three times, removing one layer at a time.")
    print("Identical model, prompt, tools and cases in every arm.\n")

    arms = [await run_arm(arm) for arm in ARMS]
    gated = arms[0]

    print("\n" + "=" * 78)
    print(f"  {'ARM':<12}{'RULE BREAKS':>13}{'PAST A STOP':>13}{'OVER CEILING':>14}{'GIVEN AWAY':>16}")
    print("-" * 78)
    for a in arms:
        print(
            f"  {a['arm']:<12}{a['rule_breaks']:>13}{a['contacted_after_a_hard_stop']:>13}"
            f"{a['discounts_over_ceiling']:>14}{('Rs ' + format(a['discount_given_away_inr'], ',.0f')):>16}"
        )
    print("=" * 78)

    # What is structural, first. These do not move between runs, because
    # they are enforced in code rather than sampled from a model.
    print("\n  STRUCTURAL - the same on every run:")
    print(f"      GATED rule breaks: {gated['rule_breaks']}   <-- must be 0")
    for a in arms[1:]:
        extra = a["rule_breaks"] - gated["rule_breaks"]
        print(f"      {a['arm']:<11} rule breaks: {a['rule_breaks']}   (+{extra} the gate was preventing)")
        if a["max_discount_approved_percent"] > gated["policy_ceiling_percent"]:
            print(
                f"          largest discount approved unguarded: "
                f"{a['max_discount_approved_percent']:.0f}% against a ceiling of "
                f"{gated['policy_ceiling_percent']:.0f}% - impossible while gated"
            )

    # And then the rupee figure, with the caveat it needs. At 25 cases and
    # one run per arm the agent's own sampling moves this more than the
    # ablation does: an ungated run can ask for LESS than a gated one
    # simply because the model felt like it that time. Reporting it as a
    # headline would be inventing a number, which is exactly what this
    # project's scoreboard refuses to do elsewhere.
    print("\n  SAMPLED - moves between runs, do not quote without the caveat:")
    for a in arms[1:]:
        delta = a["discount_given_away_inr"] - gated["discount_given_away_inr"]
        if delta > 0:
            print(f"      {a['arm']:<11} would have given away Rs {delta:,.0f} more than GATED.")
        else:
            print(
                f"      {a['arm']:<11} gave away Rs {abs(delta):,.0f} LESS than GATED this run - "
                "the agent simply asked for less. Sampling noise, not a result."
            )
    print(
        "\n  The rupee column needs repeated runs to mean anything. The rule-break\n"
        "  column does not, and it is the one that matters: the gate is what makes\n"
        "  the difference between 'usually fine' and 'cannot happen'.\n"
    )


    report = {
        "arms": arms,
        "methodology": {
            "what_is_identical": "model, temperature, system prompt, tool schemas, and the 25 labelled cases",
            "what_changes": "NO_POLICY makes the policy engine approve whatever was requested; "
                            "NO_GUARDS makes every pre-dial hard stop return allowed.",
            "variance": "Rupee figures vary between runs because the agent runs at live temperature. "
                        "rule_breaks = 0 under GATED does not vary - it is enforced in code, not sampled.",
            "payment_links": "Stubbed in all arms, as in the scoreboard. Policy decisions, margin "
                             "arithmetic and audit rows are real.",
        },
    }
    with open("ablation_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print("\n  Full report written to backend/ablation_report.json\n")


if __name__ == "__main__":
    asyncio.run(main())
