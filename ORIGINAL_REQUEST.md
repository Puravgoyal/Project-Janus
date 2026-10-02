# Original User Request

## 2026-09-13T00:34:03Z

Build **Project Janus**, a 100% offline, privacy-first local AI assistant platform for Windows 11.
It operates as a dual-engine hybrid system: an Interactive Executive Assistant with persistent memory, reminders, and work-context logging, and a Multi-Persona Character Chat with on-the-fly persona compilation from raw text/wiki dumps — all streamed in real-time over SSE via a modern dark-mode SPA.

Working directory: `D:\hahaha\Project Janus`
Integrity mode: development (write original logic; established libraries such as FastAPI, httpx, marked.js are allowed — no copy-pasting of OSS project internals)

---

## Hardware & Runtime Context

- **GPU Engine** (`127.0.0.1:11434`): RTX 4050 Laptop GPU (6 GB VRAM), `OLLAMA_FLASH_ATTENTION=1`, ctx 4096 — interactive streaming chat.
- **CPU Engine** (`127.0.0.1:11435`): Core i7 8 P-Cores, DDR5, `CUDA_VISIBLE_DEVICES=""`, ctx 8192 — silent background extraction & triage.
- **Model**: `artifish/llama3.2-uncensored:3b` (fallback `llama3.2:3b`).
- **Network**: Strict loopback (`127.0.0.1`). Zero cloud telemetry.

---

## Requirements

### R1. Dual Ollama Engine Configuration & Launcher
Create `Modelfile.gpu` (GPU chat: num_gpu 999, num_ctx 4096, temp 0.72, Flash Attention) and `Modelfile.cpu` (CPU extractor: num_gpu 0, num_thread 8, num_ctx 8192, temp 0.05, JSON-only system prompt). Provide a PowerShell launcher (`start_engines.ps1`) that spawns both Ollama processes on their respective ports with correct environment variables, compiles both models via `ollama create`, and performs health checks before exit.

### R2. Python FastAPI Backend (Full Logic — No Placeholders)
Implement three modules:
- **`memory_engine.py`**: `async extract_and_triage(user_message, assistant_reply)` — calls Port 11435 with `"format":"json"`, appends new reminders to `reminders.json`, maintains a rolling 15-item buffer in `work_context.json`, and updates `user_profile.json`. Also implements a context injection function that reads all JSON files and formats them into a system-prompt string.
- **`persona_compiler.py`**: `async compile_wiki_to_card(raw_text, character_name)` — queries Port 11435 to condense raw text into a structured JSON persona card saved under `data/personas/`.
- **`main.py`**: `POST /api/chat/stream` (SSE, GPU engine, optional background triage), `GET /api/state` (full JSON state), `POST /api/personas/compile` (wiki ingest), static file mounting.

### R3. Modern Dark-Mode Single-Page Frontend (Vanilla JS + Tailwind CDN)
Build `index.html`, `app.js`, `styles.css` with:
- Background `#0c0d10`, panels `#14171f`, emerald accents.
- Header: mode toggle (Assistant vs Persona), incognito toggle (locks memory, changes UI tint), GPU/CPU ping indicators.
- Sidebar: pending reminders with checkboxes (Assistant mode) OR persona card grid + "Ingest Wiki/Text" button (Persona mode) + Memory Inspector accordion (raw JSON facts).
- Chat window: Markdown rendering, SSE token streaming, auto-scroll, quick-action chips.

### R4. Local JSON Data Layer
Initialize scaffold files: `user_profile.json`, `reminders.json`, `work_context.json`, and a default `personas/executive_assistant.json` persona card.

### R5. Automated Test Harness
- `tests/test_engines.py`: Assert HTTP 200 from both ports and confirm models are loaded.
- `tests/test_memory_triage.py`: Send mock conversation to `extract_and_triage`, assert `reminders.json` is updated with valid schema.

---

## Acceptance Criteria

### Engine & Launch
- [ ] `start_engines.ps1` runs without errors and both `ollama create` commands succeed.
- [ ] Both ports respond with HTTP 200 within 30 s of launch.

### Backend
- [ ] `POST /api/chat/stream` returns `text/event-stream` with real tokens from `janus-chat`.
- [ ] After a chat turn (incognito off), `reminders.json` or `work_context.json` is updated within 10 s.
- [ ] `POST /api/personas/compile` saves a valid JSON file under `data/personas/`.
- [ ] `GET /api/state` returns a valid JSON object with `reminders`, `work_notes`, `facts`, and `personas` keys.

### Frontend
- [ ] SPA loads without console errors and displays correct dark-mode styling.
- [ ] Incognito toggle visually changes UI tint and suppresses triage calls.
- [ ] Mode toggle switches sidebar between reminder list and persona grid.
- [ ] Chat streams token-by-token with visible SSE updates and auto-scrolls.

### Tests
- [ ] `pytest tests/test_engines.py` passes (with both engines running).
- [ ] `pytest tests/test_memory_triage.py` passes and `reminders.json` diff is non-empty.

---

## Verification Plan
Automated: `pytest tests/` against live dual-engine stack.
Manual: Launch SPA in browser, send a message containing a reminder phrase ("remind me to..."), verify sidebar updates, toggle incognito, confirm triage is suppressed.

## 2026-09-15T15:09:16Z

Extend the existing **Project Janus** local AI platform with an advanced **Character Forge** module. This feature provides a dedicated UI and backend for creating richly customized AI persona cards from scratch, with deep controls over personality, emotion, physicality, and mature/NSFW themes. It must support both manual form-based entry and AI-assisted generation (using the CPU background model on Port 11435), and must respect the global Incognito Mode toggle to ensure ephemeral sessions never touch the disk.

**Working directory:** `d:\hahaha\Project Janus`
**Integrity mode:** demo (pre-built libraries/frameworks for UI and validation are allowed; core business logic must be implemented directly)

---

## Requirements

### R1. Backend — New Forge Endpoints
Two new FastAPI endpoints must be added to `backend/main.py`, with supporting logic in `backend/persona_compiler.py`:

- `POST /api/personas/enhance` — accepts a `base_prompt` string and an `allow_nsfw` boolean; delegates to the CPU model (`janus-extractor` on Port 11435 with `format: "json"`) to return a fully populated character schema JSON object.
- `POST /api/personas/forge` — accepts a `character_data` dict and an `incognito` boolean; if `incognito` is `false`, validates and saves the character to `data/personas/{name}.json`; if `incognito` is `true`, the data is returned directly and **no file is written to disk**.

Both endpoints must use Pydantic models for request/response validation, and the enhance endpoint must force the CPU model to return structured JSON via the schema-locked system prompt already specified.

The character schema the enhance endpoint must produce (and the forge endpoint must validate) is:
```json
{
  "name": "string",
  "personality": {
    "archetype": "string",
    "core_traits": ["list of 4 adjectives"],
    "flaws": ["list of 2 flaws"]
  },
  "emotion": {
    "default_mood": "string",
    "reaction_to_stress": "string",
    "speech_style": "string"
  },
  "physicality": {
    "appearance": "string",
    "body_language": "string"
  },
  "mature_themes": {
    "nsfw_enabled": "boolean",
    "boundaries": "string",
    "mature_dynamics": "string"
  }
}
```

The CPU model system prompt for enhancement must instruct the model to act as an expert character designer and return strictly valid JSON matching this schema, with `mature_dynamics` only populated when `nsfw_enabled` is true.

### R2. Frontend — Character Forge UI
A new "Character Forge" panel (modal or drawer) must be added to `frontend/index.html` and `frontend/app.js`, styled to match the existing matte dark theme (`#0c0d10` / `#14171f`, Tailwind classes). The panel must contain:

- **AI Auto-Forge section:** a text area for the base prompt, an NSFW toggle (visually accented in red/orange with a warning label), and an "✨ Enhance & Generate" button that calls `/api/personas/enhance` and populates the manual form below.
- **Manual Tuning section:** editable fields for all schema fields (Name, Archetype, Core Traits, Flaws, Default Mood, Stress Reaction, Speech Style, Appearance, Body Language, NSFW checkbox, Boundaries, Mature Dynamics).
- **Incognito-aware Save button:** reads the global Incognito state at submit time. When Incognito is ON the button reads "Launch Disposable Session" and calls `/api/personas/forge` with `incognito: true` (no disk write). When Incognito is OFF it reads "Save Persona" and persists the card to `data/personas/`.

### R3. Tests — Incognito Non-Persistence Assertion
A new test file `tests/test_persona_forge.py` must verify, without requiring live engines, that:

- A POST to `/api/personas/forge` with `incognito: true` does **not** create any new file in `data/personas/`.
- A POST to `/api/personas/forge` with `incognito: false` and valid data **does** create the expected file.

These tests should follow the patterns used in the existing test suite (httpx `AsyncClient`, `pytest-asyncio`).

---

## Acceptance Criteria

### Backend endpoints
- [ ] `POST /api/personas/enhance` returns a valid JSON body matching the character schema when called with a non-empty `base_prompt`.
- [ ] `POST /api/personas/forge` with `incognito: true` returns HTTP 200 with the character data and creates zero new files in `data/personas/`.
- [ ] `POST /api/personas/forge` with `incognito: false` and valid `character_data` returns HTTP 200 and exactly one new `.json` file appears in `data/personas/`.

### Frontend UI
- [ ] The Character Forge panel is reachable from the main interface (a button or nav item opens it).
- [ ] Clicking "✨ Enhance & Generate" populates all Manual Tuning fields with the server response (no manual copy-paste required).
- [ ] When the global Incognito toggle is ON, the save button reads "Launch Disposable Session" and the character data is held in session state only.
- [ ] When Incognito is OFF, the save button reads "Save Persona" and the newly saved card appears in the personas roster after saving.

### Tests
- [ ] `pytest tests/test_persona_forge.py` passes with zero failures and no live engine required.
- [ ] The incognito non-persistence test explicitly checks the filesystem (counts files before and after the request).

## 2026-09-15T22:44:09Z

# Teamwork Project Prompt — Draft

> Status: Ready for launch — awaiting user approval.
> Goal: Get user approval → delegate to teamwork_preview
> Requested team: Small, focused team

**Project Description:** 
This is a single self-contained feature and fix: Expand the Character Forge to allow users to explicitly define a custom "Roleplay Style" (e.g. narrative tone, formatting rules, setting). This field should be auto-populated by the AI during enhancement, and the backend chat pipeline must strongly enforce this style so forged characters actively engage in conversation without avoiding interaction.

**Working directory:** `d:\hahaha\Project Janus`
**Integrity mode:** development

## Requirements

### R1. Roleplay Style Customization
Update the `CharacterForgeSchema` in the backend and the Character Forge UI in the frontend (`index.html` and `app.js`) to include a new "Roleplay Style" text field. This field must be auto-generated by the CPU engine when the user clicks "✨ Enhance & Generate".

### R2. Character Interaction Fix
Ensure the new "Roleplay Style" field is injected into the character's final system prompt (in `backend/persona_compiler.py`). The chat pipeline must strongly enforce this style, ensuring the character fully embodies the user's roleplay definitions and actively converses instead of giving generic/avoidant AI responses.

## Acceptance Criteria

### Verification
- [ ] **Customization Accessible:** The UI has a dedicated text area for "Roleplay Style" which saves correctly.
- [ ] **AI Enhanced:** The "✨ Enhance & Generate" button successfully infers and populates the "Roleplay Style" based on the user's base prompt.
- [ ] **Interaction Works:** When chatting with the forged character, the model explicitly follows the narrative rules defined in the "Roleplay Style" and maintains active conversation.

