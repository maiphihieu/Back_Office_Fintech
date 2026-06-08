"""Unit tests for alternative_resolver extraction logic."""

import pytest

from fintech_agent.api.alternative_resolver import (
    extract_alternative_info,
    _extract_amount,
    _extract_bank,
    _extract_time,
)


# ─── Amount extraction ──────────────────────────────────────────

class TestExtractAmount:
    def test_plain_number(self):
        assert _extract_amount("số tiền 500000") == 500000

    def test_k_suffix(self):
        assert _extract_amount("nạp 500k") == 500000

    def test_nghin_suffix(self):
        assert _extract_amount("nạp 500 nghìn") == 500000

    def test_ngan_suffix(self):
        assert _extract_amount("nạp 500 ngàn") == 500000

    def test_tr_suffix(self):
        assert _extract_amount("nạp 1tr") == 1000000

    def test_trieu_suffix(self):
        assert _extract_amount("nạp 2 triệu") == 2000000

    def test_dot_separator(self):
        assert _extract_amount("nạp 500.000") == 500000

    def test_comma_separator(self):
        assert _extract_amount("nạp 500,000") == 500000

    def test_large_amount(self):
        assert _extract_amount("nạp 1.000.000") == 1000000

    def test_no_amount(self):
        assert _extract_amount("tôi nạp tiền rồi") is None

    def test_too_small(self):
        """Amounts < 1000 VND are filtered."""
        assert _extract_amount("nạp 50") is None


# ─── Bank extraction ────────────────────────────────────────────

class TestExtractBank:
    def test_vietcombank_full(self):
        assert _extract_bank("ngân hàng Vietcombank") == "VCB"

    def test_vcb_short(self):
        assert _extract_bank("qua VCB") == "VCB"

    def test_techcombank(self):
        assert _extract_bank("chuyển qua Techcombank") == "TCB"

    def test_bidv(self):
        assert _extract_bank("qua BIDV") == "BIDV"

    def test_mb(self):
        assert _extract_bank("ngân hàng MB") == "MB"

    def test_mbbank(self):
        assert _extract_bank("qua MBBank") == "MB"

    def test_vpbank(self):
        assert _extract_bank("qua VPBank") == "VPBANK"

    def test_no_bank(self):
        assert _extract_bank("tôi nạp tiền") is None


# ─── Time extraction ────────────────────────────────────────────

class TestExtractTime:
    def test_9h_sang(self):
        hour, period = _extract_time("khoảng 9h sáng")
        assert hour == 9

    def test_2h_chieu(self):
        hour, period = _extract_time("khoảng 2h chiều")
        assert hour == 14

    def test_8h_toi(self):
        hour, period = _extract_time("khoảng 8h tối")
        assert hour == 20

    def test_plain_hour(self):
        hour, period = _extract_time("khoảng 9h")
        assert hour == 9

    def test_sang_nay_period(self):
        hour, period = _extract_time("sáng nay tôi nạp")
        assert period is not None
        assert "sáng nay" in period

    def test_hom_qua_period(self):
        hour, period = _extract_time("hôm qua tôi nạp")
        assert period is not None
        assert "hôm qua" in period

    def test_no_time(self):
        hour, period = _extract_time("tôi nạp tiền rồi")
        assert hour is None


# ─── Combined extraction ────────────────────────────────────────

class TestExtractAlternativeInfo:
    def test_full_message(self):
        alt = extract_alternative_info(
            "Tôi nạp khoảng 9h sáng, số tiền 500000, ngân hàng Vietcombank đã trừ tiền."
        )
        assert alt.amount == 500000
        assert alt.bank_name == "VCB"
        assert alt.approximate_hour == 9

    def test_shorthand_message(self):
        alt = extract_alternative_info("Tôi nạp 500k sáng nay qua VCB.")
        assert alt.amount == 500000
        assert alt.bank_name == "VCB"
        assert alt.time_period is not None
        assert "sáng nay" in alt.time_period

    def test_amount_only(self):
        alt = extract_alternative_info("Tôi nạp 500000 đồng.")
        assert alt.amount == 500000
        assert alt.bank_name is None

    def test_bank_only(self):
        alt = extract_alternative_info("qua ngân hàng BIDV")
        assert alt.bank_name == "BIDV"
        assert alt.amount is None
