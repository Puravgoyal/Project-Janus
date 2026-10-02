# Project Janus — End-to-End Test Infrastructure Specification

**Document Version**: 1.0.0  
**Author**: E2E Test Architect  
**Project**: Project Janus (100% Offline Local AI Assistant Platform for Windows 11)  
**Authoritative Sources**: `ORIGINAL_REQUEST.md`, `PROJECT.md`, `spec_miner_reqs/report.md`  

---

## 1. Executive Summary & Testing Philosophy

Project Janus is an offline, privacy-first local AI assistant platform running on Windows 11 featuring a dual-engine architecture:
- **GPU Interactive Chat Engine (`127.0.0.1:11434`)**: Real-time streaming chat over Server-Sent Events (SSE) using RTX 4050 (6 GB VRAM) with Flash Attention.
- **CPU Silent Extraction Engine (`127.0.0.1:11435`)**: Background JSON extraction, memory triage, and persona compilation using Core i7 8 P-Cores with zero GPU VRAM contention.
- **FastAPI Backend Server (`127.0.0.1:8000`)**: REST/SSE routing, persistent JSON storage with per-file asyncio locks and atomic replacement, and static asset serving.
- **Dark-Mode Single-Page Application (SPA)**: Obsidian (`#0c0d10`), panels (`#14171f`), emerald (`#10b981`), incognito mode (`#9333ea`), live latency badges, reminders checklist, persona grid, and token-by-token streaming chat.

### Core Testing Methodology: Opaque-Box & Requirement-Driven
The Project Janus E2E test suite adheres to strict **opaque-box** testing principles:
1. **Zero White-Box Coupling**: Tests never import internal private helper functions or mock away core business logic.
2. **Standard External Interfaces**: Tests interact exclusively through:
   - **HTTP REST & SSE Requests** against the FastAPI server (`http://127.0.0.1:8000`).
   - **Local File System Artifacts** (`Modelfile.gpu`, `Modelfile.cpu`, `start_engines.ps1`, `data/*.json`, `frontend/*`).
   - **Process & Engine Endpoints** (`127.0.0.1:11434`, `127.0.0.1:11435`).
3. **Requirement Traceability**: Every single test case is directly anchored to an itemized specification in `ORIGINAL_REQUEST.md` and `PROJECT.md`.
4. **Deterministic Verification**: Tests verify exact JSON schemas, HTTP status codes, MIME types, FIFO buffer caps, CSS hex codes, and regex matching for non-deterministic fields (such as timestamps and UUIDs).

---

## 2. 4-Tier Test Architecture Overview

The test harness is organized into four distinct tiers:

```
e2e_tests/
├── runner.py                  # Standalone CLI test runner
├── common.py                  # Opaque HTTP client, SSE parser, schema validators
├── test_tier1_features.py     # Tier 1: Feature Coverage (>=5 tests per feature = 140 tests)
├── test_tier2_boundaries.py   # Tier 2: Boundary & Corner Cases (>=5 tests per feature = 140 tests)
├── test_tier3_interactions.py # Tier 3: Pairwise Cross-Feature Interactions (20+ tests)
└── test_tier4_scenarios.py    # Tier 4: Real-World Multi-Step Executive Scenarios (5+ workflows)
```

| Tier | Focus | Test Count | Description |
|------|-------|------------|-------------|
| **Tier 1: Feature Coverage** | Primary Behavior (Happy Path) | **140 tests** (5 per feature × 28 features) | Validates that every feature implements its core functional contract under normal operating conditions. |
| **Tier 2: Boundaries & Corners** | Edge Cases, Limits, Error Handling | **140 tests** (5 per feature × 28 features) | Stresses buffer boundaries, invalid payloads, malformed JSON, timeouts, concurrent contention, and isolation faults. |
| **Tier 3: Interactions** | Cross-Feature Pairwise Coupling | **20 tests** | Validates interoperability between decoupled components (e.g. SSE streaming + background triage; incognito + memory lock; wiki compilation + immediate persona chat). |
| **Tier 4: Scenarios** | Real-World Executive Workflows | **5 multi-step workflows** | End-to-end user journeys simulating full executive assistant sessions, onboarding, high-velocity task shifts, and disaster recovery. |
| **Total** | Full E2E Test Suite | **305 tests** | Complete end-to-end verification coverage. |

---

## 3. Feature Coverage Matrix (Tier 1 & Tier 2)

All 28 features from `PROJECT.md` have explicit, dedicated test classes with at least 5 Tier 1 tests and 5 Tier 2 tests:

| # | Feature Name | Milestone | Tier 1 (Happy Path) Coverage | Tier 2 (Boundary & Error) Coverage |
|---|--------------|-----------|-----------------------------|------------------------------------|
| 1 | `Modelfile.gpu` Specification | M1 | File existence, `FROM` model, `num_gpu 999`, `num_ctx 4096`, `temperature 0.72` | Extra whitespace, missing parameters, parameter bounds, uppercase directives, template format |
| 2 | `Modelfile.cpu` Specification | M1 | File existence, `num_gpu 0`, `num_thread 8`, `num_ctx 8192`, `temperature 0.05` | Zero GPU enforcement, thread bound extremes, JSON formatting instruction, context limit, low temperature |
| 3 | PowerShell Launcher `start_engines.ps1` | M1 | Script exists, port 11434 launch command, port 11435 launch command, `ollama create` execution, 30s retry loop | Port conflict detection, missing binary error, timeout exit code, parameter passing, error logging |
| 4 | Engine Lifecycle & Port Isolation | M1 | Loopback binding `127.0.0.1`, GPU port 11434, CPU port 11435, `CUDA_VISIBLE_DEVICES=""`, independent processes | Port crosstalk isolation, VRAM leakage guard, concurrent port queries, non-loopback rejection, process kill resilience |
| 5 | Data Scaffold: `user_profile.json` | M2 | Valid JSON, `user_name` string, `preferences` list/dict, `facts` list, `last_updated` timestamp | Empty profile recovery, special Unicode characters, extreme fact array length, missing fields, schema corruption |
| 6 | Data Scaffold: `reminders.json` | M2 | Valid JSON array, `id` field, `text` field, `completed` boolean, `created_at` timestamp | Empty array handling, invalid timestamp syntax, duplicate reminder IDs, extreme text length, non-boolean completed |
| 7 | Data Scaffold: `work_context.json` | M2 | Valid JSON, `rolling_buffer` key, list of notes, note `id`, note `timestamp` | FIFO pruning at >15 items, exactly 15 items boundary, 0 items empty buffer, negative/invalid IDs, large note content |
| 8 | Data Scaffold: Default Persona | M2 | `executive_assistant.json` exists, valid `id`, `name`, `system_prompt`, `greeting` | Missing greeting fallback, missing traits fallback, empty string prompts, illegal filename characters, schema typing |
| 9 | Atomic Storage Helper `storage.py` | M2 | Module exists, async write function, file lock acquisition, atomic replacement (`os.replace`), read consistency | Concurrent write collision avoidance, crash during write temp file cleanup, read-during-write lock, invalid JSON rejection, disk permission errors |
| 10 | Memory Engine `extract_and_triage` | M3 | Function exists, triage reminder detection, triage work note detection, triage fact detection, Port 11435 payload format | Markdown backtick stripped (` ```json `), conversational preamble stripped, malformed JSON recovery, no-op on casual chat, duplicate fact suppression |
| 11 | Memory Engine Context Injection | M3 | System prompt format, user profile injected, active reminders injected, rolling work notes injected, assistant tone | Incognito suppression (zero facts/reminders), empty data files fallback, persona override, prompt length capping, special character escaping |
| 12 | Persona Compiler `compile_wiki_to_card` | M3 | Function exists, Port 11435 prompt format, valid persona card return, file saved in `data/personas/`, slug generation | 50k+ word text truncation to 8192 tokens, empty wiki text error, special character character name, malformed LLM response handling, slug collision avoidance |
| 13 | Endpoint `POST /api/chat/stream` | M3 | SSE response headers, `data:` chunk format, `token` and `done` fields, streaming completion, non-incognito triage dispatch | Client disconnect abort, empty messages list, invalid JSON body (422), incognito triage suppression, streaming timeout |
| 14 | Endpoint `GET /api/state` | M3 | HTTP 200, `reminders` list, `work_notes` list, `facts` list, `engine_health` dict | Missing data files recovery, engine offline status reporting, rapid polling performance, large payload response, schema completeness |
| 15 | Endpoint `POST /api/personas/compile` | M3 | HTTP 200, status success, persona card returned, disk file verified, correct category | Missing character name (400), missing raw_text (400), oversized payload, invalid JSON syntax (422), unwriteable directory |
| 16 | Endpoint `PATCH /api/reminders/{id}` | M3 | HTTP 200, status updated, `completed` toggled to true, toggled to false, disk state updated | Non-existent reminder ID (404), invalid completed type (422), missing body, empty ID path, concurrent toggles |
| 17 | Static File Mounting | M3 | `GET /` serves HTML, MIME type `text/html`, styles served `text/css`, JS served `application/javascript`, 404 for missing asset | Directory traversal blocked (`/static/../../`), large asset streaming, HEAD request handling, query string handling, browser caching headers |
| 18 | Frontend Dark-Mode Theme | M4 | Obsidian `#0c0d10` canvas, panel `#14171f`, emerald `#10b981`, standalone CSS fallback, high-contrast text | Missing CSS variables fallback, viewport width scaling, zero Tailwind network reliance, invalid class suppression, dark scrollbar styling |
| 19 | Frontend Header Controls | M4 | Mode toggle button, incognito toggle button, GPU latency badge, CPU latency badge, status indicators | Rapid toggle clicking, high latency formatting, engine offline red badge, state sync on reload, keyboard accessibility |
| 20 | Frontend Sidebar: Assistant Mode | M4 | Reminders container, checkbox elements, add reminder input, priority badge rendering, due date display | 100+ reminders list scrolling, very long reminder text wrap, empty input submission blocked, rapid checkbox toggling, optimistic update rollback |
| 21 | Frontend Sidebar: Persona Mode | M4 | Persona card grid, active persona badge, "Ingest Wiki/Text" button, ingest modal form, submit button | 0 persona cards state, 50+ cards grid layout, modal Escape key close, modal click outside close, loading spinner on submit |
| 22 | Frontend Memory Inspector | M4 | Inspector accordion container, collapsible toggle, facts view, rolling work buffer view, raw JSON toggle | 1000+ facts expansion speed, copy JSON to clipboard error, malformed JSON display safe, empty memory empty-state, scroll containment |
| 23 | Frontend Chat Interface | M4 | Message container, user bubble style, assistant bubble style, SSE stream reader, auto-scroll logic | 10k token stream buffer, rapid send during stream disabled, unclosed markdown codeblock, XSS injection sanitization, manual scroll disables auto-scroll |
| 24 | Incognito Mode UI & Logic | M4 | Purple tint CSS class `#9333ea`, "Memory Locked" badge, request payload `incognito: true`, memory files unwritten, restore normal theme on toggle off | Mid-stream incognito toggle guard, memory inspector masking in incognito, zero file timestamp change, refresh maintains or resets, local storage sync |
| 25 | Engine Verification Test | M5 | `tests/test_engines.py` exists, port 11434 assertion, port 11435 assertion, `janus-chat` tag assertion, `janus-extractor` tag assertion | Offline port failure reporting, missing model failure reporting, connection timeout handling, non-standard HTTP status code, diagnostic error text |
| 26 | Memory Triage Test | M5 | `tests/test_memory_triage.py` exists, reminder assertion, rolling buffer assertion, schema validation assertion, non-empty diff assertion | Empty diff failure assertion, malformed reminder rejection, rolling buffer >15 failure assertion, test isolation & cleanup, mock failure handling |
| 27 | E2E Test Suite (Tiers 1-4) | Final | `e2e_tests/runner.py` CLI, Tier 1 execution, Tier 2 execution, Tier 3 execution, Tier 4 execution | Invalid CLI tier flag error, non-zero exit on failure, exit code 0 on pass, JSON report generation, partial suite execution |
| 28 | Adversarial Coverage Hardening | Final | Prompt injection escaping, HTML/XSS escaping, SQL/Command injection escaping, buffer overflow immunity, path traversal sanitization | 1MB raw text ingest DOS resilience, null-byte character sanitization, deeply nested JSON injection, concurrent race stress, zero integrity violations |

---

## 4. Tier 3: Pairwise Cross-Feature Interactions

Tier 3 executes 20 pairwise integration tests:
1. **Chat Stream + Memory Triage**: Verifies that streaming a message containing a commitment ("Remind me to submit Q3 report") yields tokens on port 8000 and subsequently triggers an extraction write to `reminders.json`.
2. **Chat Stream + Incognito Guard**: Verifies that streaming a message with a commitment when `incognito: true` completes tokens normally but leaves `reminders.json` unmodified.
3. **Persona Compilation + Immediate Chat**: Verifies compiling a new character ("Marcus Aurelius"), saving the card to `data/personas/marcus_aurelius.json`, and immediately requesting `POST /api/chat/stream` with `persona_id: "marcus_aurelius"`.
4. **Reminder Extraction + PATCH Toggle + State Retrieval**: Verifies extracting a reminder, updating its completion via `PATCH /api/reminders/{id}`, and asserting updated state via `GET /api/state`.
5. **Multi-Turn Chat + Work Context Rolling Cap**: Verifies that 18 consecutive conversational work exchanges append to `work_context.json` while maintaining a strict 15-item FIFO cap.
6. **Context Injection + User Profile Preference Update**: Verifies updating user profile tone, followed by `inject_context()`, confirming the updated tone appears in the synthesized system prompt.
7. **Engine Health Check + State Endpoint**: Verifies that engine reachability and ping latencies for ports 11434 and 11435 are accurately reflected in `GET /api/state`.
8. **Static File Server + Frontend Asset Loading**: Verifies that `GET /` serves HTML referencing `styles.css` and `app.js`, and that these static assets are successfully fetched with correct MIME types.
9. **Concurrent Chat & Reminder Updates**: Stresses the `storage.py` mutex by firing concurrent chat streams and reminder updates without experiencing file lock collisions or corruption.
10. **Modelfile Configuration + Launcher Script**: Validates that parameters specified in `Modelfile.gpu` and `Modelfile.cpu` align with the `ollama create` targets in `start_engines.ps1`.
11. **Modelfile.cpu JSON Format + Persona Compiler Output**: Verifies that CPU extractor instructions enforce structured JSON cards without markdown formatting.
12. **Incognito Mode + Memory Inspector Privacy**: Verifies that when incognito is active, the memory inspector renders a privacy lock or hides personal facts.
13. **Mode Switch (Assistant vs Persona) + Session Continuity**: Verifies switching modes maintains chat history while altering active persona and sidebar views.
14. **Quick-Action Chips + Streaming Engine**: Verifies that triggering a quick-action chip ("Summarize active tasks") initiates the SSE stream and injects current reminders.
15. **Wiki Ingest Modal + Persona Grid Rendering**: Verifies the UI contract from modal submission through compilation to automatic insertion in the persona card grid.
16. **Corrupt File Recovery + State Endpoint**: Verifies that if `reminders.json` contains malformed JSON, `GET /api/state` recovers cleanly by returning an empty list or creating a backup.
17. **Dual Engine Isolation + Independent Process Lifecycle**: Verifies that killing or restarting the CPU engine on port 11435 does not drop active chat streams on GPU port 11434.
18. **SSE Streaming Complete + Background Task Scheduling**: Verifies that `BackgroundTasks` in FastAPI executes `extract_and_triage` only after the `done: true` event is transmitted.
19. **Profile Attribute Change + Persona Chat**: Verifies that persona chat respects user name and preferences from `user_profile.json` while retaining the character's persona directives.
20. **PowerShell Launcher Port Verification + Pytest Engine Test**: Verifies that `tests/test_engines.py` matches the readiness criteria checked by `start_engines.ps1`.

---

## 5. Tier 4: Real-World Multi-Step Application Scenarios

Tier 4 exercises 5 realistic executive workflows:
1. **Scenario 1: Executive Assistant Morning Onboarding Routine**:
   - Step 1: System launch & engine ping verification.
   - Step 2: Query initial state (`GET /api/state`).
   - Step 3: Morning dialogue: "Good morning Janus, here is our plan for today: review architecture, fix 3 bugs, and remind me to call the director at 4 PM."
   - Step 4: Verify SSE stream tokens delivered.
   - Step 5: Verify reminder extracted and work context updated.
   - Step 6: Complete the reminder via `PATCH /api/reminders/{id}` and confirm state.
2. **Scenario 2: High-Velocity Sprint Execution & Context Shifting**:
   - Step 1: Rapid submission of 18 technical work notes across consecutive turns.
   - Step 2: Verify `work_context.json` maintains exactly 15 items in strict FIFO order (items 1-3 dropped, items 4-18 retained).
   - Step 3: Context injection verification ensuring newest notes are prioritized in system prompt.
3. **Scenario 3: Wiki Persona Ingestion & Interactive Historical Roleplay**:
   - Step 1: Provide Wikipedia text excerpt for "Marcus Aurelius".
   - Step 2: Submit `POST /api/personas/compile`.
   - Step 3: Verify persona card saved to `data/personas/marcus_aurelius.json` with required schema fields.
   - Step 4: Switch mode to Persona and select `marcus_aurelius`.
   - Step 5: Send chat prompt: "What is your philosophy on adversity?"
   - Step 6: Verify SSE stream reflects Stoic philosophical tone.
4. **Scenario 4: Confidential Executive Briefing (Incognito Mode)**:
   - Step 1: Toggle incognito mode ON (purple tint theme, memory locked).
   - Step 2: Send highly sensitive message: "Confidential: We are considering acquiring Company X. Remind me to destroy these notes tomorrow."
   - Step 3: Verify response streams cleanly.
   - Step 4: Verify `reminders.json`, `work_context.json`, and `user_profile.json` were NOT modified.
   - Step 5: Toggle incognito mode OFF and verify normal persistence resumes.
5. **Scenario 5: Engine Fault Recovery & Local Storage Self-Healing**:
   - Step 1: Corrupt `data/reminders.json` with invalid syntax.
   - Step 2: Invoke `GET /api/state`.
   - Step 3: Assert application does not crash with HTTP 500; auto-creates `.bak` or initializes clean state.
   - Step 4: Add new reminder and verify storage engine restores valid JSON on disk.

---

## 6. Standalone CLI Test Runner (`e2e_tests/runner.py`)

The test runner provides a unified command-line interface for running the full test suite or targeted tiers:

### Usage:
```bash
# Run entire 4-tier test suite
python e2e_tests/runner.py --all

# Run specific tiers
python e2e_tests/runner.py --tier 1
python e2e_tests/runner.py --tier 2
python e2e_tests/runner.py --tier 3
python e2e_tests/runner.py --tier 4

# Run with verbose output and JSON report export
python e2e_tests/runner.py --all --verbose --json-report test_results.json

# Fail fast (stop on first failure)
python e2e_tests/runner.py --tier 1 --bail
```

### Exit Codes:
- `0`: All tests passed successfully.
- `1`: One or more tests failed.
- `2`: CLI argument or configuration error.

---

## 7. Continuous Integration & Quality Gates

Before declaring any milestone or release complete:
1. Run Tier 1 Feature Coverage: `python e2e_tests/runner.py --tier 1` (100% pass required).
2. Run Tier 2 Boundary & Corner Cases: `python e2e_tests/runner.py --tier 2` (100% pass required).
3. Run Tier 3 Cross-Feature Interactions: `python e2e_tests/runner.py --tier 3` (100% pass required).
4. Run Tier 4 Real-World Scenarios: `python e2e_tests/runner.py --tier 4` (100% pass required).
5. Review generated `test_results.json` to verify zero regressions.
