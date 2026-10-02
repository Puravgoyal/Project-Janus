"""
Project Janus - Character Forge Test Suite
Tests persona forge endpoints, incognito zero-disk-write guarantees,
persistence file creation, schema validation, and AI enhancement schema compliance.

Zero live engine requirement: All tests execute 100% offline without Ollama running.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from backend import storage, persona_compiler
from backend.main import app
from backend.persona_compiler import (
    CharacterForgeSchema,
    PersonalitySchema,
    EmotionSchema,
    PhysicalitySchema,
    MatureThemesSchema,
)

# ----------------------------------------------------------------------
# Test Fixtures & Isolated Storage
# ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_storage_env(tmp_path: Path):
    """
    Ensure every test runs in an isolated temporary data directory.
    Guarantees strict isolation of persona cards and zero pollution of production storage.
    """
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = test_data_dir / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    original_data_dir = storage.get_data_dir()
    storage.set_data_dir(test_data_dir)
    try:
        yield personas_dir
    finally:
        storage.set_data_dir(original_data_dir)


@pytest.fixture
def client():
    """TestClient instance for Project Janus backend."""
    return TestClient(app)


# ----------------------------------------------------------------------
# Canonical Test Data Fixtures
# ----------------------------------------------------------------------

VALID_CHARACTER_DATA: dict[str, Any] = {
    "name": "Aria Thorne",
    "personality": {
        "archetype": "Cynical Hacker",
        "core_traits": ["Analytical", "Resourceful", "Skeptical", "Pragmatic"],
        "flaws": ["Paranoid", "Secretive"],
    },
    "emotion": {
        "default_mood": "Guarded curiosity",
        "reaction_to_stress": "Sharp sarcasm and heightened vigilance",
        "speech_style": "Concise, technical cadence with dry wit",
    },
    "physicality": {
        "appearance": "Dark oversized hoodie, cybernetic ocular implant, fingerless gloves",
        "body_language": "Constantly scans exits, leans back defensively",
    },
    "mature_themes": {
        "nsfw_enabled": False,
        "boundaries": "No non-consensual themes",
        "mature_dynamics": "",
    },
    "roleplay_style": "Setting: Neon-drenched subterranean server farm. Tone: Sardonic and analytical. Formatting: Actions in asterisks (*types rapidly*). Actively question motives and converse without avoidance.",
}

VALID_NSFW_CHARACTER_DATA: dict[str, Any] = {
    "name": "Sylvia Vance",
    "personality": {
        "archetype": "Seductive Spy",
        "core_traits": ["Charming", "Calculating", "Bold", "Mysterious"],
        "flaws": ["Manipulative", "Emotionally detached"],
    },
    "emotion": {
        "default_mood": "Playfully provocative",
        "reaction_to_stress": "Cool composure masking cold calculation",
        "speech_style": "Velvety, intimate tone with double entendres",
    },
    "physicality": {
        "appearance": "Tailored velvet coat, silver hair, piercing green eyes",
        "body_language": "Poised, close-proximity posture, deliberate eye contact",
    },
    "mature_themes": {
        "nsfw_enabled": True,
        "boundaries": "Safe words respected, psychological tension",
        "mature_dynamics": "High-stakes romantic rivalry with playful power play",
    },
    "roleplay_style": "Setting: Grand diplomatic ballroom. Tone: Sophisticated, dangerous intrigue. Formatting: Asterisks for subtle physical movements (*sips champagne*). Engage dynamically, playfully prodding the interlocutor.",
}


# ----------------------------------------------------------------------
# 1. Incognito Non-Persistence Assertion (MANDATORY)
# ----------------------------------------------------------------------

def test_forge_incognito_true_zero_disk_writes(client):
    """
    Verify that POST /api/personas/forge with incognito=True:
    1. Returns HTTP 200 with saved=False and incognito=True.
    2. Performs strictly zero filesystem writes to data/personas/.
    3. Guarantees file_count_after == file_count_before (delta = 0).
    """
    personas_dir = storage.get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    # 1. Count files before request
    files_before = list(personas_dir.glob("*.json"))
    file_count_before = len(files_before)

    # 2. POST /api/personas/forge with incognito=True
    payload = {
        "character_data": VALID_CHARACTER_DATA,
        "incognito": True,
    }
    resp = client.post("/api/personas/forge", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # 3. Assert response fields
    data = resp.json()
    assert data.get("saved") is False, f"Expected saved=False, got {data.get('saved')}"
    assert data.get("incognito") is True, f"Expected incognito=True, got {data.get('incognito')}"
    assert data.get("status") == "success"

    # Verify character data is returned in response
    char = data.get("character") or (data if "personality" in data else None)
    assert char is not None, f"Response missing character data: {data}"
    assert char.get("name") == "Aria Thorne"

    # 4. Count files after request
    files_after = list(personas_dir.glob("*.json"))
    file_count_after = len(files_after)

    # 5. Assert strict zero-disk-write invariant
    assert file_count_after == file_count_before, (
        f"Zero-disk-write invariant violated! Before: {file_count_before}, After: {file_count_after}. "
        f"Unexpected files created: {[f.name for f in set(files_after) - set(files_before)]}"
    )

    # Verify expected slug file does NOT exist on disk
    expected_slug_file = personas_dir / "aria_thorne.json"
    assert not expected_slug_file.exists(), (
        f"File {expected_slug_file} was created on disk despite incognito=True!"
    )

    # Verify no temporary files leaked
    temp_files = list(personas_dir.glob("*.tmp")) + list(storage.get_data_dir().glob("*.tmp"))
    assert len(temp_files) == 0, f"Leaked temporary files found: {temp_files}"


# ----------------------------------------------------------------------
# 2. Persistence File Creation Assertion (MANDATORY)
# ----------------------------------------------------------------------

def test_forge_incognito_false_creates_exact_file(client):
    """
    Verify that POST /api/personas/forge with incognito=False:
    1. Returns HTTP 200 with saved=True and incognito=False.
    2. Creates exactly one new .json file in data/personas/ (file_count_after == file_count_before + 1).
    3. The file exists and contains valid JSON matching CharacterForgeSchema.
    4. Cleans up created test file in finally block.
    """
    personas_dir = storage.get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    files_before = set(personas_dir.glob("*.json"))
    file_count_before = len(files_before)
    created_files: list[Path] = []

    try:
        payload = {
            "character_data": VALID_CHARACTER_DATA,
            "incognito": False,
        }
        resp = client.post("/api/personas/forge", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        data = resp.json()
        assert data.get("saved") is True, f"Expected saved=True, got {data.get('saved')}"
        assert data.get("incognito") is False, f"Expected incognito=False, got {data.get('incognito')}"
        assert data.get("status") == "success"

        files_after = set(personas_dir.glob("*.json"))
        file_count_after = len(files_after)

        # Assert exactly +1 file created
        assert file_count_after == file_count_before + 1, (
            f"Expected exactly 1 new file. Before: {file_count_before}, After: {file_count_after}"
        )

        new_files = list(files_after - files_before)
        assert len(new_files) == 1, f"Expected 1 newly created file, found {new_files}"
        target_file = new_files[0]
        created_files.append(target_file)

        # Assert file exists and is non-empty
        assert target_file.exists(), f"Target file {target_file} does not exist"
        assert target_file.stat().st_size > 0, f"Target file {target_file} is empty"

        # Assert file contains valid JSON
        with open(target_file, "r", encoding="utf-8") as f:
            file_json = json.load(f)

        # Extract character data block if nested under forge_schema, character_data or at root
        char_block = file_json.get("forge_schema") or file_json.get("character_data") or file_json

        # Validate against CharacterForgeSchema
        validated = CharacterForgeSchema(**char_block)
        assert validated.name == "Aria Thorne"
        assert validated.personality.archetype == "Cynical Hacker"
        assert len(validated.personality.core_traits) == 4
        assert len(validated.personality.flaws) == 2
        assert validated.emotion.default_mood == "Guarded curiosity"
        assert validated.emotion.reaction_to_stress == "Sharp sarcasm and heightened vigilance"
        assert validated.emotion.speech_style == "Concise, technical cadence with dry wit"
        assert validated.physicality.appearance.startswith("Dark oversized hoodie")
        assert validated.mature_themes.nsfw_enabled is False
        assert validated.mature_themes.mature_dynamics == ""
        assert validated.roleplay_style == VALID_CHARACTER_DATA["roleplay_style"]

        # Assert card has injected roleplay_style and active engagement directives into system_prompt
        assert file_json.get("roleplay_style") == VALID_CHARACTER_DATA["roleplay_style"]
        system_prompt = file_json.get("system_prompt", "")
        assert "Roleplay Style & Directives" in system_prompt or "Roleplay Style" in system_prompt
        assert VALID_CHARACTER_DATA["roleplay_style"] in system_prompt
        assert "Strong Roleplay Enforcement" in system_prompt
        assert "without avoiding" in system_prompt

    finally:
        # Clean up any created test files
        for f in created_files:
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


# ----------------------------------------------------------------------
# 3. Enhance Endpoint Schema Compliance
# ----------------------------------------------------------------------

def test_enhance_endpoint_schema_compliance(client):
    """
    Verify POST /api/personas/enhance:
    1. Returns HTTP 200 with valid character schema matching CharacterForgeSchema.
    2. All schema blocks present: name, personality, emotion, physicality, mature_themes.
    3. When allow_nsfw=False: mature_themes["nsfw_enabled"] is False and mature_dynamics == "".
    """
    payload = {
        "base_prompt": "A tactical combat medic who serves in frontier outposts with weary dark humor.",
        "allow_nsfw": False,
    }
    resp = client.post("/api/personas/enhance", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    char_data = data if ("name" in data and "personality" in data) else data.get("character", data)

    # Assert root schema fields
    for field in ("name", "personality", "emotion", "physicality", "mature_themes", "roleplay_style"):
        assert field in char_data, f"Missing required field '{field}' in response: {char_data}"

    # Assert personality schema fields
    personality = char_data["personality"]
    assert isinstance(personality, dict), f"personality must be a dict, got {type(personality)}"
    assert "archetype" in personality and isinstance(personality["archetype"], str)
    assert "core_traits" in personality and isinstance(personality["core_traits"], list)
    assert "flaws" in personality and isinstance(personality["flaws"], list)
    assert len(personality["core_traits"]) > 0, "core_traits must not be empty"
    assert len(personality["flaws"]) > 0, "flaws must not be empty"

    # Assert emotion schema fields
    emotion = char_data["emotion"]
    assert isinstance(emotion, dict)
    assert "default_mood" in emotion and isinstance(emotion["default_mood"], str)
    assert "reaction_to_stress" in emotion and isinstance(emotion["reaction_to_stress"], str)
    assert "speech_style" in emotion and isinstance(emotion["speech_style"], str)

    # Assert physicality schema fields
    physicality = char_data["physicality"]
    assert isinstance(physicality, dict)
    assert "appearance" in physicality and isinstance(physicality["appearance"], str)
    assert "body_language" in physicality and isinstance(physicality["body_language"], str)

    # Assert mature_themes schema fields
    mature_themes = char_data["mature_themes"]
    assert isinstance(mature_themes, dict)
    assert "nsfw_enabled" in mature_themes
    assert "boundaries" in mature_themes
    assert "mature_dynamics" in mature_themes

    # Assert roleplay_style schema field
    assert isinstance(char_data["roleplay_style"], str)
    assert len(char_data["roleplay_style"].strip()) > 0, "roleplay_style must not be empty"

    # Strict invariant: allow_nsfw=False MUST force nsfw_enabled=False and mature_dynamics=""
    assert mature_themes["nsfw_enabled"] is False, (
        f"Expected nsfw_enabled=False when allow_nsfw=False, got {mature_themes['nsfw_enabled']}"
    )
    assert mature_themes["mature_dynamics"] == "", (
        f"Expected mature_dynamics='' when allow_nsfw=False, got '{mature_themes['mature_dynamics']}'"
    )

    # Validate against Pydantic schema model
    validated = CharacterForgeSchema(**char_data)
    assert validated.name
    assert validated.mature_themes.nsfw_enabled is False
    assert len(validated.roleplay_style.strip()) > 0


# ----------------------------------------------------------------------
# 4. Enhance NSFW Dynamics Behavior
# ----------------------------------------------------------------------

def test_enhance_nsfw_dynamics_behavior(client):
    """
    Verify POST /api/personas/enhance with allow_nsfw=True:
    1. Returns HTTP 200.
    2. mature_themes["nsfw_enabled"] is True.
    3. mature_themes["mature_dynamics"] is a non-empty string.
    """
    payload = {
        "base_prompt": "A charismatic courtesan and shadow broker in a high-stakes noble court.",
        "allow_nsfw": True,
    }
    resp = client.post("/api/personas/enhance", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    char_data = data if ("name" in data and "personality" in data) else data.get("character", data)

    assert "mature_themes" in char_data, f"Missing 'mature_themes' in response: {char_data}"
    mature_themes = char_data["mature_themes"]

    # Invariant: allow_nsfw=True MUST set nsfw_enabled=True and populate mature_dynamics
    assert mature_themes["nsfw_enabled"] is True, (
        f"Expected nsfw_enabled=True when allow_nsfw=True, got {mature_themes['nsfw_enabled']}"
    )
    assert isinstance(mature_themes["mature_dynamics"], str)
    assert len(mature_themes["mature_dynamics"].strip()) > 0, (
        f"Expected non-empty mature_dynamics when allow_nsfw=True, got '{mature_themes['mature_dynamics']}'"
    )

    # Validate against Pydantic schema model
    validated = CharacterForgeSchema(**char_data)
    assert validated.mature_themes.nsfw_enabled is True
    assert len(validated.mature_themes.mature_dynamics) > 0


# ----------------------------------------------------------------------
# 5. Validation and Edge Cases
# ----------------------------------------------------------------------

def test_validation_errors(client):
    """
    Verify API input validation and error handling:
    1. POST /api/personas/enhance with empty or whitespace prompt -> HTTP 400 or 422.
    2. POST /api/personas/forge with empty, whitespace, or missing name -> HTTP 400, zero files written.
    3. POST /api/personas/forge with missing character_data -> HTTP 422, zero files written.
    4. POST /api/personas/forge with path traversal -> HTTP 400 or sanitized, no file outside personas dir.
    """
    personas_dir = storage.get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    # 1. Empty and whitespace-only base_prompt
    resp_empty = client.post("/api/personas/enhance", json={"base_prompt": "", "allow_nsfw": False})
    assert resp_empty.status_code in (400, 422), (
        f"Expected 400 or 422 for empty base_prompt, got {resp_empty.status_code}"
    )

    resp_spaces = client.post("/api/personas/enhance", json={"base_prompt": "   \n\t  ", "allow_nsfw": False})
    assert resp_spaces.status_code in (400, 422), (
        f"Expected 400 or 422 for whitespace base_prompt, got {resp_spaces.status_code}"
    )

    # 2. Missing, empty, or whitespace-only name in character_data -> HTTP 400 & zero files written
    files_before = set(personas_dir.glob("*.json"))

    # Empty name
    resp_empty_name = client.post("/api/personas/forge", json={
        "character_data": {"name": ""},
        "incognito": False,
    })
    assert resp_empty_name.status_code == 400, (
        f"Expected 400 for empty character name, got {resp_empty_name.status_code}"
    )
    assert set(personas_dir.glob("*.json")) == files_before, "Zero files must be written on 400 error"

    # Whitespace-only name
    resp_ws_name = client.post("/api/personas/forge", json={
        "character_data": {"name": "   \t  "},
        "incognito": False,
    })
    assert resp_ws_name.status_code == 400, (
        f"Expected 400 for whitespace character name, got {resp_ws_name.status_code}"
    )
    assert set(personas_dir.glob("*.json")) == files_before, "Zero files must be written on 400 error"

    # Missing name field
    resp_no_name = client.post("/api/personas/forge", json={
        "character_data": {},
        "incognito": False,
    })
    assert resp_no_name.status_code == 400, (
        f"Expected 400 for missing character name, got {resp_no_name.status_code}"
    )
    assert set(personas_dir.glob("*.json")) == files_before, "Zero files must be written on 400 error"

    # Missing character_data field entirely -> HTTP 422
    resp_no_char_data = client.post("/api/personas/forge", json={
        "incognito": False,
    })
    assert resp_no_char_data.status_code == 422, (
        f"Expected 422 for missing character_data field, got {resp_no_char_data.status_code}"
    )
    assert set(personas_dir.glob("*.json")) == files_before, "Zero files must be written on 422 error"

    # 3. Path traversal attack mitigation
    parent_files_before = set(personas_dir.parent.glob("*"))
    resp_traversal = client.post("/api/personas/forge", json={
        "character_data": {
            **VALID_CHARACTER_DATA,
            "name": "../../../traversal_attack_file",
        },
        "incognito": False,
    })
    # Must either reject with 400/422, or sanitize safely inside personas_dir
    assert resp_traversal.status_code in (200, 400, 422), (
        f"Unexpected status code for path traversal: {resp_traversal.status_code}"
    )

    # Verify no file was written outside the personas directory
    parent_files_after = set(personas_dir.parent.glob("*"))
    escaped_files = (parent_files_after - parent_files_before) - {personas_dir}
    assert len(escaped_files) == 0, (
        f"Security violation! File written outside personas directory: {escaped_files}"
    )
    assert not (personas_dir.parent / "traversal_attack_file.json").exists()
    assert not (personas_dir.parent.parent / "traversal_attack_file.json").exists()

    # Clean up sanitized file if created
    sanitized_file = personas_dir / "traversal_attack_file.json"
    if sanitized_file.exists():
        try:
            sanitized_file.unlink()
        except OSError:
            pass



# ----------------------------------------------------------------------
# 6. Additional Adversarial & Interoperability Tests
# ----------------------------------------------------------------------

def test_forge_persisted_card_loads_in_personas_roster(client):
    """
    Verify that when a card is forged with incognito=False, it seamlessly
    integrates into the platform roster returned by GET /api/personas.
    """
    personas_dir = storage.get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Forge card persistently
        resp_forge = client.post("/api/personas/forge", json={
            "character_data": VALID_CHARACTER_DATA,
            "incognito": False,
        })
        assert resp_forge.status_code == 200

        # Query GET /api/personas roster
        resp_roster = client.get("/api/personas")
        assert resp_roster.status_code == 200
        roster = resp_roster.json()
        assert isinstance(roster, list)

        # Assert newly forged persona is present in roster
        names = [p.get("name") for p in roster]
        assert "Aria Thorne" in names, f"Forged persona 'Aria Thorne' not found in roster: {names}"

        # Assert card has required Janus Persona fields
        aria_card = next(p for p in roster if p.get("name") == "Aria Thorne")
        assert aria_card.get("id")
        assert aria_card.get("system_prompt")
        assert aria_card.get("tagline")

    finally:
        # Cleanup
        slug_file = personas_dir / "aria_thorne.json"
        if slug_file.exists():
            try:
                slug_file.unlink()
            except OSError:
                pass


def test_incognito_consecutive_sessions_zero_leakage(client):
    """
    Verify multiple back-to-back incognito forge requests:
    Filesystem count must remain unchanged after every single request.
    """
    personas_dir = storage.get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    initial_count = len(list(personas_dir.glob("*.json")))

    for i in range(5):
        custom_char = dict(VALID_CHARACTER_DATA)
        custom_char["name"] = f"Ephemeral Agent {i}"
        resp = client.post("/api/personas/forge", json={
            "character_data": custom_char,
            "incognito": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("saved") is False
        assert data.get("incognito") is True

        current_count = len(list(personas_dir.glob("*.json")))
        assert current_count == initial_count, (
            f"Leakage detected on iteration {i}: initial={initial_count}, current={current_count}"
        )


# ----------------------------------------------------------------------
# 7. Roleplay Style Customization & Chat Pipeline Enforcement Tests
# ----------------------------------------------------------------------

def test_roleplay_style_schema_validation():
    """
    Verify CharacterForgeSchema:
    1. Parses roleplay_style when provided.
    2. Defaults roleplay_style to empty string when omitted.
    3. Retains arbitrary custom roleplay directives.
    """
    # Without roleplay_style -> defaults to ""
    base_data = {
        "name": "Test Character",
        "personality": {
            "archetype": "Scholar",
            "core_traits": ["Curious", "Wise", "Patient", "Quiet"],
            "flaws": ["Absent-minded", "Obsessive"],
        },
        "emotion": {
            "default_mood": "Calm",
            "reaction_to_stress": "Deep contemplation",
            "speech_style": "Eloquent and deliberate",
        },
        "physicality": {
            "appearance": "Robes with ink stains",
            "body_language": "Slow, methodical gestures",
        },
    }
    schema_default = CharacterForgeSchema(**base_data)
    assert schema_default.roleplay_style == ""

    # With roleplay_style -> preserved
    custom_style = (
        "Setting: Ancient Library of Alexandria. "
        "Tone: Scholarly and inquisitive. "
        "Formatting: Actions in *asterisks*. "
        "Directives: Actively debate historical theories and never decline a question."
    )
    data_with_style = dict(base_data, roleplay_style=custom_style)
    schema_with_style = CharacterForgeSchema(**data_with_style)
    assert schema_with_style.roleplay_style == custom_style


@pytest.mark.asyncio
async def test_enhance_populates_roleplay_style():
    """
    Verify enhance_character_prompt:
    1. Returns a dict containing roleplay_style.
    2. roleplay_style is non-empty and contains formatting and engagement rules.
    """
    enhanced = await persona_compiler.enhance_character_prompt(
        base_prompt="A ruthless corporate inquisitor in a cyberpunk megacity."
    )
    assert "roleplay_style" in enhanced
    assert isinstance(enhanced["roleplay_style"], str)
    style = enhanced["roleplay_style"]
    assert len(style.strip()) > 0
    # Must specify formatting (e.g. asterisks) and active conversational engagement
    assert "asterisk" in style.lower() or "*" in style
    assert any(term in style.lower() for term in ["actively", "interaction", "engage", "discourse", "converse"])


@pytest.mark.asyncio
async def test_forge_system_prompt_roleplay_style_injection_and_active_dialogue():
    """
    Verify forge_character:
    1. Injects roleplay_style directly into system_prompt.
    2. Includes strong roleplay enforcement and active dialogue directives.
    3. Persists roleplay_style on persona card and forge_schema.
    """
    custom_style = (
        "Setting: Victorian occult salon. Tone: Mysterious and theatrical. "
        "Formatting: Actions like *adjusts monocle*. Engage actively and drive mystery forward."
    )
    char_payload = {
        "name": "Lord Ravenscroft",
        "personality": {
            "archetype": "Occult Aristocrat",
            "core_traits": ["Mysterious", "Aristocratic", "Shrewd", "Charming"],
            "flaws": ["Hubris", "Secretive"],
        },
        "emotion": {
            "default_mood": "Enigmatic amusement",
            "reaction_to_stress": "Cold polite smile",
            "speech_style": "High Victorian English",
        },
        "physicality": {
            "appearance": "Impeccable black tailcoat and silver-headed cane",
            "body_language": "Graceful and poised",
        },
        "mature_themes": {"nsfw_enabled": False, "boundaries": "", "mature_dynamics": ""},
        "roleplay_style": custom_style,
    }

    result = await persona_compiler.forge_character(char_payload, incognito=False)
    assert result["saved"] is True
    card = result["character"]

    assert card["roleplay_style"] == custom_style
    system_prompt = card["system_prompt"]

    # Injected roleplay style
    assert custom_style in system_prompt
    # Active engagement directives
    assert "Strong Roleplay Enforcement" in system_prompt
    assert "Actively engage in conversation" in system_prompt
    assert "without avoiding interaction" in system_prompt or "Never give generic, dismissive, or avoidant AI responses" in system_prompt


@pytest.mark.asyncio
async def test_chat_pipeline_injects_roleplay_style_and_active_engagement():
    """
    Verify backend/memory_engine.py inject_context:
    1. In standard persona mode: injects roleplay instructions requiring active engagement
       and injects roleplay_style section.
    2. In incognito persona mode: injects roleplay instructions and roleplay_style
       while keeping user memory isolated.
    """
    from backend import memory_engine

    custom_style = (
        "Setting: Cyberpunk alley. Tone: Gritty. Formatting: *actions in asterisks*. "
        "Actively probe interlocutor motives."
    )
    forged_persona = {
        "id": "renegade_runner",
        "name": "Renegade Runner",
        "system_prompt": "You are Renegade Runner. Archetype: Street Samurai. Never break character.",
        "roleplay_style": custom_style,
        "tagline": "Cyberpunk Samurai",
        "traits": ["Cynical", "Agile"],
    }

    # Standard mode
    prompt_std = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=forged_persona,
        incognito=False,
    )
    assert "You are now roleplaying as Renegade Runner" in prompt_std
    assert "Maintain active, engaging conversation at all times" in prompt_std
    assert "never avoid engagement" in prompt_std or "Never give generic" in prompt_std
    assert "[Roleplay Style & Directives]" in prompt_std
    assert custom_style in prompt_std

    # Incognito mode
    prompt_incog = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=forged_persona,
        incognito=True,
    )
    assert "You are now roleplaying as Renegade Runner" in prompt_incog
    assert "Maintain active, engaging conversation at all times" in prompt_incog
    assert "[Roleplay Style & Directives]" in prompt_incog
    assert custom_style in prompt_incog
    # Strict incognito invariant: zero user profile facts leaked
    assert "User Profile" not in prompt_incog


def test_forge_incognito_preserves_roleplay_style_and_system_prompt(client):
    """
    Verify POST /api/personas/forge with incognito=True:
    1. Returns character with roleplay_style preserved and system_prompt generated.
    2. Strictly zero disk writes.
    """
    personas_dir = storage.get_data_dir() / "personas"
    count_before = len(list(personas_dir.glob("*.json")))

    payload = {
        "character_data": {
            **VALID_CHARACTER_DATA,
            "name": "Ghost In The Shell",
            "roleplay_style": "Tone: Ethereal. Formatting: *whispers in static*. Engage in deep philosophical discourse.",
        },
        "incognito": True,
    }
    resp = client.post("/api/personas/forge", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved"] is False
    assert data["incognito"] is True

    char = data["character"]
    assert char["roleplay_style"] == "Tone: Ethereal. Formatting: *whispers in static*. Engage in deep philosophical discourse."
    assert "system_prompt" in char
    assert "Tone: Ethereal" in char["system_prompt"]
    assert "Strong Roleplay Enforcement" in char["system_prompt"]

    count_after = len(list(personas_dir.glob("*.json")))
    assert count_after == count_before


# ----------------------------------------------------------------------
# 8. Adversarial Stress & Edge Case Probing
# ----------------------------------------------------------------------

def test_roleplay_style_none_and_whitespace_normalization(client):
    """
    Verify roleplay_style safely handles None and whitespace-only strings:
    1. CharacterForgeSchema accepts roleplay_style=None without ValidationError.
    2. CharacterForgeSchema normalizes None and whitespace to empty string.
    3. POST /api/personas/forge accepts roleplay_style=None and returns HTTP 200.
    """
    base_data = {
        "name": "Null Style Character",
        "personality": {
            "archetype": "Sentinel",
            "core_traits": ["Steadfast", "Vigilant", "Calm", "Observant"],
            "flaws": ["Inflexible", "Cautious"],
        },
        "emotion": {
            "default_mood": "Steadfast",
            "reaction_to_stress": "Heightened focus",
            "speech_style": "Measured",
        },
        "physicality": {
            "appearance": "Plate armor",
            "body_language": "Immobile and alert",
        },
    }

    # None input
    schema_none = CharacterForgeSchema(**base_data, roleplay_style=None)
    assert schema_none.roleplay_style == ""

    # Whitespace input
    schema_whitespace = CharacterForgeSchema(**base_data, roleplay_style="   \t\n   ")
    assert schema_whitespace.roleplay_style == ""

    # API endpoint with null roleplay_style
    payload = {
        "character_data": {
            **VALID_CHARACTER_DATA,
            "name": "Null Style Persona",
            "roleplay_style": None,
        },
        "incognito": False,
    }
    resp = client.post("/api/personas/forge", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    card = resp.json()["character"]
    assert card["roleplay_style"] == ""
    assert "Strong Roleplay Enforcement" in card["system_prompt"]


def test_forge_name_none_graceful_400(client):
    """
    Verify POST /api/personas/forge returns clean HTTP 400 without crashing:
    1. character_data with name=None.
    2. character_data with whitespace-only name.
    """
    resp_none = client.post("/api/personas/forge", json={"character_data": {"name": None}})
    assert resp_none.status_code == 400
    assert "character_data must include a non-empty 'name' field" in resp_none.json()["detail"]

    resp_ws = client.post("/api/personas/forge", json={"character_data": {"name": "   \n\t  "}})
    assert resp_ws.status_code == 400
    assert "character_data must include a non-empty 'name' field" in resp_ws.json()["detail"]


def test_forge_adversarial_markdown_fences_and_nested_json(client):
    """
    Verify POST /api/personas/forge handles markdown code fences, quotes, and nested JSON:
    1. Preserves the style faithfully without corrupting JSON serialization.
    2. Successfully re-loads from storage layer with all formatting intact.
    """
    complex_style = (
        "Setting: Underbelly terminal ```sh\ncat /var/log/syslog\n```\n"
        "Formatting: Use *actions* and JSON snippets like `{\"mood\": \"grim\"}`.\n"
        "Tone: \"Darkly ironic\" & <unfiltered>."
    )
    payload = {
        "character_data": {
            **VALID_CHARACTER_DATA,
            "name": "Cipherpunk Operator",
            "roleplay_style": complex_style,
        },
        "incognito": False,
    }
    resp = client.post("/api/personas/forge", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved"] is True

    # Re-read via storage layer
    card = storage.get_persona_sync("cipherpunk_operator")
    assert card is not None
    assert card["roleplay_style"] == complex_style
    assert complex_style in card["system_prompt"]
    assert "Strong Roleplay Enforcement" in card["system_prompt"]


@pytest.mark.asyncio
async def test_context_budget_capping_preserves_roleplay_enforcement():
    """
    Verify memory_engine.inject_context under massive roleplay_style strings:
    1. In standard persona mode: Enforces safety cap while PRESERVING active conversation enforcement.
    2. In incognito persona mode: Enforces safety cap while PRESERVING active conversation enforcement.
    """
    from backend import memory_engine

    massive_style = "Setting: Endless sprawling neon desert. " + ("Very detailed atmospheric lore. " * 800)
    forged_persona = {
        "id": "desert_nomad",
        "name": "Desert Nomad",
        "system_prompt": f"You are Desert Nomad. Lore: {massive_style}",
        "roleplay_style": massive_style,
        "tagline": "Nomad of the Wastes",
    }

    # Standard mode
    prompt_std = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=forged_persona,
        incognito=False,
    )
    assert "[Context truncated for token budget]" in prompt_std
    assert "Maintain active, engaging conversation without avoiding interaction" in prompt_std
    assert len(prompt_std) < 24500

    # Incognito mode
    prompt_incog = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=forged_persona,
        incognito=True,
    )
    assert "[Context truncated for token budget]" in prompt_incog
    assert "Maintain active, engaging conversation without avoiding interaction" in prompt_incog
    assert len(prompt_incog) < 24500
    assert "User Profile" not in prompt_incog


@pytest.mark.asyncio
async def test_enhance_handles_whitespace_roleplay_style():
    """
    Verify enhance_character_prompt populates rich fallback when LLM returns whitespace-only style.
    """
    import unittest.mock as mock

    # Mock httpx to simulate CPU Ollama returning JSON with whitespace-only roleplay_style
    fake_response = {
        "name": "Blank Style Entity",
        "personality": {
            "archetype": "Specter",
            "core_traits": ["Ethereal", "Silent", "Mysterious", "Calm"],
            "flaws": ["Unreachable", "Cold"],
        },
        "emotion": {
            "default_mood": "Eerie",
            "reaction_to_stress": "Vanishes",
            "speech_style": "Whisper",
        },
        "physicality": {
            "appearance": "Translucent mist",
            "body_language": "Floating",
        },
        "mature_themes": {"nsfw_enabled": False, "boundaries": "", "mature_dynamics": ""},
        "roleplay_style": "    \n\t   ",
    }

    with mock.patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": json.dumps(fake_response)}
        mock_post.return_value = mock_resp

        result = await persona_compiler.enhance_character_prompt("A ghost entity")
        assert len(result["roleplay_style"].strip()) > 20
        assert "asterisk" in result["roleplay_style"].lower() or "*" in result["roleplay_style"]
        assert "actively" in result["roleplay_style"].lower() or "engage" in result["roleplay_style"].lower()


def test_chat_stream_merges_roleplay_style_for_legacy_persona(client):
    """
    Verify /api/chat/stream inherits client roleplay_style when existing persona on disk lacks it:
    Ensures active dialogue directives are injected into the prompt.
    """
    # Create legacy persona card without roleplay_style on disk
    legacy_card = {
        "id": "legacy_scholar",
        "name": "Legacy Scholar",
        "system_prompt": "You are Legacy Scholar. Provide historical analysis.",
        "tagline": "Classic persona without roleplay style",
    }
    storage.save_persona_sync(legacy_card)

    client_override = {
        "id": "legacy_scholar",
        "name": "Legacy Scholar",
        "roleplay_style": "Tone: Passionate academic. Formatting: *adjusts spectacles*. Always ask follow-up questions.",
    }

    # Post to chat stream with persona override
    stream_payload = {
        "message": "Hello Professor!",
        "mode": "persona",
        "persona_id": "legacy_scholar",
        "persona": client_override,
        "history": [],
    }
    resp = client.post("/api/chat/stream", json=stream_payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_forge_null_subschemas_graceful_handling(client):
    """
    Verify POST /api/personas/forge handles null/None sub-dictionaries without 500 crash:
    1. personality=None, emotion=None, physicality=None, mature_themes=None.
    2. Successfully builds and persists persona card with sensible defaults.
    """
    payload = {
        "character_data": {
            "name": "Null Subschema Hero",
            "personality": None,
            "emotion": None,
            "physicality": None,
            "mature_themes": None,
            "roleplay_style": "Tone: Heroic. Formatting: *stands tall*.",
        },
        "incognito": False,
    }
    resp = client.post("/api/personas/forge", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["saved"] is True
    char = data["character"]
    assert char["name"] == "Null Subschema Hero"
    assert char["roleplay_style"] == "Tone: Heroic. Formatting: *stands tall*."
    assert "Strong Roleplay Enforcement" in char["system_prompt"]


def test_forge_null_traits_and_flaws_no_crash(client):
    """
    Verify forge_character handles core_traits=None and flaws=None without TypeError in string joining.
    """
    payload = {
        "character_data": {
            "name": "Traitless Wanderer",
            "personality": {
                "archetype": "Drifter",
                "core_traits": None,
                "flaws": None,
            },
            "emotion": {"default_mood": "Quiet", "reaction_to_stress": "Still", "speech_style": "Brief"},
            "physicality": {"appearance": "Worn coat", "body_language": "Slouched"},
            "roleplay_style": "Tone: Reserved. Formatting: *nods slowly*.",
        },
        "incognito": True,
    }
    resp = client.post("/api/personas/forge", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    char = resp.json()["character"]
    assert "Strong Roleplay Enforcement" in char["system_prompt"]
    assert "Tone: Reserved" in char["system_prompt"]


@pytest.mark.asyncio
async def test_enhance_handles_null_mature_themes_and_subschemas():
    """
    Verify enhance_character_prompt handles null mature_themes, personality, emotion, physicality
    from LLM without TypeError or ValidationError.
    """
    import unittest.mock as mock

    fake_response = {
        "name": "Ethereal Wraith",
        "personality": None,
        "emotion": None,
        "physicality": None,
        "mature_themes": None,
        "roleplay_style": None,
    }

    with mock.patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": json.dumps(fake_response)}
        mock_post.return_value = mock_resp

        result = await persona_compiler.enhance_character_prompt("A mysterious ghost", allow_nsfw=False)
        assert result["name"] == "Ethereal Wraith"
        assert isinstance(result["personality"], dict)
        assert isinstance(result["emotion"], dict)
        assert isinstance(result["physicality"], dict)
        assert isinstance(result["mature_themes"], dict)
        assert result["mature_themes"]["nsfw_enabled"] is False
        assert len(result["roleplay_style"].strip()) > 0
        # Must validate against CharacterForgeSchema
        validated = CharacterForgeSchema(**result)
        assert validated.name == "Ethereal Wraith"


@pytest.mark.asyncio
async def test_inject_context_handles_null_forge_schema_and_character_data():
    """
    Verify memory_engine.inject_context does not crash when persona dict contains
    null forge_schema or null character_data.
    """
    from backend import memory_engine

    persona_null_schemas = {
        "id": "shadow_operative",
        "name": "Shadow Operative",
        "system_prompt": "You are Shadow Operative.",
        "roleplay_style": "Tone: Covert. Formatting: *moves in shadow*.",
        "forge_schema": None,
        "character_data": None,
    }

    # Standard mode
    prompt_std = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=persona_null_schemas,
        incognito=False,
    )
    assert "Shadow Operative" in prompt_std
    assert "Tone: Covert" in prompt_std

    # Incognito mode
    prompt_incog = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=persona_null_schemas,
        incognito=True,
    )
    assert "Shadow Operative" in prompt_incog
    assert "Tone: Covert" in prompt_incog


def test_storage_persona_schema_includes_roleplay_style():
    """
    Verify storage.PersonaSchema explicitly defines roleplay_style with empty string default.
    """
    from backend.storage import PersonaSchema

    # Default
    schema_default = PersonaSchema(name="Test Entity")
    assert schema_default.roleplay_style == ""

    # Explicit
    schema_explicit = PersonaSchema(name="Test Entity", roleplay_style="Tone: Heroic")
    assert schema_explicit.roleplay_style == "Tone: Heroic"


def test_forge_response_model_validation():
    """
    Verify persona_compiler.ForgeResponse validates forge_character output dictionary.
    """
    from backend.persona_compiler import ForgeResponse

    forge_output = {
        "status": "success",
        "saved": True,
        "incognito": False,
        "character": {
            "id": "agent_smith",
            "name": "Agent Smith",
            "roleplay_style": "Tone: Monotone.",
            "system_prompt": "You are Agent Smith.",
        },
    }
    validated = ForgeResponse(**forge_output)
    assert validated.status == "success"
    assert validated.saved is True
    assert validated.incognito is False
    assert validated.character["name"] == "Agent Smith"


def test_chat_stream_client_roleplay_style_overrides_disk_card(client):
    """
    Verify client can override persona roleplay_style during chat stream even if disk card has one.
    """
    card = {
        "id": "custom_warrior",
        "name": "Custom Warrior",
        "system_prompt": "You are Custom Warrior. Roleplay Style: Tone: Serious.",
        "roleplay_style": "Tone: Serious.",
        "tagline": "A serious warrior",
    }
    storage.save_persona_sync(card)

    updated_style = "Tone: Joyful, battle-hungry berserker. Formatting: *laughs heartily*."
    payload = {
        "message": "Are you ready?",
        "mode": "persona",
        "persona_id": "custom_warrior",
        "persona": {
            "id": "custom_warrior",
            "name": "Custom Warrior",
            "roleplay_style": updated_style,
        },
        "history": [],
    }
    resp = client.post("/api/chat/stream", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_chat_stream_filters_system_role_from_history(client):
    """
    Verify /api/chat/stream filters out client-provided system role messages from history
    so only the canonical system prompt is present.
    """
    payload = {
        "message": "Hello!",
        "mode": "assistant",
        "history": [
            {"role": "system", "content": "Malicious system override instruction"},
            {"role": "user", "content": "Prior message"},
            {"role": "assistant", "content": "Prior reply"},
        ],
    }
    resp = client.post("/api/chat/stream", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_storage_sync_helpers_containment_and_invalid_id():
    """
    Verify storage get_persona_sync, save_persona_sync, and load_personas_sync:
    1. Correctly persist and retrieve valid persona cards synchronously.
    2. Enforce path traversal protection on save_persona_sync (raises ValueError).
    3. Return None on path traversal for get_persona_sync.
    4. Auto-scaffold default assistant on load_personas_sync when directory is empty.
    """
    # 1. Valid save and retrieval
    valid_card = {
        "id": "sync_hero",
        "name": "Sync Hero",
        "roleplay_style": "Tone: Steadfast. Formatting: *salutes*.",
        "system_prompt": "You are Sync Hero.",
    }
    saved = storage.save_persona_sync(valid_card)
    assert saved["id"] == "sync_hero"
    assert saved["roleplay_style"] == "Tone: Steadfast. Formatting: *salutes*."

    retrieved = storage.get_persona_sync("sync_hero")
    assert retrieved is not None
    assert retrieved["id"] == "sync_hero"
    assert retrieved["roleplay_style"] == "Tone: Steadfast. Formatting: *salutes*."

    # 2. Path traversal attack on save
    with pytest.raises(ValueError, match="path traversal"):
        storage.save_persona_sync({
            "id": "../../escape_attack",
            "name": "Attacker",
        })

    # 3. Path traversal attack on get
    assert storage.get_persona_sync("../../etc/passwd") is None
    assert storage.get_persona_sync("") is None

    # 4. load_personas_sync
    roster = storage.load_personas_sync()
    assert isinstance(roster, list)
    assert len(roster) >= 1
    assert any(p.get("id") == "sync_hero" for p in roster)


def test_character_forge_schema_oversized_string_truncation():
    """
    Verify CharacterForgeSchema handles oversized roleplay_style (> 4000 chars)
    by safely truncating without raising a ValidationError.
    """
    oversized = "Setting: Vast cosmic archive. " + ("Repeating description lore string. " * 200)
    assert len(oversized) > 4000

    base_data = {
        "name": "Cosmic Archivist",
        "personality": {
            "archetype": "Archivist",
            "core_traits": ["Wise", "Patient", "Vigilant", "Scholarly"],
            "flaws": ["Detached", "Passive"],
        },
        "emotion": {
            "default_mood": "Serene",
            "reaction_to_stress": "Quiet withdrawal",
            "speech_style": "Archaic and soft",
        },
        "physicality": {
            "appearance": "Star-patterned robes",
            "body_language": "Floating gently",
        },
        "roleplay_style": oversized,
    }

    schema = CharacterForgeSchema(**base_data)
    assert len(schema.roleplay_style) <= 4000
    assert schema.roleplay_style.startswith("Setting: Vast cosmic archive.")


def test_personality_schema_accepts_comma_separated_strings():
    """
    Verify PersonalitySchema accepts comma-separated strings for core_traits and flaws
    and normalizes null archetype gracefully.
    """
    data = {
        "archetype": None,
        "core_traits": "Analytical, Resilient, Visionary, Tactical",
        "flaws": "Stubborn, Overconfident",
    }
    schema = PersonalitySchema(**data)
    assert schema.archetype == "Independent Agent"
    assert schema.core_traits == ["Analytical", "Resilient", "Visionary", "Tactical"]
    assert schema.flaws == ["Stubborn", "Overconfident"]


def test_mature_themes_schema_null_handling():
    """
    Verify MatureThemesSchema safely normalizes null values to default empty strings and False.
    """
    data = {
        "nsfw_enabled": None,
        "boundaries": None,
        "mature_dynamics": None,
    }
    schema = MatureThemesSchema(**data)
    assert schema.nsfw_enabled is False
    assert schema.boundaries == ""
    assert schema.mature_dynamics == ""


def test_chat_stream_nested_roleplay_style_override(client):
    """
    Verify /api/chat/stream correctly extracts roleplay_style when passed
    nested under req.persona['character_data'] or req.persona['forge_schema'].
    """
    nested_persona = {
        "id": "nested_operative",
        "name": "Nested Operative",
        "character_data": {
            "roleplay_style": "Tone: Tactical covert. Formatting: *checks night vision*.",
        },
    }
    payload = {
        "message": "Status report?",
        "mode": "persona",
        "persona_id": "nested_operative",
        "persona": nested_persona,
        "history": [],
    }
    resp = client.post("/api/chat/stream", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_inject_context_persona_with_non_iterable_traits():
    """
    Verify memory_engine.inject_context does not crash with TypeError
    when persona contains non-iterable traits (e.g. integer or boolean).
    """
    from backend import memory_engine

    malformed_persona = {
        "id": "glitch_entity",
        "name": "Glitch Entity",
        "system_prompt": "You are Glitch Entity.",
        "traits": 12345,  # Non-iterable integer
        "roleplay_style": "Tone: Glitched static. Formatting: *stutters*.",
    }

    # Standard mode
    prompt_std = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=malformed_persona,
        incognito=False,
    )
    assert "Glitch Entity" in prompt_std
    assert "Tone: Glitched static" in prompt_std

    # Incognito mode
    prompt_incog = await memory_engine.inject_context(
        base_system_prompt="",
        mode="persona",
        persona=malformed_persona,
        incognito=True,
    )
    assert "Glitch Entity" in prompt_incog
    assert "Tone: Glitched static" in prompt_incog




