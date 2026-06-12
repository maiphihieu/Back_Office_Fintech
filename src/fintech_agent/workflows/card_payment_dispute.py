"""Card payment dispute workflow — extensibility demonstration.

This file proves that a new complaint workflow can be added to the system
purely by:
  1. Defining a ``WorkflowSpec``.
  2. Implementing a resolver callable.
  3. Calling ``registry.register(spec)``.

NO core files (customer_chat.py, generic_resolver.py, diagnostic_engine.py,
message_analyzer.py, response_composer.py) are modified.

Usage:
    # Import triggers auto-registration
    import fintech_agent.workflows.card_payment_dispute
"""

from __future__ import annotations

import logging
from typing import Any

from fintech_agent.workflows.workflow_registry import WorkflowSpec, get_registry
from fintech_agent.workflows.resolver_contract import ResolverResult, RootCause

logger = logging.getLogger(__name__)


# ─── Resolver ──────────────────────────────────────────────────

def resolve_card_payment_dispute(
    session_context: dict,
    extracted: Any,
    workflow_hint: str,
    message_type: str,
) -> "ResolutionResult":
    """Resolve card payment dispute evidence.

    In a real system, this would:
      - Look up card transactions by card_last4 + amount + merchant
      - Check chargeback status
      - Compare customer's claimed merchant vs. actual

    For the extensibility demo, this returns a structured result that
    exercises the full pipeline (router → resolver → diagnosis → compose).
    """
    # Import here to avoid circular deps at module level
    from fintech_agent.api.generic_resolver import ResolutionResult

    # Extract claim fields
    amount = None
    card_last4 = None
    merchant_claimed = None

    if extracted is not None:
        amount = getattr(extracted, "amount", None)
        card_last4 = getattr(extracted, "card_last4", None) or (
            getattr(extracted, "bank_reference", None)  # reuse bank_reference for card_last4
        )
        merchant_claimed = getattr(extracted, "merchant_name", None) or getattr(
            extracted, "provider_name", None,
        )

    # Minimal required info
    if amount is None and card_last4 is None:
        return ResolutionResult(
            resolution_status="need_more_info",
            missing_info=["card_last4", "amount", "transaction_time"],
            public_response=(
                "Để kiểm tra giao dịch thẻ, bạn vui lòng cung cấp: "
                "4 số cuối thẻ, số tiền, thời gian giao dịch gần đúng, "
                "và tên cửa hàng/merchant."
            ),
        )

    # Simulate a lookup (in production this calls the card transaction DB)
    user_id = session_context.get("user_id", "")

    logger.info(
        "[CardDisputeResolver] Looking up card disputes for user=%s "
        "amount=%s card_last4=%s merchant=%s",
        user_id, amount, card_last4, merchant_claimed,
    )

    # For demo: return a verified_match with public-safe evidence
    return ResolutionResult(
        resolution_status="resolved",
        resolved_entity_type="card_transaction",
        resolved_entity_id=f"CARD_DEMO_{user_id}",
        public_safe_evidence={
            "transaction_status": "đang kiểm tra",
            "payment_status": "pending",
            "card_status": "active",
        },
        public_response="",
        verified_amount=amount,
        verified_status="pending_review",
        verified_owner_id=user_id,
    )


# ─── Custom Diagnoser (optional) ──────────────────────────────

def diagnose_card_dispute(
    diagnosis: str = "",
    evidence_bundle: dict | None = None,
    extracted_info: dict | None = None,
    **kwargs: Any,
) -> dict:
    """Custom diagnoser for card payment disputes.

    Returns a dict that ``generic_diagnosis._wrap_custom_diagnostic``
    can convert into a ``DiagnosisResult``.
    """
    eb = evidence_bundle or {}
    status = eb.get("transaction_status", "")

    return {
        "can_explain_to_customer": True,
        "root_cause_found": False,
        "issue_location": "card_processing",
        "customer_safe_explanation": (
            "Giao dịch thẻ đang được bộ phận kiểm tra xác minh. "
            "Kết quả sẽ được cập nhật sớm nhất."
        ),
        "requires_staff_review": True,
        "requires_more_customer_info": False,
        "confidence": "medium",
    }


# ─── WorkflowSpec ─────────────────────────────────────────────

CARD_PAYMENT_DISPUTE_SPEC = WorkflowSpec(
    workflow_id="card_payment_dispute",
    display_noun="giao dịch tranh chấp thẻ",
    supported_subject_types=["wallet_user"],
    intent_examples=[
        "giao dịch thẻ lạ", "tranh chấp thanh toán", "không nhận ra giao dịch",
        "thẻ bị trừ tiền", "card dispute", "chargeback",
    ],
    required_identity_fields=["user_id"],
    searchable_claim_fields=[
        "card_last4", "amount", "merchant_name",
        "approximate_time_text",
    ],
    service_types=frozenset({"card_payment"}),
    evidence_schema={
        "card_status": "str",
        "chargeback_status": "str",
        "merchant_name": "str",
    },
    customer_response_policy="",  # Will use generic/default policy
    staff_handoff_policy={
        "requires_staff": ["chargeback", "fraud_suspected"],
        "skip_staff": ["faq", "resolved"],
    },
    resolver=resolve_card_payment_dispute,
    diagnoser=diagnose_card_dispute,
)


# ─── Auto-registration ────────────────────────────────────────

def register() -> None:
    """Register the card_payment_dispute workflow with the global registry."""
    registry = get_registry()
    registry.register(CARD_PAYMENT_DISPUTE_SPEC)
    logger.info("[CardPaymentDispute] Registered card_payment_dispute workflow")


# Auto-register on import
register()
