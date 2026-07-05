"""Smoke tests for the Phase-1 foundation core (no DB required).

Feature agents add feature/e2e suites later; these just prove the app imports,
the registry works, and security primitives round-trip.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.registry import MODULE_REGISTRY
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.main import app


def test_health_ok() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_foundation_registered() -> None:
    assert MODULE_REGISTRY.get("platform") is not None


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("Secret123")
    assert hashed != "Secret123"  # never plaintext
    assert verify_password("Secret123", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip() -> None:
    jwt_str = create_access_token(
        user_id=1, active_society_id=None, role_ids=[], password_state="active"
    )
    payload = decode_access_token(jwt_str)
    assert payload["user_id"] == 1
    assert payload["password_state"] == "active"


def test_refresh_token_hash_is_deterministic() -> None:
    assert hash_refresh_token("abc") == hash_refresh_token("abc")
    assert hash_refresh_token("abc") != hash_refresh_token("def")
