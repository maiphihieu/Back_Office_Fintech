"""API integration tests — FastAPI endpoints.

Tests:
  1. Health check
  2. Create case (TRAIN_001 → WAITING_APPROVAL)
  3. Create case (TRAIN_002 → CLOSED immediately)
  4. Get case details
  5. Get audit trail
  6. Approve case → draft created
  7. Reject case → no draft
  8. Conflict → manual review
  9. Error: approve unknown case (404)
  10. Error: approve twice (409)
  11. Error: get unknown case (404)
  12. Create dead letter case
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:

    def test_health_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestCreateCase:

    def test_create_train_001_waiting_approval(self, client: TestClient) -> None:
        """TRAIN_001: refund required → pauses at waiting_approval."""
        resp = client.post("/cases", json={
            "raw_complaint": "Tôi mua vé tàu TXN_TRAIN_001 nhưng chưa nhận. User U001",
            "user_id": "U001",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "waiting_approval"
        assert data["approval_required"] is True
        assert data["approval_status"] == "pending"
        assert data["case_id"]
        assert data["selected_workflow"] == "train_ticket"
        assert data["recommended_action"] == "create_refund_request_draft"
        assert data["draft_output"] is None
        assert "approve" in data["next_step"].lower()

    def test_create_train_002_closed_immediately(self, client: TestClient) -> None:
        """TRAIN_002: ticket issued → closed (no approval)."""
        resp = client.post("/cases", json={
            "raw_complaint": "Tôi mua vé tàu TXN_TRAIN_002 nhưng chưa nhận",
            "user_id": "U001",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "closed"
        assert data["approval_required"] is False
        assert data["draft_output"]["type"] == "customer_response_draft"

    def test_create_conflict_manual_review(self, client: TestClient) -> None:
        """CONFLICT_001: conflict detected → manual review."""
        resp = client.post("/cases", json={
            "raw_complaint": "Giao dịch TXN_CONFLICT_001 bị lỗi vé tàu",
            "user_id": "U006",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "closed"
        assert data["draft_output"]["type"] == "manual_review"

    def test_create_dead_letter(self, client: TestClient) -> None:
        """No transaction ID → dead letter → closed."""
        resp = client.post("/cases", json={
            "raw_complaint": "Tôi bị trừ tiền nhưng không nhớ mã giao dịch",
            "user_id": "U001",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "closed"
        assert len(data["errors"]) > 0

    def test_create_empty_complaint_rejected(self, client: TestClient) -> None:
        """Empty complaint → 422 validation error."""
        resp = client.post("/cases", json={
            "raw_complaint": "",
        })
        assert resp.status_code == 422


class TestGetCase:

    def test_get_existing_case(self, client: TestClient) -> None:
        """Can retrieve a created case."""
        create_resp = client.post("/cases", json={
            "raw_complaint": "TXN_TRAIN_002 vé tàu",
            "user_id": "U001",
        })
        case_id = create_resp.json()["case_id"]

        resp = client.get(f"/cases/{case_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["case_id"] == case_id
        assert data["status"] == "closed"

    def test_get_unknown_case_404(self, client: TestClient) -> None:
        resp = client.get("/cases/CASE_NONEXISTENT")
        assert resp.status_code == 404


class TestAuditTrail:

    def test_audit_trail_has_events(self, client: TestClient) -> None:
        """Audit trail must have events after case creation."""
        create_resp = client.post("/cases", json={
            "raw_complaint": "TXN_TRAIN_001 vé tàu bị lỗi. User U001",
            "user_id": "U001",
        })
        case_id = create_resp.json()["case_id"]

        resp = client.get(f"/cases/{case_id}/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["case_id"] == case_id
        assert data["event_count"] > 0
        assert len(data["events"]) == data["event_count"]

        event_types = {e["event_type"] for e in data["events"]}
        assert "case_received" in event_types
        assert "info_extracted" in event_types

    def test_audit_unknown_case_404(self, client: TestClient) -> None:
        resp = client.get("/cases/CASE_NONEXISTENT/audit")
        assert resp.status_code == 404


class TestApproveCase:

    def _create_pending_case(self, client: TestClient) -> str:
        """Helper: create TRAIN_001 case (waiting_approval)."""
        resp = client.post("/cases", json={
            "raw_complaint": "TXN_TRAIN_001 mua vé tàu bị lỗi. U001",
            "user_id": "U001",
        })
        return resp.json()["case_id"]

    def test_approve_creates_draft(self, client: TestClient) -> None:
        """Approve → draft created → closed."""
        case_id = self._create_pending_case(client)

        resp = client.post(f"/cases/{case_id}/approve", json={
            "approver": "ops_admin",
            "comment": "Evidence is sufficient",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert data["approval_status"] == "approved"
        assert data["draft_output"]["type"] == "refund_request_draft"
        assert data["draft_output"]["amount"] == 450000

    def test_approve_audit_trail(self, client: TestClient) -> None:
        """Approval must appear in audit trail."""
        case_id = self._create_pending_case(client)
        client.post(f"/cases/{case_id}/approve", json={
            "approver": "ops_admin",
        })

        resp = client.get(f"/cases/{case_id}/audit")
        events = resp.json()["events"]
        event_types = {e["event_type"] for e in events}
        assert "approval_requested" in event_types
        assert "human_approved" in event_types
        assert "draft_created" in event_types
        assert "case_closed" in event_types

    def test_approve_unknown_404(self, client: TestClient) -> None:
        resp = client.post("/cases/CASE_NONEXISTENT/approve", json={
            "approver": "admin",
        })
        assert resp.status_code == 404

    def test_approve_twice_409(self, client: TestClient) -> None:
        """Cannot approve a case that was already decided."""
        case_id = self._create_pending_case(client)
        client.post(f"/cases/{case_id}/approve", json={"approver": "admin_1"})

        resp = client.post(f"/cases/{case_id}/approve", json={"approver": "admin_2"})
        assert resp.status_code == 409


class TestRejectCase:

    def _create_pending_case(self, client: TestClient) -> str:
        resp = client.post("/cases", json={
            "raw_complaint": "TXN_TRAIN_001 mua vé tàu bị lỗi. U001",
            "user_id": "U001",
        })
        return resp.json()["case_id"]

    def test_reject_no_draft(self, client: TestClient) -> None:
        """Reject → no refund draft → closed."""
        case_id = self._create_pending_case(client)

        resp = client.post(f"/cases/{case_id}/reject", json={
            "approver": "ops_admin",
            "reason": "Insufficient evidence",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        assert data["approval_status"] == "rejected"
        assert data["draft_output"]["type"] == "rejected"

    def test_reject_audit_trail(self, client: TestClient) -> None:
        """Rejection must appear in audit trail."""
        case_id = self._create_pending_case(client)
        client.post(f"/cases/{case_id}/reject", json={
            "approver": "reviewer_X",
            "reason": "Suspicious activity",
        })

        resp = client.get(f"/cases/{case_id}/audit")
        events = resp.json()["events"]
        event_types = {e["event_type"] for e in events}
        assert "human_rejected" in event_types

    def test_reject_unknown_404(self, client: TestClient) -> None:
        resp = client.post("/cases/CASE_NONEXISTENT/reject", json={
            "approver": "admin",
            "reason": "no",
        })
        assert resp.status_code == 404

    def test_reject_after_approve_409(self, client: TestClient) -> None:
        """Cannot reject after already approved."""
        case_id = self._create_pending_case(client)
        client.post(f"/cases/{case_id}/approve", json={"approver": "admin_1"})

        resp = client.post(f"/cases/{case_id}/reject", json={
            "approver": "admin_2",
            "reason": "too late",
        })
        assert resp.status_code == 409
