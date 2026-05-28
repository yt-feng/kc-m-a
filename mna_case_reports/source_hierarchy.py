"""Classify research rows by source reliability for M&A reports."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

OFFICIAL_HINTS = (
    "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn", "neeq.com.cn", "hkexnews.hk", "sec.gov",
    "samr.gov.cn", "csrc.gov.cn", "ndrc.gov.cn", "mofcom.gov.cn", "nasdaq.com", "nyse.com",
    "londonstockexchange.com", "investor.", "ir.",
)
MEDIA_HINTS = (
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "caixin.com", "yicai.com", "36kr.com",
    "thepaper.cn", "stcn.com", "sina.com", "163.com", "qq.com", "ifeng.com", "economictimes.com",
)


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url or "").netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_official(row: dict[str, str]) -> bool:
    domain = _domain(row.get("url") or "")
    source_name = (row.get("source_name") or "").lower()
    evidence_type = (row.get("evidence_type") or "").lower()
    if any(hint in domain for hint in OFFICIAL_HINTS):
        return True
    if any(token in evidence_type for token in ("pdf", "filing", "announcement", "official")):
        return True
    return any(token in source_name for token in ("公告", "监管", "交易所", "披露", "sec", "hkex", "巨潮"))


def is_media(row: dict[str, str]) -> bool:
    if is_official(row):
        return False
    domain = _domain(row.get("url") or "")
    source_name = row.get("source_name") or ""
    if any(hint in domain for hint in MEDIA_HINTS):
        return True
    return any(token in source_name for token in ("新闻", "媒体", "报道", "Reuters", "Bloomberg", "财新", "第一财经", "证券时报"))


def _row_summary(row: dict[str, str]) -> str:
    title = row.get("title") or row.get("source_name") or ""
    summary = row.get("numeric_facts") or row.get("summary") or row.get("extracted_text") or ""
    url = row.get("url") or ""
    text = f"{title}：{summary}" if summary else title
    if url:
        text = f"{text}（来源：{url}）"
    return re.sub(r"\s+", " ", text).strip()[:360]


def build_source_hierarchy(research_rows: list[dict[str, str]], limit: int = 10) -> dict[str, Any]:
    official_facts: list[str] = []
    media_reports: list[str] = []
    inference_basis: list[str] = []
    for row in research_rows:
        text = _row_summary(row)
        if not text:
            continue
        if is_official(row):
            official_facts.append(text)
        elif is_media(row):
            media_reports.append(text)
        else:
            media_reports.append(text)
    basis_pool = official_facts[:limit] + media_reports[:4]
    for item in basis_pool:
        if any(token in item for token in ("对价", "估值", "股权", "收入", "净利润", "用户", "客户", "完成", "交割", "公告", "监管")):
            inference_basis.append(item)
    return {
        "official_facts": official_facts[:limit],
        "media_reports": media_reports[:limit],
        "inference_basis": inference_basis[:limit],
        "rule": "官方事实可作确定表述；媒体报道必须写'据媒体报道'或'市场认为'；合理推断必须说明推理依据。",
    }
