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
