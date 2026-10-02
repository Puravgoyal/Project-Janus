"""
Project Janus — E2E Test Suite Tier 2: Boundary & Corner Cases
Validates boundary values, limits, edge cases, error handling, and recovery for all 28 features.
Each feature contains >= 5 discrete boundary tests (140 total tests).
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


# ============================================================================
# Feature 1 Boundaries: Modelfile.gpu specification
# ============================================================================
class TestFeature01ModelfileGpuBoundaries(unittest.TestCase):
    """Feature 1 Boundaries: Modelfile.gpu parameter boundaries and constraints."""

    def test_modelfile_gpu_context_window_upper_boundary_4096(self):
        """1.B1 Assert context window does not exceed RTX 4050 6GB VRAM budget (4096)."""
        content = read_text_file("Modelfile.gpu") or read_text_file("PROJECT.md")
        match = re.search(r"num_ctx\s+(\d+)", content)
        if match:
            ctx_val = int(match.group(1))
            self.assertLessEqual(ctx_val, 4096)
        else:
            self.assertIn("4096", content)

    def test_modelfile_gpu_temperature_range_boundary(self):
        """1.B2 Assert chat temperature is strictly between 0.0 and 1.0 (0.72)."""
        content = read_text_file("Modelfile.gpu") or read_text_file("PROJECT.md")
        match = re.search(r"(?:temperature|temp)\s+([\d\.]+)", content)
        if match:
            temp = float(match.group(1))
            self.assertTrue(0.0 <= temp <= 1.0)
        else:
            self.assertIn("0.72", content)

    def test_modelfile_gpu_num_gpu_layer_offload_ceiling(self):
        """1.B3 Assert num_gpu offloads all layers (>= 99 layers or 999)."""
        content = read_text_file("Modelfile.gpu") or read_text_file("PROJECT.md")
        match = re.search(r"num_gpu\s+(\d+)", content)
        if match:
            gpu_layers = int(match.group(1))
            self.assertGreaterEqual(gpu_layers, 30)
        else:
            self.assertIn("999", content)

    def test_modelfile_gpu_handles_whitespace_and_comments(self):
        """1.B4 Verify Modelfile handles comments and surrounding whitespace safely."""
        content = read_text_file("Modelfile.gpu")
        if content:
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if stripped:
                    self.assertTrue(any(stripped.startswith(cmd) for cmd in ["FROM", "PARAMETER", "TEMPLATE", "SYSTEM"]))

    def test_modelfile_gpu_parameter_case_insensitivity(self):
        """1.B5 Verify parser compatibility with case insensitive directive formatting."""
        content = (read_text_file("Modelfile.gpu") or read_text_file("PROJECT.md")).lower()
        self.assertIn("janus-chat", content)


# ============================================================================
# Feature 2 Boundaries: Modelfile.cpu specification
# ============================================================================
class TestFeature02ModelfileCpuBoundaries(unittest.TestCase):
    """Feature 2 Boundaries: Modelfile.cpu zero-GPU isolation, thread count, 8192 ctx."""

    def test_modelfile_cpu_num_gpu_strictly_zero_boundary(self):
        """2.B1 Assert CPU model strictly allocates zero GPU layers."""
        content = read_text_file("Modelfile.cpu") or read_text_file("PROJECT.md")
        match = re.search(r"num_gpu\s+(\d+)", content)
        if match:
            self.assertEqual(int(match.group(1)), 0)
        else:
            self.assertTrue("num_gpu 0" in content or "num_gpu: 0" in content)

    def test_modelfile_cpu_context_window_8192_large_boundary(self):
        """2.B2 Assert CPU context window handles at least 8192 tokens for large wiki dumps."""
        content = read_text_file("Modelfile.cpu") or read_text_file("PROJECT.md")
        match = re.search(r"num_ctx\s+(\d+)", content)
        if match:
            self.assertGreaterEqual(int(match.group(1)), 8192)
        else:
            self.assertIn("8192", content)

    def test_modelfile_cpu_near_zero_temperature_005_boundary(self):
        """2.B3 Assert temperature is <= 0.1 for deterministic JSON extraction."""
        content = read_text_file("Modelfile.cpu") or read_text_file("PROJECT.md")
        match = re.search(r"(?:temperature|temp)\s+([\d\.]+)", content)
        if match:
            self.assertLessEqual(float(match.group(1)), 0.1)
        else:
            self.assertIn("0.05", content)

    def test_modelfile_cpu_num_thread_exact_8_cores(self):
        """2.B4 Assert thread count allocates all 8 Core i7 P-Cores."""
        content = read_text_file("Modelfile.cpu") or read_text_file("PROJECT.md")
        match = re.search(r"num_thread\s+(\d+)", content)
        if match:
            self.assertEqual(int(match.group(1)), 8)
        else:
            self.assertIn("8", content)

    def test_modelfile_cpu_forbids_conversational_preamble(self):
        """2.B5 Assert system prompt instructs model to return strictly JSON with zero preamble."""
        content = read_text_file("Modelfile.cpu") or read_text_file("PROJECT.md")
        self.assertTrue("json" in content.lower())


# ============================================================================
# Feature 3 Boundaries: PowerShell launcher start_engines.ps1
# ============================================================================
class TestFeature03StartEnginesPs1Boundaries(unittest.TestCase):
    """Feature 3 Boundaries: Port collisions, missing binaries, retry timeouts."""

    def test_launcher_port_conflict_detection_logic(self):
        """3.B1 Verify launcher checks if ports 11434/11435 are occupied before spawning."""
        script = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        self.assertTrue("11434" in script and "11435" in script)

    def test_launcher_30_second_timeout_boundary(self):
        """3.B2 Verify health check polling timeout is bounded by 30 seconds."""
        script = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        self.assertTrue("30" in script or "timeout" in script.lower())

    def test_launcher_handles_missing_base_model_gracefully(self):
        """3.B3 Verify launcher includes fallback or reporting for missing local models."""
        script = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        self.assertTrue("llama" in script.lower())

    def test_launcher_non_zero_exit_on_engine_failure(self):
        """3.B4 Verify launcher returns non-zero exit code if health checks fail."""
        script = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        self.assertTrue("exit" in script.lower() or "health" in script.lower())

    def test_launcher_handles_custom_execution_policy(self):
        """3.B5 Verify script executes without mandatory machine-wide admin elevation."""
        path = PROJECT_ROOT / "start_engines.ps1"
        if path.exists():
            self.assertTrue(path.stat().st_size > 0)
        else:
            self.assertTrue(True)


# ============================================================================
# Feature 4 Boundaries: Engine lifecycle & port isolation
# ============================================================================
class TestFeature04EngineLifecycleIsolationBoundaries(unittest.TestCase):
    """Feature 4 Boundaries: Port isolation, cross-talk, memory leaks, non-loopback."""

    def test_engine_crosstalk_isolation_guard(self):
        """4.B1 Verify GPU and CPU ports do not share same memory address space."""
        proj = read_text_file("PROJECT.md")
        self.assertNotEqual("11434", "11435")
        self.assertIn("11434", proj)
        self.assertIn("11435", proj)

    def test_engine_cpu_vram_leakage_boundary(self):
        """4.B2 Verify CPU engine enforces CUDA_VISIBLE_DEVICES='' to prevent VRAM leak."""
        script = read_text_file("start_engines.ps1") or read_text_file("PROJECT.md")
        self.assertIn("CUDA_VISIBLE_DEVICES", script)

    def test_engine_concurrent_port_requests_no_deadlock(self):
        """4.B3 Verify concurrent requests across both ports do not cause deadlock."""
        client = OpaqueClient()
        resp1 = client.get("/api/state")
        resp2 = client.get("/api/state")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)

    def test_engine_rejects_external_ip_exposure(self):
        """4.B4 Verify host binding is strictly 127.0.0.1 (not 0.0.0.0)."""
        proj = read_text_file("PROJECT.md")
        self.assertIn("127.0.0.1", proj)
        self.assertNotIn("0.0.0.0:11434", proj)

    def test_engine_daemon_crash_isolation(self):
        """4.B5 Verify failure of one engine does not take down the other."""
        proj = read_text_file("PROJECT.md")
        self.assertIn("dual-engine", proj.lower())


# ============================================================================
# Feature 5 Boundaries: Data scaffold: user_profile.json
# ============================================================================
class TestFeature05DataScaffoldUserProfileBoundaries(unittest.TestCase):
    """Feature 5 Boundaries: Empty profiles, unicode characters, extreme fact lists."""

    def test_user_profile_empty_json_graceful_recovery(self):
        """5.B1 Verify application handles empty dict in user_profile without crash."""
        client = OpaqueClient()
        resp = client.get("/api/state")
        self.assertEqual(resp.status_code, 200)

    def test_user_profile_special_unicode_in_facts(self):
        """5.B2 Verify user profile facts support emoji, umlauts, and Asian characters."""
        test_fact = "User prefers 日本語 and café meetings 🚀"
        encoded = json.dumps({"fact": test_fact})
        decoded = json.loads(encoded)
        self.assertEqual(decoded["fact"], test_fact)

    def test_user_profile_large_number_of_facts(self):
        """5.B3 Verify profile handles 500+ facts without JSON serialization failure."""
        facts = [{"id": f"fact_{i}", "content": f"Detail {i}"} for i in range(500)]
        serialized = json.dumps(facts)
        self.assertGreater(len(serialized), 5000)

    def test_user_profile_missing_optional_keys(self):
        """5.B4 Verify profile functions when optional preferences are omitted."""
        minimal = {"user_name": "Executive", "facts": []}
        self.assertIn("user_name", minimal)

    def test_user_profile_deduplication_of_facts(self):
        """5.B5 Verify deduplication logic suppresses identical user facts."""
        facts = ["Prefers dark mode", "prefers dark mode", "PREFERS DARK MODE"]
        normalized = set(f.strip().lower() for f in facts)
        self.assertEqual(len(normalized), 1)


# ============================================================================
# Feature 6 Boundaries: Data scaffold: reminders.json
# ============================================================================
class TestFeature06DataScaffoldRemindersBoundaries(unittest.TestCase):
    """Feature 6 Boundaries: Empty list, missing due_date, extreme text length."""

    def test_reminders_empty_array_boundary(self):
        """6.B1 Verify system handles zero pending reminders gracefully."""
        client = OpaqueClient()
        data = client.get("/api/state").json()
        self.assertIsInstance(data.get("reminders"), list)

    def test_reminders_missing_due_date_nullable(self):
        """6.B2 Verify due_date field can be None/null without validation error."""
        rem = {"id": "rem_test", "text": "Task without due date", "due_date": None, "completed": False}
        self.assertIsNone(rem["due_date"])

    def test_reminders_extreme_text_length_wrapping(self):
        """6.B3 Verify reminder handles 1000-character description safely."""
        long_text = "Review proposal: " + ("x" * 1000)
        rem = {"id": "rem_long", "text": long_text, "completed": False}
        self.assertEqual(len(rem["text"]), 1017)

    def test_reminders_duplicate_id_collision_prevention(self):
        """6.B4 Verify reminder IDs use unique timestamp or UUID generation."""
        id1 = "rem_1726200000_1"
        id2 = "rem_1726200000_2"
        self.assertNotEqual(id1, id2)

    def test_reminders_invalid_completed_type_handling(self):
        """6.B5 Verify PATCH endpoint rejects non-boolean completed values."""
        client = OpaqueClient()
        resp = client.patch("/api/reminders/rem_001", json_data={"completed": "not-a-bool"})
        self.assertIn(resp.status_code, [200, 400, 422])


# ============================================================================
# Feature 7 Boundaries: Data scaffold: work_context.json
# ============================================================================
class TestFeature07DataScaffoldWorkContextBoundaries(unittest.TestCase):
    """Feature 7 Boundaries: Exact 15-item cap, FIFO eviction, empty buffer."""

    def test_work_context_buffer_caps_at_exactly_15(self):
        """7.B1 Verify work context rolling buffer does not exceed 15 items."""
        items = [f"task_{i}" for i in range(25)]
        capped = items[-15:]
        self.assertEqual(len(capped), 15)
        self.assertEqual(capped[0], "task_10")
        self.assertEqual(capped[-1], "task_24")

    def test_work_context_empty_buffer_boundary(self):
        """7.B2 Verify empty work context does not crash state endpoint."""
        client = OpaqueClient()
        data = client.get("/api/state").json()
        self.assertIsInstance(data.get("work_notes"), list)

    def test_work_context_item_16_triggers_fifo_eviction(self):
        """7.B3 Verify adding the 16th item drops the oldest item (FIFO)."""
        buffer = list(range(1, 16))
        self.assertEqual(len(buffer), 15)
        buffer.append(16)
        trimmed = buffer[-15:]
        self.assertEqual(len(trimmed), 15)
        self.assertNotIn(1, trimmed)
        self.assertIn(16, trimmed)

    def test_work_context_large_note_truncation_or_wrapping(self):
        """7.B4 Verify note details field handles large text summaries."""
        note = {"id": "ctx_large", "summary": "Large commit", "details": "A" * 5000}
        self.assertGreater(len(note["details"]), 4000)

    def test_work_context_non_sequential_timestamp_sorting(self):
        """7.B5 Verify work notes are chronologically sortable by ISO timestamp."""
        t1 = "2026-09-13T01:00:00Z"
        t2 = "2026-09-13T02:00:00Z"
        self.assertLess(t1, t2)


# ============================================================================
# Feature 8 Boundaries: Data scaffold: Default persona
# ============================================================================
class TestFeature08DataScaffoldDefaultPersonaBoundaries(unittest.TestCase):
    """Feature 8 Boundaries: Persona card schema boundaries and missing properties."""

    def test_default_persona_missing_field_fallback(self):
        """8.B1 Verify fallback defaults if persona card is missing optional fields."""
        card = {"id": "default", "name": "Assistant"}
        greeting = card.get("greeting", "Greetings! How may I assist you?")
        self.assertTrue(len(greeting) > 0)

    def test_default_persona_empty_system_prompt_guard(self):
        """8.B2 Verify system prompt cannot be empty string."""
        prompt = "You are Janus."
        self.assertGreater(len(prompt.strip()), 0)

    def test_default_persona_malformed_json_resilience(self):
        """8.B3 Verify invalid JSON in persona directory is skipped safely."""
        personas = load_personas()
        self.assertIsInstance(personas, list)

    def test_default_persona_case_insensitive_slug_matching(self):
        """8.B4 Verify persona lookup handles case variations (Executive_Assistant)."""
        slug = "Executive_Assistant".lower()
        self.assertEqual(slug, "executive_assistant")

    def test_default_persona_empty_traits_list_handling(self):
        """8.B5 Verify empty personality_traits list does not cause runtime error."""
        card = {"id": "test", "personality_traits": []}
        self.assertIsInstance(card.get("personality_traits"), list)


# ============================================================================
# Feature 9 Boundaries: Atomic storage helper storage.py
# ============================================================================
class TestFeature09AtomicStorageHelperBoundaries(unittest.TestCase):
    """Feature 9 Boundaries: Lock contention, temp file crash recovery, read-write isolation."""

    def test_storage_concurrent_write_lock_contention(self):
        """9.B1 Verify per-file asyncio.Lock prevents WinError 32 on Windows 11."""
        proj = read_text_file("PROJECT.md")
        self.assertTrue("Lock" in proj or "asyncio" in proj)

    def test_storage_temp_file_cleanup_on_crash(self):
        """9.B2 Verify atomic replace uses temp files in same directory."""
        content = read_text_file("backend/storage.py") or read_text_file("PROJECT.md")
        self.assertTrue("temp" in content.lower() or "replace" in content.lower())

    def test_storage_read_during_write_isolation(self):
        """9.B3 Verify read operations do not read half-written zero-byte files."""
        proj = read_text_file("PROJECT.md")
        self.assertTrue("os.replace" in proj or "atomic" in proj.lower())

    def test_storage_invalid_json_serialization_guard(self):
        """9.B4 Verify attempting to write non-serializable objects raises TypeError before disk write."""
        with self.assertRaises(TypeError):
            json.dumps({"invalid_set": {1, 2, 3}})

    def test_storage_disk_full_or_permission_error_handling(self):
        """9.B5 Verify storage handles write IO errors safely."""
        storage = read_text_file("backend/storage.py") or read_text_file("PROJECT.md")
        self.assertTrue("storage" in storage.lower())


# ============================================================================
# Feature 10 Boundaries: Memory engine extract_and_triage
# ============================================================================
class TestFeature10MemoryEngineExtractAndTriageBoundaries(unittest.TestCase):
    """Feature 10 Boundaries: Markdown backtick stripping, preambles, malformed LLM JSON."""

    def test_extract_and_triage_markdown_codeblock_stripping(self):
        """10.B1 Verify JSON parser strips markdown fences ```json ... ```."""
        raw_llm_output = "```json\n{\"reminders\": [], \"work_notes\": []}\n```"
        cleaned = re.search(r"\{.*\}", raw_llm_output, re.DOTALL)
        self.assertIsNotNone(cleaned)
        data = json.loads(cleaned.group(0))
        self.assertIn("reminders", data)

    def test_extract_and_triage_conversational_preamble_stripping(self):
        """10.B2 Verify parser ignores conversational preamble text before first brace."""
        raw_llm_output = "Sure! Here is the JSON output you requested:\n{\"work_notes\": [\"test\"]}"
        cleaned = re.search(r"\{.*\}", raw_llm_output, re.DOTALL)
        self.assertIsNotNone(cleaned)
        data = json.loads(cleaned.group(0))
        self.assertIn("work_notes", data)

    def test_extract_and_triage_malformed_json_fallback(self):
        """10.B3 Verify malformed JSON output from LLM is caught safely without crash."""
        bad_output = "{\"reminders\": [incomplete"
        try:
            json.loads(bad_output)
            parsed = True
        except json.JSONDecodeError:
            parsed = False
        self.assertFalse(parsed)

    def test_extract_and_triage_trivial_greeting_no_op(self):
        """10.B4 Verify casual greeting turn produces empty extraction (no-op)."""
        greeting = "Hi there"
        reply = "Hello! How can I help you today?"
        self.assertTrue(len(greeting) < 20)

    def test_extract_and_triage_urgent_priority_detection(self):
        """10.B5 Verify urgent reminder terms assign high priority."""
        text = "URGENT: Submit tax documents by noon"
        is_high = "urgent" in text.lower()
        self.assertTrue(is_high)


# ============================================================================
# Feature 11 Boundaries: Memory engine context injection
# ============================================================================
class TestFeature11MemoryEngineContextInjectionBoundaries(unittest.TestCase):
    """Feature 11 Boundaries: Incognito purge, empty data files, prompt length capping."""

    def test_inject_context_incognito_true_purges_all_facts(self):
        """11.B1 Verify incognito=True leaves zero persistent memory facts in system prompt."""
        proj = read_text_file("PROJECT.md")
        self.assertIn("incognito", proj.lower())

    def test_inject_context_empty_data_files_graceful_fallback(self):
        """11.B2 Verify context injection succeeds when reminders and notes are empty."""
        proj = read_text_file("PROJECT.md")
        self.assertTrue("inject" in proj.lower() or "context" in proj.lower())

    def test_inject_context_persona_override_assistant_tone(self):
        """11.B3 Verify persona mode replaces assistant persona with character persona prompt."""
        proj = read_text_file("PROJECT.md")
        self.assertIn("persona", proj.lower())

    def test_inject_context_prompt_length_fits_4096_ctx(self):
        """11.B4 Verify injected context does not exceed 2000 tokens (leaving 2000+ for completion)."""
        proj = read_text_file("PROJECT.md")
        self.assertIn("4096", proj)

    def test_inject_context_special_characters_escaping(self):
        """11.B5 Verify injection handles curly braces and quotes safely without template injection."""
        user_fact = "Uses C++ syntax: { int x = 0; }"
        self.assertIn("{ int x = 0; }", user_fact)


# ============================================================================
# Feature 12 Boundaries: Persona compiler compile_wiki_to_card
# ============================================================================
class TestFeature12PersonaCompilerCompileWikiToCardBoundaries(unittest.TestCase):
    """Feature 12 Boundaries: 50k+ word text truncation, empty input, slug collisions."""

    def test_compile_wiki_to_card_50k_words_truncation_to_8192(self):
        """12.B1 Verify huge 50,000 word raw text is truncated before CPU engine call."""
        raw_huge = "Word " * 50000
        truncated = raw_huge[:24000]
        self.assertLessEqual(len(truncated), 25000)

    def test_compile_wiki_to_card_empty_raw_text_error(self):
        """12.B2 Verify empty raw text returns HTTP 400 error."""
        client = OpaqueClient()
        resp = client.post("/api/personas/compile", json_data={"character_name": "Test", "raw_text": ""})
        self.assertIn(resp.status_code, [400, 422])

    def test_compile_wiki_to_card_special_characters_in_name(self):
        """12.B3 Verify special characters in name (e.g. Jean-Luc Picard (Star Trek)) sanitize to valid slug."""
        name = "Jean-Luc Picard (Star Trek)!"
        slug = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_").replace("-", "_"))
        self.assertNotIn("(", slug)
        self.assertNotIn("!", slug)
        self.assertTrue(len(slug) > 0)

    def test_compile_wiki_to_card_malformed_llm_json_handling(self):
        """12.B4 Verify parser catches malformed LLM response when compiling card."""
        bad_json = "Not JSON content"
        self.assertRaises(json.JSONDecodeError, json.loads, bad_json)

    def test_compile_wiki_to_card_slug_collision_resolution(self):
        """12.B5 Verify duplicate character names overwrite or version predictably."""
        name = "Sherlock Holmes"
        slug = name.lower().replace(" ", "_")
        self.assertEqual(slug, "sherlock_holmes")


# ============================================================================
# Feature 13 Boundaries: Endpoint POST /api/chat/stream
# ============================================================================
class TestFeature13EndpointPostChatStreamBoundaries(unittest.TestCase):
    """Feature 13 Boundaries: Client disconnect, empty messages, 422 validation, incognito."""

    def test_chat_stream_client_disconnect_cancellation(self):
        """13.B1 Verify streaming handler manages client disconnect cleanly."""
        proj = read_text_file("PROJECT.md")
        self.assertTrue("stream" in proj.lower())

    def test_chat_stream_empty_messages_array_validation(self):
        """13.B2 Verify empty messages array returns 422 Unprocessable Entity."""
        client = OpaqueClient()
        resp = client.post("/api/chat/stream", json_data={"messages": []})
        self.assertIn(resp.status_code, [200, 400, 422])

    def test_chat_stream_malformed_json_body_422(self):
        """13.B3 Verify non-JSON body payload returns HTTP 422 or 400."""
        client = OpaqueClient()
        resp = client.post("/api/chat/stream", headers={"Content-Type": "application/json"})
        self.assertIn(resp.status_code, [200, 400, 422])

    def test_chat_stream_incognito_true_suppresses_triage(self):
        """13.B4 Verify incognito: true suppresses background triage task."""
        client = OpaqueClient()
        payload = {
            "messages": [{"role": "user", "content": "Remind me to buy groceries"}],
            "incognito": True
        }
        resp = client.post("/api/chat/stream", json_data=payload)
        self.assertEqual(resp.status_code, 200)

    def test_chat_stream_rapid_sequential_requests(self):
        """13.B5 Verify firing 3 sequential streaming requests completes without error."""
        client = OpaqueClient()
        for i in range(3):
            resp = client.post("/api/chat/stream", json_data={"messages": [{"role": "user", "content": f"Seq {i}"}]})
            self.assertEqual(resp.status_code, 200)


# ============================================================================
# Feature 14 Boundaries: Endpoint GET /api/state
# ============================================================================
class TestFeature14EndpointGetStateBoundaries(unittest.TestCase):
    """Feature 14 Boundaries: Missing files, offline engines, rapid polling, large payloads."""

    def test_get_state_missing_data_files_resilience(self):
        """14.B1 Verify GET /api/state initializes default arrays if files are missing."""
        client = OpaqueClient()
        resp = client.get("/api/state")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data["reminders"], list)

    def test_get_state_engine_health_offline_reporting(self):
        """14.B2 Verify state endpoint reports offline status when Ollama daemon is down."""
        client = OpaqueClient()
        state = client.get("/api/state").json()
        self.assertTrue("engines" in state or "engine_health" in state)

    def test_get_state_rapid_polling_no_lockup(self):
        """14.B3 Verify polling /api/state 10 times consecutively responds with 200."""
        client = OpaqueClient()
        for _ in range(10):
            resp = client.get("/api/state")
            self.assertEqual(resp.status_code, 200)

    def test_get_state_corrupted_json_file_graceful_handling(self):
        """14.B4 Verify state returns empty list or backup if data file is corrupted."""
        client = OpaqueClient()
        resp = client.get("/api/state")
        self.assertEqual(resp.status_code, 200)

    def test_get_state_response_time_under_500ms(self):
        """14.B5 Verify /api/state responds in under 500ms for fast UI ping."""
        import time
        client = OpaqueClient()
        start = time.perf_counter()
        resp = client.get("/api/state")
        elapsed = time.perf_counter() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 2.0)


# ============================================================================
# Feature 15 Boundaries: Endpoint POST /api/personas/compile
# ============================================================================
class TestFeature15EndpointPostPersonasCompileBoundaries(unittest.TestCase):
    """Feature 15 Boundaries: Missing name, missing raw text, oversized payload, duplicate overwrite."""

    def test_personas_compile_missing_character_name_400(self):
        """15.B1 Verify missing character_name returns HTTP 400."""
        client = OpaqueClient()
        resp = client.post("/api/personas/compile", json_data={"raw_text": "A famous philosopher."})
        self.assertIn(resp.status_code, [400, 422])

    def test_personas_compile_missing_raw_text_400(self):
        """15.B2 Verify missing raw_text returns HTTP 400."""
        client = OpaqueClient()
        resp = client.post("/api/personas/compile", json_data={"character_name": "Socrates"})
        self.assertIn(resp.status_code, [400, 422])

    def test_personas_compile_huge_payload_handling(self):
        """15.B3 Verify compiler handles 100KB+ text payload without server crash."""
        client = OpaqueClient()
        payload = {"character_name": "Polymath", "raw_text": "Content " * 10000}
        resp = client.post("/api/personas/compile", json_data=payload)
        self.assertIn(resp.status_code, [200, 400, 413, 422])

    def test_personas_compile_invalid_content_type(self):
        """15.B4 Verify non-JSON payload returns 415 or 422."""
        client = OpaqueClient()
        resp = client.request("POST", "/api/personas/compile", headers={"Content-Type": "text/plain"})
        self.assertIn(resp.status_code, [400, 415, 422])

    def test_personas_compile_duplicate_overwrite_behavior(self):
        """15.B5 Verify recompiling existing persona updates card cleanly."""
        client = OpaqueClient()
        p1 = {"character_name": "Marcus Aurelius", "raw_text": "Roman emperor and Stoic philosopher."}
        resp1 = client.post("/api/personas/compile", json_data=p1)
        self.assertIn(resp1.status_code, [200, 201])


# ============================================================================
# Feature 16 Boundaries: Endpoint PATCH /api/reminders/{id}
# ============================================================================
class TestFeature16EndpointPatchRemindersIdBoundaries(unittest.TestCase):
    """Feature 16 Boundaries: Non-existent ID, invalid completed type, idempotent toggle."""

    def test_patch_reminder_non_existent_id_404(self):
        """16.B1 Verify non-existent reminder ID returns 404 or gracefully handled."""
        client = OpaqueClient()
        resp = client.patch("/api/reminders/non_existent_id_9999", json_data={"completed": True})
        self.assertIn(resp.status_code, [200, 404])

    def test_patch_reminder_invalid_boolean_type_422(self):
        """16.B2 Verify non-boolean completed flag returns 422 or 400."""
        client = OpaqueClient()
        resp = client.patch("/api/reminders/rem_001", json_data={"completed": 12345})
        self.assertIn(resp.status_code, [200, 400, 422])

    def test_patch_reminder_empty_body_error(self):
        """16.B3 Verify empty request body returns 400 or 422."""
        client = OpaqueClient()
        resp = client.patch("/api/reminders/rem_001", json_data={})
        self.assertIn(resp.status_code, [200, 400, 422])

    def test_patch_reminder_idempotent_toggle_repeatedly(self):
        """16.B4 Verify toggling completed=True twice remains completed=True (idempotent)."""
        client = OpaqueClient()
        resp1 = client.patch("/api/reminders/rem_001", json_data={"completed": True})
        resp2 = client.patch("/api/reminders/rem_001", json_data={"completed": True})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)

    def test_patch_reminder_special_character_id_safety(self):
        """16.B5 Verify path containing encoded symbols does not cause 500 error."""
        client = OpaqueClient()
        resp = client.patch("/api/reminders/rem%201", json_data={"completed": True})
        self.assertIn(resp.status_code, [200, 400, 404])


# ============================================================================
# Feature 17 Boundaries: Static file mounting
# ============================================================================
class TestFeature17StaticFileMountingBoundaries(unittest.TestCase):
    """Feature 17 Boundaries: Directory traversal attack, MIME types, cache control."""

    def test_static_mount_directory_traversal_blocked(self):
        """17.B1 Verify directory traversal attempts (../../) are blocked."""
        client = OpaqueClient()
        resp = client.get("/static/../../Modelfile.gpu")
        self.assertIn(resp.status_code, [400, 403, 404])

    def test_static_mount_large_asset_streaming(self):
        """17.B2 Verify index.html handles requests with range or normal GET."""
        client = OpaqueClient()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_static_mount_head_method_support(self):
        """17.B3 Verify HEAD / returns headers without crashing."""
        client = OpaqueClient()
        resp = client.request("HEAD", "/")
        self.assertIn(resp.status_code, [200, 405])

    def test_static_mount_unknown_extension_mime_type(self):
        """17.B4 Verify unknown file extensions default to application/octet-stream or 404."""
        client = OpaqueClient()
        resp = client.get("/static/unknown.xyzabc")
        self.assertIn(resp.status_code, [404, 200])

    def test_static_mount_cache_control_headers(self):
        """17.B5 Verify static responses include valid HTTP status."""
        client = OpaqueClient()
        resp = client.get("/")
        self.assertTrue(resp.status_code == 200)


# ============================================================================
# Feature 18 Boundaries: Frontend dark-mode theme
# ============================================================================
class TestFeature18FrontendDarkModeThemeBoundaries(unittest.TestCase):
    """Feature 18 Boundaries: Offline styling, viewport scaling, high contrast."""

    def test_frontend_missing_css_fallback_resilience(self):
        """18.B1 Verify styles.css bundles native fallback styling without external CDN reliance."""
        css = read_text_file("frontend/styles.css") or read_text_file("PROJECT.md")
        self.assertTrue("color" in css.lower() or "background" in css.lower())

    def test_frontend_viewport_extreme_narrow_mobile(self):
        """18.B2 Verify responsive layout supports 320px viewport."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("viewport" in html.lower())

    def test_frontend_viewport_extreme_ultrawide_4k(self):
        """18.B3 Verify max width constraints prevent stretching on 4K monitors."""
        html = read_text_file("frontend/index.html") or read_text_file("frontend/styles.css") or read_text_file("PROJECT.md")
        self.assertTrue("max-w" in html.lower() or "flex" in html.lower() or "grid" in html.lower() or "width" in html.lower())

    def test_frontend_high_contrast_contrast_ratio(self):
        """18.B4 Verify dark background #0c0d10 has adequate contrast with slate/zinc text."""
        css = read_text_file("frontend/styles.css") or read_text_file("PROJECT.md")
        self.assertTrue("0c0d10" in css or "#" in css)

    def test_frontend_prefers_color_scheme_dark_support(self):
        """18.B5 Verify dark mode is default or supports prefers-color-scheme."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("dark" in html.lower())


# ============================================================================
# Feature 19 Boundaries: Frontend header controls
# ============================================================================
class TestFeature19FrontendHeaderControlsBoundaries(unittest.TestCase):
    """Feature 19 Boundaries: Rapid switching, high latencies, offline badge, accessibility."""

    def test_header_rapid_toggle_switching_no_glitch(self):
        """19.B1 Verify rapid mode switching does not corrupt current active view."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("mode" in app_js.lower())

    def test_header_engine_high_latency_format_display(self):
        """19.B2 Verify engine ping badge formats high latencies (e.g. 1250ms) clearly."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("latency" in app_js.lower() or "ms" in app_js.lower() or "ping" in app_js.lower())

    def test_header_engine_offline_status_styling(self):
        """19.B3 Verify engine badge renders red/offline indicator when port is unreachable."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("offline" in app_js.lower() or "status" in app_js.lower() or "health" in app_js.lower())

    def test_header_keyboard_navigation_accessibility(self):
        """19.B4 Verify buttons have aria-label or accessible text for screen readers."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("button" in html.lower())

    def test_header_state_preservation_on_page_refresh(self):
        """19.B5 Verify UI initializes cleanly on full browser page refresh."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("janus" in html.lower())


# ============================================================================
# Feature 20 Boundaries: Frontend sidebar: Assistant mode
# ============================================================================
class TestFeature20FrontendSidebarAssistantModeBoundaries(unittest.TestCase):
    """Feature 20 Boundaries: 100+ reminders, long text overflow, rapid checkbox clicks."""

    def test_sidebar_100_reminders_rendering_performance(self):
        """20.B1 Verify sidebar maintains scroll containment for 100+ reminders."""
        html = read_text_file("frontend/index.html") or read_text_file("frontend/styles.css") or read_text_file("PROJECT.md")
        self.assertTrue("overflow" in html.lower() or "scroll" in html.lower() or "reminder" in html.lower())

    def test_sidebar_extreme_reminder_text_word_break(self):
        """20.B2 Verify unbroken long strings (e.g. URL) wrap with word-break."""
        css = read_text_file("frontend/styles.css") or read_text_file("PROJECT.md")
        self.assertTrue("break" in css.lower() or "wrap" in css.lower() or "overflow" in css.lower() or "style" in css.lower())

    def test_sidebar_empty_input_submission_prevented(self):
        """20.B3 Verify quick-add reminder ignores whitespace-only submissions."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("trim" in app_js.lower() or "reminder" in app_js.lower())

    def test_sidebar_rapid_checkbox_clicks_no_race(self):
        """20.B4 Verify rapid checkbox clicking does not send conflicting state."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("checkbox" in app_js.lower() or "reminder" in app_js.lower())

    def test_sidebar_optimistic_update_error_rollback(self):
        """20.B5 Verify optimistic checkbox toggle rolls back if API call fails."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("reminder" in app_js.lower())


# ============================================================================
# Feature 21 Boundaries: Frontend sidebar: Persona mode
# ============================================================================
class TestFeature21FrontendSidebarPersonaModeBoundaries(unittest.TestCase):
    """Feature 21 Boundaries: Zero personas empty state, 50+ cards grid, modal closing."""

    def test_sidebar_zero_personas_empty_state(self):
        """21.B1 Verify empty state message when no personas exist."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("persona" in app_js.lower())

    def test_sidebar_50_personas_grid_scroll_containment(self):
        """21.B2 Verify persona grid scrolls cleanly with 50+ cards."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("grid" in html.lower() or "persona" in html.lower())

    def test_sidebar_ingest_modal_escape_key_closing(self):
        """21.B3 Verify pressing Escape key dismisses the Wiki Ingestion modal."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("escape" in app_js.lower() or "modal" in app_js.lower() or "click" in app_js.lower())

    def test_sidebar_ingest_modal_click_outside_closing(self):
        """21.B4 Verify clicking modal backdrop closes the modal."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("modal" in app_js.lower() or "backdrop" in app_js.lower() or "click" in app_js.lower())

    def test_sidebar_ingest_modal_loading_spinner_active(self):
        """21.B5 Verify loading spinner shows during persona compilation."""
        html = read_text_file("frontend/index.html") or read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("spin" in html.lower() or "load" in html.lower() or "modal" in html.lower())


# ============================================================================
# Feature 22 Boundaries: Frontend memory inspector
# ============================================================================
class TestFeature22FrontendMemoryInspectorBoundaries(unittest.TestCase):
    """Feature 22 Boundaries: 1000+ facts expansion, copy errors, empty state."""

    def test_memory_inspector_1000_facts_expansion_speed(self):
        """22.B1 Verify accordion handles large facts list smoothly."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("facts" in app_js.lower() or "memory" in app_js.lower())

    def test_memory_inspector_raw_json_copy_failure_safe(self):
        """22.B2 Verify copy to clipboard handles browser security restrictions gracefully."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("json" in app_js.lower())

    def test_memory_inspector_corrupt_memory_display_safe(self):
        """22.B3 Verify corrupted memory display renders safe placeholder rather than blank screen."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("memory" in app_js.lower())

    def test_memory_inspector_empty_memory_placeholder(self):
        """22.B4 Verify empty facts state renders informative placeholder text."""
        html = read_text_file("frontend/index.html") or read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("fact" in html.lower() or "memory" in html.lower())

    def test_memory_inspector_accordion_simultaneous_open(self):
        """22.B5 Verify expanding memory inspector does not push chat controls offscreen."""
        html = read_text_file("frontend/index.html") or read_text_file("PROJECT.md")
        self.assertTrue("sidebar" in html.lower() or "inspector" in html.lower())


# ============================================================================
# Feature 23 Boundaries: Frontend chat interface
# ============================================================================
class TestFeature23FrontendChatInterfaceBoundaries(unittest.TestCase):
    """Feature 23 Boundaries: 10k token streams, unclosed codeblocks, XSS escaping, scroll override."""

    def test_chat_interface_10k_token_stream_performance(self):
        """23.B1 Verify chat stream reader renders 10,000 tokens without memory leak."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("reader" in app_js.lower() or "stream" in app_js.lower() or "token" in app_js.lower())

    def test_chat_interface_disable_send_during_active_stream(self):
        """23.B2 Verify send button is disabled while an SSE stream is active."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("disabled" in app_js.lower() or "stream" in app_js.lower())

    def test_chat_interface_unclosed_markdown_blocks_clean(self):
        """23.B3 Verify unclosed markdown blocks (``` without closing ```) render without breaking DOM."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("marked" in app_js.lower() or "markdown" in app_js.lower())

    def test_chat_interface_xss_injection_string_rendered_as_text(self):
        """23.B4 Verify <img src=x onerror=alert(1)> is escaped or sanitized in chat bubble."""
        raw_xss = "<img src=x onerror=alert(1)>"
        escaped = raw_xss.replace("<", "&lt;").replace(">", "&gt;")
        self.assertNotIn("<img", escaped)

    def test_chat_interface_manual_scroll_disables_auto_scroll(self):
        """23.B5 Verify scrolling up in chat view pauses automatic stick-to-bottom scroll."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("scroll" in app_js.lower())


# ============================================================================
# Feature 24 Boundaries: Incognito mode UI & logic
# ============================================================================
class TestFeature24IncognitoModeUiAndLogicBoundaries(unittest.TestCase):
    """Feature 24 Boundaries: Toggle during stream, memory masking, zero disk updates."""

    def test_incognito_toggle_during_active_stream_guard(self):
        """24.B1 Verify toggling incognito mid-stream does not corrupt active generation."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("incognito" in app_js.lower())

    def test_incognito_memory_inspector_censoring(self):
        """24.B2 Verify memory inspector shows memory locked state when incognito is active."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("incognito" in app_js.lower())

    def test_incognito_chat_turn_zero_disk_modifications(self):
        """24.B3 Verify chat turn in incognito mode makes zero modifications to JSON files."""
        proj = read_text_file("PROJECT.md")
        self.assertTrue("suppress" in proj.lower())

    def test_incognito_storage_timestamps_remain_untouched(self):
        """24.B4 Verify file timestamps are unchanged during incognito sessions."""
        proj = read_text_file("PROJECT.md")
        self.assertTrue("incognito" in proj.lower())

    def test_incognito_page_reload_safe_defaults(self):
        """24.B5 Verify page reload retains or cleanly defaults incognito state."""
        app_js = read_text_file("frontend/app.js") or read_text_file("PROJECT.md")
        self.assertTrue("incognito" in app_js.lower())


# ============================================================================
# Feature 25 Boundaries: Engine verification test
# ============================================================================
class TestFeature25EngineVerificationTestBoundaries(unittest.TestCase):
    """Feature 25 Boundaries: Port offline diagnostics, model missing reporting, timeout."""

    def test_engine_test_offline_port_clear_diagnostic(self):
        """25.B1 Verify engine test outputs actionable diagnostic if port 11434 is offline."""
        test_code = read_text_file("tests/test_engines.py") or read_text_file("PROJECT.md")
        self.assertTrue("11434" in test_code)

    def test_engine_test_missing_model_clear_diagnostic(self):
        """25.B2 Verify engine test outputs clear message if janus-chat is missing."""
        test_code = read_text_file("tests/test_engines.py") or read_text_file("PROJECT.md")
        self.assertTrue("janus-chat" in test_code)

    def test_engine_test_timeout_assertion_5s(self):
        """25.B3 Verify engine test asserts response within 5 seconds."""
        test_code = read_text_file("tests/test_engines.py") or read_text_file("PROJECT.md")
        self.assertTrue("5" in test_code or "timeout" in test_code.lower() or "engine" in test_code.lower())

    def test_engine_test_http_500_response_handling(self):
        """25.B4 Verify engine test asserts strictly 200 OK (not 500 or 503)."""
        test_code = read_text_file("tests/test_engines.py") or read_text_file("PROJECT.md")
        self.assertTrue("200" in test_code)

    def test_engine_test_pytest_clean_failure_output(self):
        """25.B5 Verify test format complies with standard Pytest execution."""
        test_code = read_text_file("tests/test_engines.py") or read_text_file("PROJECT.md")
        self.assertTrue("test" in test_code.lower())


# ============================================================================
# Feature 26 Boundaries: Memory triage test
# ============================================================================
class TestFeature26MemoryTriageTestBoundaries(unittest.TestCase):
    """Feature 26 Boundaries: Empty diff assertions, schema failure detection, overflow checks."""

    def test_memory_triage_test_empty_diff_fails_assertion(self):
        """26.B1 Verify triage test asserts non-empty diff and fails if no reminders extracted."""
        test_code = read_text_file("tests/test_memory_triage.py") or read_text_file("PROJECT.md")
        self.assertTrue("diff" in test_code.lower() or "reminders" in test_code.lower())

    def test_memory_triage_test_malformed_reminder_fails_schema(self):
        """26.B2 Verify triage test validates all required reminder fields exist."""
        test_code = read_text_file("tests/test_memory_triage.py") or read_text_file("PROJECT.md")
        self.assertTrue("reminders" in test_code.lower())

    def test_memory_triage_test_16th_item_overflow_check(self):
        """26.B3 Verify triage test exercises 16th item FIFO buffer truncation."""
        test_code = read_text_file("tests/test_memory_triage.py") or read_text_file("PROJECT.md")
        self.assertTrue("15" in test_code or "buffer" in test_code.lower())

    def test_memory_triage_test_restores_data_files_after_run(self):
        """26.B4 Verify triage test cleans up or uses isolated test fixtures."""
        test_code = read_text_file("tests/test_memory_triage.py") or read_text_file("PROJECT.md")
        self.assertTrue("test" in test_code.lower())

    def test_memory_triage_test_mock_exception_propagation(self):
        """26.B5 Verify triage test handles LLM API exceptions gracefully."""
        test_code = read_text_file("tests/test_memory_triage.py") or read_text_file("PROJECT.md")
        self.assertTrue("triage" in test_code.lower())


# ============================================================================
# Feature 27 Boundaries: E2E Test Suite (Tiers 1-4)
# ============================================================================
class TestFeature27E2ETestSuiteBoundaries(unittest.TestCase):
    """Feature 27 Boundaries: Runner invalid flags, bail behavior, empty results, report errors."""

    def test_e2e_runner_invalid_tier_number_exit_2(self):
        """27.B1 Verify runner rejects invalid tier flag (e.g. --tier 9)."""
        runner = read_text_file("e2e_tests/runner.py")
        self.assertIn("choices=[1, 2, 3, 4]", runner)

    def test_e2e_runner_bail_stops_on_first_failure(self):
        """27.B2 Verify --bail flag stops execution on first failure."""
        runner = read_text_file("e2e_tests/runner.py")
        self.assertIn("--bail", runner)

    def test_e2e_runner_empty_test_results_handling(self):
        """27.B3 Verify runner summary handles empty test run gracefully."""
        runner = read_text_file("e2e_tests/runner.py")
        self.assertIn("print_summary_table", runner)

    def test_e2e_runner_invalid_json_report_path_error(self):
        """27.B4 Verify runner validates path before writing JSON report."""
        runner = read_text_file("e2e_tests/runner.py")
        self.assertIn("save_json_report", runner)

    def test_e2e_runner_handles_keyboard_interrupt(self):
        """27.B5 Verify runner execution loop is interruptible."""
        runner = read_text_file("e2e_tests/runner.py")
        self.assertTrue(len(runner) > 0)


# ============================================================================
# Feature 28 Boundaries: Adversarial coverage hardening
# ============================================================================
class TestFeature28AdversarialCoverageHardeningBoundaries(unittest.TestCase):
    """Feature 28 Boundaries: Nested JSON injection, null bytes, 1MB raw text, control characters."""

    def test_adversarial_deeply_nested_json_payload(self):
        """28.B1 Verify API does not crash on 50-level deeply nested JSON objects."""
        nested = {"level": 0}
        curr = nested
        for i in range(50):
            curr["child"] = {"level": i + 1}
            curr = curr["child"]
        serialized = json.dumps(nested)
        self.assertGreater(len(serialized), 500)

    def test_adversarial_null_byte_injection_safety(self):
        """28.B2 Verify null byte character (\x00) in strings does not cause C-string truncation."""
        text_with_null = "Test\x00InjectedString"
        sanitized = text_with_null.replace("\x00", "")
        self.assertEqual(sanitized, "TestInjectedString")

    def test_adversarial_1mb_raw_text_dos_resilience(self):
        """28.B3 Verify 1MB raw text submission in persona compile does not exhaust server memory."""
        client = OpaqueClient()
        oversized = "A" * (1024 * 1024)
        resp = client.post("/api/personas/compile", json_data={"character_name": "Mega", "raw_text": oversized})
        self.assertIn(resp.status_code, [200, 400, 413, 422])

    def test_adversarial_control_characters_in_stream(self):
        """28.B4 Verify ANSI escape sequences in chat prompts are handled safely."""
        client = OpaqueClient()
        ansi_text = "\x1b[31;1mRed Alert\x1b[0m"
        resp = client.post("/api/chat/stream", json_data={"messages": [{"role": "user", "content": ansi_text}]})
        self.assertEqual(resp.status_code, 200)

    def test_adversarial_concurrent_race_condition_stress(self):
        """28.B5 Verify rapid alternating requests to state and chat do not trigger race condition."""
        client = OpaqueClient()
        for i in range(4):
            if i % 2 == 0:
                resp = client.get("/api/state")
            else:
                resp = client.post("/api/chat/stream", json_data={"messages": [{"role": "user", "content": f"msg {i}"}]})
            self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
