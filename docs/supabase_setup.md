# Supabase Setup Guide

Hướng dẫn cài đặt Supabase PostgreSQL cho Fintech Agent Back-office.

## Kiến trúc

```
Frontend (React) → FastAPI Backend → Supabase PostgreSQL
                                    ↑
                         SUPABASE_URL + SUPABASE_KEY
                         (chỉ ở backend .env)
```

> ⚠️ **Frontend KHÔNG gọi Supabase trực tiếp.**
> ⚠️ **KHÔNG đưa SUPABASE_KEY vào frontend / VITE_SUPABASE_KEY.**

---

## 1. Tạo Supabase Project

1. Đăng nhập [supabase.com](https://supabase.com)
2. Click **New Project**
3. Chọn tên project (ví dụ: `fintech-agent-dev`)
4. Chọn region gần bạn
5. Đặt database password (lưu lại)
6. Click **Create new project**

## 2. Lấy SUPABASE_URL

1. Vào **Project Settings** → **API**
2. Copy **Project URL** (dạng `https://xxxx.supabase.co`)

## 3. Lấy SUPABASE_KEY

1. Cùng trang **API** → **Project API keys**
2. Copy **`anon` public key** (cho development)
3. Hoặc dùng **`service_role` key** (cho backend-to-backend, có full access)

> ⚠️ Nếu dùng `service_role` key, TUYỆT ĐỐI không commit hay để lộ.

## 4. Tạo `.env`

```bash
cp .env.example .env
```

Điền vào `.env`:

```env
SUPABASE_ENABLED=false   # Để false cho đến khi chạy migration + seed xong
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUz...
SUPABASE_SCHEMA=public
```

> ⚠️ **KHÔNG commit `.env`** — đã có trong `.gitignore`.

## 5. Chạy Migration SQL

1. Mở Supabase Dashboard → **SQL Editor**
2. Copy nội dung file `supabase/migrations/001_initial_schema.sql`
3. Paste vào SQL Editor
4. Click **Run**
5. Verify: kiểm tra Tables tab, phải có 9 bảng:
   - `cases`, `transactions`, `wallet_ledger_entries`
   - `train_provider_statuses`, `utility_provider_statuses`
   - `refunds`, `reconciliation_cases`
   - `approval_packets`, `audit_events`

## 6. Chạy Seed SQL

1. Trong SQL Editor, mở tab mới
2. Copy nội dung file `supabase/seed/001_seed_mock_data.sql`
3. Paste và click **Run**
4. Verify: query `SELECT * FROM transactions;` phải có 8 rows

## 7. Bật Supabase

Trong `.env`, đổi:

```env
SUPABASE_ENABLED=true
```

## 8. Check Connection

```bash
python scripts/check_supabase_connection.py
```

Expected output:
```
SUPABASE_ENABLED: True
SUPABASE_URL: https://xxxx.supabase.co
SUPABASE_KEY: ***set***
✅ Client created successfully
✅ transactions table: 8 rows
✅ wallet_ledger_entries table: 9 rows
✅ refunds table: 8 rows
🎉 All checks passed!
```

## 9. Chạy Backend

```bash
# Với Supabase
SUPABASE_ENABLED=true uvicorn fintech_agent.main:app --reload

# Với JSON fallback (không cần Supabase)
SUPABASE_ENABLED=false uvicorn fintech_agent.main:app --reload
```

## 10. Chạy Demo

```bash
# JSON mode
SUPABASE_ENABLED=false python scripts/run_demo_cases.py

# Supabase mode
SUPABASE_ENABLED=true python scripts/run_demo_cases.py
```

---

## Cảnh báo bảo mật

| Rule | Chi tiết |
|------|---------|
| ❌ Không commit `.env` | File chứa SUPABASE_KEY |
| ❌ Không đưa key vào frontend | Không tạo `VITE_SUPABASE_KEY` |
| ❌ Frontend không gọi Supabase | Chỉ qua FastAPI backend |
| ❌ Không log SUPABASE_KEY | Backend không print key |
| ✅ `service_role` key chỉ ở backend | Không expose ra client |

## Troubleshooting

### "SUPABASE_ENABLED=true but missing: SUPABASE_URL"
→ Kiểm tra `.env` có `SUPABASE_URL=https://...`

### "supabase package not installed"
→ Chạy `pip install supabase>=2.0.0`

### Seed chạy lỗi duplicate
→ Seed dùng `ON CONFLICT DO UPDATE`, chạy lại không lỗi

### Tests fail với SUPABASE_ENABLED=true
→ Tests mặc định chạy với `SUPABASE_ENABLED=false`. Kiểm tra `.env` trong thư mục test.
