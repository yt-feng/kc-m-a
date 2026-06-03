"""DeepSeek-based structuring for raw M&A candidates."""

from __future__ import annotations

import json
import logging
import os
import re
from json import JSONDecodeError
from typing import Any

import requests

from .config import CATEGORIES, CATEGORY_GUIDE, OUTPUT_COLUMNS, chunked
from .sources import RawItem, normalize_text

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_CASES_PER_BATCH = 12


class DeepSeekError(RuntimeError):
    pass


def allow_rough_fallback() -> bool:
    return os.getenv("MNA_ALLOW_ROUGH_FALLBACK", "").strip().lower() in {"1", "true", "yes", "y"}


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except JSONDecodeError as first_error:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise first_error
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except JSONDecodeError:
            raise first_error


def deepseek_chat(messages: list[dict[str, str]], *, model: str | None = None, timeout: int = 120) -> str:
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
            "temperature": 0.1,
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
        raise DeepSeekError(f"Unexpected DeepSeek response: {data}") from exc


def compact_item(item: RawItem) -> dict[str, str]:
    return {
        "title": str(item.title or "")[:180],
        "url": str(item.url or "")[:500],
        "source_name": str(item.source_name or "")[:80],
        "published_at": str(item.published_at or "")[:40],
        "summary": str(item.summary or "")[:260],
        "region_hint": str(item.region_hint or "")[:40],
        "query": str(item.query or "")[:120],
    }


def build_prompt(items: list[RawItem], start_label: str, end_label: str) -> list[dict[str, str]]:
    raw_items = [compact_item(item) for item in items]
    system = "你是资深并购案例研究员。把公告/新闻候选结构化为周度并购案例表。只基于给定候选，不要编造；无法确认的信息填 '-'。必须输出可被 json.loads 解析的严格 JSON 对象。"
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
3. 每批最多输出 {MAX_CASES_PER_BATCH} 个高质量案例；宁缺毋滥。
4. 案例一句话简介要像投行案例库标题一样精炼，突出交易亮点或资本运作特点，30-60 个中文字符为宜。
5. 交易状态优先用：已完成、进行中、审批中、终止、意向、未知。
6. 中国 A 股/港股案例优先保留；全球新闻只保留具有明确交易金额、交易双方或战略意义的案例。
7. 对中东买方（如 PIF、Mubadala、QIA、ADQ、ADIA、KIA、OIA、Mumtalakat、ICD、Prosperity7、G42、e& 等）收购、入股、控股、少数股权、业务剥离、私有化海外企业的案例优先识别；若只是 MoU/合作且没有股权或资产交易，剔除或在备注中明确说明。
8. 严格去重，同一交易只输出一行，URL 取最能证明交易的来源。
9. JSON 规则：只能输出 JSON；字符串中的英文双引号必须转义；不得输出尾随逗号、注释、Markdown、未闭合字符串或未转义换行。

候选信息 JSON：
{json.dumps(raw_items, ensure_ascii=False, separators=(",", ":"))}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def repair_json(content: str, error: Exception) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": "你是 JSON 修复器。只输出修复后的严格 JSON 对象，不添加解释。"},
        {
            "role": "user",
            "content": (
                "下面文本本应是 JSON，但无法解析。请在不新增事实、不改含义的前提下，修复为可被 json.loads 解析的 JSON 对象。"
                f"\n解析错误：{error}"
                "\n目标格式：{\"cases\":[...],\"discarded_count\":数字}。"
                "\n待修复文本：\n"
                f"{content[:12000]}"
            ),
        },
    ]
    repaired = deepseek_chat(messages, timeout=120)
    return extract_json_object(repaired)


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
    if any(k in text_n for k in ["mubadala", "public investment fund", "pif", "qia", "adq", "adia", "prosperity7", "g42", "etisalat"]):
        return "跨境并购"
    if any(k in text for k in ["跨境", "海外", "境外", "收购海外", "acquires", "acquisition", "takeover", "stake", "subscription", "placing"]):
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
            "案例一句话简介": str(item.title)[:120],
            "交易时间": item.published_at[:10] if item.published_at else "-",
            "交易对价": "-",
            "交易状态": "未知",
            "备注": f"DeepSeek 结构化失败后的候选保底行，需人工复核。原始摘要：{str(item.summary)[:180]}",
            "来源名称": item.source_name,
            "URL": item.url,
            "发布日期": item.published_at,
            "地区": item.region_hint,
        })
    return dedupe_cases(cases)


def parse_deepseek_batch(batch: list[RawItem], start_label: str, end_label: str, batch_index: int) -> list[dict[str, str]]:
    content = ""
    try:
        content = deepseek_chat(build_prompt(batch, start_label, end_label))
        try:
            parsed = extract_json_object(content)
        except JSONDecodeError as exc:
            LOGGER.warning("DeepSeek returned malformed JSON for batch %s; attempting repair: %s", batch_index, exc)
            parsed = repair_json(content, exc)
        batch_cases = parsed.get("cases") or []
        if not isinstance(batch_cases, list):
            raise DeepSeekError(f"DeepSeek cases field is not a list: {parsed}")
        normalized = [normalize_case(row) for row in batch_cases if isinstance(row, dict)]
        return normalized[:MAX_CASES_PER_BATCH]
    except Exception as exc:  # noqa: BLE001
        if not allow_rough_fallback():
            raise DeepSeekError(f"DeepSeek batch {batch_index} failed and rough fallback is disabled: {exc}") from exc
        LOGGER.warning("DeepSeek batch %s failed; using rough fallback for this batch. error=%s content_prefix=%s", batch_index, exc, content[:500])
        return rough_cases_from_items(batch, limit=3)


def structure_cases(items: list[RawItem], *, start_label: str, end_label: str, batch_size: int = 20, max_cases: int = 80) -> list[dict[str, str]]:
    if not items:
        return []
    if not os.getenv("DEEPSEEK_API_KEY"):
        if not allow_rough_fallback():
            raise DeepSeekError("DEEPSEEK_API_KEY is not set and rough fallback is disabled")
        LOGGER.warning("DEEPSEEK_API_KEY is not set; using rough fallback rows")
        return rough_cases_from_items(items, limit=max_cases)
    all_cases: list[dict[str, str]] = []
    for batch_index, batch in enumerate(chunked(items, batch_size), start=1):
        all_cases.extend(parse_deepseek_batch(batch, start_label, end_label, batch_index))
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
