"""DeepSeek-based structuring for raw M&A candidates."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

from .config import CATEGORIES, CATEGORY_GUIDE, OUTPUT_COLUMNS, chunked
from .sources import RawItem, normalize_text

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekError(RuntimeError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
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


def deepseek_chat(messages: list[dict[str, str]], *, model: str | None = None) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY is not set")
    base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model_name = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model_name, "messages": messages, "temperature": 0.1, "stream": False, "response_format": {"type": "json_object"}},
        timeout=120,
    )
    if response.status_code >= 400:
        raise DeepSeekError(f"DeepSeek API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError(f"Unexpected DeepSeek response: {data}") from exc


def build_prompt(items: list[RawItem], start_label: str, end_label: str) -> list[dict[str, str]]:
    raw_items = [item.as_dict() for item in items]
    system = "你是资深并购案例研究员。把公告/新闻候选结构化为周度并购案例表。只基于给定候选，不要编造；无法确认的信息填 '-'。输出严格 JSON 对象。"
    user = f"""
请从 {start_label} 至 {end_label} 的候选信息中抽取真实、可研究的并购案例，并剔除重复项、规则说明、纯问询进展、无明确交易主体的传闻。

Excel 列：{OUTPUT_COLUMNS}

A 列「案例分类」只能从以下 10 个分类中选择：
{json.dumps(CATEGORIES, ensure_ascii=False)}

分类口径：
{CATEGORY_GUIDE}

结构化要求：
1. 输出 JSON 对象，格式为：{{"cases": [{{...}}], "discarded_count": 数字}}。
2. cases 中每行字段必须包含：案例分类、并购方、目标方、案例所属行业、并购方主营业务、标的主营业务、案例一句话简介、交易时间、交易对价、交易状态、备注、来源名称、URL、发布日期、地区。
3. 案例一句话简介要像投行案例库标题一样精炼，突出交易亮点或资本运作特点，30-60 个中文字符为宜。
4. 交易状态优先用：已完成、进行中、审批中、终止、意向、未知。
5. 中国 A 股/港股案例优先保留；全球新闻只保留具有明确交易金额、交易双方或战略意义的案例。
6. 严格去重，同一交易只输出一行，URL 取最能证明交易的来源。

候选信息 JSON：
{json.dumps(raw_items, ensure_ascii=False, indent=2)}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def normalize_case(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for col in OUTPUT_COLUMNS:
        if col == "序号":
            continue
        value = row.get(col, "-")
        if value is None or value == "":
            value = "-"
        normalized[col] = str(value).strip()
    if normalized.get("案例分类") not in CATEGORIES:
        normalized["案例分类"] = infer_category_from_text(" ".join(normalized.values()))
    return normalized


def infer_category_from_text(text: str) -> str:
    text_n = normalize_text(text)
    if any(k in text_n for k in ["spac", "de-spac", "despac"]):
        return "SPAC"
    if any(k in text for k in ["分拆", "分拆上市"]):
        return "分拆上市"
    if any(k in text for k in ["破产", "重整"]):
        return "破产重整"
    if any(k in text for k in ["私有化", "退市", "回归A股"]):
        return "私有化+境内上市"
    if any(k in text for k in ["跨境", "海外", "境外", "收购海外", "acquires", "acquisition"]):
        return "跨境并购"
    if any(k in text for k in ["借壳", "重组上市", "置入资产"]):
        return "重组上市（借壳，含类借壳）"
    if any(k in text for k in ["控制权", "实际控制人", "要约收购", "权益变动", "协议转让"]):
        return "上市公司控股权并购"
    if any(k in text for k in ["并购基金", "PE", "私募基金", "基金"]):
        return "上市公司+PE"
    return "依托上市平台持续整合同类资产"


def rough_cases_from_items(items: list[RawItem], limit: int = 30) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for item in items[:limit]:
        text = f"{item.title} {item.summary}"
        cases.append({
            "案例分类": infer_category_from_text(text),
            "并购方": "-",
            "目标方": "-",
            "案例所属行业": "-",
            "并购方主营业务": "-",
            "标的主营业务": "-",
            "案例一句话简介": item.title[:120],
            "交易时间": item.published_at[:10] if item.published_at else "-",
            "交易对价": "-",
            "交易状态": "未知",
            "备注": f"DeepSeek 未启用，需人工复核。原始摘要：{item.summary[:180]}",
            "来源名称": item.source_name,
            "URL": item.url,
            "发布日期": item.published_at,
            "地区": item.region_hint,
        })
    return dedupe_cases(cases)


def structure_cases(items: list[RawItem], *, start_label: str, end_label: str, batch_size: int = 35, max_cases: int = 80) -> list[dict[str, str]]:
    if not items:
        return []
    if not os.getenv("DEEPSEEK_API_KEY"):
        LOGGER.warning("DEEPSEEK_API_KEY is not set; using rough fallback rows")
        return rough_cases_from_items(items, limit=max_cases)
    all_cases: list[dict[str, str]] = []
    for batch in chunked(items, batch_size):
        content = deepseek_chat(build_prompt(batch, start_label, end_label))
        parsed = extract_json_object(content)
        batch_cases = parsed.get("cases") or []
        if not isinstance(batch_cases, list):
            raise DeepSeekError(f"DeepSeek cases field is not a list: {parsed}")
        all_cases.extend(normalize_case(row) for row in batch_cases if isinstance(row, dict))
    return dedupe_cases(all_cases)[:max_cases]


def dedupe_cases(cases: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for row in cases:
        key_text = "|".join(normalize_text(row.get(field, "")) for field in ("并购方", "目标方", "案例一句话简介", "URL"))
        key = re.sub(r"\W+", "", key_text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result
