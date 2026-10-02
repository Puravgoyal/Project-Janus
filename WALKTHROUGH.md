# Project Janus — Walkthrough & Command Reference

> **100% offline · Privacy-first · Windows 11 · RTX 4050 + Core i7**

---

## Directory Structure

```
D:\hahaha\Project Janus\
├── Modelfile.gpu              # GPU chat engine (RTX 4050, port 11434)
├── Modelfile.cpu              # CPU extractor (8 P-cores, port 11435)
├── start_engines.ps1          # Dual-engine launcher & health checker
├── requirements.txt           # Python dependencies
├── backend/
│   ├── main.py                # FastAPI app + SSE endpoints
│   ├── memory_engine.py       # Background triage & context injection
│   ├── persona_compiler.py    # Wiki-to-persona-card compiler
│   └── storage.py             # Atomic JSON I/O with async mutex
├── data/
│   ├── user_profile.json      # Persistent user facts
│   ├── reminders.json         # Task/reminder items
│   ├── work_context.json      # Rolling 15-item work log
│   └── personas/              # 13 pre-compiled persona cards
├── frontend/
│   ├── index.html             # Single-page app (dark-mode)
│   ├── app.js                 # SSE streaming client + UI logic
│   └── styles.css             # Matte dark theme (#0c0d10 / #14171f)
├── tests/                     # Pytest unit + adversarial test suite
└── e2e_tests/                 # 305-test tier 1-4 E2E suite
```

---

## Prerequisites

| Requirement | Version | Check Command |
|---|---|---|
| Windows 11 | Any | — |
| PowerShell | 7+ | `$PSVersionTable.PSVersion` |
| Python | 3.10+ | `python --version` |
| Ollama | Latest | `ollama --version` |
| NVIDIA Driver | 535+ | `nvidia-smi` |

### Install Ollama (if not installed)
```powershell
winget install Ollama.Ollama
```

### Pull the model (one-time, ~2 GB)
```powershell
ollama pull artifish/llama3.2-uncensored:3b
```

---

## Step 1 — Install Python Dependencies

Open PowerShell 7 in `D:\hahaha\Project Janus`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## Step 2 — Launch the Dual Ollama Engines

```powershell
.\start_engines.ps1
```

**What this does:**
1. Locates your Ollama executable
2. Spawns GPU Engine on 127.0.0.1:11434 with OLLAMA_FLASH_ATTENTION=1
3. Spawns CPU Engine on 127.0.0.1:11435 with CUDA_VISIBLE_DEVICES="" (CPU-only)
4. Compiles janus-chat and janus-extractor models from the Modelfiles
5. Polls both endpoints until HTTP 200 or 30s timeout

**Optional flags:**
```powershell
.\start_engines.ps1 -SkipModelCompile   # Skip recompiling if models exist
.\start_engines.ps1 -ForceRestart       # Kill existing processes first
.\start_engines.ps1 -TimeoutSeconds 60  # Custom health check timeout
```

---

## Step 3 — Start the FastAPI Backend

In a new PowerShell window (with venv active):

```powershell
cd "D:\hahaha\Project Janus"
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend: http://127.0.0.1:8000
API Docs: http://127.0.0.1:8000/docs

---

## Step 4 — Open the Frontend

Navigate to: http://127.0.0.1:8000

---

## Using Project Janus

### Assistant Mode
- Chat naturally: "remind me to submit the report by Friday"
- Reminders extracted by CPU engine appear in the sidebar
- Check off completed reminders with sidebar checkboxes
- Work notes accumulate in the Memory Inspector accordion

### Persona Mode
- Click the Persona toggle in the header
- Select any of the 13 pre-loaded persona cards
- Chat proceeds in-character with the selected persona

### Compiling a New Persona from Raw Text
1. Switch to Persona mode
2. Click "Ingest Wiki/Text"
3. Paste raw Wikipedia article or biography
4. Enter character name and click Compile
5. New persona card saved to data/personas/

### Incognito Mode
- Toggle Incognito in the header
- UI tint changes to indicate active incognito
- Zero triage — nothing written to disk
- Toggle off to resume persistent memory

---

## Running the Test Suite

```powershell
cd "D:\hahaha\Project Janus"
.\.venv\Scripts\Activate.ps1

# Unit tests
pytest tests/ -v

# Specific test files
pytest tests/test_engines.py -v
pytest tests/test_memory_triage.py -v
pytest tests/test_storage.py -v
pytest tests/test_adversarial_stress.py -v

# Full E2E suite
python e2e_tests/runner.py

# Expected: 381/381 passed (61 unit + 305 E2E + 15 adversarial)
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | /api/chat/stream | SSE streaming chat via GPU engine |
| GET | /api/state | Returns reminders, work notes, facts, personas |
| POST | /api/personas/compile | Compile raw text into persona card |
| GET | /docs | Swagger API documentation |

### Example: Chat via curl
```powershell
curl -N -X POST http://127.0.0.1:8000/api/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"message": "Remind me to review the budget on Monday", "incognito": false, "persona": null}'
```

### Example: Compile a persona
```powershell
curl -X POST http://127.0.0.1:8000/api/personas/compile `
  -H "Content-Type: application/json" `
  -d '{"raw_text": "Nikola Tesla was a Serbian-American inventor...", "character_name": "nikola_tesla"}'
```

### Example: Get full state
```powershell
curl http://127.0.0.1:8000/api/state
```

---

## Pre-loaded Persona Cards

| File | Persona |
|---|---|
| executive_assistant.json | Default work assistant |
| aristotle.json | Philosopher & polymath |
| julius_caesar.json | Roman general & statesman |
| hypatia.json | Mathematician & philosopher |
| leonardo_da_vinci.json | Renaissance polymath |
| marcus_aurelius.json | Stoic emperor |
| ada_lovelace.json | Pioneer programmer |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| ollama: command not found | Add Ollama to PATH or reinstall |
| Port 11434/11435 already in use | Run .\start_engines.ps1 -ForceRestart |
| janus-chat model not found | Re-run .\start_engines.ps1 |
| Backend ModuleNotFoundError | Ensure venv is active |
| Frontend shows no stream | Verify backend on 8000 and GPU engine on 11434 |
| Model pull fails | Run: ollama pull llama3.2:3b (fallback) |

---

## Stopping the System

```powershell
# Stop backend: Ctrl+C in the uvicorn window

# Stop Ollama engines
Stop-Process -Name "ollama" -Force

# Or kill by port
netstat -ano | findstr :11434
Stop-Process -Id <PID> -Force
```

---

*Project Janus — Dual-engine local AI. All processing local. Zero telemetry. Zero cloud.*
