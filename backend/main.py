"""
Project Janus - FastAPI Central Application Server
Connects interactive streaming UI to GPU chat engine (Port 11434) and
background cognitive triage / persona compilation to CPU engine (Port 11435).
Provides persistent local state, atomic file updates, and static frontend serving.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import memory_engine, persona_compiler, storage

logger = logging.getLogger("janus.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Port & engine configuration
GPU_ENGINE_URL = os.environ.get("JANUS_GPU_URL", "http://127.0.0.1:11434")
CPU_ENGINE_URL = os.environ.get("JANUS_CPU_URL", "http://127.0.0.1:11435")
CHAT_MODEL = os.environ.get("JANUS_CHAT_MODEL", "janus-chat")

# ----------------------------------------------------------------------
# Application Initialization
# ----------------------------------------------------------------------

app = FastAPI(
    title="Project Janus",
    description="Offline, Privacy-First Local AI Assistant & Persona Engine",
    version="1.0.0"
)

# CORS middleware for local loopback clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Request & Response Schemas
# ----------------------------------------------------------------------

class ChatStreamRequest(BaseModel):
    message: Optional[str] = None
    messages: Optional[list[dict[str, Any]]] = None
    mode: Optional[str] = "assistant"
    persona_id: Optional[str] = None
    persona: Optional[dict[str, Any]] = None
    incognito: Optional[bool] = False
    history: Optional[list[dict[str, Any]]] = None


class PersonaCompileRequest(BaseModel):
    name: Optional[str] = None
    character_name: Optional[str] = None
    wiki_text: Optional[str] = None
    raw_text: Optional[str] = None


# Background task tracking to prevent garbage collection mid-execution
background_tasks: set[asyncio.Task] = set()
_BACKGROUND_TASKS = background_tasks


class ReminderPatchRequest(BaseModel):
    completed: bool


class ReminderCreateRequest(BaseModel):
    id: Optional[str] = None
    text: str
    due_date: Optional[str] = None
    priority: Optional[str] = "medium"


# ----------------------------------------------------------------------
# Engine Health Helpers
# ----------------------------------------------------------------------

async def ping_engine(url: str, timeout_sec: float = 0.4) -> tuple[bool, int]:
    """Check connectivity to an Ollama engine port and measure latency in ms."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec)) as client:
            resp = await client.get(f"{url.rstrip('/')}/api/version")
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return resp.status_code == 200, elapsed_ms
    except Exception:
        return False, 0


# ----------------------------------------------------------------------
# API Endpoints
# ----------------------------------------------------------------------

@app.get("/api/health")
@app.get("/api/health/engines")
async def health_check():
    """Health check endpoint reporting dual engine loopback connectivity and latency."""
    gpu_ok, gpu_lat = await ping_engine(GPU_ENGINE_URL)
    cpu_ok, cpu_lat = await ping_engine(CPU_ENGINE_URL)

    return {
        "status": "online" if (gpu_ok or cpu_ok) else "degraded",
        "engines": {
            "gpu": gpu_ok,
            "cpu": cpu_ok
        },
        "engine_health": {
            "gpu": gpu_ok,
            "cpu": cpu_ok
        },
        "gpu_engine": {
            "status": "online" if gpu_ok else "offline",
            "port": 11434,
            "latency_ms": gpu_lat
        },
        "cpu_engine": {
            "status": "online" if cpu_ok else "offline",
            "port": 11435,
            "latency_ms": cpu_lat
        }
    }


@app.get("/api/state")
async def get_state():
    """
    Returns the complete system cognitive state:
    Active reminders, rolling work notes, persistent user facts, personas, and engine health.
    """
    try:
        reminders = await storage.load_reminders()
    except Exception as e:
        logger.error("Error loading reminders: %s", e)
        reminders = []

    try:
        work_notes = await storage.load_work_context()
    except Exception as e:
        logger.error("Error loading work context: %s", e)
        work_notes = []

    try:
        user_profile = await storage.load_user_profile()
    except Exception as e:
        logger.error("Error loading user profile: %s", e)
        user_profile = {"name": "User", "role": "Lead Architect", "facts": [], "preferences": []}

    try:
        personas = await storage.load_personas()
    except Exception as e:
        logger.error("Error loading personas: %s", e)
        personas = []

    facts = user_profile.get("facts", [])

    gpu_ok, gpu_lat = await ping_engine(GPU_ENGINE_URL)
    cpu_ok, cpu_lat = await ping_engine(CPU_ENGINE_URL)

    return {
        "reminders": reminders,
        "work_notes": work_notes,
        "facts": facts,
        "personas": personas,
        "user_profile": user_profile,
        "engines": {
            "gpu": gpu_ok,
            "cpu": cpu_ok
        },
        "engine_health": {
            "gpu": gpu_ok,
            "cpu": cpu_ok
        },
        "gpu_engine": {
            "status": "online" if gpu_ok else "offline",
            "port": 11434,
            "latency_ms": gpu_lat
        },
        "cpu_engine": {
            "status": "online" if cpu_ok else "offline",
            "port": 11435,
            "latency_ms": cpu_lat
        }
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatStreamRequest):
    """
    Real-time chat streaming endpoint via Server-Sent Events (SSE).
    Streams tokens from GPU Ollama engine (Port 11434).
    If incognito is False, automatically dispatches background memory extraction upon completion.
    If incognito is True, personal memory injection and triage persistence are strictly suppressed.
    """
    # 1. Extract active user prompt text
    user_text = ""
    if req.message and req.message.strip():
        user_text = req.message.strip()
    elif req.messages and isinstance(req.messages, list) and len(req.messages) > 0:
        for msg in reversed(req.messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = str(msg.get("content", "")).strip()
                if content:
                    user_text = content
                    break

    if not user_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A non-empty user message string or messages array containing a user message is required."
        )

    # 2. Prepare System Prompt Context
    active_persona = None
    if req.mode == "persona":
        if req.persona_id:
            active_persona = await storage.get_persona(req.persona_id)
        if active_persona and req.persona and isinstance(req.persona, dict):
            client_style = memory_engine._extract_roleplay_style(req.persona)
            disk_style = memory_engine._extract_roleplay_style(active_persona)
            if client_style:
                active_persona["roleplay_style"] = client_style
                if disk_style and disk_style in str(active_persona.get("system_prompt", "")):
                    active_persona["system_prompt"] = active_persona["system_prompt"].replace(
                        f"Roleplay Style & Directives: {disk_style}",
                        f"Roleplay Style & Directives: {client_style}"
                    )
            elif disk_style:
                active_persona["roleplay_style"] = disk_style
        elif not active_persona and req.persona:
            active_persona = req.persona

    system_prompt = await memory_engine.inject_context(
        base_system_prompt="",
        mode=req.mode or "assistant",
        persona=active_persona,
        incognito=bool(req.incognito)
    )

    # 3. Assemble Conversation Turn History for GPU Engine
    formatted_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    history = req.history or []
    if not history and req.messages and len(req.messages) > 1:
        history = req.messages[:-1]

    for item in history:
        if isinstance(item, dict) and "role" in item and "content" in item:
            role = str(item["role"]).lower()
            if role == "system":
                continue
            formatted_messages.append({
                "role": role,
                "content": str(item["content"])
            })

    formatted_messages.append({"role": "user", "content": user_text})

    # 4. SSE Stream Generator
    async def event_generator():
        accumulated_reply = ""
        gpu_connected = False

        client_timeout = httpx.Timeout(connect=2.0, read=90.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{GPU_ENGINE_URL.rstrip('/')}/api/chat",
                    json={
                        "model": CHAT_MODEL,
                        "messages": formatted_messages,
                        "stream": True
                    }
                ) as stream_resp:
                    if stream_resp.status_code == 200:
                        gpu_connected = True
                        async for line in stream_resp.aiter_lines():
                            if not line or not line.strip():
                                continue
                            try:
                                chunk = json.loads(line)
                                token = chunk.get("message", {}).get("content", "")
                                is_done = bool(chunk.get("done", False))

                                if token:
                                    accumulated_reply += token
                                    payload = {"token": token, "done": False}
                                    yield f"data: {json.dumps(payload)}\n\n"

                                if is_done:
                                    break
                            except json.JSONDecodeError:
                                continue
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as conn_err:
            logger.warning("GPU engine at %s unreachable: %s. Using graceful stream fallback.", GPU_ENGINE_URL, conn_err)

        # Fallback simulation if GPU engine was offline
        if not gpu_connected:
            fallback_text = (
                f"Janus: Acknowledged. Operational directives noted for '{user_text[:40]}'. "
                f"Dual engine telemetry indicates GPU chat engine is offline."
            )
            accumulated_reply = fallback_text
            # Yield in chunks
            tokens = fallback_text.split(" ")
            for i, tok in enumerate(tokens):
                space = " " if i < len(tokens) - 1 else ""
                payload = {"token": tok + space, "done": False}
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(0.01)

        # Final terminal SSE event
        final_payload = {"token": "", "done": True}
        yield f"data: {json.dumps(final_payload)}\n\n"

        # 5. Background Cognitive Triage (Suppressed under Incognito mode)
        if not req.incognito:
            triage_task = asyncio.create_task(
                memory_engine.extract_and_triage(user_text, accumulated_reply)
            )
            background_tasks.add(triage_task)
            triage_task.add_done_callback(background_tasks.discard)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/personas/compile", status_code=status.HTTP_200_OK)
async def compile_persona(req: PersonaCompileRequest):
    """
    Ingests raw biographical or character documentation and compiles it
    into a structured persona card via the CPU engine on Port 11435.
    """
    character_name = (req.character_name or req.name or "").strip()
    raw_text = (req.raw_text or req.wiki_text or "").strip()

    if not character_name or not raw_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both character_name (or name) and raw_text (or wiki_text) are required."
        )

    try:
        card = await persona_compiler.compile_wiki_to_card(raw_text, character_name)
        return {
            "status": "success",
            "persona": card
        }
    except Exception as e:
        logger.error("Persona compilation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Persona compilation failed: {str(e)}"
        )


@app.patch("/api/reminders/{reminder_id}")
async def patch_reminder(reminder_id: str, req: ReminderPatchRequest):
    """Update or toggle reminder completion status."""
    updated = await storage.update_reminder(reminder_id, {"completed": req.completed})
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reminder with ID '{reminder_id}' not found."
        )

    return {
        "status": "updated",
        "reminder": updated
    }


@app.post("/api/reminders/{reminder_id}/toggle")
async def toggle_reminder_fallback(reminder_id: str, req: ReminderPatchRequest):
    """Fallback route for clients toggling reminders via POST."""
    return await patch_reminder(reminder_id, req)


@app.post("/api/reminders", status_code=status.HTTP_200_OK)
async def create_reminder(req: ReminderCreateRequest):
    """Manually add a reminder item to persistent storage."""
    text_clean = req.text.strip()
    if not text_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reminder text cannot be empty."
        )

    reminder_payload: dict[str, Any] = {
        "text": text_clean,
        "due_date": req.due_date,
        "priority": req.priority or "medium"
    }
    if req.id:
        reminder_payload["id"] = req.id

    new_item = await storage.add_reminder(
        reminder_data=reminder_payload,
        due_date=req.due_date,
        priority=req.priority or "medium"
    )
    return new_item


@app.get("/api/personas")
async def list_personas():
    """Retrieve all compiled persona cards."""
    return await storage.load_personas()


@app.get("/api/reminders")
async def list_reminders():
    """Retrieve all reminders."""
    return await storage.load_reminders()


@app.post("/api/personas/enhance", status_code=status.HTTP_200_OK)
async def enhance_persona(req: persona_compiler.EnhanceRequest):
    """
    AI-assisted character expansion endpoint.
    Sends the base_prompt to the CPU model (Port 11435) which returns a fully
    populated CharacterForgeSchema JSON object. Falls back to a deterministic
    scaffold if the CPU engine is offline.
    """
    base_prompt = (req.base_prompt or "").strip()
    if not base_prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_prompt cannot be empty."
        )

    try:
        character = await persona_compiler.enhance_character_prompt(
            base_prompt=base_prompt,
            allow_nsfw=bool(req.allow_nsfw)
        )
        return {"status": "success", "character": character}
    except Exception as e:
        logger.error("Character enhancement failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Character enhancement failed: {str(e)}"
        )


@app.post("/api/personas/forge", status_code=status.HTTP_200_OK)
async def forge_persona(req: persona_compiler.ForgeRequest):
    """
    Character Forge persistence endpoint.
    - incognito=True  -> returns the validated character JSON directly; NO file is written to disk.
    - incognito=False -> validates the schema, saves to data/personas/{name}.json,
                        and returns the persisted card.
    """
    character_data = req.character_data
    if isinstance(character_data, persona_compiler.CharacterForgeSchema):
        if hasattr(character_data, "model_dump"):
            character_data = character_data.model_dump()
        else:
            character_data = character_data.dict()

    character_name = str(character_data.get("name") or "").strip() if isinstance(character_data, dict) else ""
    if not character_data or not character_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="character_data must include a non-empty 'name' field."
        )

    try:
        result = await persona_compiler.forge_character(
            character_data=character_data,
            incognito=bool(req.incognito)
        )
        return result
    except Exception as e:
        logger.error("Character forge failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Character forge failed: {str(e)}"
        )


from backend import adventure_engine

@app.post("/api/adventure/start", status_code=status.HTTP_200_OK)
async def adventure_start(req: adventure_engine.AdventureStart):
    state = adventure_engine.AdventureState()
    state.world_context.prompt = req.prompt
    state.world_context.genres = req.genres
    state.world_context.tone = req.tone
    state.world_context.nsfw_enabled = req.nsfw_enabled
    state.world_context.starting_equipment = req.starting_equipment
    state.world_context.forbidden_magic_tech = req.forbidden_magic_tech
    state.world_context.pacing = req.pacing
    
    # Generate the opening hook
    system_prompt = adventure_engine.generate_dm_prompt(state)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Begin the campaign. Set the opening scene and ask me what I do."}
    ]
    
    opening_scene = "*The campaign begins...*"
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(
                f"{GPU_ENGINE_URL.rstrip('/')}/api/chat",
                json={
                    "model": CHAT_MODEL,
                    "messages": messages,
                    "stream": False
                }
            )
            if res.status_code == 200:
                data = res.json()
                opening_scene = data.get("message", {}).get("content", opening_scene)
    except Exception as e:
        logger.warning(f"Adventure GPU offline during start: {e}")
        
    state.history.append({"role": "user", "content": "Start campaign"})
    state.history.append({"role": "assistant", "content": opening_scene})
    
    await adventure_engine.save_state(state, req.incognito)
    return {"status": "success", "opening_scene": opening_scene}

@app.get("/api/adventure/state", status_code=status.HTTP_200_OK)
async def adventure_get_state(incognito: bool = False):
    state = await adventure_engine.get_state(incognito)
    return state.dict()

@app.post("/api/adventure/action")
async def adventure_action(req: adventure_engine.AdventureAction):
    import random
    state = await adventure_engine.get_state(req.incognito)
    roll = random.randint(1, 20)
    system_prompt = adventure_engine.generate_dm_prompt(state, roll)
    
    formatted_messages = [{"role": "system", "content": system_prompt}]
    for msg in state.history:
        formatted_messages.append(msg)
        
    formatted_messages.append({"role": "user", "content": req.action})
    
    async def event_generator():
        accumulated_reply = ""
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream(
                    "POST",
                    f"{GPU_ENGINE_URL.rstrip('/')}/api/chat",
                    json={
                        "model": CHAT_MODEL,
                        "messages": formatted_messages,
                        "stream": True
                    }
                ) as stream_resp:
                    if stream_resp.status_code == 200:
                        async for line in stream_resp.aiter_lines():
                            if not line or not line.strip():
                                continue
                            try:
                                chunk = json.loads(line)
                                token = chunk.get("message", {}).get("content", "")
                                is_done = bool(chunk.get("done", False))

                                if token:
                                    accumulated_reply += token
                                    payload = {"token": token, "done": False}
                                    yield f"data: {json.dumps(payload)}\n\n"

                                if is_done:
                                    break
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.warning(f"Adventure GPU offline: {e}")
            yield f"data: {json.dumps({'token': '*The Dungeon Master is asleep...*', 'done': True})}\n\n"
            return
            
        final_payload = {"token": "", "done": True}
        yield f"data: {json.dumps(final_payload)}\n\n"
        
        # Save history
        state.history.append({"role": "user", "content": req.action})
        state.history.append({"role": "assistant", "content": accumulated_reply})
        if len(state.history) > 10:
            state.history = state.history[-10:]
        await adventure_engine.save_state(state, req.incognito)
        
        # Trigger CPU background task
        bg_task = asyncio.create_task(
            adventure_engine.extract_mechanics(req.action, accumulated_reply, req.incognito)
        )
        background_tasks.add(bg_task)
        bg_task.add_done_callback(background_tasks.discard)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

@app.put("/api/personas/{name}")
async def update_persona(name: str, request: Request):
    data = await request.json()
    data["id"] = name # Ensure ID matches the route
    
    # Save using storage
    try:
        updated = await storage.save_persona(data)
        return {"status": "success", "persona": updated}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/personas/{name}")
async def delete_persona(name: str):
    if name.lower() == "janus":
        raise HTTPException(status_code=400, detail="Cannot delete core Janus persona.")
    
    personas_dir = storage.get_data_dir() / "personas"
    target_path = personas_dir / f"{name}.json"
    
    if target_path.exists():
        target_path.unlink()
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="Persona not found")

class TagSuggestRequest(BaseModel):
    description: str

@app.post("/api/personas/suggest-tags")
async def suggest_tags(req: TagSuggestRequest):
    prompt = f"Given this brief character description, generate exactly 5 relevant RPG/Archetype tags (e.g., Cyberpunk, Tsundere, Tactician). Return ONLY a JSON list of strings.\n\nDescription: {req.description}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
                content = data.get("response", "[]")
                return json.loads(content)
            else:
                return ["Mysterious", "Unknown", "Enigma", "Secret", "Hidden"]
    except Exception as e:
        logger.error(f"Suggest tags failed: {e}")
        return ["Hero", "Villain", "Neutral", "Mage", "Warrior"]

@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_not_found_catch_all(full_path: str):
    """Catch-all for undefined /api/* routes to prevent static files mount from returning 405."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"API endpoint '/api/{full_path}' not found."
    )


# ----------------------------------------------------------------------
# Static Frontend Assets Mounting
# ----------------------------------------------------------------------

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
frontend_dir.mkdir(parents=True, exist_ok=True)

# Mount /static for assets if requested explicitly
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static_assets")

# Mount root to serve index.html SPA
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static_root")
