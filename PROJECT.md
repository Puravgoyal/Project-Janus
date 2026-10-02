# Project: Project Janus

## Architecture
Project Janus is an offline, privacy-first local AI assistant platform for Windows 11 featuring a dual-engine architecture:
- **GPU Engine (Port 11434)**: RTX 4050 Laptop GPU (6 GB VRAM), `OLLAMA_FLASH_ATTENTION=1`, context 4096 tokens, temperature 0.72. Dedicated strictly to interactive streaming chat via Server-Sent Events (SSE).
- **CPU Engine (Port 11435)**: Core i7 8 P-Cores, DDR5, `CUDA_VISIBLE_DEVICES=""`, `num_gpu 0`, `num_thread 8`, context 8192 tokens, temperature 0.05. Dedicated strictly to background memory extraction, reminder triage, user profile updates, and wiki persona compilation. Zero VRAM contention.
- **FastAPI Backend (Port 8000)**: Asynchronous REST and SSE server connecting to both Ollama engines, providing persistent local memory management with per-file asyncio locks, atomic writes, and static asset serving.
- **Frontend SPA**: Standalone dark-mode SPA (Obsidian `#0c0d10`, Panels `#14171f`, Emerald `#10b981`, Incognito `#9333ea`) with mode toggle (Assistant vs Persona), incognito toggle, real-time GPU/CPU latency badges, markdown rendering, and SSE streaming.
- **Network & Privacy**: Strict loopback (`127.0.0.1`), 100% offline, zero cloud telemetry.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Modelfile.gpu specification | `num_gpu 999`, `num_ctx 4096`, `temp 0.72`, Flash Attention, target `janus-chat` | M1 | R1 |
| 2 | Modelfile.cpu specification | `num_gpu 0`, `num_thread 8`, `num_ctx 8192`, `temp 0.05`, JSON-only prompt, target `janus-extractor` | M1 | R1 |
| 3 | PowerShell launcher `start_engines.ps1` | Spawns dual Ollama instances on 11434 & 11435, compiles models via `ollama create`, 30s health check retry loop | M1 | R1 |
| 4 | Engine lifecycle & port isolation | Strict environment binding (`OLLAMA_HOST`, `CUDA_VISIBLE_DEVICES=""`), zero VRAM on CPU engine | M1 | R1 |
| 5 | Data scaffold: `user_profile.json` | Stores user name, preferences, facts, and persistent attributes | M2 | R4 |
| 6 | Data scaffold: `reminders.json` | Stores pending and completed reminders with timestamps, id, text, done status | M2 | R4 |
| 7 | Data scaffold: `work_context.json` | Maintains rolling 15-item buffer of work session notes and context | M2 | R4 |
| 8 | Data scaffold: Default persona | `data/personas/executive_assistant.json` structured card | M2 | R4 |
| 9 | Atomic storage helper `storage.py` | Concurrency safety via `asyncio.Lock` per file and atomic replacement (`os.replace`) | M2 | Arch Survey |
| 10 | Memory engine `extract_and_triage` | Asynchronous background call to Port 11435 with `format: json`, updates reminders, rolling buffer, user profile | M3 | R2 |
| 11 | Memory engine context injection | Formats user profile, rolling work notes, and pending reminders into system prompt string | M3 | R2 |
| 12 | Persona compiler `compile_wiki_to_card` | Condenses raw wiki/article text via Port 11435 into structured persona card in `data/personas/` | M3 | R2 |
| 13 | Endpoint `POST /api/chat/stream` | SSE streaming from Port 11434 with optional background triage dispatch, incognito suppression | M3 | R2 |
| 14 | Endpoint `GET /api/state` | Returns full system JSON state (reminders, work_notes, facts, personas, engine health) | M3 | R2 |
| 15 | Endpoint `POST /api/personas/compile` | Ingests character name and raw wiki text to compile and save persona card | M3 | R2 |
| 16 | Endpoint `PATCH /api/reminders/{id}` | Toggle reminder completion status in `reminders.json` | M3 | R2/Frontend |
| 17 | Static file mounting | Mounts frontend directory for single-page app serving | M3 | R2 |
| 18 | Frontend dark-mode theme | Obsidian `#0c0d10`, panels `#14171f`, emerald `#10b981`, standalone offline CSS fallback | M4 | R3 |
| 19 | Frontend header controls | Mode toggle (Assistant vs Persona), incognito toggle, GPU/CPU ping indicators with millisecond latency | M4 | R3 |
| 20 | Frontend sidebar: Assistant mode | Pending reminders with completion checkboxes, add reminder input | M4 | R3 |
| 21 | Frontend sidebar: Persona mode | Persona card grid, active persona selection, "Ingest Wiki/Text" modal | M4 | R3 |
| 22 | Frontend memory inspector | Collapsible accordion displaying raw JSON state and facts | M4 | R3 |
| 23 | Frontend chat interface | SSE token-by-token streaming, markdown rendering, auto-scroll, quick-action prompt chips | M4 | R3 |
| 24 | Incognito mode UI & logic | Purple theme tint shift, suppress memory injection and suppress background triage writes | M4 | R3 |
| 25 | Engine verification test | `tests/test_engines.py`: Assert HTTP 200 on 11434 and 11435, confirm loaded models | M5 | R5 |
| 26 | Memory triage test | `tests/test_memory_triage.py`: Send mock conversation, assert schema compliance and non-empty diff | M5 | R5 |
| 27 | E2E Test Suite (Tiers 1-4) | Comprehensive opaque-box test runner and test cases published in `TEST_READY.md` | Final | E2E Track |
| 28 | Adversarial coverage hardening (Tier 5) | White-box stress testing, edge cases, and boundary analysis with zero integrity violations | Final | Final Phase |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Dual Ollama Engines & Launcher | `Modelfile.gpu`, `Modelfile.cpu`, `start_engines.ps1` | none | COMPLETED (worker_m1) |
| M2 | Local JSON Data Layer & Schemas | Initial JSON files, `data/personas/`, `backend/storage.py` | none | COMPLETED (worker_m2) |
| M3 | Python FastAPI Backend Logic | `memory_engine.py`, `persona_compiler.py`, `main.py` | M1, M2 | COMPLETED (worker_m3, remediated) |
| M4 | Modern Dark-Mode SPA Frontend | `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` | M3 | COMPLETED (worker_m4) |
| M5 | Automated Test Harness | `tests/test_engines.py`, `tests/test_memory_triage.py` | M1, M2, M3 | COMPLETED (worker_m5) |
| Final | E2E Verification & Hardening | Phase 1 (100% E2E test pass) + Phase 2 (Tier 5 adversarial hardening) | M1, M2, M3, M4, M5, E2E Track | DONE (Verified Clean by Forensic Auditor, Approved by Reviewer & Challenger) |

## Interface Contracts

### 1. Modelfile & Launcher ↔ Ollama Daemons
- GPU Port: `127.0.0.1:11434`, model name `janus-chat`
- CPU Port: `127.0.0.1:11435`, model name `janus-extractor`
- Health check: `GET http://127.0.0.1:11434/api/version` and `GET http://127.0.0.1:11435/api/version` return HTTP 200 within 30s.

### 2. Backend ↔ Ollama Engines (HTTP REST)
- GPU Chat Streaming: `POST http://127.0.0.1:11434/api/chat` with `{"model": "janus-chat", "messages": [...], "stream": true}`
- CPU JSON Triage: `POST http://127.0.0.1:11435/api/chat` with `{"model": "janus-extractor", "messages": [...], "stream": false, "format": "json"}`
- CPU Persona Compile: `POST http://127.0.0.1:11435/api/generate` with `{"model": "janus-extractor", "prompt": "...", "stream": false, "format": "json"}`

### 3. Frontend ↔ Backend (FastAPI Endpoints)
- `POST /api/chat/stream`:
  - Request: `{"message": string, "mode": "assistant" | "persona", "persona_id": string | null, "incognito": bool}`
  - Response: `text/event-stream` with `data: {"token": string, "done": bool}\n\n`
- `GET /api/state`:
  - Response: `{"reminders": [...], "work_notes": [...], "facts": [...], "personas": [...], "engines": {"gpu": bool, "cpu": bool}}`
- `POST /api/personas/compile`:
  - Request: `{"name": string, "wiki_text": string}`
  - Response: `{"status": "success", "persona": {"id": string, "name": string, "description": string, "personality": [...], "system_prompt": string}}`
- `PATCH /api/reminders/{id}`:
  - Request: `{"completed": bool}`
  - Response: `{"status": "updated", "reminder": {...}}`

### 4. Storage Engine ↔ JSON Files
- Concurrency: `asyncio.Lock` per target file.
- Durability: Write to temporary file in same directory, atomic `os.replace` to destination file.
- Schemas:
  - `user_profile.json`: `{"name": string, "preferences": list[string], "facts": list[string], "last_updated": string}`
  - `reminders.json`: `list[{"id": string, "text": string, "created_at": string, "due_date": string | null, "completed": bool}]`
  - `work_context.json`: `list[{"id": string, "timestamp": string, "note": string}]` (max 15 items, FIFO)
  - `data/personas/*.json`: `{"id": string, "name": string, "tagline": string, "system_prompt": string, "greeting": string, "avatar": string, "traits": list[string]}`

## Code Layout
```
Project Janus/
├── Modelfile.gpu                  # GPU interactive chat model definition
├── Modelfile.cpu                  # CPU JSON extractor model definition
├── start_engines.ps1              # Dual-process launcher & health-check script
├── requirements.txt               # Python package dependencies
├── backend/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application, routes, SSE endpoints, static mount
│   ├── memory_engine.py           # Background triage on 11435, rolling buffer, context injection
│   ├── persona_compiler.py        # Raw text to structured persona card compiler
│   └── storage.py                 # Async mutex locking, atomic file persistence, JSON IO
├── data/
│   ├── user_profile.json          # Persistent user facts & profile attributes
│   ├── reminders.json             # Pending and completed reminders
│   ├── work_context.json          # 15-item rolling session buffer
│   └── personas/
│       └── executive_assistant.json # Default assistant persona card
├── frontend/
│   ├── index.html                 # Single-page app markup
│   ├── styles.css                 # Dark theme, obsidian/emerald styling, animations
│   └── app.js                     # State management, SSE streaming reader, DOM bindings
├── tests/
│   ├── __init__.py
│   ├── test_engines.py            # Ollama dual-port connectivity & model assertions
│   ├── test_memory_triage.py      # Triage schema validation & diff assertions
│   └── test_api.py                # Backend REST/SSE route tests
├── e2e_tests/                     # Opaque-box requirement-driven E2E test suite
│   ├── runner.py                  # E2E test runner
│   ├── test_tier1_features.py     # Tier 1: Feature coverage tests (>= 5 per feature)
│   ├── test_tier2_boundaries.py   # Tier 2: Boundary & corner case tests
│   ├── test_tier3_interactions.py # Tier 3: Cross-feature pairwise interactions
│   └── test_tier4_scenarios.py    # Tier 4: Real-world application scenarios
├── PROJECT.md                     # Living project architecture & milestone tracker
├── TEST_INFRA.md                  # E2E test methodology & inventory
└── TEST_READY.md                  # Published when E2E test suite is complete
```
