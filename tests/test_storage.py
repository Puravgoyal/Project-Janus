"""
Unit and Concurrency Tests for Project Janus Storage Layer
Verifies atomic writes, concurrent access handling, rolling buffer truncation (max 15),
schema integrity, and self-healing recovery.
"""

import asyncio
import json
import sys
from pathlib import Path

# Ensure root directory is in sys.path for direct pytest invocation
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest

from backend import storage


@pytest.fixture(autouse=True)
def isolated_storage_env(tmp_path: Path):
    """Ensure every test runs in an isolated temporary data directory."""
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = test_data_dir / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    storage.set_data_dir(test_data_dir)
    yield test_data_dir
    storage.set_data_dir(None)


# ----------------------------------------------------------------------
# 1. Atomic Persistence & Clean Disk State
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_atomic_write_and_read(isolated_storage_env: Path):
    test_file = isolated_storage_env / "test_doc.json"
    payload = {
        "title": "Dual Engine Offline Architecture",
        "gpu_port": 11434,
        "cpu_port": 11435,
        "features": ["SSE streaming", "Background triage", "Strict loopback"]
    }

    await storage.safe_write_json(test_file, payload)

    assert test_file.exists(), "Target file must exist after safe_write_json"

    # Confirm no temporary files remain in the directory
    temp_files = list(isolated_storage_env.glob("*.tmp"))
    assert len(temp_files) == 0, f"Found leaked temporary files: {temp_files}"

    # Read back through safe_read_json
    read_data = await storage.safe_read_json(test_file)
    assert read_data == payload

    # Verify raw disk bytes are valid formatted JSON
    with open(test_file, "r", encoding="utf-8") as f:
        disk_content = json.load(f)
    assert disk_content == payload


# ----------------------------------------------------------------------
# 2. Concurrency Safety & Mutex Locking
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_writes_integrity(isolated_storage_env: Path):
    """
    Test rapid concurrent writes to the same file from 30 coroutines.
    Ensures no WinError 32 sharing violations or zero-byte file corruptions occur.
    """
    target_file = isolated_storage_env / "shared_counter.json"
    await storage.safe_write_json(target_file, {"count": 0})

    async def writer_worker(worker_id: int):
        for step in range(5):
            await asyncio.sleep(0.001)
            # Write unique state
            await storage.safe_write_json(target_file, {
                "last_worker": worker_id,
                "step": step,
                "timestamp": f"t_{worker_id}_{step}"
            })

    tasks = [asyncio.create_task(writer_worker(i)) for i in range(20)]
    await asyncio.gather(*tasks)

    # Verify target file is intact and valid JSON
    final_data = await storage.safe_read_json(target_file)
    assert "last_worker" in final_data
    assert "step" in final_data

    # Verify no temp files leaked
    temp_files = list(isolated_storage_env.glob("*.tmp"))
    assert len(temp_files) == 0


@pytest.mark.asyncio
async def test_concurrent_add_reminders_no_lost_updates(isolated_storage_env: Path):
    """
    Verify that concurrent add_reminder operations do not overwrite each other.
    All reminders added by distinct tasks must be present in the final dataset.
    """
    num_reminders = 25

    async def add_worker(idx: int):
        return await storage.add_reminder(
            reminder_data=f"Concurrent Reminder #{idx}",
            priority="high" if idx % 2 == 0 else "medium"
        )

    tasks = [asyncio.create_task(add_worker(i)) for i in range(num_reminders)]
    created_items = await asyncio.gather(*tasks)

    assert len(created_items) == num_reminders

    # Check persisted reminders
    stored_reminders = await storage.load_reminders()
    stored_ids = {r["id"] for r in stored_reminders}

    for item in created_items:
        assert item["id"] in stored_ids
        assert item["text"].startswith("Concurrent Reminder #")


# ----------------------------------------------------------------------
# 3. Rolling 15-Item Buffer Mechanics (FIFO)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rolling_buffer_truncation_max_15(isolated_storage_env: Path):
    """
    Verify that appending more than 15 items truncates to exactly the latest 15 items in FIFO order.
    """
    total_notes = 25

    for i in range(total_notes):
        await storage.append_work_context(
            entry=f"Work note #{i:02d}",
            category="progress" if i % 2 == 0 else "decision"
        )

    loaded_context = await storage.load_work_context()

    # Rule: Strict max 15 items
    assert len(loaded_context) == 15, f"Expected exactly 15 items, got {len(loaded_context)}"

    # Rule: FIFO - the oldest 10 notes (00 to 09) should be dropped
    # The retained notes must be 10 through 24
    retained_notes = [item["note"] for item in loaded_context]
    for i in range(10):
        assert f"Work note #{i:02d}" not in retained_notes

    for i in range(10, total_notes):
        assert f"Work note #{i:02d}" in retained_notes

    # Verify chronological sequence
    assert retained_notes[0] == "Work note #10"
    assert retained_notes[-1] == "Work note #24"


# ----------------------------------------------------------------------
# 4. User Profile Persistence & Schema Integrity
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_profile_crud_and_schema(isolated_storage_env: Path):
    # Initial load returns default profile
    profile = await storage.load_user_profile()
    assert profile["name"] == "User"
    assert "preferences" in profile
    assert "facts" in profile
    assert "last_updated" in profile

    # Update profile
    profile["preferences"].append("Use Python asyncio for concurrency")
    profile["facts"].append("System running RTX 4050 6GB GPU")
    saved = await storage.save_user_profile(profile)

    assert "Use Python asyncio for concurrency" in saved["preferences"]
    assert "System running RTX 4050 6GB GPU" in saved["facts"]

    # Verify persisted on disk
    reloaded = await storage.load_user_profile()
    assert reloaded == saved


# ----------------------------------------------------------------------
# 5. Reminders CRUD & State Transitions
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reminder_crud_and_status_toggle(isolated_storage_env: Path):
    # Add new reminder via string
    new_rem = await storage.add_reminder(
        reminder_data="Test Ollama CPU triage port 11435",
        due_date="2026-09-15",
        priority="high"
    )
    rem_id = new_rem["id"]
    assert new_rem["completed"] is False
    assert new_rem["text"] == "Test Ollama CPU triage port 11435"

    # Update reminder completion
    updated = await storage.update_reminder(rem_id, {"completed": True})
    assert updated is not None
    assert updated["completed"] is True
    assert updated["id"] == rem_id

    # Verify update persisted
    all_reminders = await storage.load_reminders()
    match = next((r for r in all_reminders if r["id"] == rem_id), None)
    assert match is not None
    assert match["completed"] is True

    # Update non-existent reminder returns None
    missing = await storage.update_reminder("rem_nonexistent_999", {"completed": True})
    assert missing is None


# ----------------------------------------------------------------------
# 6. Persona Management & Default Card
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_personas_management_and_default_scaffold(isolated_storage_env: Path):
    # Loading personas on empty dir scaffolds janus
    personas = await storage.load_personas()
    assert len(personas) >= 1

    exec_asst = await storage.get_persona("janus")
    assert exec_asst is not None
    assert exec_asst["id"] == "janus"
    assert "name" in exec_asst
    assert "tagline" in exec_asst
    assert "system_prompt" in exec_asst
    assert "greeting" in exec_asst
    assert "traits" in exec_asst
    assert "avatar" in exec_asst

    # Save a custom compiled persona card
    marcus = {
        "id": "marcus_aurelius",
        "name": "Marcus Aurelius",
        "tagline": "Roman Emperor & Stoic Philosopher",
        "avatar": "🏛️",
        "system_prompt": "You are Marcus Aurelius. Speak with stoic gravity and measured wisdom.",
        "greeting": "Waste no more time arguing what a good man should be. Be one.",
        "traits": ["Stoic", "Philosophical", "Resolute", "Reflective"]
    }
    saved_marcus = await storage.save_persona(marcus)
    assert saved_marcus["id"] == "marcus_aurelius"

    # Retrieve specific persona
    fetched = await storage.get_persona("marcus_aurelius")
    assert fetched is not None
    assert fetched["name"] == "Marcus Aurelius"
    assert "Stoic" in fetched["traits"]

    # Ensure load_personas now includes both
    all_cards = await storage.load_personas()
    card_ids = [c["id"] for c in all_cards]
    assert "janus" in card_ids
    assert "marcus_aurelius" in card_ids


# ----------------------------------------------------------------------
# 7. Corrupt File Recovery (Self-Healing)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_corrupt_file_recovery_and_backup(isolated_storage_env: Path):
    profile_path = isolated_storage_env / "user_profile.json"

    # Intentionally corrupt the file with invalid JSON
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write("{ invalid json corrupted bytes -- <<<")

    # Loading user profile should detect corrupt JSON, back it up to .bak, and regenerate default
    recovered_profile = await storage.load_user_profile()
    assert recovered_profile["name"] == "User"
    assert len(recovered_profile["preferences"]) > 0

    # Confirm backup file was created
    bak_files = list(isolated_storage_env.glob("user_profile.json.bak.*"))
    assert len(bak_files) >= 1
    with open(bak_files[0], "r", encoding="utf-8") as f:
        bak_content = f.read()
    assert "{ invalid json" in bak_content


# ----------------------------------------------------------------------
# 8. Boundary Conditions & Error Handling
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_not_found_without_default_factory(isolated_storage_env: Path):
    missing_file = isolated_storage_env / "non_existent.json"
    with pytest.raises(FileNotFoundError):
        await storage.safe_read_json(missing_file)


@pytest.mark.asyncio
async def test_save_reminders_with_dict_envelope(isolated_storage_env: Path):
    envelope = {
        "reminders": [
            {
                "id": "rem_env_1",
                "text": "Envelope format test",
                "created_at": "2026-09-13T00:00:00Z",
                "due_date": None,
                "completed": False,
                "priority": "low"
            }
        ]
    }
    saved = await storage.save_reminders(envelope)
    assert len(saved) == 1
    assert saved[0]["id"] == "rem_env_1"

    reloaded = await storage.load_reminders()
    assert len(reloaded) == 1
    assert reloaded[0]["text"] == "Envelope format test"

