import asyncio
import json
import logging
import time
import os
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from pathlib import Path
import httpx
from backend import storage

logger = logging.getLogger("janus.adventure")

CPU_ENGINE_URL = os.environ.get("JANUS_CPU_URL", "http://127.0.0.1:11435")
# Use EXTRACTOR_MODEL (CPU) for mechanics extraction — NOT CHAT_MODEL
EXTRACTOR_MODEL = os.environ.get("JANUS_EXTRACTOR_MODEL", "janus-extractor")


class AdventureContext(BaseModel):
    genres: List[str] = []
    tone: str = ""
    nsfw_enabled: bool = False
    prompt: str = ""
    starting_equipment: str = ""
    forbidden_magic_tech: str = ""
    pacing: str = ""

class CharacterState(BaseModel):
    health: str = "Healthy"
    inventory: List[str] = []
    active_quests: List[str] = []

class AdventureState(BaseModel):
    campaign_id: str = ""
    version: int = 0
    world_context: AdventureContext = AdventureContext()
    character_state: CharacterState = CharacterState()
    history: List[Dict[str, str]] = []

class AdventureAction(BaseModel):
    action: str
    incognito: bool = False
    session_token: Optional[str] = None

class AdventureStart(BaseModel):
    prompt: str = ""
    genres: List[str] = []
    tone: str = ""
    nsfw_enabled: bool = False
    incognito: bool = False
    session_token: Optional[str] = None
    starting_equipment: str = ""
    forbidden_magic_tech: str = ""
    pacing: str = ""

class ExtractedState(BaseModel):
    health_update: Optional[str] = None
    inventory_added: List[str] = []
    inventory_removed: List[str] = []
    new_quests: List[str] = []
    completed_quests: List[str] = []

# Per-session incognito state: maps session_token -> AdventureState
# Bounded in-memory dictionary; entries are isolated per session token.
_INCOGNITO_SESSIONS: Dict[str, AdventureState] = {}
_STATE_LOCKS: Dict[str, asyncio.Lock] = {}

# Guards atomic replacement of the persistent campaign.
# adventure_start acquires this as a writer; append_action_history /
# extract_mechanics acquire it as readers (via asyncio.Lock — single writer
# excludes all concurrent readers and writers alike).
_REPLACEMENT_LOCK: Optional[asyncio.Lock] = None
_REPLACEMENT_LOCK_LOOP: Optional[object] = None


def get_replacement_lock() -> asyncio.Lock:
    global _REPLACEMENT_LOCK, _REPLACEMENT_LOCK_LOOP
    try:
        curr_loop = asyncio.get_running_loop()
    except RuntimeError:
        curr_loop = None

    if _REPLACEMENT_LOCK is not None and curr_loop is not None:
        if _REPLACEMENT_LOCK_LOOP not in (None, curr_loop):
            _REPLACEMENT_LOCK = None

    if _REPLACEMENT_LOCK is None:
        _REPLACEMENT_LOCK = asyncio.Lock()
        _REPLACEMENT_LOCK_LOOP = curr_loop
    return _REPLACEMENT_LOCK


def get_state_lock(key: str) -> asyncio.Lock:
    curr_lock = _STATE_LOCKS.get(key)
    try:
        curr_loop = asyncio.get_running_loop()
    except RuntimeError:
        curr_loop = None

    if curr_lock is not None and curr_loop is not None:
        if getattr(curr_lock, "_loop", None) not in (None, curr_loop):
            curr_lock = None

    if curr_lock is None:
        curr_lock = asyncio.Lock()
        _STATE_LOCKS[key] = curr_lock
    return curr_lock

# Active persistent campaign_id — used to detect stale delayed extractions
_ACTIVE_CAMPAIGN_ID: str = ""

def get_state_path() -> Path:
    return storage.get_data_dir() / "adventure_state.json"

async def get_state(incognito: bool, session_token: Optional[str] = None) -> AdventureState:
    global _ACTIVE_CAMPAIGN_ID
    if incognito:
        clean_token = (session_token or "").strip()
        if not clean_token or clean_token not in _INCOGNITO_SESSIONS:
            return AdventureState()
        return _INCOGNITO_SESSIONS[clean_token]

    state_path = get_state_path()
    try:
        data = await storage.safe_read_json(state_path)
        st = AdventureState(**data)
        if st.campaign_id and not _ACTIVE_CAMPAIGN_ID:
            _ACTIVE_CAMPAIGN_ID = st.campaign_id
        return st
    except FileNotFoundError:
        return AdventureState()
    except Exception as e:
        logger.error(f"Error reading adventure state: {e}")
        return AdventureState()

async def replace_persistent_campaign(state: AdventureState) -> AdventureState:
    """
    Atomically sets the active persistent campaign and writes its initial state.
    Acquires _REPLACEMENT_LOCK so ongoing or delayed action updates/extractions from
    an older campaign cannot interleave or overwrite.
    """
    global _ACTIVE_CAMPAIGN_ID
    async with get_replacement_lock():
        _ACTIVE_CAMPAIGN_ID = state.campaign_id
        state.version = 1
        state_path = get_state_path()
        await storage.safe_write_json(state_path, state.dict())
        return state

async def save_state_guarded(
    state: AdventureState,
    incognito: bool,
    session_token: Optional[str] = None,
    expected_campaign_id: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> bool:
    """
    Guarded persistence write.
    For incognito sessions, updates memory state for the isolated session token.
    For persistent campaigns, atomically checks that the active campaign ID and disk version
    still match expected_campaign_id / expected_version under get_replacement_lock() before writing.
    Returns True if successfully written, False if rejected due to campaign replacement.
    """
    global _ACTIVE_CAMPAIGN_ID
    if incognito:
        clean_token = (session_token or "").strip()
        if not clean_token:
            raise ValueError("session_token is required for incognito adventure persistence")
        state.version += 1
        if len(_INCOGNITO_SESSIONS) > 100:
            oldest_key = next(iter(_INCOGNITO_SESSIONS))
            _INCOGNITO_SESSIONS.pop(oldest_key, None)
        _INCOGNITO_SESSIONS[clean_token] = state
        return True

    async with get_replacement_lock():
        # Restore active campaign identity from persisted disk state if server restarted
        state_path = get_state_path()
        current_data = None
        try:
            current_data = await storage.safe_read_json(state_path)
            current = AdventureState(**current_data)
        except (FileNotFoundError, Exception):
            current = AdventureState()

        if not _ACTIVE_CAMPAIGN_ID and current.campaign_id:
            _ACTIVE_CAMPAIGN_ID = current.campaign_id

        if expected_campaign_id is not None:
            if expected_campaign_id != _ACTIVE_CAMPAIGN_ID:
                logger.warning(
                    "Guarded write rejected: active campaign is %s, expected %s",
                    _ACTIVE_CAMPAIGN_ID, expected_campaign_id
                )
                return False
            if current.campaign_id != expected_campaign_id:
                logger.warning(
                    "Guarded write rejected: disk campaign is %s, expected %s",
                    current.campaign_id, expected_campaign_id
                )
                return False
            if expected_version is not None and current.version != expected_version:
                logger.warning(
                    "Guarded write rejected: disk version is %s, expected %s",
                    current.version, expected_version
                )
                return False

        state.version += 1
        await storage.safe_write_json(state_path, state.dict())
        return True

async def save_state(state: AdventureState, incognito: bool, session_token: Optional[str] = None):
    await save_state_guarded(state, incognito, session_token=session_token)

async def append_action_history(
    action: str,
    reply: str,
    incognito: bool,
    session_token: Optional[str] = None,
    campaign_id: str = ""
) -> Optional[AdventureState]:
    """Atomically append a user-action / assistant-reply turn to the latest campaign state.
    Persistence is guarded atomically in save_state_guarded against concurrent campaign replacement.
    """
    clean_token = (session_token or "").strip()
    lock_key = clean_token if incognito else (campaign_id or "persistent")

    async with get_state_lock(lock_key):
        state = await get_state(incognito, clean_token)
        # Discard if this action belonged to an old campaign that has since been replaced
        if campaign_id and state.campaign_id and campaign_id != state.campaign_id:
            logger.warning("Discarding action history for replaced campaign %s", campaign_id)
            return None
        if not incognito and campaign_id and _ACTIVE_CAMPAIGN_ID and campaign_id != _ACTIVE_CAMPAIGN_ID:
            logger.warning("Discarding action history for inactive campaign %s", campaign_id)
            return None

        expected_version = state.version
        state.history.append({"role": "user", "content": action})
        state.history.append({"role": "assistant", "content": reply})
        if len(state.history) > 10:
            state.history = state.history[-10:]
        saved = await save_state_guarded(
            state,
            incognito,
            clean_token,
            expected_campaign_id=campaign_id if not incognito else None,
            expected_version=expected_version if not incognito else None,
        )
        if not saved:
            return None
        return state

def generate_dm_prompt(state: AdventureState, current_roll: int = None) -> str:
    ctx = state.world_context
    char = state.character_state

    # Use campaign state equipment and quests instead of hardcoded defaults
    inventory_str = ", ".join(char.inventory) if char.inventory else "Nothing"
    quests_str = ", ".join(char.active_quests) if char.active_quests else "None"

    prompt = f"""You are the Dungeon Master for a text-based RPG.
World Premise: {ctx.prompt}
World Tags: {", ".join(ctx.genres)}, Tone: {ctx.tone}, Mature Content: {ctx.nsfw_enabled}.
Starting Equipment: {ctx.starting_equipment}
Forbidden Magic/Tech: {ctx.forbidden_magic_tech}
Pacing: {ctx.pacing}
User Inventory: {inventory_str}.
Current Quests: {quests_str}.
User Health/Status: {char.health}.

Rules:
1. Progress the story based strictly on the user's action.
2. Be descriptive and immersive."""

    
    if current_roll is not None:
        prompt += f"\n3. The user has rolled a D20 for their current action and got a {current_roll}. Factor this into the success or failure of their skill check or action."
        
    prompt += "\nEnd your response by presenting the immediate consequences and implicitly asking what they do next."
    return prompt

def generate_extractor_prompt(action: str, response: str) -> str:
    return f"""Analyze the latest RPG turn. The user took an action, and the DM responded.
Did the user gain/lose items, take damage, or receive a new quest?
Output strictly valid JSON to update the game state. DO NOT output any markdown blocks or explanations, ONLY valid JSON.

SCHEMA:
{{
  "health_update": "String (Current physical state, leave null if unchanged)",
  "inventory_added": ["item1"],
  "inventory_removed": ["item2"],
  "new_quests": ["quest1"],
  "completed_quests": ["quest2"]
}}

User Action: {action}
DM Response: {response}
"""

async def extract_mechanics(action: str, response: str, incognito: bool,
                            session_token: str = "", campaign_id: str = ""):
    """
    Extract game mechanics (health/inventory/quest changes) from a completed turn.
    Uses EXTRACTOR_MODEL on the CPU engine (port 11435).
    campaign_id is compared to _ACTIVE_CAMPAIGN_ID before writing state, preventing
    delayed extractions from an old campaign from overwriting a new campaign's state.
    """
    global _ACTIVE_CAMPAIGN_ID

    if not action or not response:
        return

    prompt = generate_extractor_prompt(action, response)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=5.0)) as client:
            res = await client.post(
                f"{CPU_ENGINE_URL.rstrip('/')}/api/generate",
                json={
                    "model": EXTRACTOR_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                }
            )

            if res.status_code != 200:
                logger.warning("CPU extraction returned HTTP %s", res.status_code)
                return

            data = res.json()
            content = data.get("response", "")
            if not content or not content.strip():
                logger.debug("CPU extraction returned empty response")
                return

            try:
                update_data = json.loads(content)
                ext = ExtractedState(**update_data)
            except (json.JSONDecodeError, Exception) as e:
                logger.error("Failed to parse extraction JSON: %s | raw: %r", e, content[:200])
                return

            clean_token = (session_token or "").strip()
            lock_key = clean_token if incognito else (campaign_id or "persistent")

            async def _do_extract_apply():
                async with get_state_lock(lock_key):
                    # Load current state for atomic read-modify-write
                    state = await get_state(incognito, clean_token)

                    # Campaign identity check: skip if this extraction belongs to an old or replaced campaign
                    if campaign_id and state.campaign_id and campaign_id != state.campaign_id:
                        logger.warning(
                            "Discarding stale extraction for campaign %s (current: %s)",
                            campaign_id, state.campaign_id
                        )
                        return

                    if not incognito and campaign_id and _ACTIVE_CAMPAIGN_ID and campaign_id != _ACTIVE_CAMPAIGN_ID:
                        logger.warning(
                            "Discarding stale extraction for campaign %s (active: %s)",
                            campaign_id, _ACTIVE_CAMPAIGN_ID
                        )
                        return

                    expected_version = state.version
                    char = state.character_state

                    if ext.health_update and ext.health_update.strip():
                        char.health = ext.health_update.strip()

                    for item in ext.inventory_added:
                        if item and item not in char.inventory:
                            char.inventory.append(item)

                    for item in ext.inventory_removed:
                        if item in char.inventory:
                            char.inventory.remove(item)

                    for q in ext.new_quests:
                        if q and q not in char.active_quests:
                            char.active_quests.append(q)

                    for q in ext.completed_quests:
                        if q in char.active_quests:
                            char.active_quests.remove(q)

                    saved = await save_state_guarded(
                        state,
                        incognito,
                        clean_token,
                        expected_campaign_id=campaign_id if not incognito else None,
                        expected_version=expected_version if not incognito else None,
                    )
                    if not saved:
                        logger.warning("Extraction state write rejected due to replaced campaign %s", campaign_id)

            await _do_extract_apply()

    except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
        logger.debug("CPU engine unavailable for extraction: %s", conn_err)
    except Exception as e:
        logger.error("CPU extraction failed: %s", e)

