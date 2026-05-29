"""Quick demo: wallet topup complaint end-to-end."""

import os
import sys

# Force mock mode (Supabase may not have topup seed data yet)
os.environ["SUPABASE_ENABLED"] = "false"

sys.path.insert(0, "src")

from fintech_agent.graph.builder import compile_graph


def main():
    app = compile_graph()

    print("=" * 60)
    print("WALLET TOPUP DEMO: Bank debited, wallet still 0")
    print("=" * 60)

    result = app.invoke({
        "raw_complaint": (
            "Tôi nạp tiền từ ngân hàng vào ví, tài khoản ngân hàng đã trừ tiền "
            "nhưng ví vẫn báo 0 đồng. Mã giao dịch TXN_TOPUP_001"
        ),
        "user_id": "U_TOPUP_001",
    })

    print(f"\nCase ID:           {result.get('case_id')}")
    print(f"Status:            {result.get('status')}")
    print(f"Selected Workflow: {result.get('selected_workflow')}")
    print(f"Approval Status:   {result.get('approval_status')}")
    print(f"Approval Required: {result.get('recommended_action').approval_required if result.get('recommended_action') else 'N/A'}")

    action = result.get("recommended_action")
    if action:
        print(f"\nRecommended Action: {action.action_type}")
        print(f"Risk Level:         {action.risk_level}")
        print(f"Diagnosis:          {action.diagnosis}")

    packet = result.get("approval_packet")
    if packet:
        print(f"\nApproval Packet:")
        print(f"  Case ID:    {packet.case_id}")
        print(f"  Action:     {packet.proposed_action}")
        print(f"  Risk:       {packet.risk_level}")

    draft = result.get("draft_output")
    if draft:
        print(f"\nDraft Output: {draft}")

    # Verify expected behavior
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    extracted = result.get("extracted_info")
    assert extracted.service_type == "wallet_topup", f"Expected wallet_topup, got {extracted.service_type}"
    print("✅ service_type = wallet_topup")

    assert extracted.issue_type == "topup_pending", f"Expected topup_pending, got {extracted.issue_type}"
    print("✅ issue_type = topup_pending")

    assert result.get("selected_workflow") == "wallet_topup"
    print("✅ selected_workflow = wallet_topup")

    assert action.action_type.value == "create_force_success_draft"
    print("✅ action = create_force_success_draft")

    assert action.approval_required is True
    print("✅ approval_required = True")

    assert action.risk_level.value == "high"
    print("✅ risk_level = HIGH")

    assert result.get("approval_status") == "pending"
    print("✅ approval_status = pending (graph paused)")

    assert result.get("draft_output") is None
    print("✅ No draft created before approval")

    print("\n🎉 ALL CHECKS PASSED — Wallet topup use case working correctly!")


if __name__ == "__main__":
    main()
