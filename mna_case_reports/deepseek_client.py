"""DeepSeek helpers for M&A case report generation."""

from __future__ import annotations

import copy
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


def _action_notice(message: str) -> None:
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::notice::{str(message).replace(chr(10), ' ')[:1000]}", flush=True)


def _json_response_attempts() -> int:
    try:
        configured = int(os.getenv("REPORT_JSON_RESPONSE_ATTEMPTS", "2"))
    except ValueError:
        configured = 2
    return min(max(configured, 1), 4)


def _is_retryable_chat_error(exc: Exception) -> bool:
    if isinstance(exc, requests.RequestException):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "api error 408", "api error 425", "api error 429", "api error 500", "api error 502",
            "api error 503", "api error 504", "timeout", "timed out", "connection",
            "unexpected deepseek response shape", "unexpected response shape",
        )
    )


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


def _api_config(
    model: str | None = None,
    *,
    api_key_env: str = "DEEPSEEK_API_KEY",
    base_url_env: str = "DEEPSEEK_BASE_URL",
    model_env: str = "DEEPSEEK_MODEL",
    default_base_url: str = DEFAULT_BASE_URL,
    default_model: str = DEFAULT_MODEL,
    provider_label: str = "DeepSeek",
) -> tuple[str, str, str]:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise DeepSeekError(f"{provider_label} API key env {api_key_env} is not set")
    base_url = os.getenv(base_url_env, default_base_url).rstrip("/")
    model_name = model or os.getenv(model_env, default_model)
    return api_key, base_url, model_name


def _post_chat_once(api_key: str, base_url: str, payload: dict[str, Any], *, timeout: int) -> requests.Response:
    return requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )


def _post_chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    timeout: int = 180,
    temperature: float = 0.2,
    api_key_env: str = "DEEPSEEK_API_KEY",
    base_url_env: str = "DEEPSEEK_BASE_URL",
    model_env: str = "DEEPSEEK_MODEL",
    default_base_url: str = DEFAULT_BASE_URL,
    default_model: str = DEFAULT_MODEL,
    provider_label: str = "DeepSeek",
    reasoning_effort_env: str | None = None,
    max_tokens_env: str | None = None,
    max_completion_tokens_env: str | None = None,
) -> str:
    api_key, base_url, model_name = _api_config(
        model,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        model_env=model_env,
        default_base_url=default_base_url,
        default_model=default_model,
        provider_label=provider_label,
    )
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    reasoning_effort = os.getenv(reasoning_effort_env or "")
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    max_tokens = os.getenv(max_tokens_env or "")
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    max_completion_tokens = os.getenv(max_completion_tokens_env or "")
    if max_completion_tokens:
        payload["max_completion_tokens"] = int(max_completion_tokens)

    retry_payload = payload
    response = _post_chat_once(api_key, base_url, retry_payload, timeout=timeout)
    optional_fields = ("response_format", "reasoning_effort", "temperature", "max_tokens", "max_completion_tokens")
    for _ in range(4):
        rejected = [field for field in optional_fields if response.status_code >= 400 and field in response.text and field in retry_payload]
        if not rejected:
            break
        next_payload = copy.deepcopy(retry_payload)
        for field in rejected:
            next_payload.pop(field, None)
        if next_payload == retry_payload:
            break
        LOGGER.warning("%s rejected optional chat parameter(s) %s; retrying with compatible payload", provider_label, ",".join(rejected))
        retry_payload = next_payload
        response = _post_chat_once(api_key, base_url, retry_payload, timeout=timeout)
    if response.status_code >= 400:
        raise DeepSeekError(f"{provider_label} API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError(f"Unexpected {provider_label} response shape: {data}") from exc


def repair_json_text(
    text: str,
    *,
    model: str | None = None,
    timeout: int = 120,
    api_key_env: str = "DEEPSEEK_API_KEY",
    base_url_env: str = "DEEPSEEK_BASE_URL",
    model_env: str = "DEEPSEEK_MODEL",
    default_base_url: str = DEFAULT_BASE_URL,
    default_model: str = DEFAULT_MODEL,
    provider_label: str = "DeepSeek",
    reasoning_effort_env: str | None = None,
    max_tokens_env: str | None = None,
    max_completion_tokens_env: str | None = None,
) -> dict[str, Any]:
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
    repaired = _post_chat(
        messages,
        model=model,
        timeout=timeout,
        temperature=0.0,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        model_env=model_env,
        default_base_url=default_base_url,
        default_model=default_model,
        provider_label=provider_label,
        reasoning_effort_env=reasoning_effort_env,
        max_tokens_env=max_tokens_env,
        max_completion_tokens_env=max_completion_tokens_env,
    )
    return extract_json(repaired)


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    timeout: int = 180,
    repair: bool = True,
    api_key_env: str = "DEEPSEEK_API_KEY",
    base_url_env: str = "DEEPSEEK_BASE_URL",
    model_env: str = "DEEPSEEK_MODEL",
    default_base_url: str = DEFAULT_BASE_URL,
    default_model: str = DEFAULT_MODEL,
    provider_label: str = "DeepSeek",
    reasoning_effort_env: str | None = None,
    max_tokens_env: str | None = None,
    max_completion_tokens_env: str | None = None,
) -> dict[str, Any]:
    max_attempts = _json_response_attempts()
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        retry_messages = messages
        if attempt > 1:
            retry_messages = messages + [
                {
                    "role": "system",
                    "content": "上一次响应为空或无法解析。请重新完成原任务，只输出一个完整、合法的JSON对象。",
                }
            ]
        try:
            content = _post_chat(
                retry_messages,
                model=model,
                timeout=timeout,
                temperature=0.2,
                api_key_env=api_key_env,
                base_url_env=base_url_env,
                model_env=model_env,
                default_base_url=default_base_url,
                default_model=default_model,
                provider_label=provider_label,
                reasoning_effort_env=reasoning_effort_env,
                max_tokens_env=max_tokens_env,
                max_completion_tokens_env=max_completion_tokens_env,
            )
        except Exception as call_exc:  # noqa: BLE001
            last_error = call_exc
            if attempt < max_attempts and _is_retryable_chat_error(call_exc):
                LOGGER.warning(
                    "%s chat request failed transiently; retrying attempt=%s/%s error=%s",
                    provider_label,
                    attempt,
                    max_attempts,
                    call_exc,
                )
                _action_notice(
                    f"json_request_retry provider={provider_label} attempt={attempt + 1}/{max_attempts} "
                    f"error={str(call_exc)[:240]}"
                )
                continue
            raise
        try:
            if not str(content or "").strip():
                raise DeepSeekError(f"{provider_label} returned empty JSON content")
            return extract_json(content)
        except Exception as first_exc:  # noqa: BLE001
            last_error = first_exc
            if repair and str(content or "").strip():
                try:
                    locally_repaired = repair_json_like(content)
                    LOGGER.warning("%s returned malformed JSON; attempting local repair: %s", provider_label, first_exc)
                    return _loads_object(locally_repaired)
                except Exception as local_exc:  # noqa: BLE001
                    try:
                        LOGGER.warning("%s malformed JSON local repair failed; attempting model repair: %s", provider_label, local_exc)
                        return repair_json_text(
                            content,
                            model=model,
                            timeout=min(timeout, 120),
                            api_key_env=api_key_env,
                            base_url_env=base_url_env,
                            model_env=model_env,
                            default_base_url=default_base_url,
                            default_model=default_model,
                            provider_label=provider_label,
                            reasoning_effort_env=reasoning_effort_env,
                            max_tokens_env=max_tokens_env,
                            max_completion_tokens_env=max_completion_tokens_env,
                        )
                    except Exception as repair_exc:  # noqa: BLE001
                        last_error = repair_exc
            if attempt < max_attempts:
                LOGGER.warning(
                    "%s JSON response unusable; retrying original task attempt=%s/%s content_chars=%s error=%s",
                    provider_label,
                    attempt,
                    max_attempts,
                    len(str(content or "")),
                    last_error,
                )
                _action_notice(
                    f"json_response_retry provider={provider_label} attempt={attempt + 1}/{max_attempts} "
                    f"content_chars={len(str(content or ''))} error={str(last_error)[:240]}"
                )
                continue
            snippet = str(content or "")[:2000].replace("\n", " ")
            raise DeepSeekError(
                f"{provider_label} JSON response failed after {max_attempts} attempt(s): "
                f"{last_error}; original prefix={snippet}"
            ) from last_error
    raise DeepSeekError(f"{provider_label} JSON response failed: {last_error}")
