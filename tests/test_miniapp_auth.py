import pytest
from fastapi import HTTPException

from miniapp.server import (
    _SESSION_TTL_SECONDS,
    _issue_session_token,
    _require_actor,
    _require_owner,
    _verify_session_token,
)


def test_session_token_round_trip():
    token = _issue_session_token(123456, now=1_000)
    assert _verify_session_token(token, now=1_000 + _SESSION_TTL_SECONDS) == 123456


def test_session_token_rejects_tampering():
    token = _issue_session_token(123456, now=1_000)
    payload, signature = token.split(".", 1)
    replacement = "0" if signature[-1] != "0" else "1"
    tampered = f"{payload}.{signature[:-1]}{replacement}"

    with pytest.raises(HTTPException) as exc_info:
        _verify_session_token(tampered, now=1_000)
    assert exc_info.value.status_code == 401


def test_session_token_rejects_expiry():
    token = _issue_session_token(123456, now=1_000)
    with pytest.raises(HTTPException) as exc_info:
        _verify_session_token(token, now=1_001 + _SESSION_TTL_SECONDS)
    assert exc_info.value.status_code == 401


def test_actor_cannot_claim_another_user_id():
    with pytest.raises(HTTPException) as exc_info:
        _require_actor(123456, 999999)
    assert exc_info.value.status_code == 403


def test_admin_route_requires_owner_session():
    with pytest.raises(HTTPException) as exc_info:
        _require_owner(-1)
    assert exc_info.value.status_code == 403
