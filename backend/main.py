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
import uuid

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
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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
    incognito: Optional[bool] = False


# Background task tracking to prevent garbage collection mid-execution
background_tasks: set[asyncio.Task] = set()
_BACKGROUND_TASKS = background_tasks
_EXTRACTION_SEMAPHORE = asyncio.Semaphore(3)


def _on_task_done(t: asyncio.Task) -> None:
    background_tasks.discard(t)
    if not t.cancelled():
        exc = t.exception()
        if exc:
            logger.error("Background task failed: %s", exc)


async def _bounded_triage(user_text: str, accumulated_reply: str, mode: str):
    async with _EXTRACTION_SEMAPHORE:
        try:
            await memory_engine.extract_and_triage(user_text, accumulated_reply, mode=mode)
        except Exception as e:
            logger.error("Background extraction failed: %s", e)



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

    # 2. Ephemeral Persona Lookup & Privacy Binding
    is_incognito = bool(req.incognito)
    if req.persona and isinstance(req.persona, dict):
        if req.persona.get("incognito") or req.persona.get("is_disposable"):
            is_incognito = True

    active_persona = None
    if req.mode == "persona":
        # If incognito/disposable persona is provided, do NOT load disk persona by slug
        if is_incognito and req.persona:
            active_persona = req.persona
        else:
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
        incognito=is_incognito
    )
    if len(system_prompt) > 5000:
        system_prompt = system_prompt[:5000] + "\n\n[Context truncated for token budget]"

    # 3. Context Budget Accounting (system + input + history <= 14,000 chars)
    # Bound user input to 4,000 chars
    bounded_user_text = user_text[:4000] if len(user_text) <= 4000 else user_text[:3960] + " [Input truncated for context limit]"

    # Assemble and budget conversation history in reverse
    history_budget = max(2000, 14000 - len(system_prompt) - len(bounded_user_text))
    raw_history = req.history or []
    if not raw_history and req.messages and len(req.messages) > 1:
        raw_history = req.messages[:-1]

    budgeted_history: list[dict[str, str]] = []
    used_history_chars = 0
    for item in reversed(raw_history):
        if isinstance(item, dict) and "role" in item and "content" in item:
            role = str(item["role"]).lower()
            if role == "system":
                continue
            content = str(item["content"])
            # Bound single oversized history turns to 1500 chars
            if len(content) > 1500:
                content = content[:1460] + " [truncated]"
            if used_history_chars + len(content) > history_budget:
                break
            budgeted_history.append({"role": role, "content": content})
            used_history_chars += len(content)

    formatted_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in reversed(budgeted_history):
        formatted_messages.append(msg)
    formatted_messages.append({"role": "user", "content": bounded_user_text})

    # 4. SSE Stream Generator with Robust Model Error Handling
    async def event_generator():
        accumulated_reply = ""
        gpu_connected = False
        has_error = False
        completed_normally = False

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
                                if "error" in chunk:
                                    has_error = True
                                    logger.error("GPU stream model error: %s", chunk["error"])
                                    yield f"data: {json.dumps({'error': str(chunk['error']), 'interrupted': True, 'done': True})}\n\n"
                                    return

                                token = chunk.get("message", {}).get("content", "")
                                is_done = bool(chunk.get("done", False))

                                if token:
                                    accumulated_reply += token
                                    payload = {"token": token, "done": False}
                                    yield f"data: {json.dumps(payload)}\n\n"

                                if is_done:
                                    completed_normally = True
                                    break
                            except json.JSONDecodeError:
                                continue
                    else:
                        has_error = True
                        logger.error("GPU engine returned non-200 HTTP %s", stream_resp.status_code)
                        yield f"data: {json.dumps({'error': f'GPU engine returned HTTP {stream_resp.status_code}', 'interrupted': True, 'done': True})}\n\n"
                        return
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as conn_err:
            logger.warning("GPU engine at %s unreachable: %s.", GPU_ENGINE_URL, conn_err)
            has_error = True

        if not gpu_connected and not accumulated_reply:
            fallback_text = (
                f"[Degraded: GPU chat engine offline on {GPU_ENGINE_URL}]. "
                f"Ensure Ollama is running and '{CHAT_MODEL}' is loaded."
            )
            yield f"data: {json.dumps({'token': fallback_text, 'error': 'GPU offline', 'degraded': True, 'done': True})}\n\n"
            return

        if not completed_normally or has_error:
            logger.warning("Chat stream terminated prematurely without explicit done completion.")
            yield f"data: {json.dumps({'error': 'Stream terminated prematurely before completion', 'interrupted': True, 'done': True})}\n\n"
            return

        # Final terminal SSE event for successful stream
        final_payload = {"token": "", "done": True}
        yield f"data: {json.dumps(final_payload)}\n\n"

        # 5. Background Cognitive Triage (Suppressed under Incognito mode or on failed/degraded/interrupted turns)
        if not is_incognito and gpu_connected and not has_error and completed_normally and accumulated_reply.strip():
            if len(background_tasks) < 25:
                triage_task = asyncio.create_task(
                    _bounded_triage(bounded_user_text, accumulated_reply, req.mode or "assistant")
                )
                background_tasks.add(triage_task)
                triage_task.add_done_callback(_on_task_done)
            else:
                logger.warning("Background triage dropped due to pending queue capacity limit.")


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
        card = await persona_compiler.compile_wiki_to_card(raw_text, character_name, incognito=bool(req.incognito))
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
    campaign_id = f"cmp_{uuid.uuid4().hex[:8]}"
    state = adventure_engine.AdventureState(campaign_id=campaign_id)
    state.world_context.prompt = req.prompt
    state.world_context.genres = req.genres
    state.world_context.tone = req.tone
    state.world_context.nsfw_enabled = req.nsfw_enabled
    state.world_context.starting_equipment = req.starting_equipment
    state.world_context.forbidden_magic_tech = req.forbidden_magic_tech
    state.world_context.pacing = req.pacing

    session_token = (req.session_token or "").strip()
    if req.incognito and not session_token:
        session_token = f"sess_{uuid.uuid4().hex}"

    # Initialize equipment consistently from campaign starting equipment
    if req.starting_equipment:
        items = [i.strip() for i in req.starting_equipment.split(",") if i.strip()]
        state.character_state.inventory = items if items else [req.starting_equipment.strip()]

    # Generate the opening hook via GPU engine
    system_prompt = adventure_engine.generate_dm_prompt(state)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Begin the campaign. Set the opening scene and ask me what I do."}
    ]

    opening_scene = ""
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
                opening_scene = data.get("message", {}).get("content", "").strip()
    except Exception as e:
        logger.warning(f"Adventure GPU offline during start: {e}")

    # Do not enter successful campaign state when generation fails
    if not opening_scene or opening_scene == "*The campaign begins...*":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Game Master failed to generate an opening scene. Ensure GPU engine is running and model is loaded."
        )

    state.history.append({"role": "user", "content": "Start campaign"})
    state.history.append({"role": "assistant", "content": opening_scene})

    if not req.incognito:
        await adventure_engine.replace_persistent_campaign(state)
    else:
        await adventure_engine.save_state(state, req.incognito, session_token=session_token)

    return {
        "status": "success",
        "campaign_id": campaign_id,
        "opening_scene": opening_scene,
        "session_token": session_token if req.incognito else None
    }


@app.get("/api/adventure/state", status_code=status.HTTP_200_OK)
async def adventure_get_state(incognito: bool = False, session_token: Optional[str] = None):
    clean_token = (session_token or "").strip()
    if incognito and not clean_token:
        return adventure_engine.AdventureState().dict()
    state = await adventure_engine.get_state(incognito, clean_token)
    return state.dict()


@app.post("/api/adventure/action")
async def adventure_action(req: adventure_engine.AdventureAction):
    import random
    clean_token = (req.session_token or "").strip()
    if req.incognito and not clean_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_token is required for incognito adventure action."
        )

    state = await adventure_engine.get_state(req.incognito, clean_token)
    current_cid = state.campaign_id
    if not current_cid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active campaign found to take action in."
        )

    roll = random.randint(1, 20)
    system_prompt = adventure_engine.generate_dm_prompt(state, roll)

    formatted_messages = [{"role": "system", "content": system_prompt}]
    for msg in state.history:
        formatted_messages.append(msg)

    formatted_messages.append({"role": "user", "content": req.action})

    async def event_generator():
        accumulated_reply = ""
        completed_normally = False
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
                                if "error" in chunk:
                                    logger.error("Model stream error: %s", chunk["error"])
                                    yield f"data: {json.dumps({'error': chunk['error'], 'interrupted': True, 'done': True})}\n\n"
                                    return
                                token = chunk.get("message", {}).get("content", "")
                                is_done = bool(chunk.get("done", False))

                                if token:
                                    accumulated_reply += token
                                    payload = {"token": token, "done": False}
                                    yield f"data: {json.dumps(payload)}\n\n"

                                if is_done:
                                    completed_normally = True
                                    break
                            except json.JSONDecodeError:
                                continue
                    else:
                        logger.error("GPU returned HTTP %s", stream_resp.status_code)
                        yield f"data: {json.dumps({'error': f'GPU returned HTTP {stream_resp.status_code}', 'interrupted': True, 'done': True})}\n\n"
                        return
        except Exception as e:
            logger.warning(f"Adventure GPU offline: {e}")
            if not accumulated_reply:
                yield f"data: {json.dumps({'token': '*The Dungeon Master is asleep...*', 'done': True})}\n\n"
            else:
                yield f"data: {json.dumps({'error': f'Adventure stream interrupted: {e}', 'interrupted': True, 'done': True})}\n\n"
            return

        if not completed_normally:
            logger.warning("Adventure stream terminated prematurely before done:true")
            yield f"data: {json.dumps({'error': 'Adventure stream terminated prematurely before completion', 'interrupted': True, 'done': True})}\n\n"
            return

        final_payload = {"token": "", "done": True}
        yield f"data: {json.dumps(final_payload)}\n\n"

        # Do not record empty or interrupted turns
        if accumulated_reply.strip():
            updated_state = await adventure_engine.append_action_history(
                req.action,
                accumulated_reply,
                req.incognito,
                session_token=clean_token,
                campaign_id=current_cid
            )

            # Trigger CPU background task bounded by queue and semaphore
            if updated_state is not None:
                async def _bg_mechanics():
                    async with _EXTRACTION_SEMAPHORE:
                        try:
                            await adventure_engine.extract_mechanics(
                                req.action,
                                accumulated_reply,
                                req.incognito,
                                session_token=clean_token,
                                campaign_id=current_cid
                            )
                        except Exception as exc:
                            logger.error("Mechanics extraction failed: %s", exc)

                if len(background_tasks) < 25:
                    bg_task = asyncio.create_task(_bg_mechanics())
                    background_tasks.add(bg_task)
                    bg_task.add_done_callback(_on_task_done)
                else:
                    logger.warning("Background mechanics extraction dropped due to queue overload.")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@app.put("/api/personas/{name}")
async def update_persona(name: str, request: Request, incognito: Optional[bool] = False):
    data = await request.json()
    data["id"] = name  # Ensure ID matches the route

    # Check for incognito flag in query params or JSON body
    is_incognito = incognito or bool(data.get("incognito", False))

    # Core Janus protection
    if name.lower() == "janus":
        char_name = str(data.get("name") or "").strip().lower()
        if char_name and char_name != "janus":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The core Janus persona cannot be replaced or overwritten by arbitrary personas."
            )

    # Regenerate canonical system_prompt when behavioral/identity fields change
    existing_persona = await storage.get_persona(name)

    char_name = str(data.get("name") or (existing_persona.get("name") if existing_persona else name)).strip()
    char_desc = str(data.get("description") or "").strip()
    raw_traits = data.get("traits") or data.get("personality_traits") or []
    if isinstance(raw_traits, str):
        traits = [t.strip() for t in raw_traits.split(",") if t.strip()]
    elif isinstance(raw_traits, (list, tuple)):
        traits = [str(t).strip() for t in raw_traits if str(t).strip()]
    else:
        traits = []

    roleplay_style = memory_engine._extract_roleplay_style(data)
    forge_schema = data.get("forge_schema") if isinstance(data.get("forge_schema"), dict) else {}
    personality = forge_schema.get("personality") or data.get("personality") or {}
    emotion = forge_schema.get("emotion") or data.get("emotion") or {}
    physicality = forge_schema.get("physicality") or data.get("physicality") or {}
    mature = forge_schema.get("mature_themes") or data.get("mature_themes") or {}

    needs_prompt_regen = False
    if not data.get("system_prompt"):
        needs_prompt_regen = True
    elif existing_persona:
        old_name = str(existing_persona.get("name") or "").strip()
        old_desc = str(existing_persona.get("description") or "").strip()
        old_traits = existing_persona.get("traits") or existing_persona.get("personality_traits") or []
        if isinstance(old_traits, str):
            old_traits = [t.strip() for t in old_traits.split(",") if t.strip()]
        elif isinstance(old_traits, (list, tuple)):
            old_traits = [str(t).strip() for t in old_traits if str(t).strip()]
        else:
            old_traits = []

        old_roleplay = memory_engine._extract_roleplay_style(existing_persona)
        old_forge = existing_persona.get("forge_schema") if isinstance(existing_persona.get("forge_schema"), dict) else {}
        old_personality = old_forge.get("personality") or existing_persona.get("personality") or {}
        old_emotion = old_forge.get("emotion") or existing_persona.get("emotion") or {}
        old_physicality = old_forge.get("physicality") or existing_persona.get("physicality") or {}
        old_mature = old_forge.get("mature_themes") or existing_persona.get("mature_themes") or {}

        if (
            char_name != old_name
            or char_desc != old_desc
            or traits != old_traits
            or roleplay_style != old_roleplay
            or personality != old_personality
            or emotion != old_emotion
            or physicality != old_physicality
            or mature != old_mature
            or forge_schema != old_forge
            or data.get("_slider_values") != existing_persona.get("_slider_values")
        ):
            needs_prompt_regen = True
    else:
        needs_prompt_regen = True

    if needs_prompt_regen:
        raw_arch = personality.get("archetype") if isinstance(personality, dict) else None
        archetype = str(raw_arch or char_desc or "Character").strip() or "Character"
        flaws = personality.get("flaws") if isinstance(personality, dict) else []
        flaws_str = f" Character flaws: {', '.join(str(f) for f in flaws)}." if isinstance(flaws, list) and flaws else ""

        speech = emotion.get("speech_style", "") if isinstance(emotion, dict) else ""
        mood = emotion.get("default_mood", "") if isinstance(emotion, dict) else ""
        stress = emotion.get("reaction_to_stress", "") if isinstance(emotion, dict) else ""
        appearance = physicality.get("appearance", "") if isinstance(physicality, dict) else ""
        body_lang = physicality.get("body_language", "") if isinstance(physicality, dict) else ""
        boundaries = mature.get("boundaries", "") if isinstance(mature, dict) else ""
        mature_dyn = mature.get("mature_dynamics", "") if isinstance(mature, dict) else ""

        prompt_parts = [
            f"You are {char_name}.",
            f"Your archetype is {archetype}.",
        ]
        if char_desc and char_desc.lower() != archetype.lower():
            prompt_parts.append(f"About you: {char_desc}.")
        if traits:
            prompt_parts.append(f"Core traits: {', '.join(traits)}.")
        if flaws_str:
            prompt_parts.append(flaws_str.strip())
        if speech:
            prompt_parts.append(f"Your speech style: {speech}.")
        if mood:
            prompt_parts.append(f"Your default mood: {mood}.")
        if stress:
            prompt_parts.append(f"Under stress you: {stress}.")
        if appearance:
            prompt_parts.append(f"Physically: {appearance}.")
        if body_lang:
            prompt_parts.append(f"Habitual body language: {body_lang}.")
        if boundaries:
            prompt_parts.append(f"Boundaries: {boundaries}.")
        if mature_dyn:
            prompt_parts.append(f"Mature dynamics: {mature_dyn}.")
        if roleplay_style:
            prompt_parts.append(f"Roleplay Style & Directives: {roleplay_style}")

        prompt_parts.append(
            "Strong Roleplay Enforcement: Fully embody this character and roleplay style in every response. "
            "Actively engage in conversation, drive the interaction forward, and ask questions or take initiative without avoiding interaction. "
            "Never give generic, dismissive, or avoidant AI responses. "
            "Never break character or refer to yourself as an AI or assistant."
        )
        data["system_prompt"] = " ".join(prompt_parts)

    if "_slider_values" in data:
        if "forge_schema" in data and isinstance(data["forge_schema"], dict):
            data["forge_schema"]["_slider_values"] = data["_slider_values"]

    if is_incognito:
        data["saved"] = False
        data["incognito"] = True
        return {"status": "success", "persona": data, "saved": False, "incognito": True}

    try:
        updated = await storage.save_persona(data, allow_overwrite=True)
        return {"status": "success", "persona": updated, "saved": True}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/personas/{name}")
async def delete_persona(name: str):
    try:
        deleted = await storage.delete_persona(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Persona not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TagSuggestRequest(BaseModel):
    description: str


@app.post("/api/personas/suggest-tags")
async def suggest_tags(req: TagSuggestRequest):
    """Suggest 5 tags using EXTRACTOR_MODEL on CPU engine, validating output shape."""
    description = (req.description or "").strip()
    if not description:
        return ["Mysterious", "Adaptive", "Principled", "Strategic", "Enigmatic"]

    extractor_model = os.environ.get("JANUS_EXTRACTOR_MODEL", "janus-extractor")
    prompt = (
        f"Given this brief character description, generate exactly 5 relevant RPG or archetype tags "
        f"(e.g., Cyberpunk, Tsundere, Tactician). Return ONLY a JSON list of strings.\n\nDescription: {description}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"{CPU_ENGINE_URL.rstrip('/')}/api/generate",
                json={
                    "model": extractor_model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False
                }
            )
            if res.status_code == 200:
                data = res.json()
                content = data.get("response", "[]")
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        clean = [str(t).strip() for t in parsed if t and str(t).strip()]
                        if clean:
                            return clean[:5]
                    elif isinstance(parsed, dict):
                        for v in parsed.values():
                            if isinstance(v, list):
                                clean = [str(t).strip() for t in v if t and str(t).strip()]
                                if clean:
                                    return clean[:5]
                except Exception:
                    pass
    except Exception as e:
        logger.debug("Suggest tags failed: %s", e)

    return ["Mysterious", "Adaptive", "Principled", "Strategic", "Enigmatic"]


@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_not_found_catch_all(full_path: str):
    """Catch-all for undefined /api/* routes to prevent static files mount from returning 405."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"API endpoint '/api/{full_path}' not found."
    )


# ----------------------------------------------------------------------
# Static Frontend Assets Mounting (Serve compiled Vite build if present)
# ----------------------------------------------------------------------

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
dist_dir = frontend_dir / "dist"

if dist_dir.exists() and (dist_dir / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(dist_dir / "assets")), name="static_assets")
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static_root")
else:
    logger.info("Compiled frontend dist/ not found. Frontend served via Vite dev server.")

