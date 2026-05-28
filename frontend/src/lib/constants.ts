/* ─── Demo scenario definitions ─── */

export const DEMO_SCENARIOS = [
  {
    id: 'TRAIN_001',
    complaint: 'Tôi đã thanh toán mua vé tàu mã giao dịch TXN_TRAIN_001 nhưng chưa nhận được vé. Số tiền 450,000 VND đã bị trừ. Mong được hỗ trợ hoàn tiền.',
    user_id: 'U001',
    transaction_id: 'TXN_TRAIN_001',
    service_type: 'train_ticket',
    expected_action: 'create_refund_request_draft',
    expected_approval: true,
    expected_risk: 'medium',
  },
  {
    id: 'TRAIN_002',
    complaint: 'Tôi mua vé tàu TXN_TRAIN_002 nhưng chưa nhận được vé. Hãy kiểm tra giúp tôi.',
    user_id: 'U002',
    transaction_id: 'TXN_TRAIN_002',
    service_type: 'train_ticket',
    expected_action: 'draft_customer_response',
    expected_approval: false,
    expected_risk: 'low',
  },
  {
    id: 'BILL_002',
    complaint: 'Tôi đã thanh toán tiền điện TXN_BILL_002 nhưng nhà cung cấp chưa xác nhận thanh toán. Mong hỗ trợ.',
    user_id: 'U004',
    transaction_id: 'TXN_BILL_002',
    service_type: 'electric_bill',
    expected_action: 'create_reconciliation_ticket_draft',
    expected_approval: false,
    expected_risk: 'low',
  },
  {
    id: 'BILL_003',
    complaint: 'Tôi thanh toán tiền nước TXN_BILL_003 nhưng bị lỗi. Tiền đã bị trừ 310,000 VND nhưng hóa đơn chưa được thanh toán.',
    user_id: 'U005',
    transaction_id: 'TXN_BILL_003',
    service_type: 'water_bill',
    expected_action: 'create_refund_request_draft',
    expected_approval: true,
    expected_risk: 'medium',
  },
  {
    id: 'CONFLICT_001',
    complaint: 'Giao dịch TXN_CONFLICT_001 mua vé tàu bị lỗi. Ví đã trừ tiền nhưng hệ thống hiện đang pending.',
    user_id: 'U006',
    transaction_id: 'TXN_CONFLICT_001',
    service_type: 'train_ticket',
    expected_action: 'manual_review',
    expected_approval: true,
    expected_risk: 'high',
  },
] as const;

export type DemoScenario = typeof DEMO_SCENARIOS[number];
