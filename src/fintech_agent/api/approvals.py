"""Approval endpoints — included in cases router.

All approval operations are handled under /cases/{case_id}/approve and
/cases/{case_id}/reject in the cases router for URL consistency.

This module is kept for backward compatibility with the main.py import.
"""

from fastapi import APIRouter

router = APIRouter()
# No endpoints here — approval is handled in cases.py
