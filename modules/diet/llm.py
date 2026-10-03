"""LLM access for the dietitian: Gemini free tier first, local Ollama as fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from core.config import settings
from modules.ai.client import OllamaClient, OllamaError


logger = logging.getLogger(__name__)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT_SECONDS = 90.0
# The local model is slow on CPU; plans are prepared ahead of meal time, so wait longer.
OLLAMA_DIET_TIMEOUT_SECONDS = 600.0


class DietAIError(RuntimeError):
    """Raised when neither Gemini nor Ollama could produce a usable answer."""


async def _gemini(prompt: str, system_prompt: str, json_mode: bool, max_tokens: int) -> str:
    generation_config: dict[str, Any] = {
        "temperature": 0.9,
        "maxOutputTokens": max_tokens,
        "thinkingConfig": {"thinkingBudget": 0},
    }
    if json_mode:
        generation_config["responseMimeType"] = "application/json"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT_SECONDS) as client:
        response = await client.post(
            GEMINI_URL.format(model=settings.gemini_model),
            headers={"x-goog-api-key": settings.gemini_api_key},
            json=payload,
        )
        response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise ValueError("Gemini returned an empty response")
    return text


async def _complete(prompt: str, system_prompt: str, json_mode: bool, max_tokens: int) -> tuple[str, str]:
    if settings.gemini_api_key:
        try:
            return await _gemini(prompt, system_prompt, json_mode, max_tokens), "gemini"
        except Exception:
            logger.warning("Gemini request failed; falling back to Ollama", exc_info=True)
    try:
        text = await OllamaClient().chat(
            prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            json_mode=json_mode,
            timeout=OLLAMA_DIET_TIMEOUT_SECONDS,
        )
    except OllamaError as exc:
        raise DietAIError("Diyet planı için hiçbir AI servisine ulaşılamadı.") from exc
    return text, "ollama"


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise DietAIError("AI yanıtı JSON içermiyor.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise DietAIError("AI yanıtı geçerli JSON değil.") from exc
    if not isinstance(data, dict):
        raise DietAIError("AI yanıtı beklenen yapıda değil.")
    return data


async def generate_json(prompt: str, system_prompt: str, max_tokens: int = 4096) -> tuple[dict[str, Any], str]:
    text, source = await _complete(prompt, system_prompt, True, max_tokens)
    return _parse_json(text), source


async def generate_text(prompt: str, system_prompt: str, max_tokens: int = 1500) -> str:
    text, _ = await _complete(prompt, system_prompt, False, max_tokens)
    return text
