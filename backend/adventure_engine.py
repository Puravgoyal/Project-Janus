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
    world_context: AdventureContext = AdventureContext()
    character_state: CharacterState = CharacterState()
    history: List[Dict[str, str]] = []

class AdventureAction(BaseModel):
    action: str
    incognito: bool = False

class AdventureStart(BaseModel):
    prompt: str = ""
    genres: List[str] = []
    tone: str = ""
    nsfw_enabled: bool = False
    incognito: bool = False
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
# Using a simple dict; entries are created on start and last for the process lifetime.
# NOT a global singleton — each new incognito start creates a fresh entry.
_INCOGNITO_SESSIONS: Dict[str, AdventureState] = {}

# Active persistent campaign_id — used to detect stale delayed extractions
_ACTIVE_CAMPAIGN_ID: str = ""

def get_state_path() -> Path:
    return storage.get_data_dir() / "adventure_state.json"

async def get_state(incognito: bool, session_token: str = "") -> AdventureState:
    if incognito:
        return _INCOGNITO_SESSIONS.get(session_token, AdventureState())

    state_path = get_state_path()
    try:
        data = await storage.safe_read_json(state_path)
        return AdventureState(**data)
    except FileNotFoundError:
        return AdventureState()
    except Exception as e:
        logger.error(f"Error reading adventure state: {e}")
        return AdventureState()

async def save_state(state: AdventureState, incognito: bool, session_token: str = ""):
    if incognito:
        _INCOGNITO_SESSIONS[session_token] = state
        return

    state_path = get_state_path()
    await storage.safe_write_json(state_path, state.dict())

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

            # Campaign version check: skip if this extraction belongs to an old campaign
            if not incognito:
                if campaign_id and _ACTIVE_CAMPAIGN_ID and campaign_id != _ACTIVE_CAMPAIGN_ID:
                    logger.warning(
                        "Discarding stale extraction for campaign %s (active: %s)",
                        campaign_id, _ACTIVE_CAMPAIGN_ID
                    )
                    return

            # Load current state for atomic read-modify-write
            state = await get_state(incognito, session_token)

            # Second campaign check after load (race condition guard)
            if not incognito and campaign_id and state.campaign_id and campaign_id != state.campaign_id:
                logger.warning("Stale extraction discarded after state reload")
                return

            char = state.character_state

            if ext.health_update and ext.health_update.strip():
                char.health = ext.health_update

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

            await save_state(state, incognito, session_token)

    except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
        logger.debug("CPU engine unavailable for extraction: %s", conn_err)
    except Exception as e:
        logger.error("CPU extraction failed: %s", e)

