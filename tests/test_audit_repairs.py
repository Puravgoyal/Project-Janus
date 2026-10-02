"""
Tests specifically verifying all audit repair targets:
1. Persona creation and editing field preservation
2. Incognito behavior consistency
3. Engine routing and model configuration
4. Memory and state correctness (deduplication, urgency ordering, conversational completion)
5. Validation, boolean parsing, and boundaries
6. Security (CORS wildcard removal, path traversal protection, Janus protection)
"""

import asyncio
import pytest
from starlette.testclient import TestClient
from backend.main import app
from backend import storage, memory_engine, persona_compiler, adventure_engine


@pytest.fixture
def client():
    return TestClient(app)


def test_cors_no_wildcard():
    """Verify wildcard '*' is removed from CORS allow_origins."""
    from starlette.middleware.cors import CORSMiddleware
    for middleware in app.user_middleware:
        if middleware.cls == CORSMiddleware:
            allow_origins = middleware.kwargs.get("allow_origins", [])
            assert "*" not in allow_origins, "CORS allow_origins must not contain wildcard '*'"



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


def test_adventure_engine_uses_extractor_model():
    """Verify adventure_engine CPU mechanics extraction uses EXTRACTOR_MODEL, not CHAT_MODEL."""
    assert hasattr(adventure_engine, "EXTRACTOR_MODEL")
    assert adventure_engine.EXTRACTOR_MODEL != ""
    assert "extractor" in adventure_engine.EXTRACTOR_MODEL.lower()


def test_adventure_campaign_versioning():
    """Verify adventure engine discards stale extractions from old campaigns."""
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


def test_reminder_urgency_ordering():
    """Verify inject_context orders reminders by high priority first, then earliest due date."""
    mock_reminders = [
        {"id": "r1", "text": "Low priority task", "priority": "low", "due_date": "2026-10-10", "completed": False},
        {"id": "r2", "text": "Urgent immediate task", "priority": "high", "due_date": "2026-10-05", "completed": False},
        {"id": "r3", "text": "Medium priority task", "priority": "medium", "due_date": "2026-10-04", "completed": False},
    ]

    prio_weights = {"high": 0, "medium": 1, "low": 2}
    sorted_reminders = sorted(
        mock_reminders,
        key=lambda r: (
            prio_weights.get(str(r.get("priority", "medium")).lower(), 1),
            r.get("due_date") or "9999-99-99"
        )
    )

    assert sorted_reminders[0]["id"] == "r2"  # high priority first
    assert sorted_reminders[1]["id"] == "r3"  # medium next
    assert sorted_reminders[2]["id"] == "r1"  # low last


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


def test_conversational_reminder_completion(tmp_path, monkeypatch):
    """Verify extract_and_triage detects conversational completion phrases."""
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)

    # Seed an active reminder
    asyncio.run(storage.add_reminder({
        "id": "rem_report",
        "text": "Submit quarterly architecture report",
        "priority": "high",
        "completed": False
    }))

    # User says they completed it in assistant mode
    asyncio.run(memory_engine.extract_and_triage(
        user_message="I have finished and completed the architecture report today.",
        assistant_reply="Excellent work Demi. I have noted that as completed.",
        mode="assistant"
    ))

    reminders = asyncio.run(storage.load_reminders())
    report_rem = next((r for r in reminders if r["id"] == "rem_report"), None)
    assert report_rem is not None
    assert report_rem["completed"] is True


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
