"""DeepSeek helpers for M&A case report generation."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekError(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def chat_json(messages: list[dict[str, str]], *, model: str | None = None, timeout: int = 180) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY is not set")
    base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model_name = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model_name,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise DeepSeekError(f"DeepSeek API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    try:
        return extract_json(data["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001
        raise DeepSeekError(f"Unexpected DeepSeek response: {data}") from exc
