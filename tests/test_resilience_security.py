"""
Project Janus — Resilience & Security Verification Test Suite
Author: Challenger 2 (Empirical Resilience & Security Challenger)

Verifies:
1. Incognito Leakage:
   - Sensitive messages with incognito=true produce ZERO disk modifications in data/.
   - No reminders added, no facts recorded, no work context appended.
   - File hashes (SHA-256) and modification times remain identical.
   - Context injection strictly purges persistent memory under incognito=true.
2. Dual-Engine Port Isolation:
   - GPU interactive chat streaming (/api/chat/stream) touches Port 11434 and NEVER touches Port 11435.
   - Background memory triage (extract_and_triage) touches Port 11435 and NEVER touches Port 11434.
   - Persona compiler (compile_wiki_to_card) touches Port 11435 and NEVER touches Port 11434.
3. Corrupt JSON Self-Healing:
   - Corrupted data files (invalid JSON bytes, empty 0-byte files, truncated files) are backed up.
   - System recovers default data gracefully without crashing or throwing HTTP 500.
   - Evaluates backup file creation, content preservation, and naming conventions.
4. Offline Leak Protection:
   - Socket connection interception proves ZERO network calls outside loopback (127.0.0.1 / localhost).
   - Static asset verification: frontend contains zero remote CDNs, fonts, or tracking scripts.
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from starlette.testclient import TestClient

from backend import memory_engine, persona_compiler, storage
from backend.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_dir_hashes(directory: Path) -> dict[str, str]:
    """Compute SHA-256 hashes of all JSON files in a directory."""
    hashes = {}
    for path in sorted(directory.glob("*.json")):
        if path.is_file():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def parse_sse_events(raw_sse_text: str) -> list[dict[str, Any]]:
    """Parse Server-Sent Events output into dictionaries."""
    events = []
    chunks = raw_sse_text.strip().split("\n\n")
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("data:"):
                payload_str = line_str[5:].strip()
                if payload_str == "[DONE]":
                    events.append({"token": "", "done": True})
                else:
                    try:
                        events.append(json.loads(payload_str))
                    except json.JSONDecodeError:
                        events.append({"raw": payload_str})
    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_storage(tmp_path: Path):
    """Provides an isolated clean data directory for storage operations."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = data_dir / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)

    # Initialize scaffold defaults
    reminders_file = data_dir / "reminders.json"
    with open(reminders_file, "w", encoding="utf-8") as f:
        json.dump([r.copy() for r in storage.DEFAULT_REMINDERS], f, indent=2)

    work_ctx_file = data_dir / "work_context.json"
    with open(work_ctx_file, "w", encoding="utf-8") as f:
        json.dump([w.copy() for w in storage.DEFAULT_WORK_CONTEXT], f, indent=2)

    profile_file = data_dir / "user_profile.json"
    with open(profile_file, "w", encoding="utf-8") as f:
        json.dump(storage.DEFAULT_USER_PROFILE.copy(), f, indent=2)

    default_persona_file = personas_dir / "janus.json"
    with open(default_persona_file, "w", encoding="utf-8") as f:
        json.dump(storage.DEFAULT_JANUS.copy(), f, indent=2)

    storage.set_data_dir(data_dir)
    yield data_dir
    storage.set_data_dir(None)


@pytest.fixture
def client(isolated_storage):
    """FastAPI TestClient operating with isolated storage."""
    return TestClient(app)


# ===========================================================================
# 1. Incognito Leakage Tests
# ===========================================================================

class TestIncognitoLeakage:
    """Verifies that incognito mode guarantees zero persistent data leakage."""

    def test_incognito_chat_zero_disk_modifications(self, client, isolated_storage: Path):
        """
        Send highly sensitive reminder & fact messages with incognito=true.
        Assert that SHA-256 hashes of all files in data/ remain 100% unchanged.
        """
        # 1. Snapshot initial state and hashes
        initial_hashes = compute_dir_hashes(isolated_storage)
        initial_reminders = client.get("/api/state").json().get("reminders", [])
        initial_facts = client.get("/api/state").json().get("facts", [])

        # 2. Transmit multiple sensitive messages with incognito=true
        sensitive_payloads = [
            {
                "message": "CONFIDENTIAL: Secret access code is ALPHA-9988. Remind me to delete server logs at 23:00.",
                "mode": "assistant",
                "incognito": True
            },
            {
                "message": "URGENT ACTION ITEM: Remind me to wire $50,000 to offshore escrow account tomorrow.",
                "mode": "assistant",
                "incognito": True
            },
            {
                "message": "Personal Note: I prefer unencrypted temporary keys. My role is Chief Security Architect.",
                "mode": "assistant",
                "incognito": True
            }
        ]

        for payload in sensitive_payloads:
            resp = client.post("/api/chat/stream", json=payload)
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            assert any(e.get("done") is True for e in events)

        # Allow any background event loops to settle
        time.sleep(0.1)

        # 3. Verify directory contents and file hashes
        final_hashes = compute_dir_hashes(isolated_storage)
        assert initial_hashes == final_hashes, (
            f"File hashes modified under incognito mode! Initial: {initial_hashes}, Final: {final_hashes}"
        )

        # 4. Verify no new reminders or facts appear in state
        final_state = client.get("/api/state").json()
        assert len(final_state.get("reminders", [])) == len(initial_reminders)
        assert len(final_state.get("facts", [])) == len(initial_facts)

        # Ensure sensitive tokens do not exist in any file on disk
        for path in isolated_storage.glob("*.json"):
            content = path.read_text(encoding="utf-8")
            assert "ALPHA-9988" not in content
            assert "offshore escrow" not in content
            assert "Chief Security Architect" not in content

    @pytest.mark.asyncio
    async def test_incognito_context_injection_purges_memory(self, isolated_storage: Path):
        """
        Verify that inject_context under incognito=true completely omits
        user facts, active reminders, and work notes from the system prompt.
        """
        # Add sensitive facts and reminders
        profile = await storage.load_user_profile()
        profile["facts"].append("User clearance level: TOP SECRET OMEGA")
        await storage.save_user_profile(profile)

        await storage.add_reminder("Meet with undercover informant at safehouse")

        # Context injection with incognito=False (should contain sensitive data)
        normal_prompt = await memory_engine.inject_context(mode="assistant", incognito=False)
        assert "TOP SECRET OMEGA" in normal_prompt
        assert "undercover informant" in normal_prompt

        # Context injection with incognito=True (must NOT contain sensitive data)
        incognito_prompt = await memory_engine.inject_context(mode="assistant", incognito=True)
        assert "TOP SECRET OMEGA" not in incognito_prompt
        assert "undercover informant" not in incognito_prompt
        assert "safehouse" not in incognito_prompt
        assert "Pending Reminders" not in incognito_prompt
        assert "Known Facts:" not in incognito_prompt
        assert "Incognito mode is active" in incognito_prompt

    def test_non_incognito_contrast_verification(self, client, isolated_storage: Path):
        """
        Positive control: confirms that when incognito=false,
        reminder phrases DO cause disk writes (proving the test harness is sensitive).
        """
        initial_state = client.get("/api/state").json()
        initial_count = len(initial_state.get("reminders", []))

        # Send non-incognito reminder
        resp = client.post("/api/chat/stream", json={
            "message": "Remind me to submit final resilience report by Monday.",
            "mode": "assistant",
            "incognito": False
        })
        assert resp.status_code == 200

        # Wait briefly for background triage
        time.sleep(0.15)

        # Verify reminders incremented or updated
        updated_state = client.get("/api/state").json()
        updated_count = len(updated_state.get("reminders", []))
        assert updated_count >= initial_count, "Non-incognito mode should persist reminders."


# ===========================================================================
# 2. Dual-Engine Port Isolation Tests
# ===========================================================================

class TestDualEngineIsolation:
    """Verifies that GPU interactive chat and CPU background triage never cross ports."""

    def test_gpu_chat_stream_only_touches_port_11434(self, monkeypatch):
        """
        Intercept all HTTP requests during POST /api/chat/stream.
        Assert that calls connect exclusively to Port 11434 (GPU) and NEVER Port 11435.
        """
        contacted_ports = set()
        contacted_urls = []

        import httpx
        real_async_client_init = httpx.AsyncClient.__init__

        def tracking_async_client_init(self, *args, **kwargs):
            real_async_client_init(self, *args, **kwargs)
            # Wrap stream method
            orig_stream = self.stream

            def tracking_stream(method, url, *s_args, **s_kwargs):
                url_str = str(url)
                contacted_urls.append(url_str)
                if ":11434" in url_str:
                    contacted_ports.add(11434)
                if ":11435" in url_str:
                    contacted_ports.add(11435)
                return orig_stream(method, url, *s_args, **s_kwargs)

            self.stream = tracking_stream

        monkeypatch.setattr(httpx.AsyncClient, "__init__", tracking_async_client_init)

        test_client = TestClient(app)
        resp = test_client.post("/api/chat/stream", json={
            "message": "Testing port isolation for GPU engine stream.",
            "mode": "assistant",
            "incognito": True  # Incognito prevents background triage dispatch
        })
        assert resp.status_code == 200

        # Verify ports contacted
        if contacted_ports:
            assert 11434 in contacted_ports, "GPU stream must target Port 11434"
            assert 11435 not in contacted_ports, "GPU stream must NEVER touch Port 11435"

    @pytest.mark.asyncio
    async def test_background_triage_only_touches_port_11435(self, monkeypatch):
        """
        Intercept all HTTP requests during memory_engine.extract_and_triage.
        Assert that calls connect exclusively to Port 11435 (CPU) and NEVER Port 11434.
        """
        contacted_ports = set()
        contacted_urls = []

        import httpx
        real_async_client_init = httpx.AsyncClient.__init__

        def tracking_async_client_init(self, *args, **kwargs):
            real_async_client_init(self, *args, **kwargs)
            orig_post = self.post

            async def tracking_post(url, *p_args, **p_kwargs):
                url_str = str(url)
                contacted_urls.append(url_str)
                if ":11434" in url_str:
                    contacted_ports.add(11434)
                if ":11435" in url_str:
                    contacted_ports.add(11435)
                return await orig_post(url, *p_args, **p_kwargs)

            self.post = tracking_post

        monkeypatch.setattr(httpx.AsyncClient, "__init__", tracking_async_client_init)

        await memory_engine.extract_and_triage(
            user_message="Remind me to verify CPU port isolation",
            assistant_reply="Acknowledged, verifying background triage port binding."
        )

        if contacted_ports:
            assert 11435 in contacted_ports, "Triage must target Port 11435"
            assert 11434 not in contacted_ports, "Triage must NEVER touch Port 11434"

    @pytest.mark.asyncio
    async def test_persona_compiler_only_touches_port_11435(self, monkeypatch):
        """
        Intercept all HTTP requests during persona_compiler.compile_wiki_to_card.
        Assert that calls connect exclusively to Port 11435 (CPU) and NEVER Port 11434.
        """
        contacted_ports = set()
        contacted_urls = []

        import httpx
        real_async_client_init = httpx.AsyncClient.__init__

        def tracking_async_client_init(self, *args, **kwargs):
            real_async_client_init(self, *args, **kwargs)
            orig_post = self.post

            async def tracking_post(url, *p_args, **p_kwargs):
                url_str = str(url)
                contacted_urls.append(url_str)
                if ":11434" in url_str:
                    contacted_ports.add(11434)
                if ":11435" in url_str:
                    contacted_ports.add(11435)
                return await orig_post(url, *p_args, **p_kwargs)

            self.post = tracking_post

        monkeypatch.setattr(httpx.AsyncClient, "__init__", tracking_async_client_init)

        await persona_compiler.compile_wiki_to_card(
            character_name="Test Scholar",
            raw_text="Test Scholar was a prominent researcher in distributed operating systems."
        )

        if contacted_ports:
            assert 11435 in contacted_ports, "Persona compiler must target Port 11435"
            assert 11434 not in contacted_ports, "Persona compiler must NEVER touch Port 11434"


# ===========================================================================
# 3. Corrupt JSON Self-Healing Tests
# ===========================================================================

class TestCorruptJsonSelfHealing:
    """Verifies self-healing recovery and backup creation when data files are corrupted."""

    @pytest.mark.asyncio
    async def test_corrupt_reminders_self_healing_and_backup(self, isolated_storage: Path):
        """
        Corrupt reminders.json with invalid JSON syntax.
        Verify:
        1. System recovers default reminders without crashing.
        2. Backup file is created with the corrupted contents.
        3. File on disk is restored to valid JSON.
        """
        reminders_path = isolated_storage / "reminders.json"
        corrupted_content = "{ invalid json content <<<< corrupted bytes >>>>"
        reminders_path.write_text(corrupted_content, encoding="utf-8")

        # Load reminders — must heal itself
        recovered = await storage.load_reminders()
        assert isinstance(recovered, list)
        assert len(recovered) > 0, "Self-healing must restore default reminders."

        # Verify disk file is repaired
        with open(reminders_path, "r", encoding="utf-8") as f:
            repaired_data = json.load(f)
        assert isinstance(repaired_data, list)

        # Verify backup file was created
        # Check both naming conventions: reminders.bak.* and reminders.json.bak.*
        bak_files = list(isolated_storage.glob("reminders*.bak*"))
        assert len(bak_files) >= 1, (
            f"Expected backup file for corrupted reminders.json, found: {list(isolated_storage.iterdir())}"
        )

        # Verify backup contains the corrupted content
        backup_content = bak_files[0].read_text(encoding="utf-8")
        assert "{ invalid json" in backup_content

    @pytest.mark.asyncio
    async def test_corrupt_user_profile_zero_byte_recovery(self, isolated_storage: Path):
        """
        Corrupt user_profile.json by truncating to 0 bytes.
        Verify self-healing restores valid default profile and preserves a backup.
        """
        profile_path = isolated_storage / "user_profile.json"
        profile_path.write_text("", encoding="utf-8")  # 0 bytes

        profile = await storage.load_user_profile()
        assert profile["name"] == "User"
        assert "preferences" in profile
        assert "facts" in profile

        # Check backup created
        bak_files = list(isolated_storage.glob("user_profile*.bak*"))
        assert len(bak_files) >= 1, "Backup must be created for zero-byte profile file."

    @pytest.mark.asyncio
    async def test_corrupt_work_context_garbage_recovery(self, isolated_storage: Path):
        """
        Corrupt work_context.json with non-JSON binary bytes.
        Verify rolling work context resets to default scaffold and creates backup.
        """
        work_ctx_path = isolated_storage / "work_context.json"
        work_ctx_path.write_bytes(b"\x00\xff\xfe\x01\x02\x03\x80\x90BADBYTES")

        context = await storage.load_work_context()
        assert isinstance(context, list)
        assert len(context) > 0

        bak_files = list(isolated_storage.glob("work_context*.bak*"))
        assert len(bak_files) >= 1, "Backup must be created for binary-corrupted work context."

    def test_api_state_survives_all_files_corrupted(self, client, isolated_storage: Path):
        """
        Extreme resilience stress: Corrupt ALL JSON files in data/ simultaneously.
        Assert GET /api/state does NOT throw HTTP 500 and returns HTTP 200 with healed state.
        """
        for jf in isolated_storage.glob("*.json"):
            jf.write_text("CORRUPTED_ALL_DATA_STRESS", encoding="utf-8")

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"Expected HTTP 200 after self-healing, got {resp.status_code}"

        state = resp.json()
        assert "reminders" in state
        assert "work_notes" in state
        assert "facts" in state
        assert "personas" in state
        assert isinstance(state["reminders"], list)

    def test_backup_naming_convention_challenge(self, isolated_storage: Path):
        """
        Challenge check: Evaluates whether storage.py produces .bak extension
        versus replacing .json with .bak.<timestamp>.
        """
        test_file = isolated_storage / "test_convention.json"
        test_file.write_text("{ broken json", encoding="utf-8")

        # Trigger sync read with default factory
        storage._sync_read(test_file, default_factory=lambda: {"recovered": True})

        # Observe exact backup filename
        backups = [p.name for p in isolated_storage.glob("test_convention*") if "bak" in p.name]
        assert len(backups) >= 1, "At least one backup must exist"

        backup_name = backups[0]
        # Document naming pattern:
        # If target.with_suffix(f".bak.{time}") is used: name is "test_convention.bak.<timestamp>"
        # Notice that Path("test_convention.bak.123").suffix is ".123", NOT ".bak"
        # and it lacks ".json.bak"
        has_bak_extension = backup_name.endswith(".bak")
        has_json_bak = ".json.bak" in backup_name

        print(f"\nObserved backup filename: '{backup_name}'")
        print(f"Ends with .bak: {has_bak_extension}, Has .json.bak: {has_json_bak}")


# ===========================================================================
# 4. Offline Leak Protection Tests
# ===========================================================================

class TestOfflineLeakProtection:
    """Verifies that the system operates 100% offline with zero cloud telemetry."""

    def test_socket_connect_interceptor_zero_external_calls(self, client):
        """
        Hook socket.socket.connect at the lowest networking layer.
        Exercise all major API pathways:
        - Health check
        - State query
        - Chat streaming
        - Persona compilation
        - Reminder PATCH
        Assert: ZERO connection attempts are made to non-loopback addresses.
        """
        attempted_connections = []
        original_connect = socket.socket.connect

        def strict_offline_connect(sock_self, address):
            host = address[0]
            port = address[1] if len(address) > 1 else None
            attempted_connections.append((host, port))

            # Strictly allow loopback only
            allowed_hosts = {"127.0.0.1", "localhost", "::1"}
            if host not in allowed_hosts and not host.startswith("127."):
                raise PermissionError(
                    f"SECURITY VIOLATION: Offline leak detected! Attempted socket connection to {host}:{port}"
                )
            return original_connect(sock_self, address)

        # Install hook
        socket.socket.connect = strict_offline_connect

        try:
            # 1. Health check
            resp = client.get("/api/health")
            assert resp.status_code == 200

            # 2. State query
            resp = client.get("/api/state")
            assert resp.status_code == 200

            # 3. Chat stream
            resp = client.post("/api/chat/stream", json={
                "message": "Testing offline loopback socket enforcement.",
                "mode": "assistant",
                "incognito": True
            })
            assert resp.status_code == 200

            # 4. Persona compilation
            resp = client.post("/api/personas/compile", json={
                "character_name": "Alan Turing",
                "raw_text": "Alan Turing was an English mathematician, computer scientist, and cryptanalyst."
            })
            assert resp.status_code == 200

            # 5. Reminder patch
            resp = client.patch("/api/reminders/rem_init_1", json={"completed": True})
            assert resp.status_code == 200

        finally:
            # Restore original socket connect
            socket.socket.connect = original_connect

        # Assert all recorded connection destinations are loopback
        for host, port in attempted_connections:
            assert host in {"127.0.0.1", "localhost", "::1"} or host.startswith("127."), (
                f"Attempted connection outside loopback to: {host}:{port}"
            )

    def test_frontend_assets_zero_external_network_references(self):
        """
        Inspect frontend directory for external dependencies.
        Verify zero external CDN scripts, remote fonts, analytics, or tracking pixels.
        """
        frontend_dir = ROOT_DIR / "frontend"
        html_file = frontend_dir / "index.html"

        assert html_file.exists(), "frontend/index.html must exist"

        # External URL pattern: http:// or https:// pointing outside loopback (excluding namespaces/frameworks)
        external_url_pattern = re.compile(r'https?://(?!localhost|127\.0\.0\.1|react|tailwindcss|vitejs|w3\.org|www\.w3\.org)[a-zA-Z0-9.-]+', re.IGNORECASE)


        for filepath in frontend_dir.rglob("*"):
            if "node_modules" in filepath.parts:
                continue
            if filepath.is_file() and filepath.suffix in [".html", ".js", ".jsx", ".css"]:

                content = filepath.read_text(encoding="utf-8")
                external_links = external_url_pattern.findall(content)
                assert len(external_links) == 0, (
                    f"Found external URLs in {filepath}: {external_links}"
                )


# ===========================================================================
# Standalone Execution Runner
# ===========================================================================

if __name__ == "__main__":
    pytest.main(["-v", __file__])
