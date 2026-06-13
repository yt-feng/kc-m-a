"""Fact-pack stage for M&A case reports.

DeepSeek does not browse the web during generation. This module turns the
collected research rows and the selected CaseBrief into a compact fact pack that
later prompts can use as the single factual base.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any

from .article_rules import extract_research_fact_lines, party_names_for_title
from .case_selection import CaseBrief
from .deepseek_client import chat_json

OFFICIAL_DOMAIN_HINTS = (
    "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn", "neeq.com.cn",
    "hkexnews.hk", "sec.gov", "samr.gov.cn", "csrc.gov.cn", "ndrc.gov.cn", "mofcom.gov.cn",
    "londonstockexchange.com", "nasdaq.com", "nyse.com",
)


@dataclass
class FactPack:
    case_name: str
    category: str
    region: str
    acquirer: str
    target: str
    deal_value: str
    deal_status: str
    buyer_rationale: str
    seller_rationale: str
    financial_highlights: str
    timeline: list[str]
    key_numbers: list[str]
    source_titles: list[str]
    source_refs: list[str]
    authoritative_source_count: int
    analysis_angles: list[str]
    validation_issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_list(values: list[Any], limit: int = 10) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:260])
        if len(out) >= limit:
            break
    return out


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_authoritative(row: dict[str, str]) -> bool:
    url = row.get("url") or ""
    domain = _domain(url)
    source_name = (row.get("source_name") or "").lower()
    evidence_type = (row.get("evidence_type") or "").lower()
    if any(hint in domain for hint in OFFICIAL_DOMAIN_HINTS):
        return True
    if "pdf" in evidence_type or "official" in source_name or "公告" in source_name:
        return True
    return False


def _source_titles(research_rows: list[dict[str, str]], limit: int = 12) -> list[str]:
    return _clean_list([row.get("title") or row.get("source_name") or "" for row in research_rows], limit=limit)


def _source_refs(research_rows: list[dict[str, str]], limit: int = 14) -> list[str]:
    refs: list[str] = []
    for row in research_rows:
        title = row.get("title") or row.get("source_name") or "source"
        url = row.get("url") or ""
        source = row.get("source_name") or ""
        if not url and not source:
            continue
        flag = "official" if _is_authoritative(row) else "supplement"
        refs.append(f"[{flag}] {title} | {source} | {url}"[:320])
    return _clean_list(refs, limit=limit)


def _analysis_angles(brief: CaseBrief, facts: list[str]) -> list[str]:
    text = "\n".join([brief.category or "", brief.case_name or "", brief.why or ""] + facts)
    angles: list[str] = []
    if any(token in text for token in ("跨境", "美元", "欧元", "海外", "境外", "global", "international")):
        angles.append("跨境交易中的审批、交割和治理承接")
    if any(token in text for token in ("控股", "控制权", "股权", "表决权", "并表")):
        angles.append("控制权取得、股权比例和治理安排")
    if any(token in text for token in ("现金", "股份", "支付", "对价", "估值", "价格")):
        angles.append("价格、支付方式、估值口径和资金安排")
    if any(token in text for token in ("客户", "订单", "产能", "技术", "专利", "产品", "平台", "用户")):
        angles.append("标的业务质量、客户关系和能力承接")
    if any(token in text for token in ("收入", "营收", "净利润", "EBITDA", "毛利", "现金流", "负债")):
        angles.append("收入、利润、现金流和财务影响")
    if any(token in text for token in ("整合", "协同", "人员", "管理", "治理", "承接")):
        angles.append("交割后的组织、业务和管理承接")
    if angles:
        angles.append("对同类并购的资料核验和执行方法论意义")
    return _clean_list(angles, limit=6)


def _initial_fact_pack(brief: CaseBrief, research_rows: list[dict[str, str]]) -> FactPack:
    acquirer, target = party_names_for_title(brief)
    facts = extract_research_fact_lines(research_rows, limit=14)
    timeline = [x for x in facts if any(token in x for token in ("年", "月", "日", "公告", "签署", "交割", "完成", "过户", "closed", "closing"))]
    key_numbers = [x for x in facts if re.search(r"\d", x) or any(token in x for token in ("亿元", "亿美元", "万元", "%"))]
    auth_count = sum(1 for row in research_rows if _is_authoritative(row))
    return FactPack(
        case_name=brief.case_name,
        category=brief.category,
        region=brief.region,
        acquirer=brief.acquirer or acquirer,
        target=brief.target or target,
        deal_value=brief.deal_value,
        deal_status=brief.deal_status or ("已完成，" + brief.completed_year if brief.completed_year else ""),
        buyer_rationale=brief.buyer_motivation,
        seller_rationale=brief.seller_motivation,
        financial_highlights=brief.financial_highlights,
        timeline=_clean_list(timeline, limit=8),
        key_numbers=_clean_list(key_numbers, limit=12),
        source_titles=_source_titles(research_rows),
        source_refs=_source_refs(research_rows),
        authoritative_source_count=auth_count,
        analysis_angles=_analysis_angles(brief, facts),
        validation_issues=[],
    )


def validate_fact_pack(pack: FactPack) -> list[str]:
    issues: list[str] = []
    if not pack.acquirer or pack.acquirer in {"收购方", "公开披露的买方"}:
        issues.append("缺少并购方名称。")
    if not pack.target or pack.target in {"标的", "公开披露的标的"}:
        issues.append("缺少标的方/出售方名称。")
    if not pack.deal_value or "未披露" in pack.deal_value:
        issues.append("缺少可引用的交易金额、估值或支付口径。")
    if len(pack.timeline) < 1 and not any(token in pack.deal_status for token in ("2023", "2024", "2025", "2026", "完成", "交割", "签署", "公告")):
        issues.append("缺少交易时间线。")
    if not pack.buyer_rationale or len(pack.buyer_rationale) < 15:
        issues.append("缺少收购方披露的购买理由。")
    if not pack.seller_rationale or len(pack.seller_rationale) < 15:
        issues.append("缺少出售方或被整合方接受安排的原因。")
    if len(pack.key_numbers) < 3 and not re.search(r"\d", pack.financial_highlights):
        issues.append("数据密度不足，缺少财务、交易或经营数字。")
    if pack.authoritative_source_count < 1:
        issues.append("缺少公开权威资料来源，需优先补公告、监管披露、交易所文件或公司公告。")
    return issues


def build_fact_pack(brief: CaseBrief, research_rows: list[dict[str, str]]) -> FactPack:
    """Build a fact pack, with one optional model pass if the raw rows are rich enough."""
    initial = _initial_fact_pack(brief, research_rows)
    payload_rows = research_rows[:28]
    try:
        messages = [
            {"role": "system", "content": "你是并购案例事实抽取器。只输出JSON，不写文章，不补充资料外事实。"},
            {
                "role": "user",
                "content": (
                    "请从资料线索中抽取并购案例事实包。不要写正文。资料没有披露的字段保留为空或写'公开资料未披露'，不得用行业通用解释补字段。"
                    "必须区分事实与分析：事实字段只能来自公开资料；analysis_angles只写可从事实包展开的分析角度，不能写通用并购方法论。"
                    "输出JSON格式：{\"acquirer\":...,\"target\":...,\"deal_value\":...,\"deal_status\":...,\"buyer_rationale\":...,\"seller_rationale\":...,\"financial_highlights\":...,\"timeline\":[...],\"key_numbers\":[...],\"analysis_angles\":[...]}。"
                    f"\n已知案例：{json.dumps(initial.to_dict(), ensure_ascii=False)}"
                    f"\n资料线索：{json.dumps(payload_rows, ensure_ascii=False)}"
                ),
            },
        ]
        data = chat_json(messages, timeout=180)
    except Exception:
        initial.validation_issues = validate_fact_pack(initial)
        return initial

    pack = FactPack(
        case_name=brief.case_name,
        category=brief.category,
        region=brief.region,
        acquirer=str(data.get("acquirer") or initial.acquirer),
        target=str(data.get("target") or initial.target),
        deal_value=str(data.get("deal_value") or initial.deal_value),
        deal_status=str(data.get("deal_status") or initial.deal_status),
        buyer_rationale=str(data.get("buyer_rationale") or initial.buyer_rationale),
        seller_rationale=str(data.get("seller_rationale") or initial.seller_rationale),
        financial_highlights=str(data.get("financial_highlights") or initial.financial_highlights),
        timeline=_clean_list(list(data.get("timeline") or []) + initial.timeline, limit=8),
        key_numbers=_clean_list(list(data.get("key_numbers") or []) + initial.key_numbers, limit=12),
        source_titles=initial.source_titles,
        source_refs=initial.source_refs,
        authoritative_source_count=initial.authoritative_source_count,
        analysis_angles=_clean_list(list(data.get("analysis_angles") or []) + initial.analysis_angles, limit=6),
        validation_issues=[],
    )
    pack.validation_issues = validate_fact_pack(pack)
    return pack
