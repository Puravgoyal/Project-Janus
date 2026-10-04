"""
Project Janus - Backend Test Suite
Tests FastAPI application routes, SSE streaming format, state persistence,
reminder CRUD & completion toggle, persona compilation, and incognito triage suppression.
"""

import asyncio
import json
import re
import shutil
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend import storage, memory_engine, persona_compiler
from backend.main import app


@pytest.fixture(autouse=True)
def isolated_storage_env(tmp_path: Path):
    """Isolate storage in tmp_path seeded with copy of data/ so real user data is never polluted."""
    orig = storage.get_data_dir()
    test_data = tmp_path / "data"
    test_data.mkdir(parents=True, exist_ok=True)
    real_data = Path(__file__).resolve().parent.parent / "data"
    if real_data.exists():
        shutil.copytree(real_data, test_data, dirs_exist_ok=True)
    storage.set_data_dir(test_data)
    try:
        yield
    finally:
        storage.set_data_dir(orig)


@pytest.fixture
def client():
    """TestClient instance for Project Janus backend."""
    return TestClient(app)


def parse_sse_events(raw_sse_text: str) -> list[dict]:
    """Helper to parse Server-Sent Events text into a list of parsed JSON payloads."""
    events = []
    chunks = raw_sse_text.strip().split("\n\n")
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("data:"):
                payload_str = line_str[5:].strip()
                if payload_str == "[DONE]":
                    events.append({"token": "", "done": True})
                else:
                    try:
                        events.append(json.loads(payload_str))
                    except json.JSONDecodeError:
                        events.append({"raw": payload_str})
    return events


# ----------------------------------------------------------------------
# 1. Health & Status Checks
# ----------------------------------------------------------------------

def test_health_endpoints(client):
    """Verify /api/health and /api/health/engines return HTTP 200 and expected schemas."""
    resp1 = client.get("/api/health")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert "status" in data1
    assert "engines" in data1
    assert "gpu" in data1["engines"]
    assert "cpu" in data1["engines"]
    assert "gpu_engine" in data1
    assert "cpu_engine" in data1

    resp2 = client.get("/api/health/engines")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert "engines" in data2


# ----------------------------------------------------------------------
# 2. State Retrieval
# ----------------------------------------------------------------------

def test_get_state_schema_and_contents(client):
    """Verify GET /api/state returns the full cognitive state dictionary."""
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()

    assert "reminders" in data
    assert isinstance(data["reminders"], list)

    assert "work_notes" in data
    assert isinstance(data["work_notes"], list)

    assert "facts" in data
    assert isinstance(data["facts"], list)

    assert "personas" in data
    assert isinstance(data["personas"], list)

    assert "user_profile" in data
    assert isinstance(data["user_profile"], dict)

    assert "engines" in data or "engine_health" in data


# ----------------------------------------------------------------------
# 3. Reminder Lifecycle & Completion Toggle
# ----------------------------------------------------------------------

def test_reminder_crud_and_patch_toggle(client):
    """Verify manual creation, PATCH toggling, and idempotent status updates for reminders."""
    # 1. Create reminder
    create_payload = {
        "text": "Complete Janus backend verification tests",
        "due_date": "2026-09-14",
        "priority": "high"
    }
    create_resp = client.post("/api/reminders", json=create_payload)
    assert create_resp.status_code in [200, 201]
    new_rem = create_resp.json()
    assert "id" in new_rem
    assert new_rem["text"] == "Complete Janus backend verification tests"
    assert new_rem["completed"] is False
    rem_id = new_rem["id"]

    # 2. Toggle completed = True via PATCH /api/reminders/{id}
    patch_resp1 = client.patch(f"/api/reminders/{rem_id}", json={"completed": True})
    assert patch_resp1.status_code == 200
    patch_data1 = patch_resp1.json()
    assert patch_data1.get("status") == "updated"
    assert patch_data1.get("reminder", {}).get("completed") is True

    # 3. Toggle completed = False via PATCH
    patch_resp2 = client.patch(f"/api/reminders/{rem_id}", json={"completed": False})
    assert patch_resp2.status_code == 200
    patch_data2 = patch_resp2.json()
    assert patch_data2.get("reminder", {}).get("completed") is False

    # 4. Fallback POST /api/reminders/{id}/toggle
    toggle_resp = client.post(f"/api/reminders/{rem_id}/toggle", json={"completed": True})
    assert toggle_resp.status_code == 200
    assert toggle_resp.json().get("reminder", {}).get("completed") is True

    # 5. Non-existent ID returns 404
    err_resp = client.patch("/api/reminders/non_existent_rem_xyz999", json={"completed": True})
    assert err_resp.status_code == 404

    # 6. Empty text on creation returns 400
    bad_resp = client.post("/api/reminders", json={"text": "   "})
    assert bad_resp.status_code == 400


# ----------------------------------------------------------------------
# 4. Persona Compilation Endpoint
# ----------------------------------------------------------------------

def test_persona_compile_endpoint(client):
    """Verify POST /api/personas/compile ingests raw text and produces valid persona cards."""
    payload = {
        "character_name": "Hypatia of Alexandria",
        "raw_text": (
            "Hypatia was a Neoplatonist philosopher, astronomer, and mathematician in Alexandria, Egypt. "
            "She was renowned for teaching mathematics and astronomy and editing classical geometry texts."
        )
    }
    resp = client.post("/api/personas/compile", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "success"
    card = body.get("persona", body)

    assert "id" in card
    assert "hypatia" in card["id"].lower()
    assert card["name"] == "Hypatia of Alexandria"
    assert "system_prompt" in card
    assert "greeting" in card
    assert "traits" in card
    assert isinstance(card["traits"], list)

    # Missing fields return 400
    resp_no_text = client.post("/api/personas/compile", json={"character_name": "Someone"})
    assert resp_no_text.status_code == 400

    resp_no_name = client.post("/api/personas/compile", json={"raw_text": "Some text"})
    assert resp_no_name.status_code == 400


# ----------------------------------------------------------------------
# 5. Chat Stream SSE Protocol
# ----------------------------------------------------------------------

def test_chat_stream_sse_protocol(client, monkeypatch):
    """Verify POST /api/chat/stream returns valid Server-Sent Events with tokens and done: true."""
    class DummyStreamResponse:
        status_code = 200
        async def aiter_lines(self):
            chunks = [
                json.dumps({"message": {"content": "Hello"}, "done": False}),
                json.dumps({"message": {"content": " world!"}, "done": True}),
            ]
            for c in chunks:
                yield c
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "stream", lambda *args, **kwargs: DummyStreamResponse())

    payload = {
        "message": "Janus, give me a status report on today's milestones.",
        "mode": "assistant",
        "incognito": False
    }
    resp = client.post("/api/chat/stream", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = parse_sse_events(resp.text)
    assert len(events) > 0, "SSE stream must emit events"

    # Verify event structure
    for event in events:
        assert "token" in event
        assert "done" in event
        assert isinstance(event["done"], bool)

    # Verify terminal event
    has_terminal = any(e.get("done") is True for e in events)
    assert has_terminal, "SSE stream must emit a terminal done: true event"


def test_chat_stream_offline_degraded(client, monkeypatch):
    """Verify that when GPU chat engine is unreachable, SSE stream emits structured error event."""
    import httpx
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")
    monkeypatch.setattr(httpx.AsyncClient, "stream", raise_connect_error)

    resp = client.post("/api/chat/stream", json={"message": "ping"})
    assert resp.status_code == 200
    events = parse_sse_events(resp.text)
    assert len(events) == 1
    assert events[0].get("done") is True
    assert events[0].get("degraded") is True
    assert "offline" in events[0].get("error", "").lower()


def test_chat_stream_with_messages_array(client):
    """Verify POST /api/chat/stream accepts standard messages array."""
    payload = {
        "messages": [
            {"role": "system", "content": "You are Janus."},
            {"role": "user", "content": "Ping test."}
        ],
        "mode": "assistant"
    }
    resp = client.post("/api/chat/stream", json=payload)
    assert resp.status_code == 200
    events = parse_sse_events(resp.text)
    assert len(events) > 0
    assert any(e.get("done") is True for e in events)


def test_chat_stream_empty_input_validation(client):
    """Verify empty message inputs return HTTP 422 Unprocessable Entity."""
    resp1 = client.post("/api/chat/stream", json={"messages": []})
    assert resp1.status_code in [400, 422]

    resp2 = client.post("/api/chat/stream", json={"message": "   "})
    assert resp2.status_code in [400, 422]


# ----------------------------------------------------------------------
# 6. Incognito Mode Memory Lock Integrity
# ----------------------------------------------------------------------

def test_chat_stream_incognito_suppression(client):
    """
    Verify that in Incognito mode:
    1. Chat stream still completes normally.
    2. Background triage is strictly suppressed (zero disk writes).
    """
    # 1. Snapshot pre-incognito state
    pre_state = client.get("/api/state").json()
    pre_reminders = len(pre_state.get("reminders", []))

    # 2. Issue a message containing an explicit reminder phrase in incognito mode
    secret_text = "CONFIDENTIAL: Remind me to destroy negotiation logs before midnight."
    resp = client.post("/api/chat/stream", json={
        "message": secret_text,
        "mode": "assistant",
        "incognito": True
    })
    assert resp.status_code == 200
    events = parse_sse_events(resp.text)
    assert any(e.get("done") is True for e in events)

    # 3. Snapshot post-incognito state
    post_state = client.get("/api/state").json()
    post_reminders = len(post_state.get("reminders", []))

    # Assert reminder count has NOT increased
    assert post_reminders == pre_reminders, (
        f"Incognito mode must strictly suppress writing new reminders to persistent storage. "
        f"Pre: {pre_reminders}, Post: {post_reminders}"
    )


# ----------------------------------------------------------------------
# 7. Memory Engine Context Injection Unit Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_engine_inject_context():
    """Verify context injection synthesizes profile, active reminders, and work notes."""
    prompt = await memory_engine.inject_context(base_system_prompt="Base assistant.", mode="assistant", incognito=False)
    assert "System Identity" in prompt or "Janus" in prompt
    assert "User Profile" in prompt
    assert len(prompt) > 50

    # Incognito mode should purge user facts
    incognito_prompt = await memory_engine.inject_context(base_system_prompt="Base assistant.", mode="assistant", incognito=True)
    assert "Known Facts:" not in incognito_prompt
    assert "Active Work Context" not in incognito_prompt


@pytest.mark.asyncio
async def test_persona_compiler_unit():
    """Verify persona compiler creates valid schema, slug, and traits."""
    card = await persona_compiler.compile_wiki_to_card(
        character_name="Marcus Aurelius",
        raw_text="Marcus Aurelius was Roman emperor and a Stoic philosopher. He wrote Meditations on personal virtue."
    )
    assert card["name"] == "Marcus Aurelius"
    assert "marcus_aurelius" in card["id"]
    assert "system_prompt" in card
    assert "greeting" in card
    assert len(card["traits"]) > 0


@pytest.mark.asyncio
async def test_extract_and_triage_unit():
    """Verify extract_and_triage processes turn, detects reminder, and persists to storage."""
    result = await memory_engine.extract_and_triage(
        user_message="Please remind me to calibrate GPU telemetry buffers tomorrow",
        assistant_reply="I have logged that action item for you."
    )
    assert "reminders" in result
    assert isinstance(result["reminders"], list)
    assert len(result["reminders"]) > 0
    assert any("calibrate GPU telemetry" in r.get("text", "") for r in result["reminders"])

    # Confirm persistence in storage
    all_reminders = await storage.load_reminders()
    assert any("calibrate GPU telemetry" in r.get("text", "") for r in all_reminders)


# ----------------------------------------------------------------------
# 8. Static File Mounting
# ----------------------------------------------------------------------

def test_static_file_mounting(client):
    """Verify root GET / serves frontend HTML content."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


# ----------------------------------------------------------------------
# 9. Persona CRUD Endpoints
# ----------------------------------------------------------------------

def test_persona_crud_endpoints(client):
    """Verify PUT and DELETE endpoints for personas work correctly."""
    # 1. Create/Update a persona
    payload = {
        "name": "Test CRUD Persona",
        "system_prompt": "You are a test.",
        "tagline": "Just testing"
    }
    put_resp = client.put("/api/personas/test_crud", json=payload)
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["status"] == "success"
    assert put_data["persona"]["id"] == "test_crud"
    assert put_data["persona"]["name"] == "Test CRUD Persona"

    # 2. Delete the persona
    del_resp = client.delete("/api/personas/test_crud")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "success"

    # 3. Prevent deletion of Janus
    del_janus = client.delete("/api/personas/janus")
    assert del_janus.status_code == 400
    assert "Cannot delete core Janus persona" in del_janus.json()["detail"]


def test_suggest_tags_endpoint(client):
    """Verify POST /api/personas/suggest-tags returns a list of tags."""
    import unittest.mock as mock
    with mock.patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": '["Cyberpunk", "Hacker", "Rogue", "Techie", "Scout"]'}
        mock_post.return_value = mock_resp

        payload = {"description": "A cyberpunk hacker."}
        resp = client.post("/api/personas/suggest-tags", json=payload)
        assert resp.status_code == 200
        tags = resp.json()
        assert isinstance(tags, list)
        assert len(tags) == 5
        assert "Cyberpunk" in tags
