import asyncio

import pytest
from fastapi import HTTPException

from miniapp import server
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


def test_lobby_events_are_numbered_delivered_and_exclude_actor(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

    socket = FakeSocket()
    monkeypatch.setattr(server, "_lobby_event_seq", 0)
    monkeypatch.setattr(server, "_lobby_recent_events", [])
    server._lobby_connections.clear()
    server._lobby_connections.add(socket)
    asyncio.run(server._lobby_broadcast({
        "type": "player_joined",
        "game": "crash",
        "game_label": "Crash",
        "user": "@player",
        "user_id": 7,
    }))

    assert socket.messages[0]["event_id"] == 1
    assert server.lobby_events(authenticated_user=99, after=0)["events"][0]["game"] == "crash"
    assert server.lobby_events(authenticated_user=7, after=0)["events"] == []
    server._lobby_connections.clear()
