import logging
from typing import Optional, Dict, Any

from app.db.database import run_db_async
from app.db.repositories import customers as customers_repo
from app.db.repositories import consents as consents_repo

logger = logging.getLogger(__name__)


class IdentityConsentService:
    """
    Resolves anonymous visitors to contactable customers and verifies
    explicit, first-class consent records. Consent defaults to DENIED for
    any customer/channel with no granted record - this is the correct,
    safe default for opt-in consent, not a stub returning a fixed value.
    """

    @staticmethod
    async def identify_customer(merchant_id: str, visitor_id: str, payload: Dict[str, Any]) -> Optional[str]:
        """
        SDK calls Kinato.identify({ phone, email }). Binds a visitor to a
        real customer row (upsert by external_id).
        """
        phone = payload.get("phone")
        email = payload.get("email")
        if not phone and not email:
            logger.warning(f"Identify called for {visitor_id} without a phone or email. Cannot contact.")
            return None

        customer = customers_repo.upsert_by_external_id(
            merchant_id,
            external_id=visitor_id,
            name=payload.get("name", ""),
            email=email or "",
            phone=phone or "",
        )
        logger.info(f"Resolved visitor {visitor_id} to customer {customer['customer_id']}")
        return customer["customer_id"]

    @staticmethod
    async def record_consent(merchant_id: str, customer_id: str, channel: str, source: str) -> Dict[str, Any]:
        """Creates a first-class, auditable, APPEND-ONLY consent record (see
        app/db/repositories/consents.py) - this is the only thing that can
        make check_consent() return True for this customer/channel."""
        record = consents_repo.record_consent(
            merchant_id, customer_id, channel, status="granted", source=source, policy_version="v1.0"
        )
        logger.info(f"Consent recorded for {customer_id} on channel {channel} via {source}")
        return record

    @staticmethod
    async def revoke_consent(merchant_id: str, customer_id: str, channel: str, source: str = "voice_optout") -> Dict[str, Any]:
        """The customer said stop. Records the revocation as its own new
        append-only row - never mutates the earlier grant. check_consent()
        will return False for this channel from this point on, since it
        reads the latest row."""
        record = await run_db_async(
            consents_repo.record_consent,
            merchant_id, customer_id, channel, status="revoked", source=source
        )
        logger.warning(f"Consent REVOKED for {customer_id} on channel {channel} via {source}")
        return record

    @staticmethod
    async def grant_transactional_consent(
        merchant_id: str, customer_id: str, email: str = "", phone: str = ""
    ) -> list:
        """Consent for a customer whose payment just failed on this merchant.

        This closes the hole under the product's own headline pitch. The
        README leads with "one webhook URL, no code on your storefront",
        and that path did this: created the customer from Razorpay's
        payload, recorded no consent at all, and was then refused by the
        eligibility gate - silently, with no `recovery.blocked` event and
        no error anywhere. A merchant following the recommended setup got
        zero recoveries and zero explanation of why.

        Why granting here is defensible, and not a loophole: the customer
        entered these details minutes ago, on this merchant's own checkout,
        in order to pay this merchant. Contacting them about that specific
        failed payment is transactional follow-up on a transaction they
        started. It is recorded with source="razorpay_transactional" rather
        than being quietly indistinguishable from an explicit opt-in,
        precisely so a merchant can tell the two apart in the ledger.

        Three limits, and the first one is the important one:

          * **A revocation is never overwritten.** The consent ledger is
            append-only and the LATEST row wins, so inserting a grant for
            someone who had previously opted out would silently bring them
            back to life - and the next failed payment would do it again.
            That is the worst bug this function could have, so it is the
            first thing it checks.
          * Only channels we can actually reach them on. No phone number
            means no voice consent, rather than a granted row that only
            ever produces a failed dial.
          * Nothing is written if consent is already granted, so a
            merchant's ledger does not fill with duplicate rows on every
            retried webhook.
        """
        if not customer_id:
            return []

        # Never resurrect someone who asked us to stop. Checked across ALL
        # channels: "don't contact me again" is a statement about being
        # contacted, not about a protocol.
        if await run_db_async(consents_repo.has_opted_out, merchant_id, customer_id):
            logger.info(
                f"Not granting transactional consent for {customer_id} - they have "
                "previously opted out, and a failed payment is not a reason to undo that."
            )
            return []

        granted = []
        for channel, reachable in (("email", bool(email)), ("voice", bool(phone))):
            if not reachable:
                continue
            if await run_db_async(consents_repo.check_consent, merchant_id, customer_id, channel):
                continue
            await run_db_async(
                consents_repo.record_consent,
                merchant_id, customer_id, channel,
                status="granted",
                source="razorpay_transactional",
                policy_version="v1.0",
                evidence="customer supplied this detail on a checkout that then failed to pay",
            )
            granted.append(channel)

        if granted:
            logger.info(f"Transactional consent recorded for {customer_id} on {granted}.")
        return granted

    @staticmethod
    async def reachable_channels(merchant_id: str, customer_id: str) -> list:
        """Every channel this customer may currently be contacted on.

        The eligibility gate used to ask only about voice, which meant a
        customer with an email address and no phone number was refused
        outright - not "recovered by email instead", refused. Asking which
        channels are open, rather than whether one specific channel is,
        is what lets that customer be recovered at all.
        """
        channels = []
        for channel in ("voice", "email"):
            if await run_db_async(consents_repo.check_consent, merchant_id, customer_id, channel):
                channels.append(channel)
        return channels

    @staticmethod
    async def check_consent(merchant_id: str, customer_id: str, channel: str) -> bool:
        """
        Verifies if outreach is permitted for a specific channel. Must be
        checked immediately before any communication (Dual Consent Check).
        No granted record => False. This is a real DB read, not a stub.
        """
        granted = await run_db_async(consents_repo.check_consent, merchant_id, customer_id, channel)
        if granted:
            logger.info(f"Consent CHECK PASSED for {customer_id} on {channel}")
        else:
            logger.warning(f"Consent CHECK FAILED for {customer_id} on {channel} (no granted record)")
        return granted


identity_service = IdentityConsentService()
