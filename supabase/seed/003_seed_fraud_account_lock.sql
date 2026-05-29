-- ============================================================
-- SEED: Fraud Account Lock cases
-- Case 1: High-risk fraud (keep locked)
-- Case 2: False positive (should unlock)
-- ============================================================

-- ── High-risk fraud case ────────────────────────────────────

insert into accounts (
  user_id,
  wallet_id,
  account_status,
  withdrawal_enabled,
  lock_reason,
  current_balance,
  locked_at
)
values (
  'U_FRAUD_001',
  'WALLET_FRAUD_001',
  'locked',
  false,
  'fraud_detection_auto_lock',
  2500000,
  '2026-05-28T09:30:00Z'
)
on conflict (user_id) do update set
  wallet_id = excluded.wallet_id,
  account_status = excluded.account_status,
  withdrawal_enabled = excluded.withdrawal_enabled,
  lock_reason = excluded.lock_reason,
  current_balance = excluded.current_balance,
  locked_at = excluded.locked_at,
  updated_at = now();

insert into fraud_cases (
  fraud_case_id,
  user_id,
  risk_score,
  risk_level,
  fraud_status,
  trigger_reason,
  details
)
values (
  'FRAUD_CASE_001',
  'U_FRAUD_001',
  82,
  'high',
  'under_review',
  'multiple_suspicious_signals',
  '{
    "signals": {
      "multiple_new_devices": true,
      "suspicious_inbound_funds": true,
      "promotion_abuse": false,
      "velocity_anomaly": true,
      "blacklist_match": false
    },
    "recent_transactions": [
      {
        "transaction_id": "TXN_RISK_001",
        "type": "transfer_in",
        "amount": 2000000,
        "source_risk": "high",
        "created_at": "2026-05-28T08:50:00Z"
      }
    ],
    "device_events": [
      {
        "device_id": "DEVICE_NEW_001",
        "event": "new_device_login",
        "ip_country": "unknown",
        "created_at": "2026-05-28T08:40:00Z"
      }
    ],
    "recommended_decision": "keep_locked_request_documents"
  }'::jsonb
)
on conflict (fraud_case_id) do update set
  user_id = excluded.user_id,
  risk_score = excluded.risk_score,
  risk_level = excluded.risk_level,
  fraud_status = excluded.fraud_status,
  trigger_reason = excluded.trigger_reason,
  details = excluded.details,
  updated_at = now();


-- ── False-positive case ─────────────────────────────────────

insert into accounts (
  user_id,
  wallet_id,
  account_status,
  withdrawal_enabled,
  lock_reason,
  current_balance,
  locked_at
)
values (
  'U_FRAUD_002',
  'WALLET_FRAUD_002',
  'locked',
  false,
  'fraud_detection_auto_lock',
  800000,
  '2026-05-28T09:30:00Z'
)
on conflict (user_id) do update set
  wallet_id = excluded.wallet_id,
  account_status = excluded.account_status,
  withdrawal_enabled = excluded.withdrawal_enabled,
  lock_reason = excluded.lock_reason,
  current_balance = excluded.current_balance,
  locked_at = excluded.locked_at,
  updated_at = now();

insert into fraud_cases (
  fraud_case_id,
  user_id,
  risk_score,
  risk_level,
  fraud_status,
  trigger_reason,
  details
)
values (
  'FRAUD_CASE_002',
  'U_FRAUD_002',
  25,
  'low',
  'false_positive_candidate',
  'new_device_login_only',
  '{
    "signals": {
      "multiple_new_devices": false,
      "suspicious_inbound_funds": false,
      "promotion_abuse": false,
      "velocity_anomaly": false,
      "blacklist_match": false
    },
    "recent_transactions": [],
    "device_events": [
      {
        "device_id": "DEVICE_NEW_002",
        "event": "new_device_login",
        "ip_country": "VN",
        "created_at": "2026-05-28T08:40:00Z"
      }
    ],
    "recommended_decision": "unlock_account"
  }'::jsonb
)
on conflict (fraud_case_id) do update set
  user_id = excluded.user_id,
  risk_score = excluded.risk_score,
  risk_level = excluded.risk_level,
  fraud_status = excluded.fraud_status,
  trigger_reason = excluded.trigger_reason,
  details = excluded.details,
  updated_at = now();
