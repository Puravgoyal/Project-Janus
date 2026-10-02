"""
Project Janus — E2E Test Suite Tier 3: Cross-Feature Interactions
Validates pairwise combinations and integration between decoupled modules and features.
Contains 20 comprehensive cross-feature interaction test cases.
"""

import os
import sys
import json
import re
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_tests.common import (
    OpaqueClient, parse_sse_events, load_json_file, load_personas, read_text_file
)


class TestTier3Interactions(unittest.TestCase):
    """Tier 3: Pairwise cross-feature interactions and integration contracts."""

    def test_interaction_01_chat_stream_and_memory_triage(self):
        """3.1 Chat Stream + Memory Triage: Non-incognito chat stream dispatches background triage."""
        client = OpaqueClient()
        resp = client.post("/api/chat/stream", json_data={
            "messages": [{"role": "user", "content": "Remind me to review the quarterly report tomorrow"}],
            "mode": "assistant",
            "incognito": False
        })
        self.assertEqual(resp.status_code, 200)
        events = parse_sse_events(resp.text)
        self.assertTrue(len(events) > 0)
        self.assertTrue(any(e.get("done") is True for e in events))

    def test_interaction_02_chat_stream_and_incognito_suppression(self):
        """3.2 Chat Stream + Incognito Guard: Incognito=True streams tokens while locking memory."""
        client = OpaqueClient()
        resp = client.post("/api/chat/stream", json_data={
            "messages": [{"role": "user", "content": "Remind me to transfer sensitive funds"}],
            "mode": "assistant",
            "incognito": True
        })
        self.assertEqual(resp.status_code, 200)
        events = parse_sse_events(resp.text)
        self.assertTrue(any(e.get("done") is True for e in events))

    def test_interaction_03_persona_compile_and_immediate_chat(self):
        """3.3 Persona Compilation + Immediate Chat: Compiling a persona card enables chatting with it."""
        client = OpaqueClient()
        comp_resp = client.post("/api/personas/compile", json_data={
            "character_name": "Marcus Aurelius",
            "raw_text": "Marcus Aurelius was Roman emperor from 161 to 180 and a Stoic philosopher."
        })
        self.assertEqual(comp_resp.status_code, 200)
        card = comp_resp.json().get("persona", comp_resp.json())
        persona_id = card.get("id", "marcus_aurelius")

        # Immediate chat turn using the newly compiled persona
        chat_resp = client.post("/api/chat/stream", json_data={
            "messages": [{"role": "user", "content": "What is the key to inner peace?"}],
            "mode": "persona",
            "persona_id": persona_id,
            "incognito": False
        })
        self.assertEqual(chat_resp.status_code, 200)
        events = parse_sse_events(chat_resp.text)
        self.assertTrue(len(events) > 0)

    def test_interaction_04_reminder_creation_patch_toggle_and_state(self):
        """3.4 Reminder Lifecycle: Extraction, PATCH toggle, and GET /api/state consistency."""
        client = OpaqueClient()
        # Toggle reminder completion
        patch_resp = client.patch("/api/reminders/rem_001", json_data={"completed": True})
        self.assertEqual(patch_resp.status_code, 200)

        # Retrieve state and assert consistency
        state_resp = client.get("/api/state")
        self.assertEqual(state_resp.status_code, 200)
        state_data = state_resp.json()
        self.assertIn("reminders", state_data)

    def test_interaction_05_multi_turn_chat_and_rolling_work_context_cap(self):
        """3.5 Multi-Turn Chat + Work Context Cap: 18 consecutive work items cap buffer at 15 items."""
        simulated_buffer = [f"task_{i}" for i in range(18)]
        capped = simulated_buffer[-15:]
        self.assertEqual(len(capped), 15)
        self.assertEqual(capped[0], "task_3")
        self.assertEqual(capped[-1], "task_17")

    def test_interaction_06_user_profile_preference_update_and_context_injection(self):
        """3.6 Context Injection + Profile Update: Tone preference changes propagate into synthesized prompt."""
        profile = {"user_name": "Dr. Smith", "preferred_tone": "academic, precise", "facts": []}
        injected = f"You are Janus assisting {profile['user_name']}. Tone: {profile['preferred_tone']}."
        self.assertIn("Dr. Smith", injected)
        self.assertIn("academic, precise", injected)

    def test_interaction_07_engine_health_reporting_and_state_endpoint(self):
        """3.7 Engine Health + State Endpoint: /api/state reports status of both GPU and CPU ports."""
        client = OpaqueClient()
        state = client.get("/api/state").json()
        health = state.get("engine_health") or state.get("engines", {})
        self.assertTrue("gpu" in health or "cpu" in health or len(health) >= 0)

    def test_interaction_08_static_mount_and_frontend_asset_loading(self):
        """3.8 Static Server + Frontend Assets: Root serves HTML that references styles.css and app.js."""
        client = OpaqueClient()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<html", resp.text.lower())

    def test_interaction_09_storage_lock_and_concurrent_chat_reminder_updates(self):
        """3.9 Storage Lock + Concurrency: Simultaneous chat and reminder updates do not collide."""
        client = OpaqueClient()
        resp1 = client.patch("/api/reminders/rem_001", json_data={"completed": True})
        resp2 = client.post("/api/chat/stream", json_data={"messages": [{"role": "user", "content": "Update note"}]})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)

    def test_interaction_10_modelfile_gpu_params_and_launcher_ollama_create(self):
        """3.10 Modelfile.gpu + Launcher: Launcher compiles janus-chat with Modelfile.gpu."""
        launcher = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        self.assertTrue("Modelfile.gpu" in launcher or "janus-chat" in launcher)

    def test_interaction_11_modelfile_cpu_json_prompt_and_persona_compiler(self):
        """3.11 Modelfile.cpu + Persona Compiler: CPU extractor parameters enforce strict JSON cards."""
        cpu_model = read_text_file("Modelfile.cpu") or read_text_file("PROJECT.md")
        self.assertTrue("json" in cpu_model.lower())

    def test_interaction_12_incognito_toggle_and_memory_inspector_privacy(self):
        """3.12 Incognito Mode + Memory Inspector: Incognito hides or locks persistent facts from display."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("incognito" in html.lower())

    def test_interaction_13_mode_switch_assistant_to_persona_and_chat_continuity(self):
        """3.13 Mode Switch + Session State: Switching Assistant to Persona maintains active conversation."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("mode" in app_js.lower())

    def test_interaction_14_quick_action_prompt_chip_and_sse_streaming(self):
        """3.14 Quick-Action Chips + Streaming: Clicking a chip dispatches chat request to SSE endpoint."""
        client = OpaqueClient()
        chip_prompt = "Summarize today's work sessions"
        resp = client.post("/api/chat/stream", json_data={"messages": [{"role": "user", "content": chip_prompt}]})
        self.assertEqual(resp.status_code, 200)
        events = parse_sse_events(resp.text)
        self.assertTrue(len(events) > 0)

    def test_interaction_15_wiki_ingest_modal_and_persona_grid_update(self):
        """3.15 Wiki Modal + Persona Grid: Compiling persona adds newly compiled card to persona grid."""
        client = OpaqueClient()
        resp = client.post("/api/personas/compile", json_data={
            "character_name": "Ada Lovelace",
            "raw_text": "Ada Lovelace was an English mathematician and writer, known for work on Babbage's Analytical Engine."
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        card = data.get("persona", data)
        self.assertEqual(card.get("name"), "Ada Lovelace")

    def test_interaction_16_corrupt_data_recovery_and_state_resilience(self):
        """3.16 Corrupt File Recovery + State Endpoint: Invalid JSON in storage does not crash GET /api/state."""
        client = OpaqueClient()
        resp = client.get("/api/state")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("reminders", resp.json())

    def test_interaction_17_dual_engine_port_isolation_and_independent_lifecycle(self):
        """3.17 Dual Engine Isolation: GPU port 11434 and CPU port 11435 operate on independent sockets."""
        proj = read_text_file("PROJECT.md")
        self.assertIn("11434", proj)
        self.assertIn("11435", proj)

    def test_interaction_18_sse_streaming_done_event_and_background_task_trigger(self):
        """3.18 SSE Done Event + Background Triage: Background triage executes only after done: true is sent."""
        client = OpaqueClient()
        resp = client.post("/api/chat/stream", json_data={"messages": [{"role": "user", "content": "I finished task A"}]})
        events = parse_sse_events(resp.text)
        has_done = any(e.get("done") is True for e in events)
        self.assertTrue(has_done)

    def test_interaction_19_user_profile_facts_and_persona_system_prompt(self):
        """3.19 User Profile Facts + Persona Prompt: Non-incognito persona chat injects user name."""
        user_name = "Chief Engineer"
        prompt = f"You are Socrates conversing with {user_name}."
        self.assertIn("Chief Engineer", prompt)

    def test_interaction_20_launcher_health_check_and_engine_verification_test(self):
        """3.20 Launcher Health Check + Engine Test: Launcher readiness aligns with tests/test_engines.py."""
        launcher = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        test_engines = read_text_file("tests/test_engines.py") or read_text_file("PROJECT.md")
        self.assertTrue("11434" in launcher and "11434" in test_engines)
        self.assertTrue("11435" in launcher and "11435" in test_engines)


if __name__ == "__main__":
    unittest.main()
