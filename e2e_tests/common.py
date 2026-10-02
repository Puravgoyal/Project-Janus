"""
Project Janus — E2E Test Suite Common Utilities & Opaque Client
Provides opaque-box HTTP interaction, SSE stream consumption, schema validation,
and filesystem artifact verification.
"""

import os
import sys
import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Base Project Path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BASE_URL = os.environ.get("JANUS_API_BASE", "http://127.0.0.1:8000")
GPU_URL = os.environ.get("JANUS_GPU_BASE", "http://127.0.0.1:11434")
CPU_URL = os.environ.get("JANUS_CPU_BASE", "http://127.0.0.1:11435")


class OpaqueResponse:
    """Represents an opaque HTTP response."""
    def __init__(self, status_code: int, headers: Dict[str, str], body: bytes):
        self.status_code = status_code
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body
        self._text = None
        self._json = None

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = self.body.decode("utf-8", errors="replace")
        return self._text

    def json(self) -> Any:
        if self._json is None:
            self._json = json.loads(self.text)
        return self._json

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


class OpaqueClient:
    """
    Opaque HTTP Client for interacting with Project Janus backend.
    Checks live loopback port first; if unavailable, uses FastAPI TestClient in-memory transport.
    """
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._test_client = None
        self._init_fallback_client()

    def _init_fallback_client(self):
        """Attempts to load FastAPI TestClient as fallback if backend is importable."""
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from backend.main import app
            from starlette.testclient import TestClient
            self._test_client = TestClient(app)
        except Exception:
            self._test_client = None

    def _is_live_server_running(self) -> bool:
        """Pings base URL to check if live server is running on loopback."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/state", method="GET")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                return resp.status == 200
        except Exception:
            return False

    def request(self, method: str, path: str, json_data: Optional[Dict] = None, 
                headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> OpaqueResponse:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        req_headers = headers or {}

        # 1. Attempt live HTTP call first if reachable
        if self._is_live_server_running():
            try:
                data = json.dumps(json_data).encode("utf-8") if json_data is not None else None
                if json_data is not None and "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/json"
                req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_headers = dict(resp.headers)
                    body = resp.read()
                    return OpaqueResponse(resp.status, resp_headers, body)
            except urllib.error.HTTPError as e:
                resp_headers = dict(e.headers)
                body = e.read()
                return OpaqueResponse(e.code, resp_headers, body)
            except Exception:
                pass

        # 2. Use in-memory FastAPI TestClient if available
        if self._test_client:
            try:
                resp = self._test_client.request(method=method, url=path, json=json_data, headers=req_headers)
                return OpaqueResponse(resp.status_code, dict(resp.headers), resp.content)
            except Exception as e:
                return OpaqueResponse(500, {"content-type": "application/json"}, json.dumps({"error": str(e)}).encode("utf-8"))

        # 3. Transparent failure if neither live server nor TestClient is reachable
        raise RuntimeError(
            f"OpaqueClient connection failure: Cannot connect to live server on '{self.base_url}' "
            f"and FastAPI TestClient in-memory transport is not available. "
            f"Self-certifying simulations have been permanently removed."
        )

    def get(self, path: str, headers: Optional[Dict[str, str]] = None) -> OpaqueResponse:
        return self.request("GET", path, headers=headers)

    def post(self, path: str, json_data: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None) -> OpaqueResponse:
        return self.request("POST", path, json_data=json_data, headers=headers)

    def patch(self, path: str, json_data: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None) -> OpaqueResponse:
        return self.request("PATCH", path, json_data=json_data, headers=headers)

    def delete(self, path: str, headers: Optional[Dict[str, str]] = None) -> OpaqueResponse:
        return self.request("DELETE", path, headers=headers)


def parse_sse_events(sse_text: str) -> List[Dict[str, Any]]:
    """Parses text/event-stream chunks into a list of JSON data objects."""
    events = []
    lines = sse_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("data:"):
            payload_str = line[5:].strip()
            if payload_str:
                try:
                    events.append(json.loads(payload_str))
                except json.JSONDecodeError:
                    events.append({"raw": payload_str})
    return events


def load_json_file(rel_path: str, default: Any = None) -> Any:
    """Safely loads a JSON file from the project directory."""
    path = PROJECT_ROOT / rel_path
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_personas() -> List[Dict[str, Any]]:
    """Loads all persona cards from data/personas/."""
    personas_dir = PROJECT_ROOT / "data" / "personas"
    if not personas_dir.exists():
        return []
    cards = []
    for p in personas_dir.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cards.append(json.load(f))
        except Exception:
            pass
    return cards


def read_text_file(rel_path: str) -> str:
    """Reads a text file from project directory."""
    path = PROJECT_ROOT / rel_path
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
