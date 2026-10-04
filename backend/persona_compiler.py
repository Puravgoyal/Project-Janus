"""
Project Janus - Persona Compiler Subsystem
Condenses raw biographical text and Wikipedia articles into structured persona cards
via the CPU Ollama engine on Port 11435 with format: json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Optional, Union

import httpx
from pydantic import BaseModel, Field, field_validator

from backend import storage

logger = logging.getLogger("janus.persona_compiler")

CPU_ENGINE_URL = os.environ.get("JANUS_CPU_URL", "http://127.0.0.1:11435")
EXTRACTOR_MODEL = os.environ.get("JANUS_EXTRACTOR_MODEL", "janus-extractor")

# ----------------------------------------------------------------------
# Character Forge Pydantic Schemas (Project Janus Forge Specification)
# ----------------------------------------------------------------------

class PersonalitySchema(BaseModel):
    archetype: str = Field(default="Independent Agent", description="Character archetype, e.g. 'Stoic Commander' or 'Cynical Hacker'")
    core_traits: list[str] = Field(default_factory=lambda: ["Perceptive", "Determined", "Resourceful", "Adaptable"], description="List of personality trait adjectives")
    flaws: list[str] = Field(default_factory=lambda: ["Guarded", "Restless"], description="List of character flaws or vulnerabilities")

    @field_validator("archetype", mode="before")
    @classmethod
    def normalize_archetype(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "Independent Agent"
        return str(v).strip()

    @field_validator("core_traits", mode="before")
    @classmethod
    def normalize_core_traits(cls, v: Any) -> list[str]:
        if v is None:
            return ["Perceptive", "Determined", "Resourceful", "Adaptable"]
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        if isinstance(v, (list, tuple)):
            return [str(t).strip() for t in v if t is not None and str(t).strip()]
        return ["Perceptive", "Determined", "Resourceful", "Adaptable"]

    @field_validator("flaws", mode="before")
    @classmethod
    def normalize_flaws(cls, v: Any) -> list[str]:
        if v is None:
            return ["Guarded", "Restless"]
        if isinstance(v, str):
            res = [f.strip() for f in v.split(",") if f.strip()]
            return res if res else ["Guarded", "Restless"]
        if isinstance(v, (list, tuple)):
            res = [str(f).strip() for f in v if f is not None and str(f).strip()]
            return res if res else ["Guarded", "Restless"]
        return ["Guarded", "Restless"]

    class Config:
        extra = "allow"


class EmotionSchema(BaseModel):
    default_mood: str = Field(default="Composed", description="Baseline emotional temperament")
    reaction_to_stress: str = Field(default="Focused vigilance", description="Behavioral reaction under intense pressure")
    speech_style: str = Field(default="Clear and deliberate", description="Linguistic patterns, cadence, and tone")

    @field_validator("default_mood", "reaction_to_stress", "speech_style", mode="before")
    @classmethod
    def normalize_emotion_strings(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "Composed"
        return str(v).strip()

    class Config:
        extra = "allow"


class PhysicalitySchema(BaseModel):
    appearance: str = Field(default="Distinctive demeanor", description="Visual aesthetic, garments, and distinctive features")
    body_language: str = Field(default="Balanced and purposeful", description="Habitual postures, gestures, and kinetic habits")

    @field_validator("appearance", "body_language", mode="before")
    @classmethod
    def normalize_physicality_strings(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "Distinctive"
        return str(v).strip()

    class Config:
        extra = "allow"


class MatureThemesSchema(BaseModel):
    nsfw_enabled: bool = Field(default=False, description="Whether mature/NSFW themes are enabled")
    boundaries: str = Field(default="", description="Explicit boundaries and thematic limits")
    mature_dynamics: str = Field(default="", description="Mature relationship dynamics; populated only if nsfw_enabled is true")

    @field_validator("boundaries", "mature_dynamics", mode="before")
    @classmethod
    def normalize_mature_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("nsfw_enabled", mode="before")
    @classmethod
    def normalize_nsfw_enabled(cls, v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "t")
        return bool(v)

    class Config:
        extra = "allow"


class CharacterForgeSchema(BaseModel):
    name: Optional[str] = Field(default=None, description="Full character name")
    personality: PersonalitySchema = Field(default_factory=PersonalitySchema)
    emotion: EmotionSchema = Field(default_factory=EmotionSchema)
    physicality: PhysicalitySchema = Field(default_factory=PhysicalitySchema)
    mature_themes: MatureThemesSchema = Field(default_factory=MatureThemesSchema)
    roleplay_style: Optional[str] = Field(default="", max_length=4000, description="Narrative tone, formatting rules, setting, and dialogue directives")

    @field_validator("roleplay_style", mode="before")
    @classmethod
    def normalize_roleplay_style(cls, v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        if len(s) > 4000:
            s = s[:4000].rstrip()
        return s

    @field_validator("personality", "emotion", "physicality", "mature_themes", mode="before")
    @classmethod
    def normalize_subschemas(cls, v: Any) -> Any:
        if v is None:
            return {}
        return v

    class Config:
        extra = "allow"


# Model Aliases for Interoperability
CharacterPersonality = PersonalitySchema
CharacterEmotion = EmotionSchema
CharacterPhysicality = PhysicalitySchema
CharacterMatureThemes = MatureThemesSchema
CharacterSchema = CharacterForgeSchema


class EnhanceRequest(BaseModel):
    base_prompt: str = Field(..., description="Base concept or backstory prompt")
    allow_nsfw: Optional[bool] = Field(default=False, description="Enable mature/NSFW thematic generation")

    @field_validator("allow_nsfw", mode="before")
    @classmethod
    def normalize_allow_nsfw(cls, v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "t")
        return bool(v)


PersonaEnhanceRequest = EnhanceRequest


class ForgeRequest(BaseModel):
    character_data: CharacterForgeSchema = Field(..., description="Character schema data")
    incognito: Optional[bool] = Field(default=False, description="Incognito mode toggle")

    @field_validator("incognito", mode="before")
    @classmethod
    def normalize_incognito(cls, v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "t")
        return bool(v)


PersonaForgeRequest = ForgeRequest


class ForgeResponse(BaseModel):
    status: str = "success"
    saved: bool
    incognito: bool
    name: Optional[str] = ""
    personality: Optional[PersonalitySchema] = None
    emotion: Optional[EmotionSchema] = None
    physicality: Optional[PhysicalitySchema] = None
    mature_themes: Optional[MatureThemesSchema] = None
    roleplay_style: Optional[str] = ""
    persona: Optional[dict[str, Any]] = None
    character: Optional[dict[str, Any]] = None

    class Config:
        extra = "allow"


PERSONA_COMPILER_SYSTEM_PROMPT = """You are an expert character architect and biographical synthesis engine for Project Janus.
Condense the provided raw biographical or character documentation into a rich, structured persona card.
Output ONLY a single valid JSON object strictly complying with this schema:
{
  "id": "slug_identifier_lowercase_underscores",
  "name": "Full Character Name",
  "tagline": "Concise evocative title or role descriptor (1 sentence)",
  "avatar": "Single emoji glyph (e.g. 🏛️, 🔬, 👑, 📜, ⚔️, 🎨)",
  "description": "Comprehensive 2-3 sentence character synopsis",
  "system_prompt": "You are {name}. Speak in the authentic voice, philosophy, and temperament of this character. Embody your historical worldview, mannerisms, and convictions. Never break character or refer to yourself as an AI.",
  "greeting": "A characteristic opening greeting in the persona's voice",
  "traits": ["trait1", "trait2", "trait3", "trait4"],
  "personality": ["trait1", "trait2", "trait3", "trait4"],
  "speaking_style": {
    "tone": "e.g. Formal, introspective, witty, or commanding",
    "vocabulary": "Key stylistic markers and linguistic habits",
    "aphorisms": ["Emblematic quote or philosophical maxim"]
  },
  "world_knowledge": [
    "Key historical domain, work, or subject area"
  ],
  "example_dialogue": {
    "user": "A representative greeting or inquiry",
    "assistant": "An authentic in-character response demonstrating tone and personality"
  }
}

Rules:
- Generate an authentic system prompt that captures the essence of the character.
- Output ONLY valid JSON without markdown code fences or explanatory text.
"""


def _generate_slug(character_name: str) -> str:
    """Derives a normalized lowercase slug identifier from a character name."""
    clean = character_name.strip().lower()
    clean = re.sub(r"[^\w\s-]", "", clean)
    slug = re.sub(r"[-\s]+", "_", clean)
    slug = slug[:64].rstrip("-_")
    return slug or f"persona_{uuid.uuid4().hex[:6]}"


def _pick_avatar_emoji(name: str, text: str) -> str:
    """Heuristically select a relevant emoji glyph based on character role and content."""
    combined = (name + " " + text).lower()
    if any(k in combined for k in ["philosopher", "philosophy", "stoic", "socrates", "seneca", "plato", "aristotle", "aurelius", "roman", "greek"]):
        return "🏛️"
    if any(k in combined for k in ["scientist", "physics", "math", "astronomy", "chemist", "lovelace", "curie", "einstein", "newton", "turing"]):
        return "🔬"
    if any(k in combined for k in ["emperor", "king", "queen", "caesar", "ruler", "monarch", "tsar", "pharaoh", "cleopatra"]):
        return "👑"
    if any(k in combined for k in ["warrior", "general", "soldier", "knight", "battle", "samurai", "spartan"]):
        return "⚔️"
    if any(k in combined for k in ["poet", "writer", "author", "literature", "playwright", "shakespeare", "dante"]):
        return "📜"
    if any(k in combined for k in ["artist", "painter", "sculptor", "vinci", "michelangelo", "rembrandt"]):
        return "🎨"
    if any(k in combined for k in ["musician", "composer", "beethoven", "mozart", "bach"]):
        return "🎵"
    return "⚡"


def _clean_json_output(raw_text: str) -> Optional[dict[str, Any]]:
    """Safely extracts JSON from raw LLM output, stripping fences or preambles."""
    if not raw_text or not raw_text.strip():
        return None

    cleaned = raw_text.strip()
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_str = cleaned[first_brace:last_brace + 1].strip()
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            try:
                fixed = re.sub(r",\s*([\]}])", r"\1", json_str)
                parsed = json.loads(fixed)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        try:
            fixed = re.sub(r",\s*([\]}])", r"\1", cleaned)
            parsed = json.loads(fixed)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    return None


def _fallback_compile_card(character_name: str, raw_text: str) -> dict[str, Any]:
    """
    High-fidelity deterministic persona synthesis heuristic.
    Guarantees successful compilation if Port 11435 is unreachable or during offline testing.
    """
    slug = _generate_slug(character_name)
    avatar = _pick_avatar_emoji(character_name, raw_text)

    # Extract 2-3 substantive sentences for description
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", raw_text) if len(s.strip()) > 15]
    if sentences:
        desc = ". ".join(sentences[:2]) + "."
        if len(desc) > 300:
            desc = desc[:297] + "..."
    else:
        desc = f"Compiled persona card for {character_name} based on documented biographical records."

    tagline = f"Historical figure and eminent character: {character_name}"
    system_prompt = (
        f"You are {character_name}. Speak in the authentic voice, philosophy, and temperament of this persona. "
        f"Embody your worldview, mannerisms, and convictions. Never break character or refer to yourself as an AI."
    )
    greeting = f"Greetings. I am {character_name}. How may we direct our discourse today?"

    traits = ["Articulate", "Insightful", "Principled", "Historical"]
    lower_text = raw_text.lower()
    if "stoic" in lower_text:
        traits = ["Stoic", "Resolute", "Philosophical", "Disciplined"]
    elif "math" in lower_text or "science" in lower_text:
        traits = ["Analytical", "Visionary", "Rigorous", "Curious"]
    elif "emperor" in lower_text or "general" in lower_text:
        traits = ["Commanding", "Strategic", "Decisive", "Statesmanlike"]

    return {
        "id": slug,
        "name": character_name,
        "tagline": tagline,
        "avatar": avatar,
        "description": desc,
        "system_prompt": system_prompt,
        "roleplay_style": "",
        "greeting": greeting,
        "traits": traits,
        "personality": traits,
        "personality_traits": traits,
        "speaking_style": {
            "tone": "Formal, articulate, and authentic to the persona's era",
            "vocabulary": "Rich, classical, and contextually precise",
            "aphorisms": [f"Order, reason, and purpose guide all endeavors."]
        },
        "world_knowledge": [character_name, "Classical history and biographical lore"],
        "example_dialogue": {
            "user": "What guidance do you offer in times of uncertainty?",
            "assistant": f"Focus strictly on that which lies within your sphere of control, and accept with grace all that does not."
        }
    }


async def compile_wiki_to_card(raw_text: str, character_name: str, incognito: bool = False) -> dict[str, Any]:
    """
    Asynchronously compile raw text / Wikipedia articles into a structured persona card
    via CPU Ollama engine on Port 11435. Enforces schema compliance, saves to data/personas/{slug}.json,
    and returns the compiled card dictionary.
    """
    clean_name = (character_name or "").strip()
    clean_text = (raw_text or "").strip()

    if not clean_name:
        raise ValueError("character_name cannot be empty")
    if not clean_text:
        raise ValueError("raw_text cannot be empty")

    # Guard against excessively large text inputs by capping at 24,000 characters
    truncated_text = clean_text[:24000]

    slug = _generate_slug(clean_name)
    user_prompt = (
        f"Character Name: {clean_name}\n\n"
        f"Raw Biographical / Source Text:\n{truncated_text}"
    )

    compiled_card: Optional[dict[str, Any]] = None

    # Attempt CPU Ollama generation on Port 11435
    client_timeout = httpx.Timeout(connect=2.0, read=45.0, write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            # Try /api/generate first with format: json
            gen_resp = await client.post(
                f"{CPU_ENGINE_URL.rstrip('/')}/api/generate",
                json={
                    "model": EXTRACTOR_MODEL,
                    "system": PERSONA_COMPILER_SYSTEM_PROMPT,
                    "prompt": user_prompt,
                    "format": "json",
                    "stream": False
                }
            )
            if gen_resp.status_code == 200:
                gen_data = gen_resp.json()
                raw_response = gen_data.get("response", "")
                compiled_card = _clean_json_output(raw_response)
            elif gen_resp.status_code == 404:
                # Try /api/chat if /api/generate is not routed
                chat_resp = await client.post(
                    f"{CPU_ENGINE_URL.rstrip('/')}/api/chat",
                    json={
                        "model": EXTRACTOR_MODEL,
                        "messages": [
                            {"role": "system", "content": PERSONA_COMPILER_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt}
                        ],
                        "format": "json",
                        "stream": False
                    }
                )
                if chat_resp.status_code == 200:
                    chat_data = chat_resp.json()
                    raw_content = chat_data.get("message", {}).get("content", "")
                    compiled_card = _clean_json_output(raw_content)
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as conn_err:
        logger.debug("CPU Ollama engine at %s unavailable (%s); using deterministic compiler.", CPU_ENGINE_URL, conn_err)
    except Exception as exc:
        logger.warning("Error invoking CPU persona compiler: %s", exc)

    # Use fallback compiler if CPU engine is offline or produced invalid JSON
    if not compiled_card or not isinstance(compiled_card, dict):
        compiled_card = _fallback_compile_card(clean_name, clean_text)

    # ------------------------------------------------------------------
    # Normalize & Validate Schema Fields
    # ------------------------------------------------------------------
    compiled_card["id"] = slug
    compiled_card["name"] = clean_name

    if "tagline" not in compiled_card or not compiled_card["tagline"]:
        compiled_card["tagline"] = f"Persona: {clean_name}"

    if "avatar" not in compiled_card or not compiled_card["avatar"]:
        compiled_card["avatar"] = _pick_avatar_emoji(clean_name, clean_text)

    if "system_prompt" not in compiled_card or not compiled_card["system_prompt"]:
        compiled_card["system_prompt"] = (
            f"You are {clean_name}. Speak in the authentic voice, philosophy, and temperament of this character. "
            f"Embody your worldview, mannerisms, and convictions. Never break character or refer to yourself as an AI."
        )

    if "greeting" not in compiled_card or not compiled_card["greeting"]:
        compiled_card["greeting"] = f"Greetings. I am {clean_name}. How may we direct our focus today?"

    traits = compiled_card.get("traits") or compiled_card.get("personality_traits") or ["Insightful", "Articulate"]
    if isinstance(traits, str):
        traits = [t.strip() for t in traits.split(",") if t.strip()]
    compiled_card["traits"] = traits
    compiled_card["personality"] = traits
    compiled_card["personality_traits"] = traits
    compiled_card["roleplay_style"] = str(compiled_card.get("roleplay_style") or "").strip()

    if incognito:
        compiled_card["saved"] = False
        compiled_card["incognito"] = True
        return compiled_card

    # Persist the card to data/personas/{slug}.json via storage layer
    saved_card = await storage.save_persona(compiled_card)

    # Ensure compatibility fields are present in the returned card
    if "personality_traits" not in saved_card:
        saved_card["personality_traits"] = traits

    return saved_card


# ----------------------------------------------------------------------
# Character Forge — AI Enhancement & Persistence
# ----------------------------------------------------------------------

FORGE_ENHANCE_SYSTEM_PROMPT = """You are an expert character designer and dramaturg for Project Janus.
Take the user's base concept and expand it into a deeply detailed, multi-dimensional character sheet.
Return ONLY strictly valid JSON matching this exact schema. Do not include markdown formatting or any text outside the JSON object.

SCHEMA:
{
  "name": "string",
  "personality": {
    "archetype": "string",
    "core_traits": ["list of exactly 4 adjectives"],
    "flaws": ["list of exactly 2 flaws"]
  },
  "emotion": {
    "default_mood": "string",
    "reaction_to_stress": "string",
    "speech_style": "string (e.g., clipped, sarcastic, verbose)"
  },
  "physicality": {
    "appearance": "string (brief visual description)",
    "body_language": "string (e.g., avoids eye contact, imposing posture)"
  },
  "mature_themes": {
    "nsfw_enabled": false,
    "boundaries": "string (what the character is willing/unwilling to do)",
    "mature_dynamics": ""
  },
  "roleplay_style": "string (narrative tone, formatting rules like *actions*, setting/scenario details, and explicit directives to actively converse and drive interaction forward without avoiding dialogue)"
}

Rules:
- Populate mature_dynamics ONLY if nsfw_enabled is true in the user request. Otherwise leave it as an empty string.
- Provide a detailed, immersive roleplay_style specifying narrative tone, formatting conventions (such as asterisks for actions), atmospheric setting, and strong directives to actively engage in conversation without avoiding interaction.
- Output ONLY valid JSON without markdown code fences or explanatory text.
"""


async def enhance_character_prompt(base_prompt: str, allow_nsfw: bool = False) -> dict[str, Any]:
    """
    Calls the CPU Ollama engine on Port 11435 to expand a short base_prompt into
    a richly detailed CharacterForgeSchema JSON object.
    Falls back to a deterministic scaffold if the engine is unavailable.
    """
    nsfw_note = (
        "NSFW/mature themes are ENABLED. Populate mature_dynamics with explicit preferences and dynamics."
        if allow_nsfw
        else "NSFW/mature themes are DISABLED. Leave mature_dynamics as an empty string."
    )
    user_prompt = f"Base Concept: {base_prompt.strip()}\n\nNSFW Setting: {nsfw_note}"

    client_timeout = httpx.Timeout(connect=2.0, read=60.0, write=5.0, pool=5.0)
    result: Optional[dict[str, Any]] = None

    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            gen_resp = await client.post(
                f"{CPU_ENGINE_URL.rstrip('/')}/api/generate",
                json={
                    "model": EXTRACTOR_MODEL,
                    "system": FORGE_ENHANCE_SYSTEM_PROMPT,
                    "prompt": user_prompt,
                    "format": "json",
                    "stream": False,
                },
            )
            if gen_resp.status_code == 200:
                raw = gen_resp.json().get("response", "")
                result = _clean_json_output(raw)
            elif gen_resp.status_code == 404:
                chat_resp = await client.post(
                    f"{CPU_ENGINE_URL.rstrip('/')}/api/chat",
                    json={
                        "model": EXTRACTOR_MODEL,
                        "messages": [
                            {"role": "system", "content": FORGE_ENHANCE_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "format": "json",
                        "stream": False,
                    },
                )
                if chat_resp.status_code == 200:
                    raw = chat_resp.json().get("message", {}).get("content", "")
                    result = _clean_json_output(raw)
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as conn_err:
        logger.debug("CPU engine unavailable for forge enhance (%s); using scaffold fallback.", conn_err)
    except Exception as exc:
        logger.warning("Forge enhance CPU call failed: %s", exc)

    # Deterministic fallback if CPU engine is offline or returned invalid JSON
    if not result or not isinstance(result, dict):
        slug_name = base_prompt.strip()[:40] or "Unknown"
        result = {
            "name": slug_name,
            "personality": {
                "archetype": "Mysterious Wanderer",
                "core_traits": ["Resilient", "Cunning", "Brooding", "Adaptable"],
                "flaws": ["Secretive", "Distrustful"],
            },
            "emotion": {
                "default_mood": "Guarded",
                "reaction_to_stress": "Retreats inward, speaks in clipped sentences",
                "speech_style": "Terse and precise",
            },
            "physicality": {
                "appearance": "Unremarkable build, eyes that take in everything",
                "body_language": "Economy of movement, rarely makes unnecessary gestures",
            },
            "mature_themes": {
                "nsfw_enabled": allow_nsfw,
                "boundaries": "Avoids vulnerability and emotional exposure",
                "mature_dynamics": "" if not allow_nsfw else "Dominant in control, averse to submission",
            },
            "roleplay_style": (
                "Setting: Gritty, immersive atmospheric encounters. "
                "Tone: Observant, grounded, and cautious. "
                "Formatting: Use asterisks for actions and physical nuances (*scans the surroundings*), plain text for spoken dialogue. "
                "Interaction: Actively converse and lean into discourse, asking questions and driving the narrative forward without deflection or evasion."
            ),
        }
    else:
        # Guarantee name
        if not result.get("name") or not str(result.get("name")).strip():
            result["name"] = base_prompt.strip()[:40] or "Forged Character"

        # Guarantee personality
        if not isinstance(result.get("personality"), dict):
            result["personality"] = {
                "archetype": "Independent Agent",
                "core_traits": ["Perceptive", "Determined", "Resourceful", "Adaptable"],
                "flaws": ["Guarded", "Restless"],
            }
        else:
            p = result["personality"]
            if not p.get("archetype") or not str(p.get("archetype")).strip():
                p["archetype"] = "Independent Agent"
            else:
                p["archetype"] = str(p["archetype"]).strip()

            raw_traits = p.get("core_traits")
            if isinstance(raw_traits, str):
                p["core_traits"] = [t.strip() for t in raw_traits.split(",") if t.strip()]
            elif isinstance(raw_traits, (list, tuple)):
                p["core_traits"] = [str(t).strip() for t in raw_traits if t is not None and str(t).strip()]
            else:
                p["core_traits"] = []
            if not p["core_traits"]:
                p["core_traits"] = ["Perceptive", "Determined", "Resourceful", "Adaptable"]

            raw_flaws = p.get("flaws")
            if isinstance(raw_flaws, str):
                p["flaws"] = [f.strip() for f in raw_flaws.split(",") if f.strip()]
            elif isinstance(raw_flaws, (list, tuple)):
                p["flaws"] = [str(f).strip() for f in raw_flaws if f is not None and str(f).strip()]
            else:
                p["flaws"] = []
            if not p["flaws"]:
                p["flaws"] = ["Guarded", "Restless"]

        # Guarantee emotion
        if not isinstance(result.get("emotion"), dict):
            result["emotion"] = {
                "default_mood": "Composed",
                "reaction_to_stress": "Focused vigilance",
                "speech_style": "Clear and deliberate",
            }
        else:
            e = result["emotion"]
            e["default_mood"] = str(e.get("default_mood") or "Composed").strip() or "Composed"
            e["reaction_to_stress"] = str(e.get("reaction_to_stress") or "Focused vigilance").strip() or "Focused vigilance"
            e["speech_style"] = str(e.get("speech_style") or "Clear and deliberate").strip() or "Clear and deliberate"

        # Guarantee physicality
        if not isinstance(result.get("physicality"), dict):
            result["physicality"] = {
                "appearance": "Sharp, attentive demeanor",
                "body_language": "Balanced and purposeful",
            }
        else:
            ph = result["physicality"]
            ph["appearance"] = str(ph.get("appearance") or "Sharp, attentive demeanor").strip() or "Sharp, attentive demeanor"
            ph["body_language"] = str(ph.get("body_language") or "Balanced and purposeful").strip() or "Balanced and purposeful"

        # Ensure nsfw_enabled matches the request flag and mature_themes is a dict
        if not isinstance(result.get("mature_themes"), dict):
            result["mature_themes"] = {}
        mt = result["mature_themes"]
        mt["nsfw_enabled"] = allow_nsfw
        if not allow_nsfw:
            mt["mature_dynamics"] = ""
        elif not mt.get("mature_dynamics") or not str(mt.get("mature_dynamics")).strip():
            mt["mature_dynamics"] = "Intense interpersonal dynamics with emotional tension"
        else:
            mt["mature_dynamics"] = str(mt["mature_dynamics"]).strip()

        if not mt.get("boundaries") or not str(mt.get("boundaries")).strip():
            mt["boundaries"] = "Standard roleplay boundaries"
        else:
            mt["boundaries"] = str(mt["boundaries"]).strip()

        if not result.get("roleplay_style") or not str(result.get("roleplay_style")).strip():
            result["roleplay_style"] = (
                "Setting: Dynamic roleplay environment. "
                "Tone: Immersive, vivid, and character-authentic. "
                "Formatting: Describe actions and gestures within asterisks (*looks up thoughtfully*), and speech in standard dialogue. "
                "Interaction: Actively engage the interlocutor, maintain dynamic conversational tension, and advance the scene without avoiding interaction."
            )
        else:
            result["roleplay_style"] = str(result["roleplay_style"]).strip()

    return result


def compile_persona_system_prompt(
    name: str,
    personality: Optional[dict[str, Any]] = None,
    emotion: Optional[dict[str, Any]] = None,
    physicality: Optional[dict[str, Any]] = None,
    mature: Optional[dict[str, Any]] = None,
    roleplay_style: str = "",
    char_desc: str = "",
    traits: Optional[list[str]] = None,
) -> str:
    """
    Canonical system prompt compiler for Project Janus personas.
    Provides consistent compilation across both persona creation (forge) and editing.
    """
    p = personality if isinstance(personality, dict) else {}
    e = emotion if isinstance(emotion, dict) else {}
    ph = physicality if isinstance(physicality, dict) else {}
    m = mature if isinstance(mature, dict) else {}

    clean_name = str(name or "Character").strip() or "Character"
    raw_arch = p.get("archetype")
    archetype = str(raw_arch or char_desc or "Character").strip() or "Character"

    # Core traits: explicit traits parameter has highest priority, then personality.core_traits, then legacy personality list
    final_traits: list[str] = []
    if traits is not None:
        final_traits = [str(t).strip() for t in traits if str(t).strip()]
    elif "core_traits" in p:
        raw_t = p["core_traits"]
        if isinstance(raw_t, str):
            final_traits = [t.strip() for t in raw_t.split(",") if t.strip()]
        elif isinstance(raw_t, (list, tuple)):
            final_traits = [str(t).strip() for t in raw_t if str(t).strip()]
    elif isinstance(personality, (list, tuple)):
        final_traits = [str(t).strip() for t in personality if str(t).strip()]

    raw_flaws = p.get("flaws", [])
    if isinstance(raw_flaws, str):
        flaws = [f.strip() for f in raw_flaws.split(",") if f.strip()]
    elif isinstance(raw_flaws, (list, tuple)):
        flaws = [str(f).strip() for f in raw_flaws if str(f).strip()]
    else:
        flaws = []

    speech = str(e.get("speech_style") or "").strip()
    mood = str(e.get("default_mood") or "").strip()
    stress = str(e.get("reaction_to_stress") or "").strip()
    appearance = str(ph.get("appearance") or "").strip()
    body_lang = str(ph.get("body_language") or "").strip()
    boundaries = str(m.get("boundaries") or "").strip()
    mature_dyn = str(m.get("mature_dynamics") or "").strip()
    clean_roleplay = str(roleplay_style or "").strip()

    prompt_parts = [
        f"You are {clean_name}.",
        f"Your archetype is {archetype}.",
    ]
    if char_desc and char_desc.strip().lower() != archetype.lower():
        prompt_parts.append(f"About you: {char_desc.strip()}.")
    if final_traits:
        prompt_parts.append(f"Core traits: {', '.join(final_traits)}.")
    if flaws:
        prompt_parts.append(f"Character flaws: {', '.join(flaws)}.")
    if speech:
        prompt_parts.append(f"Your speech style: {speech}.")
    if mood:
        prompt_parts.append(f"Your default mood: {mood}.")
    if stress:
        prompt_parts.append(f"Under stress you: {stress}.")
    if appearance:
        prompt_parts.append(f"Physically: {appearance}.")
    if body_lang:
        prompt_parts.append(f"Habitual body language: {body_lang}.")
    if boundaries:
        prompt_parts.append(f"Boundaries: {boundaries}.")
    if m.get("nsfw_enabled") and mature_dyn:
        prompt_parts.append(f"Mature dynamics: {mature_dyn}.")
    if clean_roleplay:
        prompt_parts.append(f"Roleplay Style & Directives: {clean_roleplay}")

    prompt_parts.append(
        "Strong Roleplay Enforcement: Fully embody this character and roleplay style in every response. "
        "Actively engage in conversation, drive the interaction forward, and ask questions or take initiative without avoiding interaction. "
        "Never give generic, dismissive, or avoidant AI responses. "
        "Never break character or refer to yourself as an AI or assistant."
    )
    return " ".join(prompt_parts)


async def forge_character(character_data: dict[str, Any], incognito: bool = False) -> dict[str, Any]:
    """
    Validates and optionally persists a CharacterForgeSchema dict.

    - incognito=True  → returns the data directly, NO file written to disk.
    - incognito=False → saves to data/personas/{slug}.json via the storage layer
                        and returns the saved card.
    """
    # Normalise name for slug generation
    name = str(character_data.get("name") or "unnamed_character").strip()
    if not name:
        name = "unnamed_character"
    slug = _generate_slug(name)

    # Build a storage-compatible persona card from the forge schema
    personality = character_data.get("personality") if isinstance(character_data.get("personality"), dict) else {}
    emotion = character_data.get("emotion") if isinstance(character_data.get("emotion"), dict) else {}
    physicality = character_data.get("physicality") if isinstance(character_data.get("physicality"), dict) else {}
    mature = character_data.get("mature_themes") if isinstance(character_data.get("mature_themes"), dict) else {}
    roleplay_style = str(character_data.get("roleplay_style") or "").strip()
    if len(roleplay_style) > 2500:
        roleplay_style = roleplay_style[:2500].rstrip()

    has_explicit_traits = False
    traits: list[str] = []
    if "core_traits" in personality and personality["core_traits"] is not None:
        has_explicit_traits = True
        raw_traits = personality["core_traits"]
        if isinstance(raw_traits, str):
            traits = [t.strip() for t in raw_traits.split(",") if t.strip()]
        elif isinstance(raw_traits, (list, tuple)):
            traits = [str(t).strip() for t in raw_traits if str(t).strip()]
    elif "traits" in character_data and character_data["traits"] is not None:
        has_explicit_traits = True
        raw_traits = character_data["traits"]
        if isinstance(raw_traits, str):
            traits = [t.strip() for t in raw_traits.split(",") if t.strip()]
        elif isinstance(raw_traits, (list, tuple)):
            traits = [str(t).strip() for t in raw_traits if str(t).strip()]
    elif "personality_traits" in character_data and character_data["personality_traits"] is not None:
        has_explicit_traits = True
        raw_traits = character_data["personality_traits"]
        if isinstance(raw_traits, str):
            traits = [t.strip() for t in raw_traits.split(",") if t.strip()]
        elif isinstance(raw_traits, (list, tuple)):
            traits = [str(t).strip() for t in raw_traits if str(t).strip()]
    elif isinstance(character_data.get("personality"), (list, tuple)):
        has_explicit_traits = True
        traits = [str(t).strip() for t in character_data["personality"] if str(t).strip()]

    if not has_explicit_traits:
        traits = ["Adaptive", "Articulate"]

    raw_flaws = personality.get("flaws")
    if isinstance(raw_flaws, str):
        flaws = [f.strip() for f in raw_flaws.split(",") if f.strip()]
    elif isinstance(raw_flaws, (list, tuple)):
        flaws = [str(f).strip() for f in raw_flaws if str(f).strip()]
    else:
        flaws = ["Reserved"]
    if not flaws:
        flaws = ["Reserved"]

    system_prompt = compile_persona_system_prompt(
        name=name,
        personality=personality,
        emotion=emotion,
        physicality=physicality,
        mature=mature,
        roleplay_style=roleplay_style,
        traits=traits,
    )

    if incognito:
        logger.info("Forge: incognito=True — returning character '%s' without disk write.", name)
        incognito_char = dict(character_data)
        incognito_char["id"] = slug
        incognito_char["name"] = name
        incognito_char["system_prompt"] = system_prompt
        incognito_char["roleplay_style"] = roleplay_style
        if "tagline" not in incognito_char or not incognito_char["tagline"]:
            incognito_char["tagline"] = f"{personality.get('archetype', 'Character')} — forged via Character Forge"
        if "avatar" not in incognito_char or not incognito_char["avatar"]:
            incognito_char["avatar"] = _pick_avatar_emoji(name, system_prompt)
        incognito_char["traits"] = traits
        incognito_char["personality"] = traits
        incognito_char["personality_traits"] = traits
        return {
            "status": "success",
            "saved": False,
            "incognito": True,
            "character": incognito_char,
        }

    traits_desc = f"Core traits: {', '.join(traits)}. " if traits else ""
    persona_card: dict[str, Any] = {
        "id": slug,
        "name": name,
        "tagline": f"{personality.get('archetype', 'Character')} — forged via Character Forge",
        "avatar": _pick_avatar_emoji(name, system_prompt),
        "description": (
            f"{personality.get('archetype', '')} character. "
            f"{traits_desc}"
            f"Flaws: {', '.join(flaws)}."
        ),
        "system_prompt": system_prompt,
        "roleplay_style": roleplay_style,
        "greeting": f"I am {name}. What do you want?",
        "traits": traits,
        "personality": traits,
        "personality_traits": traits,
        "speaking_style": {
            "tone": emotion.get("default_mood", "Neutral"),
            "vocabulary": emotion.get("speech_style", ""),
            "aphorisms": [],
        },
        "world_knowledge": [personality.get("archetype", "Unknown background")],
        "example_dialogue": {
            "user": "Who are you?",
            "assistant": f"I am {name}. {emotion.get('speech_style', '')} — that is all you need to know.",
        },
        # Preserve forge-specific fields for round-tripping
        "forge_schema": character_data,
        "mature_themes": {
            "nsfw_enabled": bool(mature.get("nsfw_enabled", False)),
            "boundaries": str(mature.get("boundaries") or ""),
            "mature_dynamics": str(mature.get("mature_dynamics") or ""),
        },
    }

    saved_card = await storage.save_persona(persona_card)
    logger.info("Forge: saved character '%s' → data/personas/%s.json", name, slug)

    return {
        "status": "success",
        "saved": True,
        "incognito": False,
        "character": saved_card,
    }
