import asyncio
import json
import logging
import time
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from pathlib import Path
import httpx
from backend import storage

logger = logging.getLogger("janus.adventure")

CPU_ENGINE_URL = os.environ.get("JANUS_CPU_URL", "http://127.0.0.1:11435")
CHAT_MODEL = os.environ.get("JANUS_CHAT_MODEL", "janus-chat")

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
    inventory: List[str] = ["Starting clothes"]
    active_quests: List[str] = ["Survive the night"]

class AdventureState(BaseModel):
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

# In-memory incognito state
_INCOGNITO_STATE = AdventureState()

def get_state_path() -> Path:
    return storage.get_data_dir() / "adventure_state.json"

async def get_state(incognito: bool) -> AdventureState:
    if incognito:
        return _INCOGNITO_STATE
    
    state_path = get_state_path()
    try:
        data = await storage.safe_read_json(state_path)
        return AdventureState(**data)
    except FileNotFoundError:
        return AdventureState()
    except Exception as e:
        logger.error(f"Error reading adventure state: {e}")
        return AdventureState()

async def save_state(state: AdventureState, incognito: bool):
    if incognito:
        global _INCOGNITO_STATE
        _INCOGNITO_STATE = state
        return
        
    state_path = get_state_path()
    await storage.safe_write_json(state_path, state.dict())

def generate_dm_prompt(state: AdventureState, current_roll: int = None) -> str:
    ctx = state.world_context
    char = state.character_state
    
    prompt = f"""You are the Dungeon Master for a text-based RPG. 
World Premise: {ctx.prompt}
World Tags: {", ".join(ctx.genres)}, Tone: {ctx.tone}, Mature Content: {ctx.nsfw_enabled}.
Starting Equipment: {ctx.starting_equipment}
Forbidden Magic/Tech: {ctx.forbidden_magic_tech}
Pacing: {ctx.pacing}
User Inventory: {", ".join(char.inventory) if char.inventory else "Empty"}. 
Current Quests: {", ".join(char.active_quests) if char.active_quests else "None"}.
User Health/Status: {char.health}.

Rules:
1. Progress the story based strictly on the user's action. 
2. Be descriptive and immersive. """
    
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

async def extract_mechanics(action: str, response: str, incognito: bool):
    prompt = generate_extractor_prompt(action, response)
    
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(
                f"{CPU_ENGINE_URL.rstrip('/')}/api/generate",
                json={
                    "model": CHAT_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False
                }
            )
            
            if res.status_code == 200:
                data = res.json()
                content = data.get("response", "")
                
                try:
                    update_data = json.loads(content)
                    ext = ExtractedState(**update_data)
                    
                    # Update state
                    state = await get_state(incognito)
                    char = state.character_state
                    
                    if ext.health_update:
                        char.health = ext.health_update
                        
                    for item in ext.inventory_added:
                        if item not in char.inventory:
                            char.inventory.append(item)
                            
                    for item in ext.inventory_removed:
                        if item in char.inventory:
                            char.inventory.remove(item)
                            
                    for q in ext.new_quests:
                        if q not in char.active_quests:
                            char.active_quests.append(q)
                            
                    for q in ext.completed_quests:
                        if q in char.active_quests:
                            char.active_quests.remove(q)
                            
                    await save_state(state, incognito)
                    
                except Exception as e:
                    logger.error(f"Failed to parse or apply extraction: {e}")
                    
    except Exception as e:
        logger.error(f"CPU Extraction failed: {e}")
