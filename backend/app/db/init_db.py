"""
================================================================================
FILE: app/db/init_db.py
MODULE: Module 1 - Multi-Tenant Database Schema
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines Kinato's real multi-tenant schema: merchants, their API keys and
Razorpay credentials, their policy configuration, their catalog, their
customers and consent records, their checkouts and recovery attempts, the
offer-token money gate, a durable copy of the event bus, and an audit log of
every agent tool call.

DESIGN RULES THIS SCHEMA ENFORCES:
  1. Money is integer paise everywhere (`_paise` suffix). Never a float.
     Formatting to rupees happens only at the API boundary.
  2. Consent is append-only (`consents`). Never UPDATE a row - the latest
     row per (merchant_id, customer_id, channel) is the current state. This
     is what makes "the customer said stop and we stopped" auditable.
  3. All primary keys are TEXT (prefixed ids like mch_/chk_/rec_/off_) so no
     table needs a dialect-specific autoincrement/serial type.
  4. Every table is written ONCE in Postgres dialect and relies on
     app/db/database.py's _translate_sql_for_sqlite() for the SQLite path -
     see that module's dialect() note for why (the old schema had two full
     copies of every table and rotted because of it).
================================================================================
"""
from app.db.database import get_db


def init_db(force_reseed: bool = False) -> None:
    """Creates the schema if it doesn't exist. Idempotent - safe to call on
    every app startup. `force_reseed` is currently unused (no fixture data
    ships with this schema; see backend/scripts/seed_demo_merchant.py)."""
    with get_db() as conn:
        cursor = conn.cursor()

        # 1. Merchants
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            merchant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            store_url TEXT,
            rzp_key_id_enc TEXT,
            rzp_key_secret_enc TEXT,
            rzp_webhook_secret_enc TEXT,
            rzp_mode TEXT NOT NULL DEFAULT 'test',
            allowed_origins TEXT NOT NULL DEFAULT '[]',
            onboarding_step TEXT NOT NULL DEFAULT 'signup',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)

        # 2. API keys (pk_ publishable / sk_ secret)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            key_type TEXT NOT NULL,
            key_prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            revoked_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_merchant ON api_keys (merchant_id);")

        # 3. Merchant policies - one row per merchant, real config replacing
        # every hardcoded discount/margin/window constant in the services.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchant_policies (
            merchant_id TEXT PRIMARY KEY,
            max_discount_percent REAL NOT NULL DEFAULT 10.0,
            minimum_margin_percent REAL NOT NULL DEFAULT 15.0,
            offer_ladder TEXT NOT NULL DEFAULT '[3,7,10]',
            free_shipping_allowed BOOLEAN NOT NULL DEFAULT TRUE,
            bundle_upsell_allowed BOOLEAN NOT NULL DEFAULT FALSE,
            bundle_product_id TEXT,
            bundle_discount_percent REAL,
            calling_start_hour INTEGER NOT NULL DEFAULT 10,
            calling_end_hour INTEGER NOT NULL DEFAULT 20,
            abandonment_window_seconds INTEGER NOT NULL DEFAULT 1800,
            auto_approval_threshold_inr REAL NOT NULL DEFAULT 0,
            excluded_products TEXT NOT NULL DEFAULT '[]',
            voice_persona TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)

        # 4. Catalog, with real COGS so margin math is real, not invented.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            merchant_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_paise BIGINT NOT NULL,
            cogs_paise BIGINT,
            currency TEXT NOT NULL DEFAULT 'INR',
            inventory_count INTEGER NOT NULL DEFAULT 0,
            image_url TEXT,
            visible_to_ai_buyers BOOLEAN NOT NULL DEFAULT TRUE,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (merchant_id, product_id)
        );
        """)

        # 5. Customers
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            external_id TEXT,
            name TEXT,
            email TEXT,
            phone TEXT,
            rzp_customer_id TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_customers_merchant_email ON customers (merchant_id, email);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_customers_merchant_phone ON customers (merchant_id, phone);")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_merchant_external "
            "ON customers (merchant_id, external_id) WHERE external_id IS NOT NULL;"
        )

        # 6. Consents - APPEND-ONLY. Never UPDATE a row here. The current
        # state of consent for (merchant, customer, channel) is whichever
        # row has the latest created_at. This is what makes an opt-out
        # auditable: the revocation is its own row, not a bit flipped on an
        # existing one.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS consents (
            consent_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            policy_version TEXT,
            evidence TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_consents_lookup "
            "ON consents (merchant_id, customer_id, channel, created_at);"
        )

        # 7. Checkouts - the real cart a policy decision must be evaluated
        # against (replaces the hardcoded {"amount": 3499.0} literal).
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS checkouts (
            checkout_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            customer_id TEXT,
            cart_id TEXT,
            amount_paise BIGINT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            cogs_paise BIGINT,
            line_items TEXT,
            status TEXT NOT NULL DEFAULT 'started',
            source TEXT NOT NULL DEFAULT 'sdk',
            rzp_order_id TEXT,
            rzp_payment_id TEXT,
            started_at TIMESTAMPTZ DEFAULT NOW(),
            abandoned_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkouts_status_started ON checkouts (status, started_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkouts_merchant ON checkouts (merchant_id, started_at);")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_checkouts_merchant_rzp_order "
            "ON checkouts (merchant_id, rzp_order_id) WHERE rzp_order_id IS NOT NULL;"
        )

        # 8. Recovery attempts
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS recovery_attempts (
            recovery_attempt_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            checkout_id TEXT NOT NULL,
            customer_id TEXT,
            state TEXT NOT NULL DEFAULT 'CREATED',
            channel TEXT,
            plan TEXT,
            approved_discount_percent REAL,
            final_amount_paise BIGINT,
            rzp_payment_link_id TEXT,
            rzp_order_id TEXT,
            rzp_offer_id TEXT,
            attributed_revenue_paise BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_attempts_checkout ON recovery_attempts (checkout_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_attempts_merchant ON recovery_attempts (merchant_id, created_at);"
        )

        # 8b. Conversation turns - what was actually SAID, on both sides.
        #
        # Until this table existed, a live call's dialogue lived in a
        # module-global dict and in stdout. That meant: the recovery drawer
        # (whose own docstring calls it "the screen the whole product is
        # judged on") showed tool calls with no conversation next to them;
        # a second worker process could not serve a call the first one
        # started; a restart mid-call told the customer "I lost track of
        # our order details"; and the agent began every later attempt
        # knowing nothing about the earlier one.
        #
        # stt_confidence and input_mode are stored per turn because "we may
        # have misheard this" is a fact about the turn, and it is exactly
        # the fact a merchant needs when a call reads oddly in the drawer.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            turn_id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            recovery_attempt_id TEXT NOT NULL,
            customer_id TEXT,
            turn_index INTEGER NOT NULL,
            speaker TEXT NOT NULL,
            text TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'voice',
            stt_confidence REAL,
            input_mode TEXT NOT NULL DEFAULT 'speech',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_attempt "
            "ON conversation_turns (recovery_attempt_id, turn_index);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_customer "
            "ON conversation_turns (customer_id, created_at);"
        )

        # 9. Offer tokens - the two-phase money gate. An LLM tool call can
        # only ever reference one of these by its opaque token; the actual
        # amount is whatever this row says, never what the model argues for.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS offer_tokens (
            offer_token TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            checkout_id TEXT,
            recovery_attempt_id TEXT,
            requested_percent REAL,
            approved_percent REAL,
            decision TEXT NOT NULL,
            reason TEXT,
            base_amount_paise BIGINT,
            final_amount_paise BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ,
            consumed_at TIMESTAMPTZ
        );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_offer_tokens_recovery ON offer_tokens (recovery_attempt_id);"
        )

        # 10. Events - durable copy of the event bus. The bus itself stays
        # in-memory for hot-path speed; this table is what survives a
        # restart and what the idempotency_key UNIQUE constraint protects
        # (replacing the old unbounded in-memory idempotency set).
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            merchant_id TEXT,
            correlation_id TEXT,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            idempotency_key TEXT UNIQUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_merchant_time ON events (merchant_id, created_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events (event_type, created_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_correlation ON events (correlation_id);")

        # 11. Audit log - one row per agent tool invocation: who asked for
        # what, what the deterministic engine decided, whether the call was
        # degraded (heuristic fallback), and how long it took. This is the
        # backing data for the dashboard's Activity feed and the recovery
        # detail drawer's audit timeline.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id TEXT PRIMARY KEY,
            merchant_id TEXT,
            correlation_id TEXT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            args TEXT,
            result TEXT,
            decision TEXT,
            degraded BOOLEAN NOT NULL DEFAULT FALSE,
            latency_ms INTEGER,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_merchant_time ON audit_log (merchant_id, created_at);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_correlation ON audit_log (correlation_id);"
        )

    # Rail-health flag: set by the payment.downtime.* webhook handler
    # (app/payments/webhooks.py), checked by RecoveryEligibilityService
    # before generating any new recovery opportunity - a real stopping rule
    # (never call/email a customer over a failed payment that might just be
    # Razorpay's own outage, not a real decline). Added via ALTER since this
    # column postdates merchants' original CREATE TABLE and real deployed
    # databases already have that table without it. Run in its own
    # connection/transaction, deliberately outside the block above: init_db()
    # runs on every app startup, so the "column already exists" case is the
    # normal path after the first boot, and it must never abort the main
    # schema transaction's CREATE TABLEs.
    try:
        with get_db() as alter_conn:
            alter_conn.cursor().execute("ALTER TABLE merchants ADD COLUMN rail_degraded_at TIMESTAMPTZ")
    except Exception:
        pass  # column already exists

    # Promise-to-pay. A customer saying "I'll pay on Friday" is a STOPPING
    # rule, not a soft outcome: the correct behaviour is to stop selling,
    # stop calling, and wait until the date they named. Stored as its own
    # columns (added via ALTER for the same reason as above - real deployed
    # databases already have this table).
    for ddl in (
        "ALTER TABLE recovery_attempts ADD COLUMN promised_at TIMESTAMPTZ",
        "ALTER TABLE recovery_attempts ADD COLUMN promised_amount_paise BIGINT",
        "ALTER TABLE recovery_attempts ADD COLUMN promise_words TEXT",
        "ALTER TABLE recovery_attempts ADD COLUMN promise_reminded_at TIMESTAMPTZ",
        # The customer pressed 0 on the keypad to ask us to call back. This
        # is the ONE thing that may lift the outreach cap, so it has to be
        # a stored fact rather than an inference: "they asked us to" is the
        # difference between a follow-up and a nuisance call.
        "ALTER TABLE recovery_attempts ADD COLUMN callback_requested_at TIMESTAMPTZ",
        # Why the payment failed, as Razorpay described it and as we
        # classified it. Razorpay has always sent all of this; only
        # error_reason ever reached the event bus, and nothing read even
        # that, so a bank timeout and a stolen-card block produced the
        # identical sales call. Stored on the checkout because it is a
        # fact about the payment, not about any one recovery attempt -
        # a second attempt must diagnose from the same evidence.
        # How this merchant signs in. A Google account has no usable
        # password, and a password-reset flow must be able to tell the
        # difference rather than mailing a reset for a login that does not
        # take one.
        "ALTER TABLE merchants ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password'",
        "ALTER TABLE checkouts ADD COLUMN error_code TEXT",
        "ALTER TABLE checkouts ADD COLUMN error_reason TEXT",
        "ALTER TABLE checkouts ADD COLUMN error_description TEXT",
        "ALTER TABLE checkouts ADD COLUMN error_source TEXT",
        "ALTER TABLE checkouts ADD COLUMN error_step TEXT",
        "ALTER TABLE checkouts ADD COLUMN payment_method TEXT",
        "ALTER TABLE checkouts ADD COLUMN failure_class TEXT",
        # Held back because RAZORPAY was down, not because of anything
        # about this customer. Recorded so the case can be picked up again
        # when the outage clears - previously it was published as blocked
        # and then dropped on the floor, and nothing ever re-fired it.
        "ALTER TABLE checkouts ADD COLUMN recovery_queued_at TIMESTAMPTZ",
        # Twilio's CallSid for this attempt. Recorded so /voice/respond can
        # find its way back to the recovery attempt from the CallSid alone
        # - the only identifier Twilio sends on a mid-call turn. Without it
        # an attempt is only reachable through the in-memory session the
        # /voice/outbound request happened to create, so a restart or a
        # second worker answers the customer with "I lost track of our
        # order details" and the call is over.
        "ALTER TABLE recovery_attempts ADD COLUMN twilio_call_sid TEXT",
        # The payable URL, not just Razorpay's id for it. Only the id was
        # stored, so the one thing needed to remind a customer of a promise
        # they made - the link they were given - existed nowhere after the
        # event that carried it had been handled.
        "ALTER TABLE recovery_attempts ADD COLUMN rzp_payment_link_url TEXT",
        # Which offer the promise was made against. A promise recorded with
        # only a date loses the terms it was a promise about, so a reminder
        # a week later cannot say what was agreed.
        "ALTER TABLE recovery_attempts ADD COLUMN promised_offer_token TEXT",
        # When the payment link stops being payable.
        #
        # payment_execution sets expire_by to now+24h and then throws the
        # value away, so nothing here could tell a live link from a dead
        # one - and without that, a link can never be reused and every
        # attempt has to mint another. Razorpay's test mode allows thirty
        # per account in total, which this project has now exhausted twice
        # (see FINDINGS #7), each time looking like a code regression.
        #
        # It cannot be inferred from updated_at: every later update_state
        # on the attempt refreshes that, so a PROMISED write hours later
        # would make an expired link look fresh.
        "ALTER TABLE recovery_attempts ADD COLUMN rzp_payment_link_expires_at TIMESTAMPTZ",
        # Whether this merchant actually has EMI enabled on their Razorpay
        # account.
        #
        # Defaults FALSE, and that direction is the whole point. EMI is the
        # right answer to "I can't afford that today" - it recovers the sale
        # at FULL revenue where a discount would cost margin - but only if it
        # exists. An agent that offers instalments the checkout cannot
        # provide has told a customer something untrue about their money,
        # which is the failure this codebase keeps finding (see #2, #19).
        # Silence is the safe default; the merchant turns it on.
        "ALTER TABLE merchant_policies ADD COLUMN emi_available BOOLEAN NOT NULL DEFAULT FALSE",
    ):
        try:
            with get_db() as alter_conn:
                alter_conn.cursor().execute(ddl)
        except Exception:
            pass  # column already exists


if __name__ == "__main__":
    init_db()
    print("Schema created.")
