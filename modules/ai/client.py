"""Asynchronous client for Ollama's local chat API."""

from __future__ import annotations

from typing import Any

import httpx

from modules.ai.config import (
    OLLAMA_BASE_URL,
    OLLAMA_MAX_OUTPUT_TOKENS,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
    SYSTEM_PROMPT,
)


class OllamaError(RuntimeError):
    """Base exception for failures while communicating with Ollama."""


class OllamaConnectionError(OllamaError):
    """Raised when the local Ollama service cannot be reached."""


class OllamaTimeoutError(OllamaError):
    """Raised when Ollama does not finish generating within the timeout."""


class OllamaResponseError(OllamaError):
    """Raised when Ollama returns an invalid or unsuccessful response."""


class OllamaClient:
    """Send non-streaming chat requests to the local Ollama service."""

    async def chat(
        self,
        prompt: str,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_tokens: int = OLLAMA_MAX_OUTPUT_TOKENS,
        json_mode: bool = False,
        timeout: float = OLLAMA_TIMEOUT_SECONDS,
    ) -> str:
        """Return the assistant response generated for a user prompt."""
        payload: dict[str, Any] = {
            "model": OLLAMA_MODEL,
            "stream": False,
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            payload["format"] = "json"

        try:
            async with httpx.AsyncClient(
                base_url=OLLAMA_BASE_URL,
                timeout=timeout,
            ) as client:
                response = await client.post("/api/chat", json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                "Ollama yanıtı zaman aşımına uğradı."
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                "Yerel Ollama servisine bağlanılamadı."
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaResponseError(
                "Ollama isteği başarıyla tamamlanamadı."
            ) from exc

        try:
            content = response.json()["message"]["content"]
        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaResponseError(
                "Ollama geçerli bir asistan yanıtı döndürmedi."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError("Ollama boş bir asistan yanıtı döndürdü.")
        return content.strip()
