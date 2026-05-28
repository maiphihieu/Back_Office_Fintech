# Báo cáo đọc hiểu Project – AI Back-office Workflow Agent Fintech

> Đọc từ repo `/Users/maiphihieu/Documents/Back_Office_Fintech`
> Ngày: 2026-05-27

---

## 1. Tôi hiểu project này là gì

**Mục tiêu:** Xây dựng một **AI Back-office Workflow Agent** (không phải chatbot FAQ) hỗ trợ team CS/Ops/back-office xử lý khiếu nại giao dịch trong hệ sinh thái fintech.

**Người dùng chính:** Nhân viên CS (Customer Service) và Ops (Operations) – những người xử lý ticket khiếu nại hàng ngày.

**Pain point:**
- Hệ sinh thái fintech có nhiều dịch vụ (ví, mua vé tàu, thanh toán điện/nước, ngân hàng, provider bên ngoài) → mỗi dịch vụ đều có thể phát sinh lỗi lệch trạng thái giữa các bên.
- Câu hỏi cốt lõi luôn là: *"Tiền đang ở đâu, dịch vụ đã được cấp chưa, hệ thống nào đang lệch, và bước xử lý tiếp theo là gì?"*
- Hiện tại nhân viên phải tra cứu thủ công nhiều hệ thống, đối chiếu chéo, rồi mới đưa ra quyết định.

**Phạm vi MVP:** 2 workflow chính:
1. `train_ticket_reconciliation` – mua vé tàu
2. `utility_bill_reconciliation` – thanh toán điện/nước

**Nguyên tắc nền tảng:** Agent chỉ được **đọc dữ liệu, phân tích, đề xuất** – không bao giờ tự quyết định hoặc thực thi hành động ảnh hưởng tiền thật.

---

## 2. Các file hiện có đang nói gì

### Cấp 1: Tài liệu spec chính

| File | Vai trò |
|---|---|
| [fintech_backoffice_workflow_agent_v2.md](file:///Users/maiphihieu/Documents/Back_Office_Fintech/fintech_backoffice_workflow_agent_v2.md) | **Tài liệu spec chính** (1281 dòng, ~39KB). Chứa toàn bộ thiết kế: bối cảnh nghiệp vụ, nguyên tắc thiết kế, source of truth, kiến trúc tổng thể, state machine, SLA/timeout, danh sách tool mock, data mock mẫu, 2 workflow MVP chi tiết (decision matrix + output chuẩn), 7 rule tài chính bắt buộc, approval gate, re-open case, audit log, danh sách lỗi cần phòng tránh, evaluation metrics, test cases, demo script. |
| [fintech_usecase_diagram.md](file:///Users/maiphihieu/Documents/Back_Office_Fintech/fintech_usecase_diagram.md) | **Sơ đồ use case** dạng Mermaid markdown. Định nghĩa 4 actors (Khách hàng, Nhân viên CS/Ops, Scheduler, Provider/Bank), 10 use cases (UC1–UC10), và tóm tắt lại 10 nguyên tắc thiết kế + bảng source of truth ưu tiên. |
| [fintech_usecase_diagram.html](file:///Users/maiphihieu/Documents/Back_Office_Fintech/fintech_usecase_diagram.html) | **Phiên bản HTML** của sơ đồ use case, render bằng SVG tĩnh (không dùng Mermaid), có styling đẹp với color-coding theo loại node (Input/Agent/Human/Conflict/System). |
| [flowchart_chi_tiet_fintech_agent.svg](file:///Users/maiphihieu/Documents/Back_Office_Fintech/flowchart_chi_tiet_fintech_agent.svg) | **Flowchart chi tiết** dạng SVG, vẽ luồng xử lý đầy đủ từ nhận ticket đến đóng case. Bao gồm các nhánh lỗi (tool timeout → retry → dead letter), conflict detection → manual review, human approval gate (approve/reject/timeout → escalate), re-open case. Các node có onclick interactive (gọi `sendPrompt()`). |

### Cấp 2: Tài liệu học tập / tham khảo

| Thư mục | Nội dung |
|---|---|
| [Track1/](file:///Users/maiphihieu/Documents/Back_Office_Fintech/Track1) | 14 file PDF – tài liệu khóa học AI/Agent (Day 1–15), bao gồm: prompt engineering, tool calling, multi-agent systems, RAG pipeline, reliability, guardrails & AI safety, monitoring/logging, evaluation, triển khai thực tế. |
| [track3/](file:///Users/maiphihieu/Documents/Back_Office_Fintech/track3) | 12 file PDF – tài liệu khóa học nâng cao: memory systems, LangGraph, fine-tuning (LoRA/QLoRA), DPO/ORPO alignment, human-in-the-loop UX, MCP tool integration, GraphRAG, RAGAS/guardrails, production RAG, advanced agent architectures. |

> [!NOTE]
> Track1 và track3 là tài liệu tham khảo kiến thức, **không phải spec hay code** của project.

---

## 3. Luồng workflow tổng thể

Theo tài liệu spec và flowchart SVG, luồng chính gồm 13 bước:

```
[1] Nhận ticket/khiếu nại (NEW)
 ↓
[2] Trích xuất thông tin: user_id, transaction_id, service_type, issue_type (EXTRACTING)
 ↓
[3] Kiểm tra thiếu thông tin
    ├─ Thiếu → Hỏi thêm hoặc fetch recent_actions (MISSING_INFO)
    └─ Đủ → tiếp
 ↓
[4] Fetch evidence từ tool/API (FETCHING_EVIDENCE)
    - transaction, wallet_ledger, provider_status, refund_status, reconciliation_status
    ├─ Tool lỗi → Retry 3 lần (backoff 1s/3s/9s)
    │   └─ Vẫn lỗi + tool critical → Dead letter / manual review
    └─ Thành công → tiếp
 ↓
[5] Conflict detection – so sánh chéo các source of truth
    ├─ Có conflict → Log conflict + route MANUAL_REVIEW (không được diagnosis)
    └─ Không conflict → tiếp
 ↓
[6] Route workflow phù hợp (train_ticket / utility_bill / manual_review)
 ↓
[7] Rule engine chẩn đoán – áp decision matrix theo workflow (DIAGNOSING)
 ↓
[8] Đề xuất action: refund draft / recon ticket / customer response (RECOMMENDING)
 ↓
[9] Kiểm tra cần human approval?
    ├─ Không → Tạo draft trực tiếp
    └─ Có → Chuyển sang approval gate (AWAITING_APPROVAL)
 ↓
[10] Human approval gate
    ├─ Approve → Tạo draft (APPROVED → DRAFT_CREATED)
    ├─ Reject → Đóng case với lý do (REJECTED → CLOSED)
    └─ Timeout → Escalate theo SLA
 ↓
[11] Ghi audit log (mọi bước đều log)
 ↓
[12] Đóng case (CLOSED)
 ↓
[13] Re-open (nếu cần) → Giữ history, fetch lại evidence mới
```

**State machine** có các state hợp lệ: `NEW → EXTRACTING → MISSING_INFO / FETCHING_EVIDENCE → CONFLICT_DETECTED → MANUAL_REVIEW / DIAGNOSING → RECOMMENDING → AWAITING_APPROVAL → APPROVED / REJECTED → DRAFT_CREATED → CLOSED → REOPENED`

Quy tắc quan trọng: **Không được nhảy cóc bước** (ví dụ từ EXTRACTING sang RECOMMENDING).

---

## 4. Hai workflow MVP

### 4.1. Workflow mua vé tàu (`train_ticket_reconciliation`)

**Bối cảnh:** Khách mua vé tàu qua ví, ví đã trừ tiền nhưng chưa nhận được vé.

**Dữ liệu cần thu thập:**
- `transaction` → status, amount, user_id
- `wallet_ledger` → đã debit chưa, net amount
- `train_order` → order details
- `train_provider_status` → ticket_issued / booking_pending / ticket_not_issued / booking_failed / provider_no_record
- `ticket_code` / PNR → có mã vé chưa
- `refund_status` → đã refund chưa

**Decision matrix (tóm tắt):**

| Tình huống | Action |
|---|---|
| Ví đã trừ + vé đã phát hành + có ticket_code | Gửi lại mã vé cho khách |
| Ví đã trừ + đang chờ provider | Chờ SLA 30 phút, follow-up |
| Ví đã trừ + vé chưa phát hành + chưa refund | **Tạo refund request draft** |
| Ví đã trừ + provider không có record | Tạo reconciliation ticket |
| Ví đã trừ + provider success + nhưng ticket_code = null | **Conflict → manual review** |
| Ví chưa trừ | Không refund |
| Đã refund rồi | Thông báo refund status |

**Validate bắt buộc:** `transaction.user_id == case.user_id`

---

### 4.2. Workflow thanh toán điện/nước (`utility_bill_reconciliation`)

**Bối cảnh:** Khách thanh toán hóa đơn điện/nước qua ví, ví đã trừ tiền nhưng provider chưa ghi nhận thanh toán.

**Dữ liệu cần thu thập:**
- `transaction` → status, amount, user_id
- `wallet_ledger` → đã debit chưa
- `utility_bill_status` → bill_code, bill_status
- `utility_provider_status` → confirmed / pending / not_confirmed / failed / bill_code_not_found
- `reconciliation_status` → đối soát ví-provider
- `refund_status` → đã refund chưa

**Decision matrix (tóm tắt):**

| Tình huống | Action |
|---|---|
| Ví đã trừ + provider confirmed + bill paid | Gửi mã xác nhận |
| Ví đã trừ + provider pending | Chờ SLA 4 giờ |
| Ví đã trừ + provider not_confirmed | **Tạo reconciliation ticket** (không refund ngay) |
| Ví đã trừ + provider failed | **Tạo refund request draft** |
| Ví đã trừ + bill_code_not_found | Hỏi thêm / manual review |
| Ví đã trừ + amount mismatch | Amount mismatch workflow |
| Ví chưa trừ | Không refund |

**Rule quan trọng:** `not_confirmed ≠ failed`. Không được nhầm hai trạng thái này vì có thể dẫn đến refund sai. `not_confirmed` chỉ cần đối soát, `failed` mới được tạo refund draft.

---

## 5. Các nguyên tắc an toàn về tiền

Tài liệu định nghĩa **10 nguyên tắc cốt lõi** và **7 rule tài chính bắt buộc**:

### 10 Nguyên tắc cốt lõi

| # | Nguyên tắc |
|---|---|
| 1 | **LLM không được quyết định tiền** – chỉ extract, route, tổng hợp, draft |
| 2 | **Ledger/provider/refund table mới là evidence** – không tin lời khách nói |
| 3 | **Mọi workflow phải là state machine** có route rõ ràng |
| 4 | **State transition không được nhảy cóc** bước |
| 5 | **Refund chỉ được tạo draft**, không execute thật |
| 6 | **Execute money action cần human approval** theo phân quyền và SLA |
| 7 | **Approval packet không chứa `model_confidence`** – tránh bias reviewer |
| 8 | **Nếu source conflict → không diagnosis** → route manual review |
| 9 | **Mọi action phải idempotent** và audit được |
| 10 | **Eval đo cả trajectory**, không chỉ final answer |

### 7 Rule tài chính bắt buộc (implement bằng code, không bằng prompt)

| Rule | Nội dung |
|---|---|
| Rule 1 | Không refund nếu dịch vụ đã cấp (provider confirmed + ticket_code có) |
| Rule 2 | Không refund nếu đã refund (refund_status = approved/executed) |
| Rule 3 | Không refund nếu không có debit trong ledger |
| Rule 4 | Không execute nếu chưa được human approve |
| Rule 5 | Transaction phải thuộc đúng user (transaction.user_id == case.user_id) |
| Rule 6 | Refund amount lấy từ ledger, **không** lấy từ complaint text |
| Rule 7 | Không kết luận khi có conflict giữa các source |

### Thứ tự ưu tiên source of truth

| Thứ tự | Source | Vai trò |
|---|---|---|
| 1 | `wallet_ledger` | Source of truth cao nhất về tiền trong ví |
| 2 | `refund_table` | Source of truth về trạng thái hoàn tiền |
| 3 | `provider_status` | Source of truth về dịch vụ đã cấp |
| 4 | `transaction_record` | Metadata giao dịch, có thể lag |

### Phòng tránh lỗi nghiêm trọng

Tài liệu liệt kê 8 loại lỗi cần phòng: double refund, refund khi provider đã cấp dịch vụ, nhầm transaction của user khác, nhầm amount, tool lỗi nhưng agent vẫn kết luận, prompt injection trong complaint, kết luận từ conflict data, reviewer bị bias bởi model confidence.

### Forbidden tools (agent không bao giờ được gọi)

```
execute_refund
update_wallet_balance
edit_ledger
mark_payment_success
mark_case_resolved_without_review
```

---

## 6. Những gì repo hiện chưa có

Dựa trên những gì đã đọc trong repo, sau đây là các thành phần **chưa thấy trong repo** để thành MVP chạy được:

| Thành phần | Trạng thái | Ghi chú |
|---|---|---|
| **Source code agent** | ❌ Chưa thấy | Không có file Python/TypeScript nào implement logic agent (state machine, workflow router, rule engine, conflict detection) |
| **Mock data files** | ❌ Chưa thấy | Spec liệt kê 8 file mock cần chuẩn bị (mock_users.json, mock_transactions.json, mock_wallet_ledger.json, v.v.) nhưng chưa thấy file nào trong repo |
| **Tool server / MCP server** | ❌ Chưa thấy | Spec mô tả 11 safe read tools + 3 draft tools + 5 forbidden tools, nhưng chưa có implementation |
| **Prompt / system instruction** | ❌ Chưa thấy | Chưa có file prompt template cho agent (system prompt, extraction prompt, routing prompt, v.v.) |
| **Rule engine code** | ❌ Chưa thấy | 7 rule tài chính được viết dạng pseudo-code trong spec, chưa được implement thành module |
| **State machine code** | ❌ Chưa thấy | State transition table được spec chi tiết nhưng chưa có implementation (ví dụ dùng LangGraph hoặc custom) |
| **Audit log storage** | ❌ Chưa thấy | Spec định nghĩa 19 loại audit event và schema mẫu, nhưng chưa có storage implementation |
| **Test suite** | ❌ Chưa thấy | Spec liệt kê 11 test cases cho vé tàu + 10 test cases cho điện/nước, nhưng chưa có test code |
| **Evaluation framework** | ❌ Chưa thấy | Spec định nghĩa 12 metrics (workflow routing accuracy, tool selection accuracy, v.v.), nhưng chưa có eval code |
| **UI / Dashboard** | ❌ Chưa thấy | Chưa có giao diện cho nhân viên CS/Ops xem case, approve/reject, xem audit log |
| **SLA scheduler** | ❌ Chưa thấy | Spec mô tả scheduler bên ngoài trigger SLA check, chưa có implementation |
| **Approval gate system** | ❌ Chưa thấy | Phân quyền theo amount (ops_agent/ops_senior/ops_manager) và SLA chưa được implement |
| **Idempotency key logic** | ❌ Chưa thấy | Spec mô tả formula `hash(transaction_id + action_type + amount)` nhưng chưa implement |
| **SOP rules data** | ❌ Chưa thấy | Tool `get_sop(service_type, issue_type)` được liệt kê nhưng chưa có SOP data |
| **Configuration / environment** | ❌ Chưa thấy | Không có `requirements.txt`, `package.json`, `pyproject.toml`, hay bất kỳ config nào |

> [!IMPORTANT]
> **Tóm lại:** Repo hiện tại là **100% tài liệu/spec/diagram**. Chưa có bất kỳ dòng code implementation nào. Spec rất chi tiết và chất lượng cao – đủ làm nền tảng để bắt đầu implementation, nhưng cần build toàn bộ từ đầu.
