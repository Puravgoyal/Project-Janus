"""
Project Janus - Autonomous Memory Triage Test Suite
Verifies:
  - extract_and_triage extracts actionable reminders, work context, and user facts.
  - reminders.json is updated with a valid schema conforming to ReminderSchema.
  - Diff on reminders.json before and after triage is non-empty.
  - Urgent terms ("urgent", "asap", "critical", "immediately") receive "high" priority.
  - Work session notes are appended to work_context.json and respect the 15-item FIFO limit.
  - User profile facts are extracted and deduplicated in user_profile.json.
  - Handles markdown code fences and API transport exceptions gracefully with heuristic fallback.
  - Ensures clean test isolation and restores data files after execution.
"""

from __future__ import annotations

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

import httpx
import pytest

from backend import memory_engine, storage


# ============================================================================
# Test Fixtures & Isolation
# ============================================================================

@pytest.fixture(autouse=True)
def isolated_storage_env(tmp_path: Path):
    """
    Ensure every test runs in an isolated temporary data directory.
    Restores the active data directory after test completion to guarantee
    zero pollution of production/scaffold data files.
    """
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = test_data_dir / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    # Initialize scaffold defaults into isolated directory
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


def validate_reminder_schema(item: dict[str, Any]) -> None:
    """
    Validate that a reminder dictionary adheres strictly to the required schema:
    - id: non-empty string
    - text: non-empty string
    - completed: boolean
    - created_at: non-empty ISO timestamp string
    - priority: one of 'low', 'medium', 'high'
    - due_date: string or None
    """
    assert isinstance(item, dict), "Reminder record must be a dictionary"
    assert "id" in item and isinstance(item["id"], str) and len(item["id"]) > 0, "Missing or invalid 'id'"
    assert "text" in item and isinstance(item["text"], str) and len(item["text"].strip()) > 0, "Missing or invalid 'text'"
    assert "completed" in item and isinstance(item["completed"], bool), "Missing or invalid 'completed' boolean"
    assert "created_at" in item and isinstance(item["created_at"], str) and len(item["created_at"]) > 0, "Missing 'created_at'"
    assert "priority" in item and item["priority"] in ["low", "medium", "high"], f"Invalid priority: {item.get('priority')}"
    if item.get("due_date") is not None:
        assert isinstance(item["due_date"], str), "due_date must be string or None"


def calculate_file_diff(before_text: str, after_text: str, filename: str = "reminders.json") -> list[str]:
    """Calculate unified diff between two raw file string snapshots."""
    return list(difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after_text.splitlines(keepends=True),
        fromfile=f"{filename}.before",
        tofile=f"{filename}.after"
    ))


# ============================================================================
# 1. Primary Triage & Non-Empty Diff Assertions
# ============================================================================

@pytest.mark.asyncio
async def test_extract_and_triage_reminders_schema_and_non_empty_diff(isolated_storage_env: Path):
    """
    Sends mock conversation to extract_and_triage.
    Asserts reminders.json is updated with valid schema.
    Asserts that the diff on reminders.json is non-empty.
    """
    reminders_path = isolated_storage_env / "reminders.json"
    before_text = reminders_path.read_text(encoding="utf-8")
    before_reminders = await storage.load_reminders()
    before_count = len(before_reminders)

    user_msg = "Remind me to file quarterly tax report by Friday"
    asst_reply = "I've scheduled a reminder to file your quarterly tax report by Friday."

    triage_result = await memory_engine.extract_and_triage(user_msg, asst_reply)

    assert "reminders" in triage_result
    assert isinstance(triage_result["reminders"], list)
    assert len(triage_result["reminders"]) > 0

    after_text = reminders_path.read_text(encoding="utf-8")
    after_reminders = await storage.load_reminders()
    after_count = len(after_reminders)

    # 1. Assert count increased
    assert after_count > before_count, f"Expected more reminders, went from {before_count} to {after_count}"

    # 2. Assert non-empty diff on reminders.json
    diff_lines = calculate_file_diff(before_text, after_text, filename="reminders.json")
    assert len(diff_lines) > 0, "Diff on reminders.json must be non-empty after triage"
    assert any("quarterly tax report" in line for line in diff_lines), "Diff must contain the extracted reminder text"

    # 3. Assert schema validation on all persisted reminders
    for rem in after_reminders:
        validate_reminder_schema(rem)

    # 4. Confirm specific newly added reminder content
    new_items = [r for r in after_reminders if r["id"] not in {b["id"] for b in before_reminders}]
    assert len(new_items) >= 1
    new_tax_rem = next((r for r in new_items if "quarterly tax report" in r["text"].lower()), None)
    assert new_tax_rem is not None
    assert new_tax_rem["completed"] is False
    assert new_tax_rem["priority"] in ["low", "medium", "high"]


@pytest.mark.asyncio
async def test_empty_diff_when_no_action_items(isolated_storage_env: Path):
    """
    Verify triage test asserts empty diff when conversation contains zero reminders or casual chat.
    """
    reminders_path = isolated_storage_env / "reminders.json"
    before_text = reminders_path.read_text(encoding="utf-8")
    before_reminders = await storage.load_reminders()

    casual_msg = "Hello Janus, how are you functioning today?"
    casual_reply = "Greetings. All offline engines are operating within nominal parameters."

    triage_result = await memory_engine.extract_and_triage(casual_msg, casual_reply)

    after_text = reminders_path.read_text(encoding="utf-8")
    after_reminders = await storage.load_reminders()

    # Reminders list must remain identical
    assert len(after_reminders) == len(before_reminders)
    diff_lines = calculate_file_diff(before_text, after_text, filename="reminders.json")
    assert len(diff_lines) == 0, f"Expected empty diff for casual conversation, got: {diff_lines}"


# ============================================================================
# 2. Priority Handling: Urgent Terms
# ============================================================================

@pytest.mark.asyncio
async def test_urgent_terms_receive_high_priority(isolated_storage_env: Path):
    """
    Tests that terms like 'urgent', 'asap', 'critical', 'immediately' force high priority.
    """
    urgent_msg = "URGENT: Remind me to apply the critical security patch immediately"
    urgent_reply = "Understood. Prioritizing critical security patch immediately."

    result = await memory_engine.extract_and_triage(urgent_msg, urgent_reply)

    reminders = await storage.load_reminders()
    patch_rem = next((r for r in reminders if "security patch" in r["text"].lower()), None)

    assert patch_rem is not None, "Critical security reminder should be in reminders.json"
    assert patch_rem["priority"] == "high", f"Urgent reminder must have priority 'high', got '{patch_rem['priority']}'"


@pytest.mark.asyncio
async def test_standard_terms_receive_medium_or_low_priority(isolated_storage_env: Path):
    """
    Tests that non-urgent reminders receive default 'medium' or 'low' priority.
    """
    normal_msg = "Remind me to review the documentation when time permits next week"
    normal_reply = "I will keep that on your task list."

    await memory_engine.extract_and_triage(normal_msg, normal_reply)

    reminders = await storage.load_reminders()
    doc_rem = next((r for r in reminders if "documentation" in r["text"].lower()), None)

    assert doc_rem is not None
    assert doc_rem["priority"] in ["medium", "low"]


# ============================================================================
# 3. Work Context & 15-Item Rolling Buffer Limit
# ============================================================================

@pytest.mark.asyncio
async def test_work_notes_recorded_in_work_context(isolated_storage_env: Path):
    """
    Tests that substantive work descriptions are recorded into work_context.json.
    """
    work_msg = "I am working on configuring the dual-engine Ollama architecture for Project Janus"
    work_reply = "I have recorded this session milestone into your active work context."

    await memory_engine.extract_and_triage(work_msg, work_reply)

    work_ctx = await storage.load_work_context()
    assert len(work_ctx) > 0

    matching_note = next((w for w in work_ctx if "dual-engine" in w["note"].lower() or "ollama" in w["note"].lower()), None)
    assert matching_note is not None, "Work note should be recorded in work_context.json"
    assert "timestamp" in matching_note
    assert "id" in matching_note
    assert matching_note["category"] in ["progress", "decision", "blocker", "context"]


@pytest.mark.asyncio
async def test_rolling_buffer_15_item_limit_and_16th_item_overflow(isolated_storage_env: Path):
    """
    Tests that work_context.json enforces a strict 15-item maximum buffer.
    Exercises 16th to 25th item FIFO buffer truncation (oldest notes dropped).
    """
    total_notes_to_add = 25

    # Insert 25 distinct notes sequentially
    for i in range(total_notes_to_add):
        await storage.append_work_context(
            entry=f"Sprint Note #{i:02d}: Autonomous memory testing",
            category="progress"
        )

    loaded_context = await storage.load_work_context()

    # 1. Assert exactly 15 items maximum
    assert len(loaded_context) == 15, f"Expected strictly 15 items in buffer, found {len(loaded_context)}"

    # 2. Assert FIFO truncation: items #00 to #09 evicted; items #10 to #24 retained
    retained_texts = [item["note"] for item in loaded_context]
    for evicted_idx in range(10):
        assert f"Sprint Note #{evicted_idx:02d}" not in retained_texts, (
            f"Sprint Note #{evicted_idx:02d} should have been evicted by FIFO rolling buffer"
        )

    for retained_idx in range(10, total_notes_to_add):
        assert any(f"Sprint Note #{retained_idx:02d}" in txt for txt in retained_texts), (
            f"Sprint Note #{retained_idx:02d} should be retained in buffer"
        )

    # 3. Assert chronological order preserved (oldest retained note is #10, latest is #24)
    assert "Sprint Note #10" in retained_texts[0]
    assert f"Sprint Note #{total_notes_to_add - 1:02d}" in retained_texts[-1]


# ============================================================================
# 4. User Profile Fact Extraction
# ============================================================================

@pytest.mark.asyncio
async def test_user_facts_extracted_into_user_profile(isolated_storage_env: Path):
    """
    Tests that user facts and preferences are extracted and recorded in user_profile.json.
    """
    fact_msg = "I prefer dark-mode obsidian styling and I am a Lead Systems Architect"
    fact_reply = "I have updated your user profile with your interface preferences and role."

    await memory_engine.extract_and_triage(fact_msg, fact_reply)

    profile = await storage.load_user_profile()
    assert "facts" in profile
    assert isinstance(profile["facts"], list)

    # Check for recorded facts or preferences
    has_architect = any("architect" in f.lower() for f in profile["facts"])
    has_preference = any("dark-mode" in p.lower() or "obsidian" in p.lower() for p in profile.get("preferences", [])) or \
                     any("dark-mode" in f.lower() or "obsidian" in f.lower() for f in profile["facts"])

    assert has_architect or has_preference, "User profile should reflect extracted facts or preferences"


# ============================================================================
# 5. Schema Validation & Corrupt Record Rejection
# ============================================================================

def test_malformed_reminder_fails_schema_validation():
    """
    Verify validate_reminder_schema detects and rejects invalid reminder schemas.
    """
    # Missing text
    with pytest.raises(AssertionError):
        validate_reminder_schema({"id": "rem_123", "completed": False, "priority": "high", "created_at": "2026-09-13T00:00:00Z"})

    # Missing id
    with pytest.raises(AssertionError):
        validate_reminder_schema({"text": "Test reminder", "completed": False, "priority": "high", "created_at": "2026-09-13T00:00:00Z"})

    # Invalid priority
    with pytest.raises(AssertionError):
        validate_reminder_schema({"id": "rem_123", "text": "Test", "completed": False, "priority": "super_urgent", "created_at": "2026-09-13T00:00:00Z"})

    # Non-boolean completed
    with pytest.raises(AssertionError):
        validate_reminder_schema({"id": "rem_123", "text": "Test", "completed": "yes", "priority": "medium", "created_at": "2026-09-13T00:00:00Z"})


# ============================================================================
# 6. LLM API Exception Handling & Markdown Fence Stripping
# ============================================================================

@pytest.mark.asyncio
async def test_mock_exception_propagation_and_fallback(isolated_storage_env: Path, monkeypatch):
    """
    Verify triage test handles LLM API transport exceptions gracefully:
    When the CPU engine at Port 11435 is offline or throws ConnectionError,
    extract_and_triage falls back to deterministic heuristic extraction without crashing.
    """
    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("Connection refused on Port 11435")

    monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)

    # Even with Port 11435 unreachable, extract_and_triage should fall back to heuristic extraction
    result = await memory_engine.extract_and_triage(
        user_message="Remind me to verify fallback triage behavior",
        assistant_reply="I'll test the offline fallback."
    )

    assert "reminders" in result
    assert isinstance(result["reminders"], list)
    assert len(result["reminders"]) > 0
    assert any("fallback triage" in r["text"].lower() for r in result["reminders"])


@pytest.mark.asyncio
async def test_markdown_code_fences_stripped(isolated_storage_env: Path, monkeypatch):
    """
    Verify that if the LLM produces markdown code fences (```json ... ```),
    the memory engine cleans and parses the inner JSON properly.
    """
    llm_content = """```json
{
  "reminders": [
    {
      "text": "Submit Q3 compliance audit",
      "due_date": "2026-09-20",
      "priority": "high"
    }
  ],
  "work_note": {
    "note": "Finalizing Q3 audit preparation",
    "category": "progress"
  },
  "facts": ["Audit due by Q3 close"],
  "preferences": []
}
```"""

    class MockedSuccessAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": llm_content}}
            )

    monkeypatch.setattr(httpx, "AsyncClient", MockedSuccessAsyncClient)

    result = await memory_engine.extract_and_triage("Turn with fenced response", "Acknowledged.")

    assert len(result["reminders"]) == 1
    assert result["reminders"][0]["text"] == "Submit Q3 compliance audit"
    assert result["reminders"][0]["priority"] == "high"

    # Verify saved to reminders.json
    reminders = await storage.load_reminders()
    audit_rem = next((r for r in reminders if "compliance audit" in r["text"].lower()), None)
    assert audit_rem is not None
    assert audit_rem["priority"] == "high"


# ============================================================================
# 7. Persistent JSON Disk File Integrity
# ============================================================================

@pytest.mark.asyncio
async def test_storage_files_remain_valid_json_on_disk(isolated_storage_env: Path):
    """
    Verify triage operations leave reminders.json, work_context.json,
    and user_profile.json as strictly valid JSON files on disk.
    """
    # Execute a few triage cycles
    await memory_engine.extract_and_triage(
        "Remind me to inspect memory integrity",
        "Logging memory inspection."
    )
    await memory_engine.extract_and_triage(
        "I am working on verifying storage serialization",
        "Updated work context."
    )

    for filename in ["reminders.json", "work_context.json", "user_profile.json"]:
        file_path = isolated_storage_env / filename
        assert file_path.exists(), f"File {filename} must exist on disk"
        raw_text = file_path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
        assert isinstance(parsed, (dict, list)), f"File {filename} must parse as dict or list"
