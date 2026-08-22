"""
================================================================================
SCRIPT: tests/run_tests.py
MODULE: Module 5 - Automated Test Runner
--------------------------------------------------------------------------------
Runs all safety, policy refusal, HMAC tamper invalidation, and idempotency tests.
Works out of the box with zero external test runner dependencies.
================================================================================
"""
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_policy_refusal import (
    test_merchant_floor_price_refusal,
    test_buyer_cashflow_limit_refusal,
)
from tests.test_hmac_tampering import test_hmac_price_tamper_invalidation
from tests.test_idempotency import test_razorpay_order_idempotency


def main():
    print("================================================================")
    print("Running Kinato Automated Safety & Resilience Test Suite")
    print("================================================================")

    tests = [
        ("Merchant Floor Price Refusal Guardrail", test_merchant_floor_price_refusal),
        ("Buyer Cashflow Limit Refusal Guardrail", test_buyer_cashflow_limit_refusal),
        ("HMAC Cryptographic Tamper Invalidation", test_hmac_price_tamper_invalidation),
        ("Razorpay Idempotency Deduplication Engine", test_razorpay_order_idempotency),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {str(e)}")
            failed += 1

    print("================================================================")
    print(f"Results: {passed} PASSED, {failed} FAILED across {len(tests)} tests.")
    print("================================================================")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
