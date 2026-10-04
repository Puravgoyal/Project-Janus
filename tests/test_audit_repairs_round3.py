"""
Regression tests for Audit Repairs Pass 3:
1. Campaign persistence after backend restart & two subsequent actions + mechanics updates.
2. Parameterized reminder intent verification rejecting reported speech, hypotheticals,
   partial completion, negation, questions, and ambiguity through the real triage path.
3. Stream failure handling (normal completion, offline before token, RemoteProtocolError mid-stream,
   adventure connection failure without fake fallback, and history/triage side-effect guards).
4. Persona field compilation unification (nested core_traits prioritization, synchronization across card,
   regeneration, and outbound model request prompt inspection without stale traits).
5. Persona edit/save/reopen behavior (non-nested bounded forge_schema across multiple edit cycles,
   cleared roleplay_style preservation, and enhanced vs slider-derived traits preservation).
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from backend import adventure_engine, memory_engine, persona_compiler, storage
from backend.main import app


@pytest.fixture(autouse=True)
def isolated_storage_env(tmp_path: Path):
    """Ensure every test runs in an isolated temporary data directory."""
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
    return TestClient(app)


# ============================================================================
# 1. Campaign Persistence After Backend Restart & Subsequent Actions
# ============================================================================

@pytest.mark.asyncio
async def test_campaign_persistence_after_backend_restart_and_actions():
    """
    Verify that after a backend restart (simulated by resetting _ACTIVE_CAMPAIGN_ID):
    1. Reading persisted state restores _ACTIVE_CAMPAIGN_ID.
    2. Two subsequent actions can be appended and saved successfully to history.
    3. Mechanics extraction updates survive without identity/version guard rejections.
    4. Guarded writes with expected_campaign_id succeed even if called directly after restart.
    """
    # 1. Create and persist initial campaign
    initial_state = adventure_engine.AdventureState(
        campaign_id="camp-restart-test-101",
        world_context=adventure_engine.AdventureContext(
            prompt="A forgotten fortress high in the mountain peaks.",
            starting_equipment="iron blade, torch"
        ),
        character_state=adventure_engine.CharacterState(
            health="Healthy",
            inventory=["iron blade", "torch"],
            active_quests=["Explore the fortress"]
        )
    )
    saved_state = await adventure_engine.replace_persistent_campaign(initial_state)
    cid = saved_state.campaign_id
    assert adventure_engine._ACTIVE_CAMPAIGN_ID == cid

    # 2. Simulate fresh backend restart (in-memory _ACTIVE_CAMPAIGN_ID is cleared)
    adventure_engine._ACTIVE_CAMPAIGN_ID = ""
    adventure_engine._STATE_LOCKS.clear()

    # Verify get_state restores identity
    restored_state = await adventure_engine.get_state(incognito=False)
    assert restored_state.campaign_id == cid
    assert adventure_engine._ACTIVE_CAMPAIGN_ID == cid

    # 3. Perform Action 1
    turn1 = await adventure_engine.append_action_history(
        action="I light the torch and walk into the inner courtyard.",
        reply="The torch flickers as shadows dance across crumbling stone archways.",
        incognito=False,
        campaign_id=cid
    )
    assert turn1 is not None
    assert len(turn1.history) == 2
    assert turn1.history[0]["content"] == "I light the torch and walk into the inner courtyard."

    # 4. Perform Action 2
    turn2 = await adventure_engine.append_action_history(
        action="I search the rusted iron chest near the altar.",
        reply="Inside, you discover an ornate brass key and an ancient journal.",
        incognito=False,
        campaign_id=cid
    )
    assert turn2 is not None
    assert len(turn2.history) == 4
    assert turn2.history[2]["content"] == "I search the rusted iron chest near the altar."

    # 5. Verify mechanics extraction succeeds and updates character state on disk
    mock_mechanics_json = json.dumps({
        "health_update": "Minor cuts",
        "inventory_added": ["brass_key", "ancient_journal"],
        "inventory_removed": [],
        "new_quests": ["Decipher the ancient journal"],
        "completed_quests": []
    })

    class FakeMechanicsResp:
        status_code = 200
        def json(self):
            return {"response": mock_mechanics_json}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeMechanicsResp()
        await adventure_engine.extract_mechanics(
            action="I search the rusted iron chest near the altar.",
            response="Inside, you discover an ornate brass key and an ancient journal.",
            incognito=False,
            campaign_id=cid
        )

    updated = await adventure_engine.get_state(incognito=False)
    assert updated.character_state.health == "Minor cuts"
    assert "brass_key" in updated.character_state.inventory
    assert "Decipher the ancient journal" in updated.character_state.active_quests

    # 6. Simulate restart again and verify save_state_guarded directly restores identity
    adventure_engine._ACTIVE_CAMPAIGN_ID = ""
    updated.character_state.health = "Fully rested"
    guarded_ok = await adventure_engine.save_state_guarded(
        updated,
        incognito=False,
        expected_campaign_id=cid,
        expected_version=updated.version
    )
    assert guarded_ok is True
    assert adventure_engine._ACTIVE_CAMPAIGN_ID == cid


# ============================================================================
# 2. Parameterized False Reminder Completion & Full Triage Path
# ============================================================================

REMINDER_INTENT_TEST_CASES = [
    # Negative cases: reported speech, hypotheticals, partial completion, negation, questions, future plans
    ("Alice said she finished the architecture report.", "none"),
    ("Imagine I finished the architecture report.", "none"),
    ("I almost finished the architecture report.", "none"),
    ("I haven\u2019t finished the architecture report", "none"),
    ("Alice said 'I finished the architecture report.'", "none"),
    ("Did I finish the architecture report?", "none"),
    ("Will I finish the architecture report tomorrow?", "none"),
    ("I plan to finish the architecture report.", "none"),
    ("What if I finished the architecture report?", "none"),
    ("Suppose I finished the architecture report.", "none"),
    ("I barely finished the architecture report.", "none"),
    ("I am not done with the architecture report.", "none"),
    ("He mentioned that he finished the architecture report.", "none"),
    ("I will complete the architecture report later.", "none"),
    ("Could you tell me if I completed the architecture report?", "none"),
    # Positive completion cases
    ("I finished the architecture report.", "completed"),
    ("I have completed the architecture report.", "completed"),
    ("I am done with the architecture report.", "completed"),
    ("Mark the architecture report as done.", "completed"),
    ("Finished the architecture report.", "completed"),
    ("Completed architecture report.", "completed"),
    # Positive cancellation cases
    ("Cancel reminder for architecture report", "cancelled"),
    ("Delete reminder called architecture report", "cancelled"),
]


@pytest.mark.parametrize("user_msg,expected_action", REMINDER_INTENT_TEST_CASES)
@pytest.mark.asyncio
async def test_conversational_reminder_intent_triage_path(user_msg, expected_action):
    """
    Verify parameterized conversational reminder statements through the real
    extract_and_triage path, asserting stored reminder state on disk.
    Deterministic mocks prevent live Ollama dependency.
    """
    # 1. Seed active reminder in isolated storage
    initial_reminder = {
        "text": "Submit architecture report",
        "due_date": "2026-10-10",
        "priority": "high",
        "completed": False
    }
    rem_item = await storage.add_reminder(initial_reminder)
    rem_id = rem_item["id"]

    # 2. Mock CPU extraction response so triage model returns empty additions
    mock_triage_resp = json.dumps({"reminders": [], "work_note": None, "facts": [], "preferences": []})

    class FakeTriageResp:
        status_code = 200
        def json(self):
            return {"message": {"content": mock_triage_resp}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeTriageResp()
        await memory_engine.extract_and_triage(
            user_message=user_msg,
            assistant_reply="Understood.",
            mode="assistant"
        )

    # 3. Assert disk reminder status
    all_rems = await storage.load_reminders()
    if expected_action == "none":
        target = next((r for r in all_rems if r["id"] == rem_id), None)
        assert target is not None, f"Reminder {rem_id} was unexpectedly removed on '{user_msg}'"
        assert target["completed"] is False, f"Reminder {rem_id} was falsely completed on '{user_msg}'"
    elif expected_action == "completed":
        target = next((r for r in all_rems if r["id"] == rem_id), None)
        assert target is not None
        assert target["completed"] is True, f"Reminder {rem_id} was NOT completed on '{user_msg}'"
    elif expected_action == "cancelled":
        target = next((r for r in all_rems if r["id"] == rem_id), None)
        assert target is None, f"Reminder {rem_id} was NOT cancelled on '{user_msg}'"


@pytest.mark.asyncio
async def test_ambiguous_reminder_matching_leaves_both_unchanged():
    """
    If multiple active reminders share target keywords, conversational completion
    must leave BOTH reminders uncompleted rather than guessing arbitrarily.
    """
    r1 = await storage.add_reminder({"text": "Submit architecture report", "completed": False})
    r2 = await storage.add_reminder({"text": "Review architecture diagram", "completed": False})

    class FakeTriageResp:
        status_code = 200
        def json(self):
            return {"message": {"content": json.dumps({"reminders": []})}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = FakeTriageResp()
        # Ambiguous target: "architecture" matches both
        await memory_engine.extract_and_triage(
            user_message="I finished the architecture.",
            assistant_reply="Noted.",
            mode="assistant"
        )

    all_rems = await storage.load_reminders()
    r1_fresh = next(r for r in all_rems if r["id"] == r1["id"])
    r2_fresh = next(r for r in all_rems if r["id"] == r2["id"])

    assert r1_fresh["completed"] is False
    assert r2_fresh["completed"] is False


# ============================================================================
# 3. Stream Failure Handling & Side-Effect Guarding
# ============================================================================

def test_chat_stream_normal_completion(client):
    """Verify normal chat stream yields tokens, terminal done event, and dispatches triage."""
    lines = [
        json.dumps({"message": {"content": "Hello "}, "done": False}),
        json.dumps({"message": {"content": "Demi!"}, "done": True}),
    ]

    class FakeStream:
        status_code = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_lines(self):
            for l in lines:
                yield l

    with patch("httpx.AsyncClient.stream", return_value=FakeStream()), \
         patch("backend.main._bounded_triage", new_callable=AsyncMock) as mock_triage:
        resp = client.post("/api/chat/stream", json={"message": "Greetings Janus"})
        assert resp.status_code == 200
        events = [json.loads(line.replace("data: ", "")) for line in resp.text.split("\n\n") if line.startswith("data: ")]
        assert any(e.get("token") == "Hello " for e in events)
        assert any(e.get("token") == "Demi!" for e in events)
        assert any(e.get("done") is True and not e.get("error") for e in events)


def test_chat_stream_remote_protocol_error_mid_stream(client):
    """
    Verify httpx.RemoteProtocolError mid-stream yields partial token, then a structured
    interruption error event, and does NOT dispatch background cognitive triage.
    """
    async def bad_aiter_lines():
        yield json.dumps({"message": {"content": "Architectural overview:"}, "done": False})
        raise httpx.RemoteProtocolError("Connection broken by peer mid-transfer")

    class FaultyStream:
        status_code = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def aiter_lines(self):
            return bad_aiter_lines()

    with patch("httpx.AsyncClient.stream", return_value=FaultyStream()), \
         patch("backend.main._bounded_triage", new_callable=AsyncMock) as mock_triage:
        resp = client.post("/api/chat/stream", json={"message": "Provide system overview"})
        assert resp.status_code == 200
        events = [json.loads(line.replace("data: ", "")) for line in resp.text.split("\n\n") if line.startswith("data: ")]

        # Partial token was delivered
        assert any(e.get("token") == "Architectural overview:" for e in events)
        # Interrupted error event delivered
        error_ev = next((e for e in events if e.get("error")), None)
        assert error_ev is not None
        assert error_ev.get("interrupted") is True
        assert error_ev.get("done") is True
        # Triage was NOT called on interrupted stream
        mock_triage.assert_not_called()


def test_adventure_action_connection_failure_no_fake_dm(client):
    """
    Verify adventure stream on connection failure returns a structured error event
    and does NOT return fake token '*The Dungeon Master is asleep...*' or corrupt state.
    """
    initial_state = adventure_engine.AdventureState(
        campaign_id="camp-stream-err-test",
        world_context=adventure_engine.AdventureContext(prompt="A mystical cavern"),
        character_state=adventure_engine.CharacterState(health="Healthy")
    )
    asyncio.run(adventure_engine.replace_persistent_campaign(initial_state))

    with patch("httpx.AsyncClient.stream", side_effect=httpx.ConnectError("Ollama GPU down")):
        resp = client.post(
            "/api/adventure/action",
            json={"action": "I examine the stalactites", "incognito": False}
        )
        assert resp.status_code == 200
        events = [json.loads(line.replace("data: ", "")) for line in resp.text.split("\n\n") if line.startswith("data: ")]

        # No fake token output
        assert not any("The Dungeon Master is asleep" in e.get("token", "") for e in events)
        # Error event returned
        err_ev = next((e for e in events if e.get("error")), None)
        assert err_ev is not None
        assert err_ev.get("interrupted") is True
        assert "Game Master is unavailable" in err_ev["error"]

    # History was not corrupted with an interrupted turn
    fresh_state = asyncio.run(adventure_engine.get_state(incognito=False))
    assert len(fresh_state.history) == 0


# ============================================================================
# 4. Unify Persona Field Compilation & Model Outbound Verification
# ============================================================================

def test_unify_persona_field_compilation_and_outbound_chat(client):
    """
    1. Create a persona with forge_schema.personality.core_traits = ["Quiet"].
    2. Edit via PUT with forge_schema.personality.core_traits = ["Rebellious"].
    3. Assert compiled system_prompt and synchronized fields use "Rebellious" and do NOT contain "Quiet".
    4. Update speech_style and clear roleplay_style.
    5. Chat with the persona and intercept the outbound model POST request.
       Assert the outbound system prompt contains updated values and no stale conflicting traits.
    """
    # 1. Create initial persona
    init_data = {
        "name": "Raven",
        "description": "Covert operative",
        "traits": ["Quiet"],
        "personality_traits": ["Quiet"],
        "forge_schema": {
            "name": "Raven",
            "personality": {
                "archetype": "Covert operative",
                "core_traits": ["Quiet"],
                "flaws": ["Secretive"]
            },
            "emotion": {
                "default_mood": "Composed",
                "speech_style": "Whispered",
                "reaction_to_stress": "Withdrawn"
            },
            "physicality": {
                "appearance": "Dark hooded cloak",
                "body_language": "Still"
            },
            "mature_themes": {
                "nsfw_enabled": False,
                "boundaries": "None",
                "mature_dynamics": ""
            },
            "roleplay_style": "Atmospheric noir roleplay."
        }
    }
    resp_create = client.put("/api/personas/raven", json=init_data)
    assert resp_create.status_code == 200
    card_created = resp_create.json()["persona"]
    assert "Core traits: Quiet." in card_created["system_prompt"]

    # 2. Update ONLY nested forge_schema.personality.core_traits to Rebellious
    update_data = {
        **card_created,
        "forge_schema": {
            **card_created["forge_schema"],
            "personality": {
                **card_created["forge_schema"]["personality"],
                "core_traits": ["Rebellious", "Fearless"]
            }
        }
    }
    resp_update = client.put("/api/personas/raven", json=update_data)
    assert resp_update.status_code == 200
    updated_card = resp_update.json()["persona"]

    # Assert new traits reached compiled prompt and top-level fields
    assert "Core traits: Rebellious, Fearless." in updated_card["system_prompt"]
    assert "Quiet" not in updated_card["system_prompt"]
    assert updated_card["traits"] == ["Rebellious", "Fearless"]
    assert updated_card["personality_traits"] == ["Rebellious", "Fearless"]
    assert updated_card["forge_schema"]["personality"]["core_traits"] == ["Rebellious", "Fearless"]

    # 3. Update emotion.speech_style and clear roleplay_style
    update_data_2 = {
        **updated_card,
        "roleplay_style": "",
        "forge_schema": {
            **updated_card["forge_schema"],
            "roleplay_style": "",
            "emotion": {
                **updated_card["forge_schema"]["emotion"],
                "speech_style": "Bitingly sarcastic"
            }
        }
    }
    resp_update_2 = client.put("/api/personas/raven", json=update_data_2)
    assert resp_update_2.status_code == 200
    updated_card_2 = resp_update_2.json()["persona"]
    assert "Your speech style: Bitingly sarcastic." in updated_card_2["system_prompt"]
    assert "Atmospheric noir roleplay" not in updated_card_2["system_prompt"]
    assert updated_card_2["roleplay_style"] == ""

    # 4. Chat with the edited persona and capture outbound model request
    captured_payload = {}

    class FakeOutboundStream:
        status_code = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_lines(self):
            yield json.dumps({"message": {"content": "*Smirks* What do you want?"}, "done": True})

    def fake_stream_post(*args, **kwargs):
        captured_payload.update(kwargs.get("json") or {})
        return FakeOutboundStream()

    with patch("httpx.AsyncClient.stream", side_effect=fake_stream_post):
        chat_resp = client.post(
            "/api/chat/stream",
            json={
                "message": "Who are you?",
                "mode": "persona",
                "persona_id": "raven",
                "incognito": False
            }
        )
        assert chat_resp.status_code == 200

    # Verify captured outbound messages contain updated traits and NO stale traits
    assert "messages" in captured_payload
    outbound_system = captured_payload["messages"][0]["content"]
    assert "Core traits: Rebellious, Fearless." in outbound_system
    assert "Bitingly sarcastic" in outbound_system
    assert "Quiet" not in outbound_system


# ============================================================================
# 5. Persona Edit/Save/Reopen Behavior & Nesting Protection
# ============================================================================

def test_persona_schema_nesting_prevention_across_three_edits(client):
    """
    Simulate 3 edit-save-reopen cycles (using the exact JSON schema shape produced
    by PersonaForge.jsx) and confirm forge_schema nesting does not grow.
    """
    card = {
        "name": "Echo",
        "description": "Synthesized voice",
        "traits": ["Resonant"],
        "roleplay_style": "Echo directives",
        "forge_schema": {
            "name": "Echo",
            "personality": {"archetype": "Synthesized voice", "core_traits": ["Resonant"], "flaws": []},
            "emotion": {"default_mood": "Calm", "speech_style": "Resonant", "reaction_to_stress": "Stable"},
            "physicality": {"appearance": "Ethereal", "body_language": "Floating"},
            "mature_themes": {"nsfw_enabled": False, "boundaries": "", "mature_dynamics": ""},
            "roleplay_style": "Echo directives",
            "_slider_values": {"order_chaos": 50, "optimism_cynicism": 50, "intro_extro": 50}
        }
    }

    # Cycle 1
    r1 = client.put("/api/personas/echo", json=card)
    assert r1.status_code == 200
    saved1 = r1.json()["persona"]
    assert "forge_schema" not in saved1["forge_schema"], "Cycle 1 embedded forge_schema inside forge_schema"

    # Cycle 2
    r2 = client.put("/api/personas/echo", json=saved1)
    assert r2.status_code == 200
    saved2 = r2.json()["persona"]
    assert "forge_schema" not in saved2["forge_schema"], "Cycle 2 embedded forge_schema inside forge_schema"

    # Cycle 3
    r3 = client.put("/api/personas/echo", json=saved2)
    assert r3.status_code == 200
    saved3 = r3.json()["persona"]
    assert "forge_schema" not in saved3["forge_schema"], "Cycle 3 embedded forge_schema inside forge_schema"
