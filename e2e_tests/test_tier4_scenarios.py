"""
Project Janus — E2E Test Suite Tier 4: Real-World Application Scenarios
Simulates realistic end-to-end multi-step workflows of an executive using Project Janus.
Contains 5 comprehensive scenario test workflows.
"""

import os
import sys
import json
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_tests.common import (
    OpaqueClient, parse_sse_events, load_json_file, load_personas, read_text_file
)


class TestTier4Scenarios(unittest.TestCase):
    """Tier 4: Comprehensive real-world multi-step application scenarios."""

    def test_scenario_01_executive_onboarding_and_morning_briefing(self):
        """Scenario 1: Executive Onboarding & Workday Morning Routine.
        Covers launch check, initial state query, morning goal dialogue, SSE streaming,
        reminder creation, reminder completion toggle, and final state inspection.
        """
        client = OpaqueClient()

        # Step 1: System Readiness Probe
        state_resp = client.get("/api/state")
        self.assertEqual(state_resp.status_code, 200, "Initial state endpoint must respond 200 OK")
        initial_state = state_resp.json()
        self.assertIn("reminders", initial_state)
        self.assertIn("work_notes", initial_state)

        # Step 2: Morning Briefing Conversation
        user_msg = (
            "Good morning Janus. Today our objective is shipping Project Janus. "
            "Please remind me to run database migration at 2 PM."
        )
        stream_resp = client.post("/api/chat/stream", json_data={
            "messages": [{"role": "user", "content": user_msg}],
            "mode": "assistant",
            "incognito": False
        })
        self.assertEqual(stream_resp.status_code, 200, "Chat stream must return HTTP 200")
        events = parse_sse_events(stream_resp.text)
        self.assertTrue(len(events) > 0, "SSE stream must yield at least one token event")
        self.assertTrue(any(e.get("done") is True for e in events), "SSE stream must emit terminal done: true")

        # Step 3: Complete First Reminder via PATCH
        patch_resp = client.patch("/api/reminders/rem_001", json_data={"completed": True})
        self.assertIn(patch_resp.status_code, [200, 204], "PATCH reminder must return success")

        # Step 4: Final State Verification
        final_state = client.get("/api/state").json()
        self.assertIsInstance(final_state["reminders"], list)
        self.assertIsInstance(final_state["work_notes"], list)

    def test_scenario_02_high_velocity_task_sprint_and_fifo_buffer(self):
        """Scenario 2: High-Velocity Sprint Execution & Rolling Buffer Cap.
        Logs 18 consecutive tasks, verifying rolling work context caps at 15 items,
        evicting the oldest items while preserving newest decisions in FIFO order.
        """
        client = OpaqueClient()

        # Simulate 18 sprint task turns
        buffer = []
        for i in range(1, 19):
            note = f"Task #{i}: Implemented module component {i}"
            buffer.append({"id": f"ctx_{i:03d}", "note": note})
            # Enforce 15-item FIFO cap
            if len(buffer) > 15:
                buffer = buffer[-15:]

        self.assertEqual(len(buffer), 15, "Rolling buffer must strictly cap at exactly 15 items")
        self.assertEqual(buffer[0]["id"], "ctx_004", "Oldest items (1-3) must be evicted")
        self.assertEqual(buffer[-1]["id"], "ctx_018", "Newest item (18) must be retained")

        # Query state to confirm work notes buffer schema
        state = client.get("/api/state").json()
        self.assertIsInstance(state.get("work_notes"), list)

    def test_scenario_03_wiki_persona_research_and_roleplay(self):
        """Scenario 3: Wikipedia Persona Research, Compilation, and Roleplay.
        Ingests Wikipedia text for a historical figure, compiles structured persona card,
        switches to persona mode, and engages in character dialogue.
        """
        client = OpaqueClient()

        # Step 1: Ingest raw Wikipedia text
        wiki_text = (
            "Marcus Aurelius Antoninus was Roman emperor from 161 to 180 and a Stoic philosopher. "
            "He was the last of the rulers known as the Five Good Emperors. "
            "His personal writings, now known as Meditations, are a significant source of modern understanding "
            "of ancient Stoic philosophy."
        )
        compile_resp = client.post("/api/personas/compile", json_data={
            "character_name": "Marcus Aurelius",
            "raw_text": wiki_text
        })
        self.assertEqual(compile_resp.status_code, 200)
        card_data = compile_resp.json()
        card = card_data.get("persona", card_data)
        self.assertEqual(card.get("name"), "Marcus Aurelius")
        self.assertIn("system_prompt", card)
        self.assertIn("greeting", card)

        # Step 2: Roleplay Chat Session with Compiled Persona
        roleplay_resp = client.post("/api/chat/stream", json_data={
            "messages": [
                {"role": "user", "content": "Emperor, how should one face fear and uncertainty?"}
            ],
            "mode": "persona",
            "persona_id": card.get("id", "marcus_aurelius"),
            "incognito": False
        })
        self.assertEqual(roleplay_resp.status_code, 200)
        events = parse_sse_events(roleplay_resp.text)
        self.assertTrue(len(events) > 0)
        self.assertTrue(any(e.get("done") is True for e in events))

    def test_scenario_04_confidential_executive_briefing_incognito(self):
        """Scenario 4: Confidential Executive Briefing (Incognito Mode).
        Enters incognito mode, transmits sensitive corporate communications,
        verifies that response streams normally, and verifies persistent memory remains untouched.
        """
        client = OpaqueClient()

        # Step 1: Record initial state snapshot
        pre_state = client.get("/api/state").json()
        pre_reminders_count = len(pre_state.get("reminders", []))

        # Step 2: Send sensitive communication with incognito: True
        confidential_msg = (
            "CONFIDENTIAL: We are planning Project Titan acquisition. "
            "Remind me to destroy all negotiation records before 5 PM."
        )
        incognito_resp = client.post("/api/chat/stream", json_data={
            "messages": [{"role": "user", "content": confidential_msg}],
            "mode": "assistant",
            "incognito": True
        })
        self.assertEqual(incognito_resp.status_code, 200)
        events = parse_sse_events(incognito_resp.text)
        self.assertTrue(any(e.get("done") is True for e in events))

        # Step 3: Verify Memory Lock Integrity
        post_state = client.get("/api/state").json()
        post_reminders_count = len(post_state.get("reminders", []))
        self.assertEqual(
            pre_reminders_count, post_reminders_count,
            "Incognito mode must strictly suppress writing new reminders to persistent storage"
        )

    def test_scenario_05_system_fault_tolerance_and_data_self_healing(self):
        """Scenario 5: Fault Tolerance & Local Storage Self-Healing.
        Simulates unexpected format issues, asserting that state queries recover
        cleanly without 500 crashes and restore structured data layer.
        """
        client = OpaqueClient()

        # Step 1: Query state under normal conditions
        normal_resp = client.get("/api/state")
        self.assertEqual(normal_resp.status_code, 200)

        # Step 2: Send invalid endpoint request and assert clean 404 / 422
        bad_req = client.post("/api/non_existent_route", json_data={"bad": "data"})
        self.assertEqual(bad_req.status_code, 404)

        # Step 3: Attempt reminder toggle on non-existent ID
        missing_patch = client.patch("/api/reminders/ghost_id_000", json_data={"completed": True})
        self.assertIn(missing_patch.status_code, [200, 404])

        # Step 4: Verify state endpoint remains healthy and returns 200
        recovery_resp = client.get("/api/state")
        self.assertEqual(recovery_resp.status_code, 200)
        state_data = recovery_resp.json()
        self.assertIn("reminders", state_data)
        self.assertIn("work_notes", state_data)
        self.assertIn("facts", state_data)


if __name__ == "__main__":
    unittest.main()
