"""
Project Janus - Dual Ollama Engine Health & Connectivity Test Suite
Verifies connectivity to:
  - GPU Engine on Port 11434 (target model: janus-chat)
  - CPU Engine on Port 11435 (target model: janus-extractor)

Supports both live testing against running Ollama daemons and structured
contract/mock testing using httpx.MockTransport with clear diagnostic messages.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import httpx
import pytest

# Engine connection constants
GPU_PORT = 11434
CPU_PORT = 11435
GPU_HOST = f"127.0.0.1:{GPU_PORT}"
CPU_HOST = f"127.0.0.1:{CPU_PORT}"
GPU_ENGINE_URL = os.environ.get("JANUS_GPU_URL", f"http://{GPU_HOST}")
CPU_ENGINE_URL = os.environ.get("JANUS_CPU_URL", f"http://{CPU_HOST}")
GPU_MODEL = "janus-chat"
CPU_MODEL = "janus-extractor"
DEFAULT_TIMEOUT_SECONDS = 5.0


def is_live_port_online(url: str, timeout: float = 1.0) -> bool:
    """Quick loopback check to see if an engine port is actively listening and responsive."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{url.rstrip('/')}/api/version")
            return resp.status_code == 200
    except Exception:
        return False


def verify_engine_health(
    base_url: str,
    expected_model: str,
    client: Optional[httpx.Client] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """
    Asserts that an Ollama engine endpoint is healthy:
    1. Returns HTTP 200 from /api/version within timeout (max 5.0s).
    2. Returns HTTP 200 from /api/tags within timeout.
    3. Confirms that expected_model is present in loaded models list.

    Raises ConnectionError with clear diagnostic text if the port is offline.
    Raises AssertionError if HTTP status is not 200 or the model is missing.
    """
    clean_url = base_url.rstrip("/")
    port_str = "11434" if "11434" in clean_url else ("11435" if "11435" in clean_url else clean_url)

    close_client = False
    if client is None:
        client = httpx.Client(timeout=timeout)
        close_client = True

    try:
        # 1. Check /api/version
        try:
            ver_resp = client.get(f"{clean_url}/api/version")
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as conn_err:
            raise ConnectionError(
                f"[DIAGNOSTIC] Ollama engine port {port_str} ({clean_url}) is offline or unreachable: {conn_err}. "
                f"Please start the dual Ollama daemons using start_engines.ps1 before running live tests."
            ) from conn_err
        except httpx.TimeoutException as time_err:
            raise TimeoutError(
                f"[DIAGNOSTIC] Engine at {clean_url} failed to respond within {timeout} seconds: {time_err}."
            ) from time_err

        assert ver_resp.status_code == 200, (
            f"[DIAGNOSTIC] Expected HTTP 200 from {clean_url}/api/version, got status {ver_resp.status_code}. "
            f"Response body: {ver_resp.text}"
        )

        version_data = ver_resp.json() if ver_resp.content else {}
        version_str = version_data.get("version", "unknown")

        # 2. Check /api/tags for model presence
        tags_resp = client.get(f"{clean_url}/api/tags")
        assert tags_resp.status_code == 200, (
            f"[DIAGNOSTIC] Expected HTTP 200 from {clean_url}/api/tags, got status {tags_resp.status_code}. "
            f"Response body: {tags_resp.text}"
        )

        tags_data = tags_resp.json()
        raw_models = tags_data.get("models", [])
        model_names: list[str] = []
        for m in raw_models:
            if isinstance(m, dict):
                name = m.get("name") or m.get("model") or ""
                if name:
                    model_names.append(name)
            elif isinstance(m, str):
                model_names.append(m)

        # Check for model presence (accepts either exact match 'janus-chat' or tagged 'janus-chat:latest')
        model_found = any(
            m == expected_model or m.startswith(f"{expected_model}:")
            for m in model_names
        )

        assert model_found, (
            f"[DIAGNOSTIC] Required model '{expected_model}' not found in engine at port {port_str} ({clean_url}). "
            f"Available models: {model_names}. "
            f"Compile the model by running 'ollama create {expected_model} -f Modelfile' or via start_engines.ps1."
        )

        return {
            "status": "online",
            "status_code": 200,
            "port": port_str,
            "url": clean_url,
            "version": version_str,
            "model": expected_model,
            "models_present": model_names
        }
    finally:
        if close_client:
            client.close()


async def verify_engine_health_async(
    base_url: str,
    expected_model: str,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Asynchronous variant of verify_engine_health for asyncio test suites."""
    clean_url = base_url.rstrip("/")
    port_str = "11434" if "11434" in clean_url else ("11435" if "11435" in clean_url else clean_url)

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)
        close_client = True

    try:
        try:
            ver_resp = await client.get(f"{clean_url}/api/version")
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as conn_err:
            raise ConnectionError(
                f"[DIAGNOSTIC] Ollama engine port {port_str} ({clean_url}) is offline or unreachable: {conn_err}. "
                f"Please start the dual Ollama daemons using start_engines.ps1 before running live tests."
            ) from conn_err
        except httpx.TimeoutException as time_err:
            raise TimeoutError(
                f"[DIAGNOSTIC] Engine at {clean_url} failed to respond within {timeout} seconds: {time_err}."
            ) from time_err

        assert ver_resp.status_code == 200, (
            f"[DIAGNOSTIC] Expected HTTP 200 from {clean_url}/api/version, got status {ver_resp.status_code}."
        )

        tags_resp = await client.get(f"{clean_url}/api/tags")
        assert tags_resp.status_code == 200, (
            f"[DIAGNOSTIC] Expected HTTP 200 from {clean_url}/api/tags, got status {tags_resp.status_code}."
        )

        tags_data = tags_resp.json()
        raw_models = tags_data.get("models", [])
        model_names = [m.get("name", "") for m in raw_models if isinstance(m, dict)]

        model_found = any(
            m == expected_model or m.startswith(f"{expected_model}:")
            for m in model_names
        )

        assert model_found, (
            f"[DIAGNOSTIC] Required model '{expected_model}' not found in engine at port {port_str} ({clean_url}). "
            f"Available models: {model_names}."
        )

        return {
            "status": "online",
            "status_code": 200,
            "port": port_str,
            "url": clean_url,
            "version": ver_resp.json().get("version", "unknown"),
            "model": expected_model,
            "models_present": model_names
        }
    finally:
        if close_client:
            await client.aclose()


# ============================================================================
# 1. Live Dual Engine Connectivity Tests
# ============================================================================

def test_gpu_engine_live_connectivity():
    """
    Verify live connectivity to Port 11434 (GPU Engine) and check for janus-chat.
    Asserts HTTP 200 and confirms janus-chat model presence.
    If the daemon is not currently active, skips with clear instruction.
    """
    require_live = os.environ.get("JANUS_REQUIRE_LIVE_ENGINES", "0") == "1"
    if not is_live_port_online(GPU_ENGINE_URL) and not require_live:
        pytest.skip(
            f"GPU engine at {GPU_ENGINE_URL} (Port 11434) is not running. "
            f"Launch via start_engines.ps1 to execute live engine tests."
        )

    result = verify_engine_health(
        base_url=GPU_ENGINE_URL,
        expected_model=GPU_MODEL,
        timeout=DEFAULT_TIMEOUT_SECONDS
    )
    assert result["status"] == "online"
    assert result["status_code"] == 200
    assert result["model"] == "janus-chat"
    assert any("janus-chat" in m for m in result["models_present"])


def test_cpu_engine_live_connectivity():
    """
    Verify live connectivity to Port 11435 (CPU Engine) and check for janus-extractor.
    Asserts HTTP 200 and confirms janus-extractor model presence.
    If the daemon is not currently active, skips with clear instruction.
    """
    require_live = os.environ.get("JANUS_REQUIRE_LIVE_ENGINES", "0") == "1"
    if not is_live_port_online(CPU_ENGINE_URL) and not require_live:
        pytest.skip(
            f"CPU engine at {CPU_ENGINE_URL} (Port 11435) is not running. "
            f"Launch via start_engines.ps1 to execute live engine tests."
        )

    result = verify_engine_health(
        base_url=CPU_ENGINE_URL,
        expected_model=CPU_MODEL,
        timeout=DEFAULT_TIMEOUT_SECONDS
    )
    assert result["status"] == "online"
    assert result["status_code"] == 200
    assert result["model"] == "janus-extractor"
    assert any("janus-extractor" in m for m in result["models_present"])


# ============================================================================
# 2. Structured Mock / Contract Verification Tests
# ============================================================================

def test_gpu_engine_contract_mock():
    """
    Structured contract test asserting HTTP 200 and janus-chat presence on Port 11434.
    Uses httpx.MockTransport for deterministic contract verification.
    """
    def mock_gpu_handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        if url_path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.32"})
        if url_path == "/api/tags":
            return httpx.Response(200, json={
                "models": [
                    {
                        "name": "janus-chat:latest",
                        "model": "janus-chat:latest",
                        "modified_at": "2026-09-13T00:00:00Z",
                        "size": 3800000000,
                        "digest": "sha256:gpu_mock_digest"
                    }
                ]
            })
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(mock_gpu_handler)
    with httpx.Client(transport=transport) as client:
        result = verify_engine_health(
            base_url="http://127.0.0.1:11434",
            expected_model="janus-chat",
            client=client,
            timeout=5.0
        )
        assert result["status"] == "online"
        assert result["status_code"] == 200
        assert result["model"] == "janus-chat"
        assert "janus-chat:latest" in result["models_present"]


def test_cpu_engine_contract_mock():
    """
    Structured contract test asserting HTTP 200 and janus-extractor presence on Port 11435.
    Uses httpx.MockTransport for deterministic contract verification.
    """
    def mock_cpu_handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        if url_path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.32"})
        if url_path == "/api/tags":
            return httpx.Response(200, json={
                "models": [
                    {
                        "name": "janus-extractor:latest",
                        "model": "janus-extractor:latest",
                        "modified_at": "2026-09-13T00:00:00Z",
                        "size": 3800000000,
                        "digest": "sha256:cpu_mock_digest"
                    }
                ]
            })
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(mock_cpu_handler)
    with httpx.Client(transport=transport) as client:
        result = verify_engine_health(
            base_url="http://127.0.0.1:11435",
            expected_model="janus-extractor",
            client=client,
            timeout=5.0
        )
        assert result["status"] == "online"
        assert result["status_code"] == 200
        assert result["model"] == "janus-extractor"
        assert "janus-extractor:latest" in result["models_present"]


def test_engine_offline_port_clear_diagnostic():
    """
    Verify engine test outputs clear actionable diagnostic text if port 11434 is offline.
    """
    def mock_fail_handler(request: httpx.Request):
        raise httpx.ConnectError("Connection refused on 127.0.0.1:11434")

    transport = httpx.MockTransport(mock_fail_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ConnectionError) as exc_info:
            verify_engine_health(
                base_url="http://127.0.0.1:11434",
                expected_model="janus-chat",
                client=client,
                timeout=5.0
            )
        err_msg = str(exc_info.value)
        assert "11434" in err_msg
        assert "offline" in err_msg.lower() or "unreachable" in err_msg.lower()
        assert "start_engines.ps1" in err_msg


def test_engine_missing_model_clear_diagnostic():
    """
    Verify engine test outputs clear diagnostic message if janus-chat model is missing.
    """
    def mock_missing_handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        if url_path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.32"})
        if url_path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "other-model:latest"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_missing_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(AssertionError) as exc_info:
            verify_engine_health(
                base_url="http://127.0.0.1:11434",
                expected_model="janus-chat",
                client=client,
                timeout=5.0
            )
        err_msg = str(exc_info.value)
        assert "janus-chat" in err_msg
        assert "not found" in err_msg.lower()


def test_engine_http_500_response_handling():
    """
    Verify engine test asserts strictly 200 OK (not 500 Internal Server Error or 503).
    """
    def mock_500_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal daemon failure"})

    transport = httpx.MockTransport(mock_500_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(AssertionError) as exc_info:
            verify_engine_health(
                base_url="http://127.0.0.1:11434",
                expected_model="janus-chat",
                client=client,
                timeout=5.0
            )
        assert "200" in str(exc_info.value)


def test_engine_timeout_assertion_5s():
    """
    Verify engine test asserts response within 5 seconds and handles timeout properly.
    """
    def mock_timeout_handler(request: httpx.Request):
        raise httpx.ReadTimeout("Read timed out after 5.0 seconds")

    transport = httpx.MockTransport(mock_timeout_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises((TimeoutError, httpx.TimeoutException)):
            verify_engine_health(
                base_url="http://127.0.0.1:11434",
                expected_model="janus-chat",
                client=client,
                timeout=5.0
            )


@pytest.mark.asyncio
async def test_async_dual_engine_health():
    """
    Verify asynchronous dual engine verification can check both ports concurrently.
    """
    def mock_dual_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        host = request.url.host
        port = request.url.port

        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.32"})
        if path == "/api/tags":
            if port == 11434:
                return httpx.Response(200, json={"models": [{"name": "janus-chat:latest"}]})
            elif port == 11435:
                return httpx.Response(200, json={"models": [{"name": "janus-extractor:latest"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_dual_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gpu_task = verify_engine_health_async("http://127.0.0.1:11434", "janus-chat", client=client)
        cpu_task = verify_engine_health_async("http://127.0.0.1:11435", "janus-extractor", client=client)
        gpu_res, cpu_res = await asyncio.gather(gpu_task, cpu_task)

        assert gpu_res["status_code"] == 200
        assert gpu_res["model"] == "janus-chat"
        assert cpu_res["status_code"] == 200
        assert cpu_res["model"] == "janus-extractor"
