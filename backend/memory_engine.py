"""
Project Janus - Autonomous Memory Engine & Context Injection Subsystem
Executes background extraction and triage on CPU Engine (Port 11435) with format: json.
Maintains persistent cognitive state across user profile, active reminders, and rolling work context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend import storage

logger = logging.getLogger("janus.memory_engine")

# Engine connection defaults
CPU_ENGINE_URL = os.environ.get("JANUS_CPU_URL", "http://127.0.0.1:11435")
EXTRACTOR_MODEL = os.environ.get("JANUS_EXTRACTOR_MODEL", "janus-extractor")


def _validate_due_date(val: Any) -> Optional[str]:
    """Validate that due_date string is a real calendar date in YYYY-MM-DD format."""
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        try:
            datetime.strptime(val, "%Y-%m-%d")
            return val
        except ValueError:
            return None
    return None


TRIAGE_SYSTEM_PROMPT = """You are an ultra-fast, deterministic background triage and data extraction engine for Project Janus.
Analyze the provided conversation turn between the User and the Assistant.
Extract:
1. Any new reminders, action items, or scheduled commitments mentioned.
2. A concise work context note summarizing the turn if substantive work or planning occurred.
3. Any personal user facts, preferences, role information, or tooling choices revealed.

You must output strictly a single valid JSON object complying exactly with this schema:
{
  "reminders": [
    {
      "text": "Exact actionable reminder text",
      "due_date": "YYYY-MM-DD or null if unspecified",
      "priority": "low" | "medium" | "high"
    }
  ],
  "work_note": {
    "note": "Concise summary of work progress, blocker, decision, or context",
    "category": "progress" | "decision" | "blocker" | "context"
  },
  "facts": [
    "Atomic factual statement about user preferences, environment, or profile"
  ],
  "preferences": [
    "Specific user preference mentioned"
  ]
}

Rules:
- If no reminders were mentioned, return "reminders": [].
- If turn is casual chat, greeting, or contains no substantive work, return "work_note": null.
- If no new facts/preferences were revealed, return "facts": [] and "preferences": [].
- Mark priority as "high" if marked urgent, critical, or time-sensitive.
- Output ONLY valid unescaped JSON. NEVER output markdown fences or explanatory text.
"""


def _clean_json_output(raw_text: str) -> Optional[dict[str, Any]]:
    """
    Safely clean and parse JSON from LLM output:
    1. Strips markdown fences (```json ... ```).
    2. Strips conversational preamble before first '{' and trailing text after last '}'.
    3. Handles malformed JSON gracefully.
    """
    if not raw_text or not raw_text.strip():
        return None

    cleaned = raw_text.strip()

    # Strip markdown code blocks if present
    if "```" in cleaned:
        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if code_block_match:
            cleaned = code_block_match.group(1).strip()

    # Locate first { and last } to strip preambles and postscripts
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")

    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_str = cleaned[first_brace:last_brace + 1].strip()
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as err:
            logger.debug("Failed to parse JSON substring: %s", err)

    # Direct fallback attempt
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    return None


def _heuristic_triage(user_message: str, assistant_reply: str) -> dict[str, Any]:
    """
    Rule-based deterministic extraction heuristic.
    Guarantees reliable triage functionality when the offline CPU Ollama daemon
    is unreachable or transitioning.
    """
    extracted: dict[str, Any] = {
        "reminders": [],
        "work_note": None,
        "facts": [],
        "preferences": []
    }

    user_lower = user_message.lower()

    # 1. Reminder heuristics
    # Patterns like "remind me to...", "remember to...", "urgent:...", "todo:..."
    reminder_patterns = [
        r"(?:remind\s+me\s+to|remember\s+to|don't\s+forget\s+to)\s+([^.!?\n]+)",
        r"(?:todo|action\s+item):\s*([^.!?\n]+)",
        r"(?:urgent|critical):\s*([^.!?\n]+)"
    ]

    for pat in reminder_patterns:
        match = re.search(pat, user_message, re.IGNORECASE)
        if match:
            task_text = match.group(1).strip()
            # Clean trailing punctuation
            task_text = re.sub(r"[.!?]+$", "", task_text)
            if len(task_text) > 3:
                is_urgent = any(kw in user_lower for kw in ["urgent", "asap", "critical", "immediately", "today", "emergency"])
                # Check for due date hints
                due_date = None
                date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", user_message)
                if date_match:
                    due_date = date_match.group(1)

                extracted["reminders"].append({
                    "text": task_text,
                    "due_date": due_date,
                    "priority": "high" if is_urgent else "medium"
                })
                break

    # 2. Work context note heuristics
    # If user describes current tasks, progress, or decisions
    work_patterns = [
        r"(?:i\s+am\s+working\s+on|currently\s+building|we\s+are\s+planning|objective\s+is|focus\s+today\s+is)\s+([^.!?\n]+)",
        r"(?:finished|completed|implemented|shipped)\s+([^.!?\n]+)"
    ]

    for pat in work_patterns:
        match = re.search(pat, user_message, re.IGNORECASE)
        if match:
            work_text = match.group(0).strip()
            category = "progress" if any(w in user_lower for w in ["finished", "completed", "shipped"]) else "context"
            extracted["work_note"] = {
                "note": work_text,
                "category": category
            }
            break

    # 3. User profile facts & preferences heuristics
    fact_patterns = [
        r"(?:i\s+prefer|my\s+preference\s+is)\s+([^.!?\n]+)",
        r"(?:i\s+am\s+a|my\s+role\s+is)\s+([^.!?\n]+)"
    ]

    for pat in fact_patterns:
        match = re.search(pat, user_message, re.IGNORECASE)
        if match:
            extracted["facts"].append(match.group(0).strip())

    return extracted


async def extract_and_triage(user_message: str, assistant_reply: str, mode: str = "assistant") -> dict[str, Any]:
    """
    Asynchronously analyze a completed conversation turn via CPU Ollama engine (Port 11435).
    Extracts action items, work notes, and user facts, and atomically updates local storage.
    If mode != "assistant" (e.g. persona or adventure), personal memory extraction is suppressed
    to prevent fictional narrative from contaminating real personal state.
    """
    # Guard: separate real personal memory from fictional persona/adventure state
    if mode != "assistant":
        logger.debug("Memory triage: mode is %s, suppressing personal cognitive extraction.", mode)
        return {"reminders": [], "work_note": None, "facts": [], "preferences": []}

    user_msg_clean = (user_message or "").strip()
    asst_reply_clean = (assistant_reply or "").strip()

    # Fast-path no-op for trivial inputs or casual greetings
    if len(user_msg_clean) < 4:
        return {"reminders": [], "work_note": None, "facts": [], "preferences": []}

    trivial_greetings = {"hi", "hello", "hey", "ping", "test", "thanks", "thank you", "good morning", "good evening"}
    if user_msg_clean.lower() in trivial_greetings and len(asst_reply_clean) < 120:
        return {"reminders": [], "work_note": None, "facts": [], "preferences": []}

    # Conversational reminder completion check:
    # If user says "I finished...", "completed...", "done with...", match active reminders
    user_lower = user_msg_clean.lower()
    if any(k in user_lower for k in ["done with", "finished", "completed", "cancel reminder", "remove reminder", "delete reminder"]):
        try:
            existing_reminders = await storage.load_reminders()
            for r in existing_reminders:
                if not r.get("completed", False):
                    r_text = str(r.get("text", "")).strip().lower()
                    if r_text and (r_text in user_lower or any(word in user_lower for word in r_text.split() if len(word) > 4)):
                        await storage.update_reminder(r["id"], {"completed": True})
                        logger.info("Conversational reminder completion: marked reminder %s completed", r["id"])
        except Exception as e:
            logger.debug("Conversational reminder completion check error: %s", e)

    triage_result: Optional[dict[str, Any]] = None

    # Supply explicit current date and UTC timezone for resolving relative dates
    current_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt_content = (
        f"Current Date: {current_date_str} (UTC)\n\n"
        f"Conversation Turn to Analyze:\n"
        f"User: {user_msg_clean}\n"
        f"Assistant: {asst_reply_clean}"
    )

    client_timeout = httpx.Timeout(connect=1.5, read=15.0, write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            resp = await client.post(
                f"{CPU_ENGINE_URL.rstrip('/')}/api/chat",
                json={
                    "model": EXTRACTOR_MODEL,
                    "messages": [
                        {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt_content}
                    ],
                    "format": "json",
                    "stream": False
                }
            )
            if resp.status_code == 200:
                body = resp.json()
                content = body.get("message", {}).get("content", "")
                triage_result = _clean_json_output(content)
            elif resp.status_code == 404:
                # Fallback to /api/generate endpoint if /api/chat not configured on CPU engine
                gen_resp = await client.post(
                    f"{CPU_ENGINE_URL.rstrip('/')}/api/generate",
                    json={
                        "model": EXTRACTOR_MODEL,
                        "system": TRIAGE_SYSTEM_PROMPT,
                        "prompt": prompt_content,
                        "format": "json",
                        "stream": False
                    }
                )
                if gen_resp.status_code == 200:
                    gen_body = gen_resp.json()
                    triage_result = _clean_json_output(gen_body.get("response", ""))
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as conn_err:
        logger.debug("CPU Ollama engine at %s unavailable (%s); using heuristic triage.", CPU_ENGINE_URL, conn_err)
    except Exception as exc:
        logger.warning("Error invoking CPU extraction engine: %s", exc)

    # If CPU engine unavailable or failed to produce valid JSON, use heuristic extractor
    if not triage_result:
        triage_result = _heuristic_triage(user_msg_clean, asst_reply_clean)

    # ------------------------------------------------------------------
    # Apply Extracted State to Storage
    # ------------------------------------------------------------------

    # 1. Reminders with deduplication & date/priority validation
    raw_reminders = triage_result.get("reminders")
    if isinstance(raw_reminders, list):
        try:
            existing_all = await storage.load_reminders()
            existing_active_texts = {
                str(r.get("text", "")).strip().lower()
                for r in existing_all
                if isinstance(r, dict) and not r.get("completed", False)
            }
        except Exception:
            existing_active_texts = set()

        for r_item in raw_reminders:
            if isinstance(r_item, dict):
                text = str(r_item.get("text", "")).strip()
                if text and text.lower() not in existing_active_texts:
                    is_urgent = any(w in text.lower() or w in user_msg_clean.lower() for w in ["urgent", "asap", "critical", "immediately"])
                    prio = "high" if is_urgent else str(r_item.get("priority", "medium")).lower()
                    if prio not in ["low", "medium", "high"]:
                        prio = "medium"
                    due_date = _validate_due_date(r_item.get("due_date"))
                    await storage.add_reminder({
                        "text": text,
                        "due_date": due_date,
                        "priority": prio,
                        "completed": False
                    })
                    existing_active_texts.add(text.lower())
            elif isinstance(r_item, str) and r_item.strip():
                text = r_item.strip()
                if text.lower() not in existing_active_texts:
                    is_urgent = "urgent" in text.lower()
                    await storage.add_reminder({
                        "text": text,
                        "due_date": None,
                        "priority": "high" if is_urgent else "medium",
                        "completed": False
                    })
                    existing_active_texts.add(text.lower())

    # 2. Work Context
    work_note = triage_result.get("work_note")
    # Support alternate key work_notes
    if not work_note and "work_notes" in triage_result:
        wn_val = triage_result["work_notes"]
        if isinstance(wn_val, list) and wn_val:
            work_note = wn_val[0]
        elif isinstance(wn_val, (dict, str)):
            work_note = wn_val

    if work_note:
        note_content: str = ""
        category: str = "context"
        if isinstance(work_note, dict):
            note_content = str(work_note.get("note") or work_note.get("summary") or "").strip()
            category = str(work_note.get("category", "context")).strip()
        elif isinstance(work_note, str):
            note_content = work_note.strip()

        if note_content:
            await storage.append_work_context(note_content, category=category)

    # 3. User Profile Facts & Preferences
    raw_facts = triage_result.get("facts", [])
    if isinstance(raw_facts, str):
        raw_facts = [raw_facts]
    raw_prefs = triage_result.get("preferences", [])
    if isinstance(raw_prefs, str):
        raw_prefs = [raw_prefs]

    if (isinstance(raw_facts, list) and raw_facts) or (isinstance(raw_prefs, list) and raw_prefs):
        try:
            def mutate_profile(profile: dict[str, Any]) -> bool:
                existing_facts: list[str] = list(profile.get("facts", []))
                normalized_facts = {f.strip().lower() for f in existing_facts if isinstance(f, str)}
                profile_modified = False

                if isinstance(raw_facts, list):
                    for fact in raw_facts:
                        if isinstance(fact, str):
                            clean_fact = fact.strip()
                            if clean_fact and clean_fact.lower() not in normalized_facts:
                                existing_facts.append(clean_fact)
                                normalized_facts.add(clean_fact.lower())
                                profile_modified = True

                # Cap facts at 50
                profile["facts"] = existing_facts[-50:]

                # Preferences
                existing_prefs = profile.get("preferences")
                if isinstance(existing_prefs, list) and isinstance(raw_prefs, list):
                    for pref in raw_prefs:
                        if isinstance(pref, str):
                            clean_pref = pref.strip()
                            if clean_pref and clean_pref not in existing_prefs:
                                existing_prefs.append(clean_pref)
                                profile_modified = True
                    profile["preferences"] = existing_prefs

                return profile_modified

            await storage.update_user_profile_atomic(mutate_profile)
        except Exception as exc:
            logger.error("Error updating user profile during triage: %s", exc)

    return triage_result


def _extract_roleplay_style(persona: Optional[dict[str, Any]]) -> str:
    """Safely extract roleplay_style from persona, forge_schema, or character_data."""
    if not isinstance(persona, dict):
        return ""
    if persona.get("roleplay_style") is not None:
        val = str(persona["roleplay_style"]).strip()
        if val:
            return val
    forge_schema = persona.get("forge_schema")
    if isinstance(forge_schema, dict) and forge_schema.get("roleplay_style") is not None:
        val = str(forge_schema["roleplay_style"]).strip()
        if val:
            return val
    char_data = persona.get("character_data")
    if isinstance(char_data, dict) and char_data.get("roleplay_style") is not None:
        val = str(char_data["roleplay_style"]).strip()
        if val:
            return val
    return ""


async def inject_context(
    base_system_prompt: str = "",
    mode: str = "assistant",
    persona: Optional[dict[str, Any]] = None,
    incognito: bool = False
) -> str:
    """
    Synthesizes active cognitive state (user profile, pending reminders, rolling work context)
    into a structured markdown system prompt string for the GPU chat engine.

    Incognito mode completely purges persistent user facts, active reminders, and work notes.
    """
    # ------------------------------------------------------------------
    # Incognito Mode Guard: Zero Private Data Injected
    # ------------------------------------------------------------------
    if incognito:
        if mode == "persona" and persona:
            persona_name = str(persona.get("name") or "Character").strip() or "Character"
            persona_prompt = str(persona.get("system_prompt") or base_system_prompt or "You are a roleplay character.")
            roleplay_style = _extract_roleplay_style(persona)

            # Extract additional schema attributes
            forge_s = persona.get("forge_schema") if isinstance(persona.get("forge_schema"), dict) else {}
            traits = persona.get("traits") or persona.get("personality_traits") or forge_s.get("personality", {}).get("core_traits", [])
            flaws = forge_s.get("personality", {}).get("flaws", [])
            body_lang = forge_s.get("physicality", {}).get("body_language", "")
            boundaries = persona.get("mature_themes", {}).get("boundaries", "") if isinstance(persona.get("mature_themes"), dict) else forge_s.get("mature_themes", {}).get("boundaries", "")

            roleplay_directive = (
                f"You are now roleplaying as {persona_name}. "
                "Respond ONLY as this character. "
                "Use asterisks like *this* to describe your actions, expressions, and body language. "
                "Never break character. Never refer to yourself as an AI, assistant, or language model. "
                "Maintain active, engaging conversation at all times: converse directly, advance the interaction, ask questions or take initiative, and never avoid engagement or provide generic AI responses. "
                "Fully embody the character's speech style, worldview, and emotional state in every single response."
            )
            extra_sections = []
            if traits:
                clean_t = [str(t).strip() for t in traits if t and str(t).strip()] if isinstance(traits, (list, tuple)) else [str(traits).strip()]
                if clean_t:
                    extra_sections.append(f"[Character Traits]\n{', '.join(clean_t)}")
            if flaws:
                clean_f = [str(f).strip() for f in flaws if f and str(f).strip()] if isinstance(flaws, (list, tuple)) else [str(flaws).strip()]
                if clean_f:
                    extra_sections.append(f"[Character Flaws]\n{', '.join(clean_f)}")
            if body_lang:
                extra_sections.append(f"[Habitual Body Language]\n{body_lang}")
            if boundaries:
                extra_sections.append(f"[Boundaries & Thematic Limits]\n{boundaries}")
            if roleplay_style and roleplay_style not in persona_prompt:
                extra_sections.append(
                    f"[Roleplay Style & Directives]\n{roleplay_style}\n"
                    "Strong Enforcement: Strictly adhere to the narrative tone, formatting rules, setting, and behavioral rules defined above. "
                    "Actively converse without avoiding interaction."
                )

            extra_str = ("\n\n" + "\n\n".join(extra_sections)) if extra_sections else ""
            incog_prompt = f"[Roleplay Instructions]\n{roleplay_directive}\n\n[Character Identity]\n{persona_prompt}{extra_str}"
            if len(incog_prompt) > 6000:
                incog_prompt = incog_prompt[:6000] + "\n\n[Context truncated for token budget]\n[Roleplay Enforcement]: Maintain active, engaging conversation without avoiding interaction. Never break character."
            return incog_prompt

        return base_system_prompt or (
            "You are Janus, an elite executive assistant running 100% locally and offline. "
            "You communicate with precision, depth, and intelligence. "
            "Incognito mode is active: maintain strict privacy and zero cognitive recording."
        )

    # ------------------------------------------------------------------
    # Load Cognitive State from Storage
    # ------------------------------------------------------------------
    try:
        profile = await storage.load_user_profile()
    except Exception:
        profile = {"name": "Demi", "role": "Lead Architect", "facts": [], "preferences": []}

    try:
        reminders = await storage.load_reminders()
    except Exception:
        reminders = []

    try:
        work_context = await storage.load_work_context()
    except Exception:
        work_context = []

    # Filter to uncompleted active reminders only
    active_reminders = [r for r in reminders if isinstance(r, dict) and not r.get("completed", False)]

    # ------------------------------------------------------------------
    # Assemble Persona vs Assistant Prompt
    # ------------------------------------------------------------------
    sections: list[str] = []

    if mode == "persona" and persona:
        persona_name = str(persona.get("name") or "Character").strip() or "Character"
        persona_prompt = str(persona.get("system_prompt") or base_system_prompt or "You are a roleplay character.")
        roleplay_style = _extract_roleplay_style(persona)

        # Extract schema attributes
        forge_s = persona.get("forge_schema") if isinstance(persona.get("forge_schema"), dict) else {}
        flaws = forge_s.get("personality", {}).get("flaws", [])
        body_lang = forge_s.get("physicality", {}).get("body_language", "")
        boundaries = persona.get("mature_themes", {}).get("boundaries", "") if isinstance(persona.get("mature_themes"), dict) else forge_s.get("mature_themes", {}).get("boundaries", "")

        roleplay_directive = (
            f"You are now roleplaying as {persona_name}. "
            "Respond ONLY as this character. "
            "Use asterisks like *this* to describe your actions, expressions, and body language. "
            "Never break character. Never refer to yourself as an AI, assistant, or language model. "
            "Maintain active, engaging conversation at all times: converse directly, advance the interaction, ask questions or take initiative, and never avoid engagement or provide generic AI responses. "
            "Fully embody the character's speech style, worldview, and emotional state in every single response."
        )
        sections.append(f"[Roleplay Instructions]\n{roleplay_directive}\n\n[Character Identity]\n{persona_prompt}")

        if roleplay_style and roleplay_style not in str(persona_prompt):
            sections.append(
                f"[Roleplay Style & Directives]\n{roleplay_style}\n"
                "Strong Enforcement: Strictly adhere to the narrative tone, formatting rules, setting, and behavioral rules defined above. "
                "Actively converse and engage without avoiding interaction."
            )

        if persona.get("tagline"):
            sections.append(f"[Tagline]\n{persona.get('tagline')}")

        traits = persona.get("traits") or persona.get("personality_traits") or forge_s.get("personality", {}).get("core_traits", [])
        if traits:
            if isinstance(traits, (list, tuple, set)):
                clean_traits = [str(t).strip() for t in traits if t is not None and str(t).strip()]
                if clean_traits:
                    sections.append(f"[Character Traits]\n" + ", ".join(clean_traits))
            elif isinstance(traits, str) and traits.strip():
                sections.append(f"[Character Traits]\n{traits.strip()}")

        if flaws:
            clean_flaws = [str(f).strip() for f in flaws if f and str(f).strip()] if isinstance(flaws, (list, tuple)) else [str(flaws).strip()]
            if clean_flaws:
                sections.append(f"[Character Flaws]\n" + ", ".join(clean_flaws))

        if body_lang:
            sections.append(f"[Habitual Body Language]\n{body_lang}")

        if boundaries:
            sections.append(f"[Boundaries & Thematic Limits]\n{boundaries}")

        # Contextual awareness of who they are talking to
        user_name = profile.get("name", "Demi")
        user_role = profile.get("role", "Lead Architect")
        sections.append(f"[Interlocutor]\nYou are in dialogue with {user_name} ({user_role}).")

    else:
        # Default Executive Assistant Mode
        identity = base_system_prompt.strip() or (
            "You are Janus, an elite executive assistant and multi-persona cognitive engine running 100% locally and offline.\n"
            "You provide rigorous, structured, and proactive strategic operational support.\n"
            "Maintain an authoritative yet collaborative, efficient, and discreet tone at all times.\n"
            "You must address the user strictly as 'Demi'."
        )
        sections.append(f"[System Identity]\n{identity}")

        # User Profile Block
        user_name = profile.get("name", "Demi")
        user_role = profile.get("role", "Lead Architect")
        profile_lines = [f"Name: {user_name}", f"Role: {user_role}"]

        facts = profile.get("facts", [])
        if facts and isinstance(facts, list):
            profile_lines.append("Known Facts:")
            for f in facts[-8:]:  # Include up to 8 most recent facts
                profile_lines.append(f"- {f}")

        prefs = profile.get("preferences", [])
        if prefs:
            if isinstance(prefs, list):
                profile_lines.append("Preferences: " + ", ".join(str(p) for p in prefs))
            elif isinstance(prefs, dict):
                profile_lines.append("Preferences: " + ", ".join(f"{k}: {v}" for k, v in prefs.items()))

        sections.append("[User Profile]\n" + "\n".join(profile_lines))

        # Active Work Context (Rolling Session Notes)
        if work_context:
            context_lines = []
            for item in work_context[-6:]:  # Include up to 6 most recent session notes
                ts = item.get("timestamp", "")
                ts_short = ts.split("T")[-1][:5] if "T" in ts else ts
                note = item.get("note") or item.get("summary") or ""
                cat = item.get("category", "context")
                if note:
                    context_lines.append(f"- [{ts_short}] ({cat}): {note}")
            if context_lines:
                sections.append("[Active Work Context (Recent Notes)]\n" + "\n".join(context_lines))

        # Pending Reminders Block - Ordered by urgency and earliest due date
        if active_reminders:
            prio_weights = {"high": 0, "medium": 1, "low": 2}
            sorted_reminders = sorted(
                active_reminders,
                key=lambda r: (
                    prio_weights.get(str(r.get("priority", "medium")).lower(), 1),
                    r.get("due_date") or "9999-99-99"
                )
            )
            reminder_lines = []
            for r in sorted_reminders[:8]:  # Up to 8 highest-priority / earliest due reminders
                prio = str(r.get("priority", "medium")).upper()
                due_info = f" (Due: {r['due_date']})" if r.get("due_date") else ""
                reminder_lines.append(f"- [{prio}] {r.get('text', '')}{due_info}")
            if reminder_lines:
                sections.append("[Pending Reminders]\n" + "\n".join(reminder_lines))

        sections.append(
            "[Executive Directive]\n"
            "Respond proactively, accurately, and concisely. Keep pending tasks and active work context in mind. "
            "Never fabricate state; communicate with clarity, technical rigor, and zero cloud telemetry."
        )

    # Context budget: GPU context is 4096 tokens (~16,000 chars total across prompt + history + generation)
    # Bound system context to 6,000 characters to reserve ample budget for dialogue history and response.
    full_prompt = "\n\n".join(sections)
    if len(full_prompt) > 6000:
        trunc_enforcement = ""
        if mode == "persona":
            trunc_enforcement = "\n[Roleplay Enforcement]: Maintain active, engaging conversation without avoiding interaction. Never break character."
        full_prompt = full_prompt[:6000] + f"\n\n[Context truncated for token budget]{trunc_enforcement}"

    return full_prompt
