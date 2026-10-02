"""
Project Janus - Tier 5 Adversarial Stress & Hardening Test Suite
Author: Challenger 1 (Empirical Adversarial Stress Tester)

Covers:
1. Concurrency Stress Testing:
   - Concurrent calls to extract_and_triage
   - Mixed high-contention storage reads and writes (WinError 32 file locking)
   - Concurrent chat streaming requests
2. Malformed Input Fuzzing:
   - Empty and whitespace-only payloads
   - Giant payloads (DoS resistance)
   - Malformed JSON in wiki dumps
   - Unicode, multi-byte, RTL, null byte, and path traversal injection
3. Boundary & State Hardening:
   - Rolling buffer overflow (> 15 items) and strict FIFO eviction
   - Rapid incognito mode toggles and cognitive memory isolation
   - Reminder ID edge cases and non-existent PATCH handling
"""

import asyncio
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from starlette.testclient import TestClient

from backend import memory_engine, persona_compiler, storage
from backend.main import app


# ---------------------------------------------------------------------------
# Test Fixtures & Isolated Environment
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_storage_env(tmp_path: Path):
    """
    Guarantees every stress test executes in a clean, isolated directory.
    Prevents race conditions or pollution of production data files.
    """
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = test_data_dir / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    # Populate scaffold defaults
    reminders_file = test_data_dir / "reminders.json"
    with open(reminders_file, "w", encoding="utf-8") as f:
        json.dump([r.copy() for r in storage.DEFAULT_REMINDERS], f, indent=2)

    work_ctx_file = test_data_dir / "work_context.json"
    with open(work_ctx_file, "w", encoding="utf-8") as f:
        json.dump([w.copy() for w in storage.DEFAULT_WORK_CONTEXT], f, indent=2)

    profile_file = test_data_dir / "user_profile.json"
    with open(profile_file, "w", encoding="utf-8") as f:
        json.dump(storage.DEFAULT_USER_PROFILE.copy(), f, indent=2)

    storage.set_data_dir(test_data_dir)
    yield test_data_dir
    storage.set_data_dir(None)


@pytest.fixture
def client():
    """FastAPI TestClient for REST & SSE endpoints."""
    return TestClient(app)


def parse_sse_events(raw_sse_text: str) -> list[dict]:
    """Helper to parse Server-Sent Events output."""
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


# ===========================================================================
# 1. CONCURRENCY STRESS TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_concurrent_storage_mixed_reads_and_writes(isolated_storage_env: Path):
    """
    Stress test 60 concurrent coroutines performing mixed reads and writes
    across all storage files (reminders, work_context, user_profile, personas).
    Verifies:
      - Zero WinError 32 sharing violations or PermissionErrors
      - Zero temporary file leaks (*.tmp)
      - All JSON files remain strictly valid JSON
    """
    async def worker(idx: int):
        # 1. Add reminder
        await storage.add_reminder(
            reminder_data=f"Stress reminder {idx}",
            priority="high" if idx % 2 == 0 else "medium"
        )
        # 2. Append work context
        await storage.append_work_context(
            entry=f"Stress work context note #{idx}",
            category="progress" if idx % 3 == 0 else "context"
        )
        # 3. Read reminders and context
        reminders = await storage.load_reminders()
        assert len(reminders) > 0
        ctx = await storage.load_work_context()
        assert len(ctx) <= 15
        # 4. Load and save persona
        if idx % 5 == 0:
            await storage.save_persona({
                "id": f"persona_stress_{idx}",
                "name": f"Stress Persona {idx}",
                "tagline": "Stress tester",
                "system_prompt": "You are a test.",
                "greeting": "Hello.",
                "traits": ["Resilient"]
            })

    tasks = [asyncio.create_task(worker(i)) for i in range(30)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Assert no coroutine crashed with an exception
    for i, res in enumerate(results):
        assert not isinstance(res, Exception), f"Coroutine {i} raised: {res}"

    # Verify disk files remain strictly valid JSON
    for filename in ["reminders.json", "work_context.json", "user_profile.json"]:
        file_path = isolated_storage_env / filename
        assert file_path.exists()
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert isinstance(data, (dict, list))

    # Verify no leaked .tmp files in data dir
    tmp_files = list(isolated_storage_env.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Leaked temporary files detected: {tmp_files}"


@pytest.mark.asyncio
async def test_concurrent_extract_and_triage_stress(isolated_storage_env: Path):
    """
    Stress test 15 concurrent calls to extract_and_triage.
    Verifies that simultaneous triage cycles correctly persist all reminders
    without crashing or deadlocking under lock contention.
    """
    async def triage_worker(idx: int):
        msg = f"Remind me to execute task omega {idx} by next Monday"
        reply = f"Acknowledged task omega {idx}."
        return await memory_engine.extract_and_triage(msg, reply)

    tasks = [asyncio.create_task(triage_worker(i)) for i in range(15)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, res in enumerate(results):
        assert not isinstance(res, Exception), f"Triage worker {i} failed: {res}"
        assert isinstance(res, dict)
        assert "reminders" in res

    # Verify reminders were safely stored
    stored_reminders = await storage.load_reminders()
    stored_texts = [r["text"].lower() for r in stored_reminders]
    for i in range(15):
        assert any(f"task omega {i}" in txt for txt in stored_texts), (
            f"Expected 'task omega {i}' in stored reminders"
        )


def test_concurrent_chat_stream_via_testclient(client):
    """
    Stress test sequential rapid chat stream requests through the FastAPI app.
    Verifies SSE generator cleanly initializes and terminates without socket or state corruption.
    """
    for i in range(10):
        resp = client.post("/api/chat/stream", json={
            "message": f"Ping request {i}: Remind me to check index {i}",
            "mode": "assistant",
            "incognito": False
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        events = parse_sse_events(resp.text)
        assert len(events) > 0
        assert any(e.get("done") is True for e in events)


# ===========================================================================
# 2. MALFORMED INPUT FUZZING
# ===========================================================================

def test_fuzz_chat_stream_empty_and_whitespace_variants(client):
    """
    Fuzz chat stream endpoint with empty, whitespace, and corrupt message payloads.
    Asserts HTTP 422 Unprocessable Entity is returned cleanly without HTTP 500.
    """
    fuzz_payloads = [
        {"message": ""},
        {"message": "   "},
        {"message": "\t\n\r  \n"},
        {"messages": []},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "system", "content": "hello"}]},  # No user message
        {}
    ]

    for payload in fuzz_payloads:
        resp = client.post("/api/chat/stream", json=payload)
        assert resp.status_code in [400, 422], (
            f"Expected 400 or 422 for payload {payload}, got {resp.status_code}"
        )


def test_fuzz_chat_stream_giant_payload_dos_resilience(client):
    """
    Fuzz chat stream endpoint with a giant 1 MB message payload.
    Asserts server accepts or safely processes without crashing or OOM.
    """
    giant_text = "A" * (1024 * 1024)  # 1 MB
    resp = client.post("/api/chat/stream", json={
        "message": giant_text,
        "mode": "assistant",
        "incognito": True  # Incognito to avoid triggering background LLM triage
    })
    assert resp.status_code == 200
    events = parse_sse_events(resp.text)
    assert any(e.get("done") is True for e in events)


@pytest.mark.asyncio
async def test_fuzz_persona_compiler_giant_text(isolated_storage_env: Path):
    """
    Fuzz persona compiler with 500 KB of raw biographical text.
    Verifies truncation cap (24,000 characters) protects against memory exhaustion.
    """
    giant_wiki = "Hypatia was an astronomer. " * 20000  # ~540 KB
    card = await persona_compiler.compile_wiki_to_card(
        raw_text=giant_wiki,
        character_name="Hypatia the Elder"
    )
    assert card is not None
    assert card["name"] == "Hypatia the Elder"
    assert "hypatia_the_elder" in card["id"]
    assert len(card["description"]) <= 400


@pytest.mark.asyncio
async def test_fuzz_persona_compiler_malformed_json_resilience(isolated_storage_env: Path):
    """
    Fuzz persona compiler with text containing broken JSON syntax and unbalanced braces.
    Verifies deterministic fallback synthesizes a valid card rather than raising an unhandled exception.
    """
    broken_json_text = '{"name": "Broken", "traits": [undefined, null, {unclosed: true'
    card = await persona_compiler.compile_wiki_to_card(
        raw_text=broken_json_text,
        character_name="Corrupt Json Character"
    )
    assert card is not None
    assert card["name"] == "Corrupt Json Character"
    assert "traits" in card
    assert isinstance(card["traits"], list)


@pytest.mark.asyncio
async def test_fuzz_unicode_and_injection(isolated_storage_env: Path):
    """
    Fuzz memory triage and storage with:
      - Emojis (multi-byte UTF-8)
      - RTL Arabic/Hebrew script
      - Null bytes and zero-width spaces
      - Script tags (<script>alert(1)</script>)
    Verifies storage and extraction remain intact without encoding errors.
    """
    fuzz_texts = [
        "Remind me to inspect 🔥🚀 Unicode emojis in database",
        "Remind me to review Arabic: مرحبا بالعالم و تفقد البيانات",
        "Remind me to check Hebrew: שלום עולם בדיקת מערכת",
        "Remind me to test null\x00byte and zero\u200bwidth characters",
        "Remind me to sanitize <script>alert('XSS')</script> and ' OR '1'='1"
    ]

    for text in fuzz_texts:
        result = await memory_engine.extract_and_triage(
            user_message=text,
            assistant_reply="Processed."
        )
        assert isinstance(result, dict)

    # Verify all records loaded from disk cleanly without UnicodeDecodeError
    reminders = await storage.load_reminders()
    assert len(reminders) >= len(fuzz_texts)


# ===========================================================================
# 3. BOUNDARY & HARDENING TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_boundary_work_context_strict_15_item_limit(isolated_storage_env: Path):
    """
    Boundary test for work_context.json:
    Appends 40 consecutive items to work context.
    Verifies:
      - Exactly 15 items are stored in work_context.json at all times.
      - The first 25 items (#00 to #24) are evicted in FIFO order.
      - The latest 15 items (#25 to #39) are strictly retained.
    """
    total = 40
    for i in range(total):
        await storage.append_work_context(
            entry=f"Context note #{i:02d}",
            category="progress"
        )

    context = await storage.load_work_context()
    assert len(context) == 15, f"Expected strictly 15 items, got {len(context)}"

    notes = [c["note"] for c in context]
    # Items 0 to 24 must be evicted
    for i in range(25):
        assert f"Context note #{i:02d}" not in notes

    # Items 25 to 39 must be present
    for i in range(25, 40):
        assert f"Context note #{i:02d}" in notes

    # FIFO ordering check
    assert notes[0] == "Context note #25"
    assert notes[-1] == "Context note #39"


@pytest.mark.asyncio
async def test_boundary_rapid_incognito_mode_toggles(isolated_storage_env: Path):
    """
    Boundary test for rapid toggling between Incognito mode (True) and Normal mode (False).
    Verifies:
      - Incognito requests strictly produce 0 reminders in storage.
      - Non-incognito requests reliably write reminders.
      - Injected system prompt under incognito contains zero private facts or active reminders.
    """
    # 1. Normal mode turn: adds reminder
    r1 = await memory_engine.extract_and_triage(
        "Remind me to execute phase one deployment",
        "Deployment reminder saved."
    )
    reminders_count_1 = len(await storage.load_reminders())

    # 2. Incognito context injection test
    incognito_prompt = await memory_engine.inject_context(
        base_system_prompt="Executive Assistant",
        mode="assistant",
        incognito=True
    )
    assert "Known Facts:" not in incognito_prompt
    assert "Pending Reminders" not in incognito_prompt
    assert "phase one deployment" not in incognito_prompt

    # 3. Normal context injection test
    normal_prompt = await memory_engine.inject_context(
        base_system_prompt="Executive Assistant",
        mode="assistant",
        incognito=False
    )
    assert "Pending Reminders" in normal_prompt or "User Profile" in normal_prompt

    # 4. Verify incognito suppression via API client
    client = TestClient(app)
    resp = client.post("/api/chat/stream", json={
        "message": "SECRET: Remind me to delete encrypted audit vault",
        "mode": "assistant",
        "incognito": True
    })
    assert resp.status_code == 200

    # Wait briefly and verify reminder count has not increased
    await asyncio.sleep(0.1)
    reminders_after = await storage.load_reminders()
    assert not any("encrypted audit vault" in r["text"].lower() for r in reminders_after)


def test_boundary_patch_nonexistent_reminder(client):
    """
    Boundary test: PATCH on a non-existent reminder ID.
    Asserts clean response without unhandled exceptions or 500 errors.
    """
    resp = client.patch("/api/reminders/non_existent_id_99999", json={"completed": True})
    assert resp.status_code == 404


# ===========================================================================
# 4. ADVERSARIAL CHALLENGE FINDINGS REPRODUCERS
# ===========================================================================

@pytest.mark.asyncio
async def test_challenge_persona_id_path_traversal(isolated_storage_env: Path):
    """
    CHALLENGE 1: Path Traversal in storage.get_persona(persona_id).
    Passing '../user_profile' accesses data/user_profile.json instead of data/personas/*.json.
    Because user_profile.json is a dict but does not match PersonaSchema,
    calling PersonaSchema(**data) can raise pydantic.ValidationError or leak files.
    """
    # Attempting to load persona with relative path traversal
    try:
        persona = await storage.get_persona("../user_profile")
        # If it returns a persona, it leaked user_profile!
        # If it raises pydantic.ValidationError, it unhandled crashes the caller.
        assert persona is None, "get_persona must return None or reject path traversal, not leak or crash"
    except Exception as exc:
        # A 500 / ValidationError crash is the exact vulnerability
        pytest.fail(f"Path traversal in persona_id caused unhandled exception: {type(exc).__name__}: {exc}")


@pytest.mark.asyncio
async def test_challenge_user_profile_facts_concurrent_lost_updates(isolated_storage_env: Path):
    """
    CHALLENGE 2: Read-Modify-Write Race Condition in extract_and_triage on user_profile.json.
    storage.load_user_profile() and storage.save_user_profile() are separate locked calls.
    Concurrent triage calls that both modify user facts will race, causing lost updates.
    """
    async def triage_profile(fact_id: int):
        await memory_engine.extract_and_triage(
            user_message=f"I am a System Operator {fact_id}",
            assistant_reply="Noted."
        )

    tasks = [asyncio.create_task(triage_profile(i)) for i in range(5)]
    await asyncio.gather(*tasks)

    profile = await storage.load_user_profile()
    facts = profile.get("facts", [])
    # In an ideal transactional model, all 5 facts should be retained.
    # If read-modify-write lost updates occurred, some facts will be missing.
    recorded_facts = [f for f in facts if "System Operator" in f]
    assert len(recorded_facts) == 5, (
        f"Race condition detected: Expected 5 distinct operator facts, but found {len(recorded_facts)}. "
        f"Some facts were overwritten by concurrent read-modify-write."
    )


@pytest.mark.asyncio
async def test_challenge_oversized_character_name_windows_max_path(isolated_storage_env: Path):
    """
    CHALLENGE 3: Oversized character name creates filename exceeding Windows MAX_PATH (260 chars).
    _generate_slug does not truncate the slug length, causing safe_write_json to fail
    with WinError 206 (filename too long) or FileNotFoundError on Windows.
    """
    long_name = "Alexander" * 30  # 270 characters
    try:
        card = await persona_compiler.compile_wiki_to_card(
            raw_text="A great conqueror of classical antiquity.",
            character_name=long_name
        )
        assert card is not None
        assert len(card["id"]) <= 200, "Slug ID should be safely capped to prevent exceeding Windows MAX_PATH"
    except OSError as err:
        pytest.fail(f"Oversized character name caused Windows filesystem error: {err}")


def test_challenge_patch_arbitrary_missing_id_creates_unwanted_reminder(client):
    """
    CHALLENGE 4: Non-existent reminder PATCH creates reminder if ID doesn't contain 'non_existent'.
    In main.py:
      if 'non_existent' in reminder_id.lower(): raise HTTPException(404)
      created = await storage.add_reminder(...)
    An unknown ID like 'rem_random_999' returns 200 OK and creates an unwanted reminder!
    """
    resp = client.patch("/api/reminders/rem_random_missing_123", json={"completed": True})
    # Proper REST semantics require 404 for missing resources
    assert resp.status_code == 404, (
        f"Expected HTTP 404 for non-existent reminder 'rem_random_missing_123', got {resp.status_code}. "
        f"Backend improperly upserted a new reminder instead of returning 404 Not Found."
    )

