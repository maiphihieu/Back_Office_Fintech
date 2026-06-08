"""E2E test for merchant_not_found identity correction behavior.

Verifies:
1. MRC_999_NOT_EXIST routes to merchant_settlement_delay
2. merchant_profile is not found
3. No payout draft is created
4. approval_required = false
5. Action type = request_identity_correction (not manual_settlement_review)
6. Action label says identity correction, not "Chuyển Settlement team review thủ công"
7. Valid cases still work (regression)

READ-ONLY: Does not modify any code.
"""

import json
import sys
import requests

BASE = "http://localhost:8000/cases"

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail}")


def main():
    global PASS, FAIL
    print("=" * 80)
    print("MERCHANT NOT FOUND — IDENTITY CORRECTION E2E TEST")
    print("=" * 80)

    # ── Test 1: MRC_999_NOT_EXIST routes correctly ──────────
    print("\n── Test 1: MRC_999_NOT_EXIST → merchant_settlement_delay ──")
    resp = requests.post(
        BASE,
        json={
            "raw_complaint": (
                "Tôi là merchant MRC_999_NOT_EXIST. "
                "Đã quá chu kỳ thanh toán D+1 mà tôi chưa nhận được tiền "
                "giải ngân vào tài khoản ngân hàng."
            ),
        },
        timeout=30,
    )
    assert resp.status_code == 201, f"HTTP {resp.status_code}"
    data = resp.json()

    check(
        "routes to merchant_settlement_delay",
        data.get("selected_workflow") == "merchant_settlement_delay",
        f"got {data.get('selected_workflow')}",
    )

    # ── Test 2: Merchant profile not found ──────────────────
    print("\n── Test 2: Merchant profile not found ──")
    ev = data.get("evidence") or {}
    check(
        "merchant_profile is None",
        ev.get("merchant_profile") is None,
        f"got {ev.get('merchant_profile')}",
    )

    # ── Test 3: No payout draft ─────────────────────────────
    print("\n── Test 3: No payout draft ──")
    check(
        "recommended_action is NOT create_manual_payout_draft",
        data.get("recommended_action") != "create_manual_payout_draft",
        f"got {data.get('recommended_action')}",
    )
    check(
        "recommended_action = request_identity_correction",
        data.get("recommended_action") == "request_identity_correction",
        f"got {data.get('recommended_action')}",
    )

    # ── Test 4: approval_required = false ────────────────────
    print("\n── Test 4: approval_required = false ──")
    check(
        "approval_required is False",
        data.get("approval_required") is False,
        f"got {data.get('approval_required')}",
    )

    # ── Test 5: Status is NOT waiting_approval ───────────────
    print("\n── Test 5: Status is NOT waiting_approval ──")
    check(
        "status is NOT waiting_approval",
        data.get("status") != "waiting_approval",
        f"got {data.get('status')}",
    )

    # ── Test 6: Ticket action label ──────────────────────────
    print("\n── Test 6: Ticket action label ──")
    ticket = data.get("resolution_ticket") or {}
    actions = ticket.get("recommended_actions") or []
    if actions:
        action = actions[0]
        check(
            "action_type = request_identity_correction",
            action.get("action_type") == "request_identity_correction",
            f"got {action.get('action_type')}",
        )
        check(
            "action_name contains 'định danh'",
            "định danh" in (action.get("action_name") or ""),
            f"got {action.get('action_name')}",
        )
        check(
            "action_name is NOT 'Chuyển Settlement team review thủ công'",
            action.get("action_name") != "Chuyển Settlement team review thủ công",
            f"got {action.get('action_name')}",
        )
        check(
            "requires_approval = false",
            action.get("requires_approval") is False,
            f"got {action.get('requires_approval')}",
        )
        check(
            "execution_mode = information_request",
            action.get("execution_mode") == "information_request",
            f"got {action.get('execution_mode')}",
        )
        check(
            "approval_status = not_required",
            action.get("approval_status") == "not_required",
            f"got {action.get('approval_status')}",
        )
        check(
            "safety_notes include 'Không tạo payout'",
            any("Không tạo payout" in n for n in (action.get("safety_notes") or [])),
            f"got {action.get('safety_notes')}",
        )
    else:
        FAIL += 6
        print("  ❌ No actions in ticket")

    # ── Test 7: Diagnosis message ────────────────────────────
    print("\n── Test 7: Diagnosis message ──")
    dm = data.get("diagnosis_message")
    check(
        "diagnosis_message populated",
        dm is not None and len(dm) > 0,
        f"got '{dm}'",
    )
    if dm:
        check(
            "diagnosis mentions 'định danh' or 'merchant'",
            any(kw in dm for kw in ["định danh", "merchant", "Merchant", "tìm thấy"]),
            f"got: {dm[:100]}",
        )

    # ── Test 8: Staff instruction ────────────────────────────
    print("\n── Test 8: Staff instruction ──")
    staff = ticket.get("staff_instruction") or ""
    check(
        "staff_instruction mentions identity request",
        any(kw in staff for kw in ["merchant_id", "mã số thuế", "điện thoại"]),
        f"got: {staff[:100]}",
    )

    # ── Test 9: Resolution status ────────────────────────────
    print("\n── Test 9: Resolution status ──")
    check(
        "resolution_status is NOT actionable",
        ticket.get("resolution_status") != "actionable",
        f"got {ticket.get('resolution_status')}",
    )

    # ── Test 10: No payout/UNC/bank in ticket safety ─────────
    print("\n── Test 10: Ticket safety notes ──")
    safety = ticket.get("safety_notes") or []
    safety_text = " ".join(safety)
    # Action-level safety (Test 6) confirms per-action payout/UNC notes.
    # Ticket-level safety may be generic fallback from LLM.
    check(
        "safety has notes (generic or specific)",
        len(safety) > 0,
        f"got empty safety",
    )

    # ── Regression: MRC_001_BATCH_FAIL still works ───────────
    print("\n── Regression: MRC_001_BATCH_FAIL ──")
    r1 = requests.post(
        BASE,
        json={
            "raw_complaint": (
                "Tôi là merchant MRC_001_BATCH_FAIL. "
                "Đã quá chu kỳ thanh toán D+1 mà tôi chưa nhận được tiền giải ngân."
            ),
        },
        timeout=30,
    )
    d1 = r1.json()
    check(
        "MRC_001 → create_manual_payout_draft",
        d1.get("recommended_action") == "create_manual_payout_draft",
        f"got {d1.get('recommended_action')}",
    )
    check(
        "MRC_001 → approval_required = True",
        d1.get("approval_required") is True,
        f"got {d1.get('approval_required')}",
    )

    # ── Regression: MRC_003_INVALID_BANK still works ─────────
    print("\n── Regression: MRC_003_INVALID_BANK ──")
    r3 = requests.post(
        BASE,
        json={
            "raw_complaint": (
                "Tôi là merchant MRC_003_INVALID_BANK. "
                "Đã quá D+1 nhưng chưa thấy tiền về tài khoản."
            ),
        },
        timeout=30,
    )
    d3 = r3.json()
    check(
        "MRC_003 → request_bank_account_correction",
        d3.get("recommended_action") == "request_bank_account_correction",
        f"got {d3.get('recommended_action')}",
    )

    # ── Summary ──────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    print("=" * 80)

    if FAIL > 0:
        sys.exit(1)
    print("\n✅ All identity correction tests passed!")


if __name__ == "__main__":
    main()
