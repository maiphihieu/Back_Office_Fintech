"""Workflow Registry — central, extensible catalogue of complaint workflows.

A new workflow is added by:
  1. Defining a ``WorkflowSpec``.
  2. Calling ``registry.register(spec)``.
  3. Providing a resolver callable and (optionally) a diagnoser callable.

The chatbot core reads the registry — it never hard-codes workflow IDs,
display nouns, service types, or resolver dispatch.

IMPORTANT: Do NOT import heavyweight modules (database, LLM, OpenAI) at
module level. Resolver/diagnoser callables are imported lazily when
``resolve()`` / ``diagnose()`` are actually invoked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─── WorkflowSpec ──────────────────────────────────────────────

@dataclass
class WorkflowSpec:
    """Full specification of a single complaint workflow.

    Everything the chatbot core needs to know about a workflow without
    importing workflow-specific code.
    """

    workflow_id: str
    display_noun: str  # human noun for messages, e.g. "giao dịch nạp tiền"

    # Who can file this complaint
    supported_subject_types: list[str] = field(default_factory=lambda: ["wallet_user"])

    # Example intents (used for hint matching; NOT for phrase-match routing)
    intent_examples: list[str] = field(default_factory=list)

    # Identity fields that MUST come from session (never from customer message)
    required_identity_fields: list[str] = field(default_factory=lambda: ["user_id"])

    # Fields the resolver can search on (from customer claims)
    searchable_claim_fields: list[str] = field(
        default_factory=lambda: ["transaction_id", "amount", "bank_name"],
    )

    # Transaction service_type values that this workflow covers
    service_types: frozenset[str] = field(default_factory=frozenset)

    # Evidence schema (describes the shape of verified_evidence)
    evidence_schema: dict[str, Any] = field(default_factory=dict)

    # Rules applied during contradiction detection (field-level config)
    contradiction_rules: dict[str, Any] = field(default_factory=dict)

    # Rules applied during diagnosis (workflow-specific logic selectors)
    diagnosis_rules: dict[str, Any] = field(default_factory=dict)

    # Customer response policy key (matches customer_response_policy.yaml)
    customer_response_policy: str = ""

    # Staff handoff policy
    staff_handoff_policy: dict[str, Any] = field(default_factory=dict)

    # ── Callables (lazy-loaded) ──
    # resolver(session, extracted, workflow_hint, message_type) → ResolutionResult
    resolver: Callable[..., Any] | None = None
    # diagnoser(diagnosis_str, evidence_bundle, extracted_info) → DiagnosticResult
    diagnoser: Callable[..., Any] | None = None


# ─── WorkflowRegistry ─────────────────────────────────────────

class WorkflowRegistry:
    """Thread-safe, extensible workflow catalogue.

    Usage::

        registry = get_registry()
        registry.register(WorkflowSpec(workflow_id="wallet_topup", ...))

        spec = registry.get("wallet_topup")
        all_ids = registry.known_workflow_ids()
    """

    def __init__(self) -> None:
        self._specs: dict[str, WorkflowSpec] = {}

    # ── CRUD ──

    def register(self, spec: WorkflowSpec) -> None:
        """Register (or replace) a workflow spec."""
        if spec.workflow_id in self._specs:
            logger.info(
                "[WorkflowRegistry] Replacing workflow '%s'", spec.workflow_id,
            )
        self._specs[spec.workflow_id] = spec
        logger.info(
            "[WorkflowRegistry] Registered workflow '%s' (noun=%s, subjects=%s)",
            spec.workflow_id, spec.display_noun, spec.supported_subject_types,
        )

    def get(self, workflow_id: str) -> WorkflowSpec | None:
        """Retrieve a registered spec, or ``None``."""
        return self._specs.get(workflow_id)

    def list_ids(self) -> list[str]:
        """All registered workflow IDs (insertion order)."""
        return list(self._specs.keys())

    def known_workflow_ids(self) -> frozenset[str]:
        """Immutable set of all registered workflow IDs.

        Drop-in replacement for the old ``_KNOWN_WORKFLOWS`` frozenset.
        """
        return frozenset(self._specs.keys())

    def list_specs(self) -> list[WorkflowSpec]:
        """All registered specs."""
        return list(self._specs.values())

    # ── Lookup helpers ──

    def get_display_noun(self, workflow_id: str) -> str:
        """Human-readable noun for the workflow (e.g. "giao dịch nạp tiền").

        Returns a generic fallback when the workflow is not registered.
        """
        spec = self._specs.get(workflow_id)
        return spec.display_noun if spec else "giao dịch"

    def get_service_types(self, workflow_id: str) -> frozenset[str] | None:
        """Transaction service_type values covered by a workflow.

        Returns ``None`` when the workflow is not registered (meaning
        "no filter — scan all service types").
        """
        spec = self._specs.get(workflow_id)
        return spec.service_types if spec else None

    def match_workflow_for_service_type(self, svc_type: str) -> str | None:
        """Reverse lookup: given a transaction's service_type, find the
        matching workflow_id. Returns ``None`` if no match.
        """
        svc_lower = svc_type.lower()
        for spec in self._specs.values():
            if svc_lower in spec.service_types:
                return spec.workflow_id
        return None


# ─── Singleton ─────────────────────────────────────────────────

_global_registry: WorkflowRegistry | None = None


def get_registry() -> WorkflowRegistry:
    """Module-level singleton.

    The first call creates the registry AND registers the built-in
    workflows (lazy — importable anywhere without circular deps).
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = WorkflowRegistry()
        _register_builtin_workflows(_global_registry)
    return _global_registry


def reset_registry() -> None:
    """Drop and re-create the global registry (for tests)."""
    global _global_registry
    _global_registry = None


# ─── Built-in Workflow Registration ────────────────────────────

def _register_builtin_workflows(reg: WorkflowRegistry) -> None:
    """Register the five existing workflows.

    Resolver/diagnoser callables are module-level functions — they are
    looked up lazily to avoid circular imports.
    """

    # ── wallet_topup ──
    reg.register(WorkflowSpec(
        workflow_id="wallet_topup",
        display_noun="giao dịch nạp tiền",
        supported_subject_types=["wallet_user"],
        intent_examples=[
            "nạp tiền", "nạp ví", "bank trừ rồi mà ví chưa có",
            "chuyển tiền vào ví", "wallet topup",
        ],
        required_identity_fields=["user_id"],
        searchable_claim_fields=[
            "transaction_id", "amount", "bank_name", "bank_reference",
            "approximate_time_text",
        ],
        service_types=frozenset({"wallet_topup"}),
        evidence_schema={
            "bank_status": "str", "wallet_status": "str",
            "reconciliation_status": "str", "mismatch_type": "str",
        },
        customer_response_policy="wallet_topup",
        staff_handoff_policy={
            "requires_staff": ["contradiction", "unresolved_financial"],
            "skip_staff": ["faq", "off_topic", "resolved"],
        },
    ))

    # ── train_ticket ──
    reg.register(WorkflowSpec(
        workflow_id="train_ticket",
        display_noun="giao dịch vé tàu",
        supported_subject_types=["wallet_user"],
        intent_examples=[
            "vé tàu", "đặt vé tàu", "chưa nhận vé", "mua vé",
            "train ticket",
        ],
        required_identity_fields=["user_id"],
        searchable_claim_fields=[
            "transaction_id", "order_id", "amount",
            "approximate_time_text",
        ],
        service_types=frozenset({"train_ticket"}),
        evidence_schema={
            "ticket_status": "str", "provider_status": "str",
        },
        customer_response_policy="train_ticket",
        staff_handoff_policy={
            "requires_staff": ["contradiction", "unresolved_financial"],
            "skip_staff": ["faq", "off_topic", "resolved"],
        },
    ))

    # ── utility_bill ──
    reg.register(WorkflowSpec(
        workflow_id="utility_bill",
        display_noun="giao dịch thanh toán hóa đơn",
        supported_subject_types=["wallet_user"],
        intent_examples=[
            "hóa đơn điện", "hóa đơn nước", "thanh toán hóa đơn",
            "bill điện", "tiền điện", "tiền nước", "utility bill",
        ],
        required_identity_fields=["user_id"],
        searchable_claim_fields=[
            "transaction_id", "bill_code", "customer_code",
            "amount", "provider_name",
        ],
        service_types=frozenset({"electric_bill", "water_bill"}),
        evidence_schema={
            "bill_status": "str", "provider_status": "str",
        },
        customer_response_policy="utility_bill",
        staff_handoff_policy={
            "requires_staff": ["contradiction", "unresolved_financial"],
            "skip_staff": ["faq", "off_topic", "resolved"],
        },
    ))

    # ── fraud_account_lock ──
    reg.register(WorkflowSpec(
        workflow_id="fraud_account_lock",
        display_noun="tài khoản bị khóa",
        supported_subject_types=["wallet_user"],
        intent_examples=[
            "tài khoản bị khóa", "bị khóa tài khoản", "không đăng nhập được",
            "account locked", "bị chặn tài khoản",
        ],
        required_identity_fields=["user_id"],
        searchable_claim_fields=[],
        service_types=frozenset(),
        evidence_schema={
            "account_status": "str", "withdrawal_enabled": "bool",
        },
        customer_response_policy="fraud_account_lock",
        staff_handoff_policy={
            "requires_staff": ["account_locked", "under_review"],
            "skip_staff": ["account_active", "faq"],
        },
    ))

    # ── merchant_settlement_delay ──
    reg.register(WorkflowSpec(
        workflow_id="merchant_settlement_delay",
        display_noun="giao dịch settlement",
        supported_subject_types=["merchant"],
        intent_examples=[
            "settlement", "giải ngân", "payout", "chưa nhận tiền",
            "merchant settlement",
        ],
        required_identity_fields=["merchant_id"],
        searchable_claim_fields=[
            "payout_id", "batch_id", "settlement_date",
        ],
        service_types=frozenset(),
        evidence_schema={
            "payment_status": "str", "payout_status": "str",
            "settlement_status": "str", "bank_account_status": "str",
        },
        customer_response_policy="merchant_settlement_delay",
        staff_handoff_policy={
            "requires_staff": ["payout_failed", "bank_account_invalid"],
            "skip_staff": ["faq", "off_topic", "completed"],
        },
    ))

    logger.info(
        "[WorkflowRegistry] Registered %d built-in workflows: %s",
        len(reg.list_ids()), reg.list_ids(),
    )
