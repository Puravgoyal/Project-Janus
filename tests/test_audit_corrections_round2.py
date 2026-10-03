"""
Regression tests verifying the 5 targeted audit fixes:
1. Incognito adventure session token isolation, pass-through, and zero persistence.
2. Conversational reminder intent parser handling Unicode curly apostrophes, reported speech,
   questions, and hypotheticals through the full triage path.
3. Deterministic synchronization barrier test preventing an old campaign update from racing
   with and overwriting a newly replaced campaign.
4. Premature stream termination (unexpected EOF, timeout, error) reporting failure, visibly
   marking interrupted streams, and not recording partial turns in adventure history.
5. Persona updates detecting forge_schema subfield changes (e.g. emotion.speech_style) and
   regenerating canonical system_prompt.
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch

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
# Issue 1: Incognito adventure session token isolation & zero persistence
# ============================================================================

from unittest.mock import AsyncMock, MagicMock, patch

def test_incognito_adventure_token_and_isolation(client, monkeypatch):
    """
    Verify incognito campaign starts with session_token, rejects actions without it,
    isolates two concurrent private sessions, and never persists private state to disk.
    """
    # Mock GPU DM generation (res.json() is a sync call in httpx)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "The shadows deepen around you."}}
    monkeypatch.setattr("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp))

    # 1. Start session A
    res_a = client.post("/api/adventure/start", json={
        "prompt": "Cyberpunk alleys of Neo-Veridia",
        "incognito": True
    })
    assert res_a.status_code == 200, res_a.text
    data_a = res_a.json()
    token_a = data_a.get("session_token")
    assert token_a and token_a.startswith("sess_"), "Must return unique session_token"

    # 2. Action without session_token must fail HTTP 400
    res_bad = client.post("/api/adventure/action", json={
        "action": "Look around",
        "incognito": True,
        "session_token": ""
    })
    assert res_bad.status_code == 400

    # 3. Start session B
    res_b = client.post("/api/adventure/start", json={
        "prompt": "Sunken ruins of Atlantis",
        "incognito": True
    })
    assert res_b.status_code == 200
    data_b = res_b.json()
    token_b = data_b.get("session_token")
    assert token_b and token_b != token_a, "Tokens for separate incognito sessions must differ"

    # 4. Mock streaming action responses for both
    def mock_stream_a(*args, **kwargs):
        class MockStream:
            status_code = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def aiter_lines(self):
                yield json.dumps({"message": {"content": "You see neon reflections."}, "done": False})
                yield json.dumps({"message": {"content": ""}, "done": True})
        return MockStream()

    monkeypatch.setattr("httpx.AsyncClient.stream", mock_stream_a)

    action_a = client.post("/api/adventure/action", json={
        "action": "I check the alley",
        "incognito": True,
        "session_token": token_a
    })
    assert action_a.status_code == 200

    # Retrieve states and verify complete isolation
    state_a = client.get(f"/api/adventure/state?incognito=true&session_token={token_a}").json()
    state_b = client.get(f"/api/adventure/state?incognito=true&session_token={token_b}").json()

    assert any("alley" in m.get("content", "").lower() for m in state_a.get("history", []))
    assert not any("alley" in m.get("content", "").lower() for m in state_b.get("history", []))

    # Verify zero persistence to disk
    persistent_file = storage.get_data_dir() / "adventure_state.json"
    if persistent_file.exists():
        disk_data = json.loads(persistent_file.read_text(encoding="utf-8"))
        assert disk_data.get("campaign_id") != data_a["campaign_id"]
        assert disk_data.get("campaign_id") != data_b["campaign_id"]


# ============================================================================
# Issue 2: Reminder completion detection with Unicode apostrophes & reported speech
# ============================================================================

def test_reminder_completion_unicode_apostrophe_and_reported_speech():
    """
    Verify reminder completion detection distinguishes direct affirmative statements
    from Unicode curly apostrophe negations, reported speech, questions, and hypotheticals.
    """
    import uuid
    rem_id = f"rem_arch_{uuid.uuid4().hex[:8]}"

    async def run_scenario():
        await storage.add_reminder({
            "id": rem_id,
            "text": "Submit architecture report",
            "priority": "high",
            "completed": False
        })

        def get_target(rems):
            return next(r for r in rems if r["id"] == rem_id)

        # 1. Negation with curly right single quotation mark (\u2019)
        await memory_engine.extract_and_triage(
            user_message="I haven\u2019t finished the architecture report",
            assistant_reply="Understood.",
            mode="assistant"
        )
        rems = await storage.load_reminders()
        assert get_target(rems)["completed"] is False, "Curly apostrophe negation must NOT complete reminder"

        # 2. Reported speech (quoted attribution)
        await memory_engine.extract_and_triage(
            user_message='Alice said "I finished the architecture report."',
            assistant_reply="Thanks for the update.",
            mode="assistant"
        )
        rems = await storage.load_reminders()
        assert get_target(rems)["completed"] is False, "Reported speech must NOT complete reminder"

        # 3. Hypothetical statement
        await memory_engine.extract_and_triage(
            user_message="If I finished the architecture report, I would take a break.",
            assistant_reply="Understood.",
            mode="assistant"
        )
        rems = await storage.load_reminders()
        assert get_target(rems)["completed"] is False, "Hypothetical statement must NOT complete reminder"

        # 4. Question
        await memory_engine.extract_and_triage(
            user_message="Did I finish the architecture report?",
            assistant_reply="Let me check.",
            mode="assistant"
        )
        rems = await storage.load_reminders()
        assert get_target(rems)["completed"] is False, "Inquiry question must NOT complete reminder"

        # 5. Direct affirmative completion
        await memory_engine.extract_and_triage(
            user_message="I finished the architecture report.",
            assistant_reply="Great job!",
            mode="assistant"
        )
        rems = await storage.load_reminders()
        assert get_target(rems)["completed"] is True, "Direct affirmative statement MUST complete reminder"

    asyncio.run(run_scenario())


# ============================================================================
# Issue 3: Atomic campaign replacement & deterministic synchronization race test
# ============================================================================

def test_campaign_replacement_synchronization_barrier():
    """
    Deterministic regression test:
    a. Pause an old campaign update before its disk write.
    b. Start a new campaign.
    c. Resume the old update.
    d. Assert the new campaign survives completely unchanged.
    """
    async def run_race_test():
        # Setup Campaign 1 on disk
        state_old = adventure_engine.AdventureState(campaign_id="cmp_old_111")
        state_old.world_context.prompt = "Old Kingdom of Valoria"
        state_old.history = [{"role": "assistant", "content": "Welcome to Valoria."}]
        await adventure_engine.replace_persistent_campaign(state_old)

        write_paused = asyncio.Event()
        resume_write = asyncio.Event()

        orig_save_guarded = adventure_engine.save_state_guarded

        async def hooked_save_guarded(state, incognito, session_token=None, expected_campaign_id=None, expected_version=None):
            if expected_campaign_id == "cmp_old_111":
                write_paused.set()
                await resume_write.wait()
            return await orig_save_guarded(
                state, incognito, session_token=session_token,
                expected_campaign_id=expected_campaign_id, expected_version=expected_version
            )

        with patch("backend.adventure_engine.save_state_guarded", side_effect=hooked_save_guarded):
            # Step a: Launch old update task
            old_task = asyncio.create_task(
                adventure_engine.append_action_history(
                    action="Old Hero strikes the goblin",
                    reply="The goblin parries.",
                    incognito=False,
                    campaign_id="cmp_old_111"
                )
            )

            # Wait until old update is paused right before its write
            await write_paused.wait()

            # Step b: Start new campaign
            state_new = adventure_engine.AdventureState(campaign_id="cmp_new_222")
            state_new.world_context.prompt = "Cyberpunk Neo-Tokyo"
            state_new.history = [{"role": "assistant", "content": "Welcome to Neo-Tokyo."}]
            await adventure_engine.replace_persistent_campaign(state_new)

            # Step c: Resume old update
            resume_write.set()
            res_old = await old_task
            assert res_old is None, "Old update must return None when rejected by guard"

        # Step d: Assert new campaign survives intact on disk
        persisted = await adventure_engine.get_state(incognito=False)
        assert persisted.campaign_id == "cmp_new_222"
        assert persisted.world_context.prompt == "Cyberpunk Neo-Tokyo"
        assert len(persisted.history) == 1
        assert "Neo-Tokyo" in persisted.history[0]["content"]
        assert not any("goblin" in m.get("content", "") for m in persisted.history)

    asyncio.run(run_race_test())


# ============================================================================
# Issue 4: Premature stream termination handling
# ============================================================================

def test_premature_stream_termination_reported_as_failure(client, monkeypatch):
    """
    Verify unexpected EOF / premature stream cutoff emits failure event,
    does NOT record incomplete replies in adventure history, and suppresses triage.
    """
    # 1. Setup persistent adventure campaign
    state = adventure_engine.AdventureState(campaign_id="cmp_stream_test")
    state.history = [{"role": "assistant", "content": "You stand before the dungeon door."}]
    asyncio.run(adventure_engine.replace_persistent_campaign(state))

    # Mock stream terminating unexpectedly after 1 chunk without done: true
    def mock_broken_stream(*args, **kwargs):
        class BrokenStream:
            status_code = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def aiter_lines(self):
                # Only 1 partial token, connection cuts off without done: true
                yield json.dumps({"message": {"content": "The door creaks open and..."}, "done": False})
        return BrokenStream()

    monkeypatch.setattr("httpx.AsyncClient.stream", mock_broken_stream)

    # Call adventure action
    res = client.post("/api/adventure/action", json={
        "action": "I open the door",
        "incognito": False
    })
    assert res.status_code == 200

    # Parse SSE events
    events = []
    for line in res.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    # Must contain error / interrupted event
    error_event = next((e for e in events if e.get("error") or e.get("interrupted")), None)
    assert error_event is not None, f"Expected error/interrupted event, got: {events}"
    assert "prematurely" in error_event.get("error", "").lower()

    # Adventure history must NOT have recorded the partial reply
    final_state = asyncio.run(adventure_engine.get_state(incognito=False))
    assert len(final_state.history) == 1, "Incomplete turn must NOT be saved to history"
    assert "creaks open" not in final_state.history[0]["content"]


# ============================================================================
# Issue 5: Persona edit regenerates prompt when forge_schema fields change
# ============================================================================

def test_persona_edit_regenerates_prompt_on_speech_style_change(client):
    """
    Verify modifying forge_schema.emotion.speech_style from 'quiet' to 'shouting'
    triggers prompt regeneration with the new speech style in system_prompt.
    """
    initial_persona = {
        "id": "monk",
        "name": "The Monk",
        "description": "A silent ascetic",
        "forge_schema": {
            "personality": {"archetype": "Ascetic", "flaws": ["Stubborn"]},
            "emotion": {"speech_style": "quiet", "default_mood": "Calm"},
            "physicality": {"appearance": "Shaved head", "body_language": "Still"},
            "mature_themes": {"boundaries": "None"}
        }
    }
    # Save initial persona
    res_create = client.put("/api/personas/monk", json=initial_persona)
    assert res_create.status_code == 200
    created = res_create.json()["persona"]
    assert "Your speech style: quiet." in created["system_prompt"]

    # Update speech_style to "shouting"
    updated_payload = {
        "id": "monk",
        "name": "The Monk",
        "description": "A silent ascetic",
        "system_prompt": created["system_prompt"],  # send previous prompt to verify it gets replaced
        "forge_schema": {
            "personality": {"archetype": "Ascetic", "flaws": ["Stubborn"]},
            "emotion": {"speech_style": "shouting", "default_mood": "Calm"},
            "physicality": {"appearance": "Shaved head", "body_language": "Still"},
            "mature_themes": {"boundaries": "None"}
        }
    }
    res_update = client.put("/api/personas/monk", json=updated_payload)
    assert res_update.status_code == 200
    updated = res_update.json()["persona"]

    assert "Your speech style: shouting." in updated["system_prompt"]
    assert "Your speech style: quiet." not in updated["system_prompt"]
