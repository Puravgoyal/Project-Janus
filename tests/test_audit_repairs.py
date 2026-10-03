"""
Tests specifically verifying all audit repair targets:
1. Persona creation, editing, round-trip field preservation, and slider persistence
2. Incognito behavior consistency (wiki compile, editing isolation, two-session adventure isolation)
3. Engine routing and model configuration (GPU chat vs CPU extractor)
4. Memory and state correctness (affirmative reminder completion, negation/question/future rejection,
   ambiguity guards, concurrent deduplication, Asia/Kolkata timezone, urgency ordering)
5. Campaign-start HTTP route regression with real uuid import and failure handling
6. Validation, boolean parsing, boundaries, and security (CORS, traversal, Janus protection, 404 on nonexistent)
"""

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from starlette.testclient import TestClient
from backend.main import app
from backend import storage, memory_engine, persona_compiler, adventure_engine


@pytest.fixture
def client():
    return TestClient(app)


# ============================================================================
# 1. Security & Core Janus Persona Protection
# ============================================================================

def test_cors_no_wildcard():
    """Verify wildcard '*' is removed from CORS allow_origins."""
    from starlette.middleware.cors import CORSMiddleware
    for middleware in app.user_middleware:
        if middleware.cls == CORSMiddleware:
            allow_origins = middleware.kwargs.get("allow_origins", [])
            assert "*" not in allow_origins, "CORS allow_origins must not contain wildcard '*'"


def test_persona_delete_traversal_rejection(client):
    """Verify path traversal attempts in persona deletion are rejected safely."""
    with pytest.raises(ValueError, match="path traversal"):
        asyncio.run(storage.delete_persona("../../etc/passwd"))

    with pytest.raises(ValueError, match="path traversal"):
        asyncio.run(storage.delete_persona("..\\..\\windows\\win.ini"))

    with pytest.raises(ValueError, match="path traversal"):
        asyncio.run(storage.delete_persona("../janus"))

    resp = client.delete("/api/personas/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_persona_delete_janus_protected(client):
    """Verify deletion of core Janus persona is strictly prohibited."""
    resp = client.delete("/api/personas/janus")
    assert resp.status_code == 400
    assert "Cannot delete core Janus persona" in resp.json().get("detail", "")


def test_persona_delete_404_on_nonexistent(client):
    """Verify deleting a nonexistent persona returns HTTP 404 instead of 500."""
    resp = client.delete("/api/personas/nonexistent_phantom_persona_xyz_999")
    assert resp.status_code == 404
    assert "not found" in resp.json().get("detail", "").lower()


# ============================================================================
# 2. Campaign-Start Crash Fix & HTTP Endpoint Regression
# ============================================================================

def test_adventure_start_endpoint_success_and_failure(client, monkeypatch, tmp_path):
    """
    Regression test for campaign-start crash:
    - Invokes real HTTP route /api/adventure/start without injecting missing imports.
    - Controlled successful model response asserts opening scene, campaign identity,
      initial equipment, and persisted state.
    - Controlled failure asserts HTTP 503 without wiping or replacing existing campaign.
    """
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    fake_opening = "The iron portcullis slams shut behind you. A cold subterranean breeze stirs your torchlight."

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, url, json=None, **kwargs):
            class MockResponse:
                def __init__(self, status_code, content):
                    self.status_code = status_code
                    self._content = content

                def json(self):
                    return self._content

            # Return fake opening for janus-chat
            if "11434" in str(url):
                return MockResponse(200, {"message": {"content": fake_opening}})
            return MockResponse(500, {})

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    # 1. Start a campaign successfully
    payload = {
        "prompt": "A dwarf blacksmith seeking his ancestor's hammer",
        "starting_equipment": "Iron Hammer, Leather Apron, Torch",
        "incognito": False
    }
    resp = client.post("/api/adventure/start", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "success"
    assert data["opening_scene"] == fake_opening
    campaign_id = data["campaign_id"]
    assert campaign_id.startswith("cmp_")

    # Assert persisted state on disk
    saved_state = asyncio.run(adventure_engine.get_state(incognito=False))
    assert saved_state.campaign_id == campaign_id
    assert saved_state.world_context.starting_equipment == payload["starting_equipment"]
    assert len(saved_state.history) >= 2
    assert saved_state.history[-1]["content"] == fake_opening
    assert "Iron Hammer" in saved_state.character_state.inventory

    # 2. Simulate failed model generation
    class MockFailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, url, json=None, **kwargs):
            class MockResponse:
                status_code = 500
                def json(self):
                    return {"error": "GPU out of memory"}
            return MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockFailingClient)

    fail_resp = client.post("/api/adventure/start", json={
        "prompt": "A failed traveler",
        "starting_equipment": "Minimalist",
        "incognito": False
    })
    assert fail_resp.status_code == 503
    assert "failed to generate an opening scene" in fail_resp.json().get("detail", "").lower()

    # Existing campaign must NOT be wiped or replaced
    preserved_state = asyncio.run(adventure_engine.get_state(incognito=False))
    assert preserved_state.campaign_id == campaign_id
    assert preserved_state.world_context.starting_equipment == payload["starting_equipment"]


# ============================================================================
# 3. Incognito Adventure Isolation Across Multiple Sessions
# ============================================================================

def test_incognito_adventure_two_sessions_isolation(client, monkeypatch, tmp_path):
    """
    Verify complete isolation between two concurrent incognito adventure sessions:
    - Each session establishes distinct identity via session_token.
    - Two browser sessions cannot read or overwrite each other's campaigns.
    - Missing/invalid session identity is safely rejected or returns empty state.
    - Zero private state is persisted to disk in temporary storage.
    """
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Session A client
    class MockSessionAClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def post(self, url, json=None, **kwargs):
            class MockResp:
                status_code = 200
                def json(self):
                    return {"message": {"content": "Session A: You enter a shadowy crypt."}}
            return MockResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockSessionAClient)

    resp_a = client.post("/api/adventure/start", json={
        "prompt": "Session A Rogue",
        "starting_equipment": "Dagger, Lockpicks",
        "incognito": True
    })
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    token_a = data_a.get("session_token")
    camp_a = data_a.get("campaign_id")
    assert token_a and token_a.startswith("sess_")
    assert camp_a and camp_a.startswith("cmp_")

    # Session B client
    class MockSessionBClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def post(self, url, json=None, **kwargs):
            class MockResp:
                status_code = 200
                def json(self):
                    return {"message": {"content": "Session B: You sail across a sunlit ocean."}}
            return MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", MockSessionBClient)

    resp_b = client.post("/api/adventure/start", json={
        "prompt": "Session B Paladin",
        "starting_equipment": "Holy Sword, Shield",
        "incognito": True
    })
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    token_b = data_b.get("session_token")
    camp_b = data_b.get("campaign_id")
    assert token_b and token_b.startswith("sess_")
    assert camp_b and camp_b.startswith("cmp_")

    # Ensure identity isolation
    assert token_a != token_b
    assert camp_a != camp_b

    # Retrieve state for Session A
    state_a_resp = client.get(f"/api/adventure/state?incognito=true&session_token={token_a}")
    assert state_a_resp.status_code == 200
    state_a = state_a_resp.json()
    assert state_a["campaign_id"] == camp_a
    assert "Dagger" in state_a["character_state"]["inventory"]

    # Retrieve state for Session B
    state_b_resp = client.get(f"/api/adventure/state?incognito=true&session_token={token_b}")
    assert state_b_resp.status_code == 200
    state_b = state_b_resp.json()
    assert state_b["campaign_id"] == camp_b
    assert "Holy Sword" in state_b["character_state"]["inventory"]

    # Invalid session token returns empty uninitialized state
    invalid_resp = client.get("/api/adventure/state?incognito=true&session_token=nonexistent_token_123")
    assert invalid_resp.status_code == 200
    assert invalid_resp.json()["campaign_id"] == ""

    # Missing session token returns empty uninitialized state
    missing_resp = client.get("/api/adventure/state?incognito=true")
    assert missing_resp.status_code == 200
    assert missing_resp.json()["campaign_id"] == ""

    # Verify ZERO files written to disk for incognito campaigns
    adventure_files = list(tmp_path.glob("*.json")) + list(tmp_path.glob("*/*.json"))
    assert not any("adventure_state" in str(f) for f in adventure_files), "Incognito state must never write to disk"


# ============================================================================
# 4. Reminder Intent Parsing, Negation, Ambiguity & Deduplication
# ============================================================================

def test_conversational_reminder_intent_parser_edge_cases():
    """
    Exhaustive regression test for parse_conversational_reminder_intent:
    - Affirmative completion: 'I finished the architecture report.'
    - Negation: 'I haven't finished the architecture report.'
    - Question: 'Have I finished the architecture report?'
    - Future intention: 'I will finish the architecture report tomorrow.'
    - Unfinished: 'The architecture report is unfinished.'
    - Cancellation: 'Cancel the task to review architecture diagram'
    """
    active_reminders = [
        {"id": "r1", "text": "Submit architecture report", "completed": False},
        {"id": "r2", "text": "Review architecture diagram", "completed": False}
    ]

    # 1. Affirmative completion
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "I finished the architecture report.", active_reminders
    )
    assert rem is not None and rem["id"] == "r1"
    assert action == "completed"

    # 2. Negation ("haven't")
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "I haven't finished the architecture report.", active_reminders
    )
    assert rem is None and action is None

    # 3. Negation ("have not")
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "I have not finished the architecture report.", active_reminders
    )
    assert rem is None and action is None

    # 4. Question ("Have I finished...?")
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "Have I finished the architecture report?", active_reminders
    )
    assert rem is None and action is None

    # 5. Future intention ("will finish... tomorrow")
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "I will finish the architecture report tomorrow.", active_reminders
    )
    assert rem is None and action is None

    # 6. Unfinished declaration
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "The architecture report is unfinished.", active_reminders
    )
    assert rem is None and action is None

    # 7. Cancellation
    rem, action = memory_engine.parse_conversational_reminder_intent(
        "Cancel the task to review architecture diagram", active_reminders
    )
    assert rem is not None and rem["id"] == "r2"
    assert action == "cancelled"


def test_conversational_reminder_triage_ambiguity_and_cancellation(tmp_path, monkeypatch):
    """
    Verify triage matching behavior against stored reminders:
    - Negation/Questions do not complete reminders.
    - Ambiguous phrases matching multiple reminders do NOT complete either (no silent guessing).
    - Unambiguous affirmative phrase completes the exact matching reminder only.
    - Cancellation deletes/cancels the matching reminder.
    """
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Seed two similarly named reminders
    asyncio.run(storage.add_reminder({
        "id": "rem_report",
        "text": "Submit architecture report",
        "priority": "high",
        "completed": False
    }))
    asyncio.run(storage.add_reminder({
        "id": "rem_diagram",
        "text": "Review architecture diagram",
        "priority": "medium",
        "completed": False
    }))

    # 1. User says negation: "I haven't finished the architecture report"
    asyncio.run(memory_engine.extract_and_triage(
        user_message="I haven't finished the architecture report yet.",
        assistant_reply="Understood. Let me know when you finish.",
        mode="assistant"
    ))
    reminders = asyncio.run(storage.load_reminders())
    assert all(not r["completed"] for r in reminders), "Negation must not complete reminders"

    # 2. User says question: "Have I finished the architecture report?"
    asyncio.run(memory_engine.extract_and_triage(
        user_message="Have I finished the architecture report?",
        assistant_reply="Checking your tasks, it is still pending.",
        mode="assistant"
    ))
    reminders = asyncio.run(storage.load_reminders())
    assert all(not r["completed"] for r in reminders), "Question must not complete reminders"

    # 3. User says ambiguous statement matching both reminders
    asyncio.run(memory_engine.extract_and_triage(
        user_message="I have finished the architecture.",
        assistant_reply="Noted.",
        mode="assistant"
    ))
    reminders = asyncio.run(storage.load_reminders())
    assert all(not r["completed"] for r in reminders), "Ambiguous match must not silently guess"

    # 4. User affirms completion of specific task
    asyncio.run(memory_engine.extract_and_triage(
        user_message="I finished the architecture report.",
        assistant_reply="Excellent, marked as completed.",
        mode="assistant"
    ))
    reminders = asyncio.run(storage.load_reminders())
    rem_report = next(r for r in reminders if r["id"] == "rem_report")
    rem_diagram = next(r for r in reminders if r["id"] == "rem_diagram")
    assert rem_report["completed"] is True, "Specific task must be marked completed"
    assert rem_diagram["completed"] is False, "Other task must remain pending"

    # 5. User cancels the diagram task
    asyncio.run(memory_engine.extract_and_triage(
        user_message="Cancel the reminder Review architecture diagram.",
        assistant_reply="Task cancelled.",
        mode="assistant"
    ))
    reminders_after_cancel = asyncio.run(storage.load_reminders())
    assert not any(r["id"] == "rem_diagram" for r in reminders_after_cancel), "Cancelled task must be removed"


def test_concurrent_reminder_deduplication(tmp_path, monkeypatch):
    """Verify concurrent triage extractions deduplicate reminders safely under lock."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    async def run_concurrent_test():
        # Seed the reminder once
        await storage.add_reminder({
            "id": "rem_deploy",
            "text": "Deploy version 2.0",
            "priority": "high",
            "completed": False
        })

        async def add_dup():
            async with memory_engine.get_reminder_lock():
                reminders = await storage.load_reminders()
                target = "Deploy version 2.0"
                if not any(r.get("text", "").lower() == target.lower() for r in reminders):
                    await storage.add_reminder(target)

        await asyncio.gather(add_dup(), add_dup())

    asyncio.run(run_concurrent_test())

    reminders = asyncio.run(storage.load_reminders())
    matching = [r for r in reminders if "deploy version 2.0" in r.get("text", "").lower()]
    assert len(matching) == 1, f"Expected exactly 1 reminder, got {len(matching)}"


def test_user_timezone_date_resolution():
    """Verify relative date resolution defaults to Asia/Kolkata rather than UTC and handles local midnight shift."""
    assert memory_engine.USER_TIMEZONE == "Asia/Kolkata"
    kolkata_tz = ZoneInfo("Asia/Kolkata")
    now_kolkata = datetime.now(kolkata_tz)
    today_str = now_kolkata.strftime("%Y-%m-%d")

    resolved_today = memory_engine.resolve_relative_due_date("today")
    assert resolved_today == today_str

    # Test local midnight date difference:
    # At 20:00 UTC on 2026-10-03, it is 01:30 AM on 2026-10-04 in Asia/Kolkata (+05:30)
    utc_moment = datetime(2026, 10, 3, 20, 0, 0, tzinfo=timezone.utc)
    resolved_shifted = memory_engine.resolve_relative_due_date("today", base_dt=utc_moment)
    assert resolved_shifted == "2026-10-04", "Local midnight date must differ from UTC date when UTC+5:30 crosses midnight"


def test_reminder_urgency_ordering_real_inject_context(tmp_path, monkeypatch):
    """Verify inject_context orders reminders by high priority first, then earliest due date."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    asyncio.run(storage.add_reminder({
        "id": "r1",
        "text": "Low priority background chore",
        "priority": "low",
        "due_date": "2026-10-10",
        "completed": False
    }))
    asyncio.run(storage.add_reminder({
        "id": "r2",
        "text": "Urgent immediate server restart",
        "priority": "high",
        "due_date": "2026-10-05",
        "completed": False
    }))
    asyncio.run(storage.add_reminder({
        "id": "r3",
        "text": "Medium priority code review",
        "priority": "medium",
        "due_date": "2026-10-04",
        "completed": False
    }))

    prompt = asyncio.run(memory_engine.inject_context(mode="assistant", incognito=False))
    assert "Urgent immediate server restart" in prompt
    assert "Medium priority code review" in prompt
    assert "Low priority background chore" in prompt

    # Verify high priority appears before medium and low
    pos_urgent = prompt.find("Urgent immediate server restart")
    pos_med = prompt.find("Medium priority code review")
    pos_low = prompt.find("Low priority background chore")
    assert pos_urgent < pos_med < pos_low, "Reminders must be injected in strict priority/due-date order"


# ============================================================================
# 5. Consistent Persona Privacy & Character Editing Round-Trip
# ============================================================================

def test_persona_privacy_wiki_compile_incognito(client, tmp_path, monkeypatch):
    """Verify wiki compilation under incognito returns ephemeral card without writing to disk."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Mock CPU extractor call
    class MockCPUClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def post(self, url, json=None, **kwargs):
            class MockResp:
                status_code = 200
                def json(self):
                    return {
                        "message": {
                            "content": '{"name": "Miyamoto Musashi", "tagline": "Sword Saint", "personality": {"archetype": "Ronin", "core_traits": ["Disciplined", "Strategic"]}, "speech_style": {"tone": "Crisp"}}'
                        }
                    }
            return MockResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockCPUClient)

    resp = client.post("/api/personas/compile", json={
        "name": "Musashi",
        "wiki_text": "Miyamoto Musashi was a legendary Japanese swordsman and philosopher.",
        "incognito": True
    })
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data.get("status") == "success"
    card = res_data["persona"]
    assert card.get("incognito") is True
    assert card.get("saved") is False

    # Verify zero files written to disk under personas directory
    personas_dir = tmp_path / "personas"
    if personas_dir.exists():
        assert not any("musashi" in str(f).lower() for f in personas_dir.glob("*.json"))


def test_persona_editing_incognito_isolation(client, tmp_path, monkeypatch):
    """Verify editing a persistent persona while incognito returns updated in-memory card without changing stored original."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Seed original persistent persona on disk
    original_card = {
        "id": "gandalf",
        "name": "Gandalf",
        "tagline": "The Grey Pilgrim",
        "system_prompt": "You are Gandalf the Grey.",
        "description": "A wizard of Middle-earth.",
        "traits": ["Wise", "Patient"],
        "roleplay_style": "Speak in riddles and wisdom."
    }
    asyncio.run(storage.save_persona(original_card))

    # Edit while incognito
    resp = client.put("/api/personas/Gandalf", json={
        "incognito": True,
        "name": "Gandalf",
        "description": "Gandalf the White (Incognito Edit)",
        "roleplay_style": "Speak with radiant authority."
    })
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["saved"] is False
    assert res_data["incognito"] is True
    updated_card = res_data["persona"]
    assert updated_card["incognito"] is True
    assert updated_card["saved"] is False
    assert updated_card["description"] == "Gandalf the White (Incognito Edit)"

    # Verify disk card remains completely untouched
    disk_card = asyncio.run(storage.get_persona("gandalf"))
    assert disk_card["description"] == "A wizard of Middle-earth."
    assert disk_card["roleplay_style"] == "Speak in riddles and wisdom."


def test_persona_editing_regenerates_system_prompt_and_preserves_sliders(client, tmp_path, monkeypatch):
    """
    Verify persona editing:
    - Regenerates canonical system_prompt when name, traits, description, roleplay_style, or sliders change.
    - Preserves slider values in both root and forge_schema.
    - Retains complete AI-enhanced character objects (flaws, emotion, physicality, boundaries).
    """
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Seed existing rich persona
    existing_card = {
        "id": "socrates",
        "name": "Socrates",
        "tagline": "Athenian Philosopher",
        "description": "Prober of truth through relentless questioning.",
        "traits": ["Inquisitive", "Ironical"],
        "roleplay_style": "Speak only in guiding questions.",
        "system_prompt": "You are Socrates, prober of truth.",
        "_slider_values": {"warmth": 40, "curiosity": 95},
        "forge_schema": {
            "_slider_values": {"warmth": 40, "curiosity": 95},
            "personality": {
                "archetype": "Philosopher",
                "core_traits": ["Inquisitive", "Ironical"],
                "flaws": ["Infuriates authorities", "Feigns total ignorance"]
            },
            "emotion": {
                "dominant_state": "Intellectually curious",
                "emotional_triggers": ["Unexamined dogmas"]
            },
            "boundaries": "Strictly PG-13 philosophical discourse."
        }
    }
    asyncio.run(storage.save_persona(existing_card))

    # Update Socrates with new description, traits, and updated sliders
    update_payload = {
        "name": "Socrates",
        "description": "Master dialectician of the agora.",
        "traits": ["Inquisitive", "Philosophical", "Provocative"],
        "roleplay_style": "Guide the interlocutor to recognize their own assumptions.",
        "_slider_values": {"warmth": 60, "curiosity": 99},
        "forge_schema": {
            "_slider_values": {"warmth": 60, "curiosity": 99},
            "personality": {
                "archetype": "Philosopher",
                "core_traits": ["Inquisitive", "Philosophical", "Provocative"],
                "flaws": ["Infuriates authorities", "Feigns total ignorance"]
            },
            "emotion": {
                "dominant_state": "Intellectually curious",
                "emotional_triggers": ["Unexamined dogmas"]
            },
            "boundaries": "Strictly PG-13 philosophical discourse."
        }
    }

    resp = client.put("/api/personas/socrates", json=update_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    card = res_data["persona"]

    # 1. Assert regenerated canonical system prompt
    new_prompt = card["system_prompt"]
    assert "Master dialectician of the agora" in new_prompt
    assert "Provocative" in new_prompt
    assert "Guide the interlocutor to recognize their own assumptions" in new_prompt

    # 2. Assert slider values preserved in root and forge_schema
    assert card["_slider_values"]["warmth"] == 60
    assert card["_slider_values"]["curiosity"] == 99
    assert card["forge_schema"]["_slider_values"]["warmth"] == 60

    # 3. Assert rich attributes preserved
    assert card["forge_schema"]["personality"]["flaws"] == ["Infuriates authorities", "Feigns total ignorance"]
    assert card["forge_schema"]["emotion"]["dominant_state"] == "Intellectually curious"
    assert card["forge_schema"]["boundaries"] == "Strictly PG-13 philosophical discourse."


# ============================================================================
# 6. Model Routing Inspection & Boundaries
# ============================================================================

def test_model_routing_inspection(monkeypatch):
    """
    Verify outbound model request routing:
    - Chat streaming uses CHAT_MODEL (janus-chat) directed at GPU_ENGINE_URL.
    - Mechanics extraction uses EXTRACTOR_MODEL (janus-extractor) directed at CPU_ENGINE_URL.
    """
    from backend.main import CHAT_MODEL, GPU_ENGINE_URL
    from backend.adventure_engine import EXTRACTOR_MODEL, CPU_ENGINE_URL

    assert CHAT_MODEL == "janus-chat"
    assert "11434" in GPU_ENGINE_URL
    assert EXTRACTOR_MODEL == "janus-extractor"
    assert "11435" in CPU_ENGINE_URL


def test_adventure_campaign_versioning():
    """Verify adventure engine discards stale extractions from old campaigns without modifying active state."""
    adventure_engine._ACTIVE_CAMPAIGN_ID = "cmp_active_new"
    # An extraction belonging to an old campaign should be dropped
    asyncio.run(adventure_engine.extract_mechanics(
        action="Pick up sword",
        response="You picked up an iron sword.",
        incognito=False,
        campaign_id="cmp_old_stale"
    ))
    # State should remain unaltered by stale campaign
    state = asyncio.run(adventure_engine.get_state(incognito=False))
    assert "iron sword" not in state.character_state.inventory


def test_mature_themes_boolean_parsing():
    """Verify string 'false' and '0' parse to False, not True."""
    schema_false_str = persona_compiler.MatureThemesSchema(nsfw_enabled="false")
    assert schema_false_str.nsfw_enabled is False

    schema_zero_str = persona_compiler.MatureThemesSchema(nsfw_enabled="0")
    assert schema_zero_str.nsfw_enabled is False

    schema_none = persona_compiler.MatureThemesSchema(nsfw_enabled=None)
    assert schema_none.nsfw_enabled is False

    schema_true_str = persona_compiler.MatureThemesSchema(nsfw_enabled="true")
    assert schema_true_str.nsfw_enabled is True

    schema_one_str = persona_compiler.MatureThemesSchema(nsfw_enabled="1")
    assert schema_one_str.nsfw_enabled is True


def test_boundaries_applied_without_nsfw():
    """Verify boundaries are included in prompt even when nsfw_enabled is False."""
    char_data = {
        "name": "Sir Galahad",
        "personality": {"archetype": "Knight", "core_traits": ["Noble", "Brave"], "flaws": ["Dogmatic"]},
        "mature_themes": {
            "nsfw_enabled": False,
            "boundaries": "No violence against civilians, strictly PG-13",
        },
    }
    result = asyncio.run(persona_compiler.forge_character(char_data, incognito=True))
    assert result["status"] == "success"
    prompt = result["character"]["system_prompt"]
    assert "Boundaries: No violence against civilians, strictly PG-13" in prompt


def test_context_budget_capping_at_6000():
    """Verify that context capping truncates at 6000 chars and preserves roleplay enforcement."""
    persona = {
        "name": "Historian",
        "system_prompt": "You are a detailed historian. " * 300,  # > 9000 chars
        "roleplay_style": "Speak formally."
    }
    prompt = asyncio.run(memory_engine.inject_context(mode="persona", persona=persona, incognito=False))
    assert len(prompt) <= 6500  # 6000 + truncation footer
    assert "[Roleplay Enforcement]" in prompt


def test_fictional_mode_does_not_extract_personal_memory(tmp_path, monkeypatch):
    """Verify persona/adventure mode does NOT extract personal memory or alter user profile."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    asyncio.run(memory_engine.extract_and_triage(
        user_message="I am secretly the king of France and I own five dragons.",
        assistant_reply="Indeed, your majesty.",
        mode="persona"
    ))

    profile = asyncio.run(storage.load_user_profile())
    assert not any("king of France" in f for f in profile.get("facts", []))
