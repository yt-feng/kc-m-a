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


def _strip_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_json_span(text: str) -> str:
    cleaned = _strip_code_fence(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found", cleaned, 0)
    return cleaned[start : end + 1]


def _error_snippet(text: str, pos: int, radius: int = 240) -> str:
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return text[start:end].replace("\n", " ")


def _loads_object(text: str) -> dict[str, Any]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def repair_json_like(text: str) -> str:
    """Conservative local repair rules for LLM JSON output.

    Important: do not replace Chinese smart quotes. Strings such as “银泰系” are
    valid JSON string content. Converting them to ASCII quotes creates unescaped
    quotes and breaks otherwise valid JSON.
    """
    fixed = _extract_json_span(text)
    fixed = fixed.replace("\ufeff", "").replace("\u0000", "")
    # Keep Chinese quotes untouched. Only normalize single smart quotes because
    # they are not JSON delimiters and are safe within JSON strings.
    fixed = fixed.replace("‘", "'").replace("’", "'")
    fixed = re.sub(r"}\s*{", "},\n{", fixed)
    fixed = re.sub(r"([}\]])\s*(\"[A-Za-z_][A-Za-z0-9_\-]*\"\s*:)", r"\1,\n\2", fixed)
    fixed = re.sub(r"(\"(?:[^\"\\]|\\.)*\")\s*(\"[A-Za-z_][A-Za-z0-9_\-]*\"\s*:)", r"\1,\n\2", fixed)
    fixed = re.sub(r"(\"(?:[^\"\\]|\\.)*\")\s*(\{)", r"\1,\n\2", fixed)
    fixed = re.sub(r"(\])\s*(\{)", r"\1,\n\2", fixed)
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    return fixed


def extract_json(text: str) -> dict[str, Any]:
    raw = _extract_json_span(text)
    try:
        return _loads_object(raw)
    except Exception as raw_exc:  # noqa: BLE001
        cleaned = repair_json_like(raw)
        try:
            return _loads_object(cleaned)
        except json.JSONDecodeError as exc:
            snippet = _error_snippet(cleaned, exc.pos)
            raise json.JSONDecodeError(f"{exc.msg}. Nearby text: {snippet}", exc.doc, exc.pos) from raw_exc


def _api_config(model: str | None = None) -> tuple[str, str, str]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY is not set")
    base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model_name = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    return api_key, base_url, model_name


def _post_chat(messages: list[dict[str, str]], *, model: str | None = None, timeout: int = 180, temperature: float = 0.2) -> str:
    api_key, base_url, model_name = _api_config(model)
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise DeepSeekError(f"DeepSeek API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError(f"Unexpected DeepSeek response shape: {data}") from exc


def repair_json_text(text: str, *, model: str | None = None, timeout: int = 120) -> dict[str, Any]:
    candidate = _extract_json_span(text)
    if len(candidate) > 60000:
        candidate = candidate[:60000]
    messages = [
        {"role": "system", "content": "你是JSON修复器。只输出合法JSON对象，不要新增事实，不要删除字段，不要输出解释。保留中文引号为普通文本内容。"},
        {
            "role": "user",
            "content": "下面内容接近JSON但语法可能有缺失逗号、错误引号或尾逗号。请修复为合法JSON对象：\n" + candidate,
        },
    ]
    repaired = _post_chat(messages, model=model, timeout=timeout, temperature=0.0)
    return extract_json(repaired)


def chat_json(messages: list[dict[str, str]], *, model: str | None = None, timeout: int = 180, repair: bool = True) -> dict[str, Any]:
    content = _post_chat(messages, model=model, timeout=timeout, temperature=0.2)
    try:
        return extract_json(content)
    except Exception as first_exc:  # noqa: BLE001
        if repair:
            try:
                locally_repaired = repair_json_like(content)
                LOGGER.warning("DeepSeek returned malformed JSON; attempting local repair: %s", first_exc)
                return _loads_object(locally_repaired)
            except Exception as local_exc:  # noqa: BLE001
                try:
                    LOGGER.warning("DeepSeek malformed JSON local repair failed; attempting model repair: %s", local_exc)
                    return repair_json_text(content, model=model, timeout=min(timeout, 120))
                except Exception as repair_exc:  # noqa: BLE001
                    snippet = content[:2000].replace("\n", " ")
                    raise DeepSeekError(f"DeepSeek JSON repair failed: {repair_exc}; original prefix={snippet}") from repair_exc
        snippet = content[:2000].replace("\n", " ")
        raise DeepSeekError(f"Unexpected DeepSeek response JSON: {first_exc}; prefix={snippet}") from first_exc
