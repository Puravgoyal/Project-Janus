import json
import pytest
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient

from backend.main import app
from backend import memory_engine, persona_compiler, storage


# ============================================================================
# 1. Prevent Reminders From Matching The Wrong Task
# ============================================================================

@pytest.mark.asyncio
async def test_reminder_app_vs_appliance_no_false_match():
    """Verify 'app' does NOT match 'appliance' via prefix matching."""
    rem_appliance = {"id": "rem_appliance", "text": "Install the appliance", "completed": False}
    rem_app = {"id": "rem_app", "text": "Install the app", "completed": False}

    # 1. Only appliance reminder present: 'I finished installing the app.' must leave it UNCHANGED
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "I finished installing the app.",
        [rem_appliance]
    )
    assert matched is None
    assert action is None

    # 2. Both reminders present: 'I finished installing the app.' must complete ONLY the app reminder
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "I finished installing the app.",
        [rem_appliance, rem_app]
    )
    assert matched is not None
    assert matched["id"] == "rem_app"
    assert action == "completed"

    # 3. Both reminders present: 'I finished installing the appliance.' must complete ONLY the appliance reminder
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "I finished installing the appliance.",
        [rem_appliance, rem_app]
    )
    assert matched is not None
    assert matched["id"] == "rem_appliance"
    assert action == "completed"

    # 4. Cancellation protection: 'Cancel reminder for the app' must NOT cancel appliance
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "Cancel the reminder for the app",
        [rem_appliance]
    )
    assert matched is None
    assert action is None


@pytest.mark.asyncio
async def test_reminder_triage_persisted_state_isolation(tmp_path: Path, monkeypatch):
    """Exercise full conversational triage and assert persisted reminders on disk."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Seed isolated reminders
    await storage.add_reminder("Install the appliance", priority="medium")
    reminders = await storage.load_reminders()
    appliance_id = next(r["id"] for r in reminders if "appliance" in r["text"].lower())

    # Mock CPU extraction to return empty so we isolate conversational triage
    async def dummy_cpu_call(*args, **kwargs):
        class DummyResp:
            status_code = 200
            def json(self):
                return {"message": {"content": "{}"}}
        return DummyResp()

    monkeypatch.setattr("httpx.AsyncClient.post", dummy_cpu_call)

    # 1. User says "I finished installing the app." -> appliance reminder remains incomplete
    await memory_engine.extract_and_triage(
        user_message="I finished installing the app.",
        assistant_reply="Understood."
    )
    reloaded = await storage.load_reminders()
    appliance_rem = next(r for r in reloaded if r["id"] == appliance_id)
    assert appliance_rem["completed"] is False, "Appliance reminder must remain incomplete!"

    # 2. User says "I finished installing the appliance." -> appliance reminder completes
    await memory_engine.extract_and_triage(
        user_message="I finished installing the appliance.",
        assistant_reply="Great job!"
    )
    reloaded = await storage.load_reminders()
    appliance_rem = next(r for r in reloaded if r["id"] == appliance_id)
    assert appliance_rem["completed"] is True, "Appliance reminder must now be completed!"


# ============================================================================
# 2. Interpret Completion Within The Relevant Clause
# ============================================================================

@pytest.mark.asyncio
async def test_reminder_clause_boundaries_and_guards():
    """Verify clause-aware parsing for Case A (uncertainty), Case B (sample), and Case C (future in adjacent sentence)."""
    reminders = [
        {"id": "rem_arch", "text": "Submit architecture report", "completed": False},
        {"id": "rem_sample", "text": "Review sample", "completed": False},
    ]

    # Case A: Uncertainty in completion clause -> UNCHANGED
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "Maybe I finished the architecture report.",
        reminders
    )
    assert matched is None
    assert action is None

    # Case B: Review sample -> COMPLETED (noun 'sample' must not be blacklisted)
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "I finished reviewing the sample.",
        reminders
    )
    assert matched is not None
    assert matched["id"] == "rem_sample"
    assert action == "completed"

    # Case C: Adjacent sentence with future tense -> COMPLETED
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "I finished the architecture report. I will rest tomorrow.",
        reminders
    )
    assert matched is not None
    assert matched["id"] == "rem_arch"
    assert action == "completed"

    # Same-clause future tense -> UNCHANGED
    matched, action = memory_engine.parse_conversational_reminder_intent(
        "I will finish the architecture report tomorrow.",
        reminders
    )
    assert matched is None
    assert action is None


# ============================================================================
# 3. Preserve Empty Traits Through Complete Lifecycle
# ============================================================================

@pytest.mark.asyncio
async def test_empty_traits_complete_lifecycle(tmp_path: Path, monkeypatch):
    """
    Verify explicit empty list [] is preserved across creation, incognito,
    saving, reopening, and multiple save cycles.
    """
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)
    client = TestClient(app)

    # 1. Pydantic validation preserves []
    p = persona_compiler.PersonalitySchema(core_traits=[])
    assert p.core_traits == []

    # 2. Creation via forge endpoint with personality.core_traits: []
    forge_payload = {
        "character_data": {
            "name": "EmptyTraitHero",
            "personality": {
                "archetype": "Silent Wanderer",
                "core_traits": [],
                "flaws": ["Taciturn"]
            },
            "emotion": {
                "default_mood": "Serene",
                "speech_style": "Concise"
            }
        },
        "incognito": False
    }
    resp = client.post("/api/personas/forge", json=forge_payload)
    assert resp.status_code == 200, f"Forge failed: {resp.text}"
    created = resp.json()["character"]
    assert created["traits"] == [], f"Expected empty traits, got: {created['traits']}"
    assert created["personality_traits"] == []
    assert created["forge_schema"]["personality"]["core_traits"] == []
    assert "Core traits:" not in created["system_prompt"]

    # 3. Incognito forge preserves []
    forge_payload["incognito"] = True
    forge_payload["character_data"]["name"] = "IncognitoEmptyHero"
    resp_incog = client.post("/api/personas/forge", json=forge_payload)
    assert resp_incog.status_code == 200
    incog_char = resp_incog.json()["character"]
    assert incog_char["traits"] == []
    assert incog_char["personality_traits"] == []

    # 4. Reopen saved card from disk
    slug = created["id"]
    get_resp = client.get(f"/api/personas/{slug}")
    assert get_resp.status_code == 200
    loaded = get_resp.json()
    assert loaded["traits"] == []
    assert loaded["personality_traits"] == []

    # 5. Cycle 1 Save without modifying traits controls
    put_resp = client.put(f"/api/personas/{slug}", json=loaded)
    assert put_resp.status_code == 200
    saved_cycle1 = put_resp.json()["persona"]
    assert saved_cycle1["traits"] == [], "Cycle 1 save must preserve empty traits"
    assert saved_cycle1["personality_traits"] == []

    # 6. Cycle 2 Save again
    put_resp2 = client.put(f"/api/personas/{slug}", json=saved_cycle1)
    assert put_resp2.status_code == 200
    saved_cycle2 = put_resp2.json()["persona"]
    assert saved_cycle2["traits"] == [], "Cycle 2 save must preserve empty traits"

    # 7. Absent traits fallback to defaults
    absent_payload = {
        "character_data": {
            "name": "DefaultTraitHero",
            "personality": {
                "archetype": "Default Agent"
            }
        },
        "incognito": True
    }
    resp_absent = client.post("/api/personas/forge", json=absent_payload)
    assert resp_absent.status_code == 200
    absent_char = resp_absent.json()["character"]
    assert len(absent_char["traits"]) > 0, "Absent traits must default to non-empty list"


# ============================================================================
# 4. Deterministic Streaming Success and Failure Assertions
# ============================================================================

def test_chat_stream_deterministic_success_and_failure():
    """Verify chat stream deterministic token success and failure event structures."""
    client = TestClient(app)

    # 1. Success case
    class DummySuccessStream:
        status_code = 200
        async def aiter_lines(self):
            chunks = [
                json.dumps({"message": {"content": "Hello"}, "done": False}),
                json.dumps({"message": {"content": " Janus"}, "done": True}),
            ]
            for c in chunks:
                yield c
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient.stream", return_value=DummySuccessStream()):
        resp = client.post("/api/chat/stream", json={"message": "Test ping"})
        assert resp.status_code == 200
        lines = [line.strip() for line in resp.text.split("\n") if line.startswith("data: ")]
        events = [json.loads(line[6:]) for line in lines]
        assert len(events) >= 2
        # First event is token
        assert "token" in events[0]
        assert events[0]["token"] == "Hello"
        assert events[0]["done"] is False
        assert "error" not in events[0]
        # Terminal event
        terminal = events[-1]
        assert terminal["done"] is True
        assert "error" not in terminal

    # 2. Failure case
    import httpx
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("Simulated GPU offline")

    with patch("httpx.AsyncClient.stream", side_effect=raise_connect_error):
        resp = client.post("/api/chat/stream", json={"message": "Test ping offline"})
        assert resp.status_code == 200
        lines = [line.strip() for line in resp.text.split("\n") if line.startswith("data: ")]
        events = [json.loads(line[6:]) for line in lines]
        assert len(events) == 1
        assert "error" in events[0]
        assert "token" not in events[0]
        assert events[0]["done"] is True
        assert events[0]["interrupted"] is True
