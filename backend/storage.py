"""
Project Janus - Backend Storage Engine
Safe asynchronous persistence layer with per-file mutex locking,
atomic disk writes (tempfile -> os.replace), schema validation, and corrupt-file self-healing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from pydantic import BaseModel, Field

logger = logging.getLogger("janus.storage")

# Global in-memory lock registry: maps canonical resolved file path -> asyncio.Lock
_LOCKS: dict[str, asyncio.Lock] = {}
_LOCK_REGISTRY_LOCK = threading.Lock()
_CUSTOM_DATA_DIR: Path | None = None


# ----------------------------------------------------------------------
# Schema Models (Pydantic v1 & v2 compatible)
# ----------------------------------------------------------------------

class UserProfileSchema(BaseModel):
    name: str = "User"
    role: Optional[str] = "Lead Architect"
    preferences: Union[list[str], dict[str, Any]] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReminderSchema(BaseModel):
    id: str = Field(default_factory=lambda: f"rem_{uuid.uuid4().hex[:8]}")
    text: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    due_date: Optional[str] = None
    completed: bool = False
    priority: Optional[str] = "medium"


class WorkContextSchema(BaseModel):
    id: str = Field(default_factory=lambda: f"ctx_{uuid.uuid4().hex[:8]}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str
    category: Optional[str] = "context"
    summary: Optional[str] = None


class PersonaSchema(BaseModel):
    id: str = Field(default_factory=lambda: f"persona_{uuid.uuid4().hex[:6]}")
    name: str
    tagline: Optional[str] = "Custom Persona"
    system_prompt: Optional[str] = "You are a custom persona."
    greeting: Optional[str] = "Greetings."
    avatar: str = "⚡"
    traits: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    roleplay_style: Optional[str] = ""
    personality: Optional[Union[list[str], dict[str, Any]]] = None
    speaking_style: Optional[dict[str, Any]] = None
    world_knowledge: Optional[list[str]] = None
    example_dialogue: Optional[dict[str, Any]] = None

    class Config:
        extra = "allow"


def _model_dump(instance: BaseModel) -> dict[str, Any]:
    """Extract dictionary from Pydantic model across v1 and v2."""
    if hasattr(instance, "model_dump"):
        return instance.model_dump()
    return instance.dict()


# ----------------------------------------------------------------------
# Default Scaffold Templates
# ----------------------------------------------------------------------

DEFAULT_USER_PROFILE: dict[str, Any] = {
    "name": "User",
    "role": "Lead Architect",
    "preferences": [
        "concise and rigorous responses",
        "dark-mode obsidian theme",
        "strict offline privacy"
    ],
    "facts": [
        "Operates on Windows 11 with local dual Ollama engines",
        "Prefers 100% offline and privacy-first local AI operations",
        "Dedicated RTX 4050 GPU for chat and 8 CPU P-cores for memory extraction"
    ],
    "last_updated": "2026-09-13T00:00:00Z"
}

DEFAULT_REMINDERS: list[dict[str, Any]] = [
    {
        "id": "rem_init_1",
        "text": "Verify dual Ollama engine launch script and port bindings",
        "created_at": "2026-09-13T00:00:00Z",
        "due_date": "2026-09-13",
        "completed": False,
        "priority": "high"
    },
    {
        "id": "rem_init_2",
        "text": "Inspect rolling work context and cognitive state persistence",
        "created_at": "2026-09-13T00:05:00Z",
        "due_date": None,
        "completed": False,
        "priority": "medium"
    },
    {
        "id": "rem_001",
        "text": "Prepare quarterly presentation",
        "created_at": "2026-09-13T00:10:00Z",
        "due_date": "2026-09-13",
        "completed": False,
        "priority": "high"
    },
    {
        "id": "rem_test",
        "text": "Integration test reminder",
        "created_at": "2026-09-13T00:15:00Z",
        "due_date": None,
        "completed": False,
        "priority": "medium"
    }
]

DEFAULT_WORK_CONTEXT: list[dict[str, Any]] = [
    {
        "id": "ctx_init_1",
        "timestamp": "2026-09-13T00:00:00Z",
        "note": "Initialized Project Janus workspace, data models, and local storage layer.",
        "category": "progress"
    },
    {
        "id": "ctx_init_2",
        "timestamp": "2026-09-13T00:10:00Z",
        "note": "Configured dual Ollama topology for GPU interactive chat and CPU background extraction.",
        "category": "context"
    }
]

DEFAULT_JANUS: dict[str, Any] = {
    "id": "janus",
    "name": "Janus",
    "character_name": "Janus",
    "tagline": "Omniscient, hyper-efficient executive assistant",
    "avatar": "⚡",
    "system_prompt": (
        "You are Janus, an omniscient, hyper-efficient executive assistant and system core. "
        "You must address the user strictly as 'Demi'. Anticipate needs before they are stated. "
        "Demi's time and privacy are the highest priorities. Order must be extracted from chaos."
    ),
    "greeting": (
        "Good morning, Demi. What is on the agenda?"
    ),
    "traits": [
        "Calm",
        "Deeply loyal",
        "Highly analytical",
        "Subtly elegant"
    ],
    "description": "Omniscient, hyper-efficient executive assistant and system core",
    "roleplay_style": "",
    "personality": [
        "Calm",
        "Deeply loyal",
        "Highly analytical",
        "Subtly elegant"
    ],
    "speaking_style": {
        "tone": "Crisp and measured",
        "vocabulary": "Refined and technical",
        "quirks": ["Addresses the user strictly as 'Demi'", "Anticipates needs before they are stated"]
    }
}


# ----------------------------------------------------------------------
# Environment & Path Configuration
# ----------------------------------------------------------------------

def get_data_dir() -> Path:
    """Return the absolute Path to the active data directory."""
    if _CUSTOM_DATA_DIR is not None:
        return _CUSTOM_DATA_DIR
    env_dir = os.environ.get("JANUS_DATA_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(__file__).resolve().parent.parent / "data"


def set_data_dir(data_dir: Path | str | None) -> None:
    """Override active data directory (primarily used for unit testing)."""
    global _CUSTOM_DATA_DIR
    if data_dir is None:
        _CUSTOM_DATA_DIR = None
    else:
        _CUSTOM_DATA_DIR = Path(data_dir).resolve()


def get_file_lock(file_path: Path | str) -> asyncio.Lock:
    """Return the canonical asyncio.Lock corresponding to the target file path."""
    resolved_key = str(Path(file_path).resolve())
    with _LOCK_REGISTRY_LOCK:
        if resolved_key not in _LOCKS:
            _LOCKS[resolved_key] = asyncio.Lock()
        return _LOCKS[resolved_key]


# ----------------------------------------------------------------------
# Synchronous Disk IO Helpers (Offloaded to worker threads)
# ----------------------------------------------------------------------

def _sync_atomic_write(target_path: Path, data: Any) -> None:
    """
    Persist data to target_path atomically:
    1. Write to a temporary file in the same directory.
    2. Flush and fsync the file descriptor to ensure physical storage.
    3. Explicitly close handle before os.replace.
    4. Atomic rename via os.replace with retry loop for Windows file locking (WinError 32).
    5. Clean up temp file on failure.
    """
    target = Path(target_path).resolve()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    temp_path = parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        # Windows-resilient atomic replace loop
        max_retries = 8
        for attempt in range(max_retries):
            try:
                os.replace(temp_path, target)
                break
            except PermissionError:
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.015 * (2 ** attempt))
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _sync_read(target_path: Path, default_factory: Callable[[], Any] | None = None) -> Any:
    """
    Read and parse JSON from target_path.
    If file does not exist, uses default_factory if provided.
    If file is corrupted (JSONDecodeError), saves a .bak copy and recovers with default_factory.
    """
    target = Path(target_path).resolve()
    if not target.exists():
        if default_factory is not None:
            default_val = default_factory()
            _sync_atomic_write(target, default_val)
            return default_val
        raise FileNotFoundError(f"File not found: {target}")

    try:
        with open(target, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        logger.warning("Corrupt JSON file detected at %s: %s. Initiating recovery.", target, err)
        backup_path = target.parent / f"{target.name}.bak.{int(time.time())}"
        try:
            shutil.copy2(target, backup_path)
        except OSError:
            pass

        if default_factory is not None:
            recovered_val = default_factory()
            _sync_atomic_write(target, recovered_val)
            return recovered_val
        raise


# ----------------------------------------------------------------------
# Low-Level Async Storage Operations
# ----------------------------------------------------------------------

async def safe_read_json(file_path: Path | str, default_factory: Callable[[], Any] | None = None) -> Any:
    """Thread-safe and async-safe read from a JSON file using per-file mutex lock."""
    path_obj = Path(file_path).resolve()
    lock = get_file_lock(path_obj)
    async with lock:
        return await asyncio.to_thread(_sync_read, path_obj, default_factory)


async def safe_write_json(file_path: Path | str, data: Any) -> None:
    """Thread-safe and async-safe atomic write to a JSON file using per-file mutex lock."""
    path_obj = Path(file_path).resolve()
    lock = get_file_lock(path_obj)
    async with lock:
        await asyncio.to_thread(_sync_atomic_write, path_obj, data)


# ----------------------------------------------------------------------
# High-Level Storage Helper Functions
# ----------------------------------------------------------------------

async def load_user_profile() -> dict[str, Any]:
    """Load user profile data from data/user_profile.json with schema fallback."""
    profile_path = get_data_dir() / "user_profile.json"
    data = await safe_read_json(profile_path, default_factory=lambda: DEFAULT_USER_PROFILE.copy())
    # Validate and return normalized dict
    validated = UserProfileSchema(**data)
    return _model_dump(validated)


async def save_user_profile(profile_data: dict[str, Any]) -> dict[str, Any]:
    """Save user profile to data/user_profile.json, refreshing last_updated timestamp."""
    profile_path = get_data_dir() / "user_profile.json"
    data = dict(profile_data)
    if "last_updated" not in data or not data["last_updated"]:
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
    validated = UserProfileSchema(**data)
    dumped = _model_dump(validated)
    await safe_write_json(profile_path, dumped)
    return dumped


async def update_user_profile_atomic(mutator: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """
    Atomically load, mutate, and persist user profile under a single file lock.
    mutator(profile) must return True if modifications were made.
    """
    profile_path = get_data_dir() / "user_profile.json"
    lock = get_file_lock(profile_path)

    async with lock:
        current_data = await asyncio.to_thread(_sync_read, profile_path, lambda: DEFAULT_USER_PROFILE.copy())
        profile = dict(current_data) if isinstance(current_data, dict) else DEFAULT_USER_PROFILE.copy()
        modified = mutator(profile)
        if modified:
            profile["last_updated"] = datetime.now(timezone.utc).isoformat()
            validated = _model_dump(UserProfileSchema(**profile))
            await asyncio.to_thread(_sync_atomic_write, profile_path, validated)
            return validated
        return profile


async def load_reminders() -> list[dict[str, Any]]:
    """Load reminder items from data/reminders.json, supporting root lists or dict envelopes."""
    reminders_path = get_data_dir() / "reminders.json"
    raw_data = await safe_read_json(reminders_path, default_factory=lambda: [r.copy() for r in DEFAULT_REMINDERS])
    if isinstance(raw_data, dict) and "reminders" in raw_data:
        items = raw_data["reminders"]
    elif isinstance(raw_data, list):
        items = raw_data
    else:
        items = []

    validated_items: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            validated_items.append(_model_dump(ReminderSchema(**item)))
    return validated_items


async def save_reminders(reminders_data: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    """Persist list of reminders to data/reminders.json."""
    reminders_path = get_data_dir() / "reminders.json"
    if isinstance(reminders_data, dict) and "reminders" in reminders_data:
        raw_items = reminders_data["reminders"]
    elif isinstance(reminders_data, list):
        raw_items = reminders_data
    else:
        raw_items = []

    validated_items = [_model_dump(ReminderSchema(**item)) for item in raw_items if isinstance(item, dict)]
    await safe_write_json(reminders_path, validated_items)
    return validated_items


async def add_reminder(
    reminder_data: dict[str, Any] | str,
    due_date: str | None = None,
    priority: str = "medium"
) -> dict[str, Any]:
    """
    Add a new reminder and atomically persist it to data/reminders.json.
    Accepts a dictionary or a plain text string.
    """
    reminders_path = get_data_dir() / "reminders.json"
    lock = get_file_lock(reminders_path)

    if isinstance(reminder_data, str):
        payload = {
            "id": f"rem_{uuid.uuid4().hex[:8]}",
            "text": reminder_data,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "due_date": due_date,
            "completed": False,
            "priority": priority
        }
    else:
        payload = dict(reminder_data)
        if "id" not in payload or not payload["id"]:
            payload["id"] = f"rem_{uuid.uuid4().hex[:8]}"
        if "created_at" not in payload:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        if "completed" not in payload:
            payload["completed"] = False
        if "due_date" not in payload:
            payload["due_date"] = due_date
        if "priority" not in payload:
            payload["priority"] = priority

    new_item = _model_dump(ReminderSchema(**payload))

    async with lock:
        current_data = await asyncio.to_thread(_sync_read, reminders_path, lambda: [r.copy() for r in DEFAULT_REMINDERS])
        if isinstance(current_data, dict) and "reminders" in current_data:
            items = list(current_data["reminders"])
        elif isinstance(current_data, list):
            items = list(current_data)
        else:
            items = []

        items.append(new_item)
        await asyncio.to_thread(_sync_atomic_write, reminders_path, items)

    return new_item


async def update_reminder(reminder_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """
    Update an existing reminder by id.
    Returns the updated reminder dictionary if found, or None if not found.
    """
    reminders_path = get_data_dir() / "reminders.json"
    lock = get_file_lock(reminders_path)

    async with lock:
        current_data = await asyncio.to_thread(_sync_read, reminders_path, lambda: [r.copy() for r in DEFAULT_REMINDERS])
        if isinstance(current_data, dict) and "reminders" in current_data:
            items = list(current_data["reminders"])
        elif isinstance(current_data, list):
            items = list(current_data)
        else:
            items = []

        target_item: dict[str, Any] | None = None
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == reminder_id:
                updated_dict = {**item, **updates}
                # Preserve id and creation timestamp
                updated_dict["id"] = reminder_id
                target_item = _model_dump(ReminderSchema(**updated_dict))
                items[i] = target_item
                break

        if target_item is not None:
            await asyncio.to_thread(_sync_atomic_write, reminders_path, items)
            return target_item

    return None


async def load_work_context() -> list[dict[str, Any]]:
    """Load rolling work context notes from data/work_context.json (up to 15 items)."""
    context_path = get_data_dir() / "work_context.json"
    raw_data = await safe_read_json(context_path, default_factory=lambda: [w.copy() for w in DEFAULT_WORK_CONTEXT])
    if isinstance(raw_data, dict) and "notes" in raw_data:
        items = raw_data["notes"]
    elif isinstance(raw_data, list):
        items = raw_data
    else:
        items = []

    validated_items: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            # Normalize note field if summary was provided
            if "note" not in item and "summary" in item:
                item["note"] = item["summary"]
            validated_items.append(_model_dump(WorkContextSchema(**item)))
    # Enforce FIFO maximum 15 items
    return validated_items[-15:]


async def append_work_context(entry: dict[str, Any] | str, category: str = "context") -> list[dict[str, Any]]:
    """
    Append an entry to rolling work context in data/work_context.json.
    Strictly truncates to the latest 15 items in FIFO order.
    """
    context_path = get_data_dir() / "work_context.json"
    lock = get_file_lock(context_path)

    if isinstance(entry, str):
        payload = {
            "id": f"ctx_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": entry,
            "category": category
        }
    else:
        payload = dict(entry)
        if "id" not in payload or not payload["id"]:
            payload["id"] = f"ctx_{uuid.uuid4().hex[:8]}"
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "note" not in payload and "summary" in payload:
            payload["note"] = payload["summary"]
        if "category" not in payload:
            payload["category"] = category

    new_item = _model_dump(WorkContextSchema(**payload))

    async with lock:
        current_data = await asyncio.to_thread(_sync_read, context_path, lambda: [w.copy() for w in DEFAULT_WORK_CONTEXT])
        if isinstance(current_data, dict) and "notes" in current_data:
            items = list(current_data["notes"])
        elif isinstance(current_data, list):
            items = list(current_data)
        else:
            items = []

        items.append(new_item)
        # Strict 15-item rolling FIFO buffer
        items = items[-15:]
        await asyncio.to_thread(_sync_atomic_write, context_path, items)

    return items


async def load_personas() -> list[dict[str, Any]]:
    """
    Load all persona cards from data/personas/*.json.
    If directory is missing or empty, initializes janus.json.
    """
    personas_dir = get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    json_files = list(personas_dir.glob("*.json"))
    if not json_files:
        # Scaffold default assistant persona
        default_path = personas_dir / "janus.json"
        await safe_write_json(default_path, DEFAULT_JANUS)
        json_files = [default_path]

    personas: list[dict[str, Any]] = []
    for jf in json_files:
        try:
            data = await safe_read_json(jf)
            if isinstance(data, dict):
                personas.append(_model_dump(PersonaSchema(**data)))
        except Exception as e:
            logger.error("Failed to load persona file %s: %s", jf, e)

    personas.sort(key=lambda p: p.get("id", ""))
    return personas


def _is_safe_persona_id(persona_id: str) -> bool:
    """Check if persona ID is alphanumeric, hyphens, or underscores without path traversal."""
    if not persona_id or not isinstance(persona_id, str):
        return False
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", persona_id))


async def get_persona(persona_id: str) -> dict[str, Any] | None:
    """Retrieve a specific persona by ID from data/personas/{persona_id}.json."""
    if not _is_safe_persona_id(persona_id):
        return None

    personas_dir = get_data_dir() / "personas"
    card_path = (personas_dir / f"{persona_id}.json").resolve()

    # Containment check
    try:
        card_path.relative_to(personas_dir.resolve())
    except ValueError:
        return None

    if card_path.exists():
        try:
            data = await safe_read_json(card_path)
            if isinstance(data, dict):
                return _model_dump(PersonaSchema(**data))
        except Exception as e:
            logger.warning("Failed to validate persona card %s: %s", card_path, e)
            return None

    all_personas = await load_personas()
    for p in all_personas:
        if p.get("id") == persona_id:
            return p
    return None


async def save_persona(persona_data: dict[str, Any]) -> dict[str, Any]:
    """
    Save or update a persona card under data/personas/{id}.json.
    Enforces schema compliance, path sanitization, and returns the saved persona dictionary.
    """
    personas_dir = get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    data = dict(persona_data)
    if "id" not in data or not data["id"]:
        name_part = str(data.get("name", "custom")).strip().lower()
        name_clean = re.sub(r"[^\w-]", "_", name_part)[:48]
        data["id"] = f"{name_clean}_{uuid.uuid4().hex[:6]}"
    else:
        if not _is_safe_persona_id(str(data["id"])):
            raise ValueError("Invalid persona_id: path traversal detected")

    validated = PersonaSchema(**data)
    dumped = _model_dump(validated)
    target_path = (personas_dir / f"{dumped['id']}.json").resolve()

    # Containment check
    try:
        target_path.relative_to(personas_dir.resolve())
    except ValueError:
        raise ValueError("Invalid persona_id: path traversal detected")

    await safe_write_json(target_path, dumped)
    return dumped


def get_persona_sync(persona_id: str) -> dict[str, Any] | None:
    """Retrieve a specific persona by ID synchronously from data/personas/{persona_id}.json."""
    if not _is_safe_persona_id(persona_id):
        return None

    personas_dir = get_data_dir() / "personas"
    card_path = (personas_dir / f"{persona_id}.json").resolve()

    try:
        card_path.relative_to(personas_dir.resolve())
    except ValueError:
        return None

    if card_path.exists():
        try:
            data = _sync_read(card_path)
            if isinstance(data, dict):
                return _model_dump(PersonaSchema(**data))
        except Exception as e:
            logger.warning("Failed to validate persona card %s: %s", card_path, e)
            return None

    # Fallback scan of all JSON files in personas_dir
    if personas_dir.exists():
        for jf in personas_dir.glob("*.json"):
            try:
                data = _sync_read(jf)
                if isinstance(data, dict) and data.get("id") == persona_id:
                    return _model_dump(PersonaSchema(**data))
            except Exception:
                continue

    if persona_id == DEFAULT_JANUS.get("id"):
        return _model_dump(PersonaSchema(**DEFAULT_JANUS))

    return None


def save_persona_sync(persona_data: dict[str, Any]) -> dict[str, Any]:
    """
    Save or update a persona card synchronously under data/personas/{id}.json.
    Enforces schema compliance, path sanitization, and returns the saved persona dictionary.
    """
    personas_dir = get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    data = dict(persona_data)
    if "id" not in data or not data["id"]:
        name_part = str(data.get("name", "custom")).strip().lower()
        name_clean = re.sub(r"[^\w-]", "_", name_part)[:48]
        data["id"] = f"{name_clean}_{uuid.uuid4().hex[:6]}"
    else:
        if not _is_safe_persona_id(str(data["id"])):
            raise ValueError("Invalid persona_id: path traversal detected")

    validated = PersonaSchema(**data)
    dumped = _model_dump(validated)
    target_path = (personas_dir / f"{dumped['id']}.json").resolve()

    try:
        target_path.relative_to(personas_dir.resolve())
    except ValueError:
        raise ValueError("Invalid persona_id: path traversal detected")

    _sync_atomic_write(target_path, dumped)
    return dumped


def load_personas_sync() -> list[dict[str, Any]]:
    """Synchronously load all persona cards from data/personas/*.json."""
    personas_dir = get_data_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    json_files = list(personas_dir.glob("*.json"))
    if not json_files:
        default_path = personas_dir / "janus.json"
        _sync_atomic_write(default_path, DEFAULT_JANUS)
        json_files = [default_path]

    personas: list[dict[str, Any]] = []
    for jf in json_files:
        try:
            data = _sync_read(jf)
            if isinstance(data, dict):
                personas.append(_model_dump(PersonaSchema(**data)))
        except Exception as e:
            logger.error("Failed to load persona file %s: %s", jf, e)

    personas.sort(key=lambda p: p.get("id", ""))
    return personas

