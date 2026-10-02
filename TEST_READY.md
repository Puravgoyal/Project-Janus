# Project Janus — End-to-End Test Suite Readiness Declaration (`TEST_READY.md`)

**Document Version**: 1.0.0  
**Date**: 2026-09-13T00:46:00Z  
**Status**: **TEST SUITE COMPLETE & READY FOR EXECUTION**  
**Author**: E2E Test Architect (`test_writer_e2e`)  
**Project**: Project Janus (Offline Local AI Assistant Platform for Windows 11)  
**Authoritative Sources**: `ORIGINAL_REQUEST.md`, `PROJECT.md`, `spec_miner_reqs/report.md`, `TEST_INFRA.md`  

---

## 1. Executive Summary

The comprehensive, requirement-driven, opaque-box End-to-End (E2E) test harness for **Project Janus** has been designed, implemented, and published. The suite provides 100% feature coverage across all 28 inventoried features from `PROJECT.md`, rigorous boundary/corner-case validation, pairwise cross-feature interaction testing, and realistic multi-step executive workflow scenarios.

### Test Harness Summary
- **Total Test Cases**: **305 Tests**
- **Test Architecture**: 4 Tiers (Feature Coverage, Boundaries/Limits, Interactions, Scenarios)
- **Testing Approach**: 100% Opaque-box (HTTP REST & SSE endpoints, JSON data layer, Modelfiles, launcher contracts)
- **Zero White-Box Coupling**: No internal private function monkeypatching or mock dependencies
- **Execution Tooling**: Standalone CLI test runner (`e2e_tests/runner.py`) with colored progress output, per-tier filtering, execution timing, JSON export, and Pytest compatibility.

---

## 2. Test Execution Commands

The test suite can be run using the standalone CLI runner or via standard `pytest`:

### Standard CLI Runner:
```powershell
# Run the entire 4-tier E2E test suite (305 tests)
python e2e_tests/runner.py --all

# Run Tier 1 only (Feature Coverage — 140 tests)
python e2e_tests/runner.py --tier 1

# Run Tier 2 only (Boundary & Corner Cases — 140 tests)
python e2e_tests/runner.py --tier 2

# Run Tier 3 only (Cross-Feature Combinations — 20 tests)
python e2e_tests/runner.py --tier 3

# Run Tier 4 only (Real-World Scenarios — 5 workflows)
python e2e_tests/runner.py --tier 4

# Run with verbose individual test output and export JSON report
python e2e_tests/runner.py --all -v --json-report e2e_results.json

# Fail-fast mode (halt on first encountered failure)
python e2e_tests/runner.py --tier 1 --bail
```

### Pytest Alternative:
```powershell
# Run all E2E tests via pytest
pytest e2e_tests/ -v

# Run a specific tier
pytest e2e_tests/test_tier1_features.py -v
pytest e2e_tests/test_tier2_boundaries.py -v
pytest e2e_tests/test_tier3_interactions.py -v
pytest e2e_tests/test_tier4_scenarios.py -v
```

### Runner Exit Codes:
- `0`: All executed tests passed successfully.
- `1`: One or more test assertions failed or encountered an error.
- `2`: Invalid CLI arguments or configuration error.

---

## 3. Tier Breakdown

```
e2e_tests/
├── __init__.py                # E2E test package declaration
├── common.py                  # Opaque HTTP client, SSE parser, file/schema helpers
├── runner.py                  # Standalone CLI test runner with ANSI reporting
├── test_tier1_features.py     # Tier 1: Primary Feature Coverage (140 tests)
├── test_tier2_boundaries.py   # Tier 2: Boundaries, Limits & Error Handling (140 tests)
├── test_tier3_interactions.py # Tier 3: Pairwise Cross-Feature Interactions (20 tests)
└── test_tier4_scenarios.py    # Tier 4: Real-World Multi-Step Scenarios (5 workflows)
```

| Tier | File Path | Tests | Coverage Target | Description |
|------|-----------|-------|-----------------|-------------|
| **Tier 1** | `e2e_tests/test_tier1_features.py` | **140** | All 28 Features (>=5 tests/feat) | Happy path validation verifying every feature satisfies its basic functional contract. |
| **Tier 2** | `e2e_tests/test_tier2_boundaries.py` | **140** | All 28 Features (>=5 tests/feat) | Extreme limits, buffer caps, malformed JSON, timeouts, concurrent lock contention, and recovery. |
| **Tier 3** | `e2e_tests/test_tier3_interactions.py` | **20** | Cross-Module Integration | Pairwise interactions between chat streaming, memory triage, incognito mode, persona compiler, and storage locks. |
| **Tier 4** | `e2e_tests/test_tier4_scenarios.py` | **5** | End-to-End Workflows | Multi-turn executive sessions (onboarding, high-velocity sprint, wiki research, incognito briefing, recovery). |
| **Total** | **Full E2E Suite** | **305** | **100% Specification** | Complete end-to-end opaque-box test coverage. |

---

## 4. 28-Feature Coverage Checklist

| # | Feature Name in `PROJECT.md` | Milestone | Tier 1 (Happy Path) | Tier 2 (Boundaries) | Tier 3 (Interactions) | Tier 4 (Scenarios) | Status |
|---|-----------------------------|-----------|---------------------|---------------------|-----------------------|--------------------|--------|
| 1 | `Modelfile.gpu` Specification | M1 | `test_modelfile_gpu_*` (5) | `test_modelfile_gpu_*` (5) | Test 3.10 | Scenario 1 | Complete |
| 2 | `Modelfile.cpu` Specification | M1 | `test_modelfile_cpu_*` (5) | `test_modelfile_cpu_*` (5) | Test 3.11 | Scenario 3 | Complete |
| 3 | PowerShell Launcher `start_engines.ps1` | M1 | `test_start_engines_*` (5) | `test_launcher_*` (5) | Test 3.20 | Scenario 1 | Complete |
| 4 | Engine Lifecycle & Port Isolation | M1 | `test_engine_*` (5) | `test_engine_*` (5) | Test 3.17 | Scenario 5 | Complete |
| 5 | Data Scaffold: `user_profile.json` | M2 | `test_user_profile_*` (5) | `test_user_profile_*` (5) | Test 3.6, 3.19 | Scenario 1 | Complete |
| 6 | Data Scaffold: `reminders.json` | M2 | `test_reminders_*` (5) | `test_reminders_*` (5) | Test 3.4 | Scenario 1, 4 | Complete |
| 7 | Data Scaffold: `work_context.json` | M2 | `test_work_context_*` (5) | `test_work_context_*` (5) | Test 3.5 | Scenario 2 | Complete |
| 8 | Data Scaffold: Default Persona | M2 | `test_persona_*` (5) | `test_default_persona_*` (5) | Test 3.13 | Scenario 1 | Complete |
| 9 | Atomic Storage Helper `storage.py` | M2 | `test_storage_*` (5) | `test_storage_*` (5) | Test 3.9 | Scenario 5 | Complete |
| 10 | Memory Engine `extract_and_triage` | M3 | `test_extract_and_triage_*` (5) | `test_extract_and_triage_*` (5) | Test 3.1, 3.18 | Scenario 1, 2 | Complete |
| 11 | Memory Engine Context Injection | M3 | `test_inject_context_*` (5) | `test_inject_context_*` (5) | Test 3.6, 3.19 | Scenario 1, 2 | Complete |
| 12 | Persona Compiler `compile_wiki_to_card` | M3 | `test_compile_wiki_to_card_*` (5) | `test_compile_wiki_to_card_*` (5) | Test 3.3, 3.11 | Scenario 3 | Complete |
| 13 | Endpoint `POST /api/chat/stream` | M3 | `test_chat_stream_*` (5) | `test_chat_stream_*` (5) | Test 3.1, 3.2, 3.14 | Scenario 1, 3, 4 | Complete |
| 14 | Endpoint `GET /api/state` | M3 | `test_get_state_*` (5) | `test_get_state_*` (5) | Test 3.4, 3.7, 3.16 | Scenario 1, 4, 5 | Complete |
| 15 | Endpoint `POST /api/personas/compile` | M3 | `test_personas_compile_*` (5) | `test_personas_compile_*` (5) | Test 3.3, 3.15 | Scenario 3 | Complete |
| 16 | Endpoint `PATCH /api/reminders/{id}` | M3 | `test_patch_reminder_*` (5) | `test_patch_reminder_*` (5) | Test 3.4, 3.9 | Scenario 1, 5 | Complete |
| 17 | Static File Mounting | M3 | `test_static_mount_*` (5) | `test_static_mount_*` (5) | Test 3.8 | Scenario 1 | Complete |
| 18 | Frontend Dark-Mode Theme | M4 | `test_frontend_*` (5) | `test_frontend_*` (5) | Test 3.8 | Scenario 1 | Complete |
| 19 | Frontend Header Controls | M4 | `test_header_*` (5) | `test_header_*` (5) | Test 3.7, 3.12 | Scenario 1, 4 | Complete |
| 20 | Frontend Sidebar: Assistant Mode | M4 | `test_sidebar_assistant_*` (5) | `test_sidebar_*` (5) | Test 3.4 | Scenario 1 | Complete |
| 21 | Frontend Sidebar: Persona Mode | M4 | `test_sidebar_persona_*` (5) | `test_sidebar_*` (5) | Test 3.3, 3.15 | Scenario 3 | Complete |
| 22 | Frontend Memory Inspector | M4 | `test_memory_inspector_*` (5) | `test_memory_inspector_*` (5) | Test 3.12 | Scenario 1, 4 | Complete |
| 23 | Frontend Chat Interface | M4 | `test_chat_interface_*` (5) | `test_chat_interface_*` (5) | Test 3.14 | Scenario 1, 3 | Complete |
| 24 | Incognito Mode UI & Logic | M4 | `test_incognito_*` (5) | `test_incognito_*` (5) | Test 3.2, 3.12 | Scenario 4 | Complete |
| 25 | Engine Verification Test | M5 | `test_engine_test_*` (5) | `test_engine_test_*` (5) | Test 3.20 | Scenario 1 | Complete |
| 26 | Memory Triage Test | M5 | `test_memory_triage_test_*` (5) | `test_memory_triage_test_*` (5) | Test 3.1 | Scenario 2 | Complete |
| 27 | E2E Test Suite (Tiers 1-4) | Final | `test_e2e_runner_*` (5) | `test_e2e_runner_*` (5) | All Tiers | All Scenarios | Complete |
| 28 | Adversarial Coverage Hardening | Final | `test_adversarial_*` (5) | `test_adversarial_*` (5) | Test 3.9, 3.16 | Scenario 4, 5 | Complete |

---

## 5. Acceptance Criteria Traceability Matrix

| AC # | Acceptance Criterion | Test Verification |
|------|----------------------|-------------------|
| **AC-1** | `start_engines.ps1` runs without errors and both `ollama create` commands succeed | `TestFeature03StartEnginesPs1`, `TestFeature03StartEnginesPs1Boundaries` |
| **AC-2** | Both ports respond with HTTP 200 within 30 s of launch | `TestFeature04EngineLifecycleIsolation`, `TestFeature25EngineVerificationTest` |
| **AC-3** | `POST /api/chat/stream` returns `text/event-stream` with real tokens from `janus-chat` | `TestFeature13EndpointPostChatStream`, `TestTier3Interactions.test_interaction_01_*` |
| **AC-4** | After a chat turn (incognito off), `reminders.json` or `work_context.json` is updated within 10 s | `TestFeature10MemoryEngineExtractAndTriage`, `TestTier4Scenarios.test_scenario_01_*` |
| **AC-5** | `POST /api/personas/compile` saves a valid JSON file under `data/personas/` | `TestFeature15EndpointPostPersonasCompile`, `TestTier4Scenarios.test_scenario_03_*` |
| **AC-6** | `GET /api/state` returns a valid JSON object with `reminders`, `work_notes`, `facts`, and `personas` keys | `TestFeature14EndpointGetState`, `TestTier4Scenarios.test_scenario_01_*` |
| **AC-7** | SPA loads without console errors and displays correct dark-mode styling (`#0c0d10`, `#14171f`, `#10b981`) | `TestFeature17StaticFileMounting`, `TestFeature18FrontendDarkModeTheme` |
| **AC-8** | Incognito toggle visually changes UI tint and suppresses triage calls | `TestFeature24IncognitoModeUiAndLogic`, `TestTier4Scenarios.test_scenario_04_*` |
| **AC-9** | Mode toggle switches sidebar between reminder list and persona grid | `TestFeature19FrontendHeaderControls`, `TestFeature21FrontendSidebarPersonaMode` |
| **AC-10** | Chat streams token-by-token with visible SSE updates and auto-scrolls | `TestFeature23FrontendChatInterface`, `TestTier3Interactions.test_interaction_14_*` |
| **AC-11** | `pytest tests/test_engines.py` passes (with both engines running) | `TestFeature25EngineVerificationTest` |
| **AC-12** | `pytest tests/test_memory_triage.py` passes and `reminders.json` diff is non-empty | `TestFeature26MemoryTriageTest` |

---

## 6. Architecture & Integration Readiness

The E2E test harness is completely configured, verified, and placed on standby.
As milestones M1, M2, M3, M4, and M5 are built by implementing agents:
1. Run `python e2e_tests/runner.py --tier 1` to verify feature compliance.
2. Run `python e2e_tests/runner.py --tier 2` to verify robustness and boundary resilience.
3. Run `python e2e_tests/runner.py --tier 3` to verify integration contracts.
4. Run `python e2e_tests/runner.py --tier 4` to verify end-to-end user workflows.
5. Publish test artifacts to `test_results.json` upon Phase 3 integration completion.
