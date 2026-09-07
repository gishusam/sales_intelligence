import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from app.routers import communications


def test_send_via_resend_blocks_when_api_key_is_missing(monkeypatch):
    monkeypatch.setattr(communications, "_get_resend_key", lambda: "")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            communications.send_via_resend(
                to_email="safe-test@example.com",
                to_name="Safe Test",
                from_email="sender@example.com",
                from_name="Nyumba Zetu",
                subject="Test",
                body="Test body",
            )
        )

    assert exc_info.value.status_code == 503
    assert "RESEND_API_KEY" in str(exc_info.value.detail)
