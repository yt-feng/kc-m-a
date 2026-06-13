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
from .sources_fixed import is_aggregator_url, is_likely_homepage_url, is_usable_article_url, unwrap_news_url

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_CASES_PER_BATCH = 12
PLACEHOLDER_VALUES = {"", "-", "无", "未知", "不详", "未披露", "n/a", "na", "none", "null"}
PARTY_SUFFIX_RE = re.compile(
    r"(股份有限公司|有限责任公司|有限公司|控股集团|控股有限公司|集团股份|集团|公司|"
    r"corporation|inc\.?|limited|ltd\.?|plc|holdings?)",
    re.I,
)


class DeepSeekError(RuntimeError):
    pass


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
        "url": unwrap_news_url(str(item.url or ""))[:500],
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
9. URL 必须使用候选信息中的原始公告/媒体链接，不得输出 news.google.com、news.google.com/rss/articles、bing.com/news/apiclick 这类聚合跳转链接。
10. JSON 规则：只能输出 JSON；字符串中的英文双引号必须转义；不得输出尾随逗号、注释、Markdown、未闭合字符串或未转义换行。

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
    normalized["URL"] = unwrap_news_url(normalized.get("URL", ""))
    if is_aggregator_url(normalized["URL"]) or is_likely_homepage_url(normalized["URL"]):
        normalized["URL"] = "-"
    if normalized.get("案例分类") not in CATEGORIES:
        normalized["案例分类"] = infer_category_from_text(" ".join(normalized.values()))
    return normalized


def compact_match_text(value: str | None) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalize_text(value or ""))


def raw_item_match_text(item: RawItem) -> str:
    return compact_match_text(f"{item.title} {item.summary} {item.source_name} {item.query}")


def raw_item_host(item: RawItem) -> str:
    match = re.match(r"https?://([^/]+)", unwrap_news_url(item.url or ""), re.I)
    return match.group(1).lower().removeprefix("www.") if match else ""


def source_host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", unwrap_news_url(url or ""), re.I)
    return match.group(1).lower().removeprefix("www.") if match else ""


def url_matches_raw_item(url: str, item: RawItem) -> bool:
    left = unwrap_news_url(url or "").rstrip("/")
    right = unwrap_news_url(item.url or "").rstrip("/")
    return bool(left and right and left == right)


def score_raw_item_for_case(row: dict[str, str], item: RawItem) -> int:
    item_text = raw_item_match_text(item)
    row_url = row.get("URL", "")
    score = 0
    if row_url and source_host(row_url) and source_host(row_url) == raw_item_host(item):
        score += 6
    source_name = compact_match_text(row.get("来源名称", ""))
    item_source = compact_match_text(item.source_name)
    if source_name and item_source and (source_name in item_source or item_source in source_name):
        score += 3
    for field in ("并购方", "目标方"):
        party = compact_match_text(normalize_party(row.get(field)))
        if len(party) >= 3 and party in item_text:
            score += 5
    description = compact_match_text(row.get("案例一句话简介", ""))
    if description:
        for token in re.findall(r"[a-z0-9]{4,}|[\u4e00-\u9fff]{2,}", normalize_text(row.get("案例一句话简介", ""))):
            token_c = compact_match_text(token)
            if len(token_c) >= 3 and token_c in item_text:
                score += 1
    return score


def reconcile_case_urls(cases: list[dict[str, str]], batch: list[RawItem]) -> list[dict[str, str]]:
    usable_items = [item for item in batch if is_usable_article_url(unwrap_news_url(item.url or ""))]
    for row in cases:
        row_url = unwrap_news_url(row.get("URL", ""))
        if any(url_matches_raw_item(row_url, item) for item in usable_items):
            row["URL"] = row_url
            continue
        best_item = None
        best_score = 0
        for item in usable_items:
            score = score_raw_item_for_case(row, item)
            if score > best_score:
                best_item = item
                best_score = score
        if best_item and best_score >= 8:
            row["URL"] = unwrap_news_url(best_item.url)
            if not row.get("来源名称") or row.get("来源名称") == "-":
                row["来源名称"] = best_item.source_name or "-"
            if not row.get("发布日期") or row.get("发布日期") == "-":
                row["发布日期"] = best_item.published_at or "-"
            continue
        row["URL"] = "-"
    return cases


def normalize_party(value: str | None) -> str:
    text = normalize_text(value or "")
    text = re.sub(r"（.*?）|\(.*?\)", "", text)
    text = PARTY_SUFFIX_RE.sub("", text)
    text = re.sub(r"[\s·・,，.。:：;；/\\|&＋+_-]+", "", text)
    return "" if text in PLACEHOLDER_VALUES or any(token in text for token in ("未披露", "未知", "不详", "某标的", "标的资产", "标的公司")) else text


def party_part_matches(left: str, right: str) -> bool:
    left = normalize_party(left)
    right = normalize_party(right)
    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) >= 3 and len(right) >= 3 and (left in right or right in left):
        return True
    return False


def normalize_case_title(value: str | None) -> str:
    text = normalize_text(value or "")
    text = re.sub(r"^(?:weekly|backfill)_\d{8}_\d{6}_", "", text)
    text = re.split(r"[:：]", text, maxsplit=1)[0]
    text = re.sub(r"20\d{2}[年/-]?\d{0,2}[月/-]?\d{0,2}日?", "", text)
    text = re.sub(r"\d+(?:,\d{3})*(?:\.\d+)?(?:亿元|亿美元|亿港元|万元|美元|港元|元|%|％|股|股份|股权)?", "", text)
    text = re.sub(r"(交易复盘|案例分析|并购案例|交易启示|交易观察|并购启示|案例研究)", "", text)
    text = re.sub(r"\W+", "", text)
    return text[:80]


def case_identity(row: dict[str, str]) -> str:
    acquirer = normalize_party(row.get("并购方"))
    target = normalize_party(row.get("目标方"))
    if acquirer and target:
        return f"party:{acquirer}->{target}"
    title = normalize_case_title(row.get("案例一句话简介") or row.get("备注") or row.get("URL"))
    return f"title:{title}" if title else ""


def case_identities(row: dict[str, str]) -> set[str]:
    acquirer = normalize_party(row.get("并购方"))
    target = normalize_party(row.get("目标方"))
    title = normalize_case_title(row.get("案例一句话简介") or row.get("备注") or row.get("URL"))
    keys: set[str] = set()
    if acquirer and target:
        keys.add(f"party:{acquirer}->{target}")
    if title:
        keys.add(f"title:{title}")
    return keys


def case_identity_matches(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_kind, _, left_value = left.partition(":")
    right_kind, _, right_value = right.partition(":")
    if left_kind != right_kind:
        return False
    if left_kind == "party":
        left_a, _, left_t = left_value.partition("->")
        right_a, _, right_t = right_value.partition("->")
        return party_part_matches(left_a, right_a) and party_part_matches(left_t, right_t)
    if left_kind == "title":
        return len(left_value) >= 6 and len(right_value) >= 6 and (left_value in right_value or right_value in left_value)
    return False


def identity_in_set(identity: str, previous_identities: set[str]) -> bool:
    return any(case_identity_matches(identity, previous) for previous in previous_identities)


def identities_overlap(identities: set[str], previous_identities: set[str]) -> bool:
    return any(identity_in_set(identity, previous_identities) for identity in identities)


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
        normalized = reconcile_case_urls(normalized, batch)
        return normalized[:MAX_CASES_PER_BATCH]
    except Exception as exc:  # noqa: BLE001
        raise DeepSeekError(f"DeepSeek batch {batch_index} failed: {exc}; content_prefix={content[:500]}") from exc


def structure_cases(items: list[RawItem], *, start_label: str, end_label: str, batch_size: int = 20, max_cases: int = 80) -> list[dict[str, str]]:
    if not items:
        return []
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise DeepSeekError("DEEPSEEK_API_KEY is not set")
    all_cases: list[dict[str, str]] = []
    for batch_index, batch in enumerate(chunked(items, batch_size), start=1):
        all_cases.extend(parse_deepseek_batch(batch, start_label, end_label, batch_index))
    return dedupe_cases(all_cases)[:max_cases]


def dedupe_cases(cases: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for row in cases:
        keys = case_identities(row)
        if not keys:
            key_text = "|".join(normalize_text(row.get(field, "")) for field in ("并购方", "目标方", "案例一句话简介", "URL"))
            key = re.sub(r"\W+", "", key_text)
            keys = {key} if key else set()
        if not keys or identities_overlap(keys, seen):
            continue
        seen.update(keys)
        result.append(row)
    return result
