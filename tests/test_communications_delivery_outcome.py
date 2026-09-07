import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from app.routers import communications


def test_delivery_outcome_is_sent_when_everything_succeeds():
    assert communications._delivery_outcome(
        sent_count=4,
        failed_count=0,
    ) == "sent"


def test_delivery_outcome_is_sent_with_issues_when_some_fail():
    assert communications._delivery_outcome(
        sent_count=3,
        failed_count=1,
    ) == "sent_with_issues"


def test_delivery_outcome_is_failed_when_everything_fails():
    assert communications._delivery_outcome(
        sent_count=0,
        failed_count=4,
    ) == "failed"
