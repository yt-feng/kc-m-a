"""Fact-pack stage for M&A case reports.

DeepSeek does not browse the web during generation. This module turns the
collected research rows and the selected CaseBrief into a compact fact pack that
later prompts can use as the single factual base.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from .article_rules import extract_research_fact_lines, party_names_for_title
from .case_selection import CaseBrief
from .deepseek_client import chat_json


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


def _source_titles(research_rows: list[dict[str, str]], limit: int = 12) -> list[str]:
    return _clean_list([row.get("title") or row.get("source_name") or "" for row in research_rows], limit=limit)


def _initial_fact_pack(brief: CaseBrief, research_rows: list[dict[str, str]]) -> FactPack:
    acquirer, target = party_names_for_title(brief)
    facts = extract_research_fact_lines(research_rows, limit=14)
    timeline = [x for x in facts if any(token in x for token in ("年", "月", "日", "公告", "签署", "交割", "完成", "过户", "closed", "closing"))]
    key_numbers = [x for x in facts if re.search(r"\d", x) or any(token in x for token in ("亿元", "亿美元", "万元", "%"))]
    return FactPack(
        case_name=brief.case_name,
        category=brief.category,
        region=brief.region,
        acquirer=brief.acquirer or acquirer,
        target=brief.target or target,
        deal_value=brief.deal_value or "公开资料未披露统一口径的完整金额",
        deal_status=brief.deal_status or ("已完成，" + brief.completed_year if brief.completed_year else "公开资料披露了交易进展"),
        buyer_rationale=brief.buyer_motivation or "公开资料显示，收购方围绕业务协同、能力补强、资产控制或上市平台整合推进交易。",
        seller_rationale=brief.seller_motivation or "公开资料显示，出售方或被整合方接受交易安排，与股权转让、资源承接、平台整合或资本化路径有关。",
        financial_highlights=brief.financial_highlights or "公开资料披露的经营数据需要与公告、年报和交割文件交叉核验。",
        timeline=_clean_list(timeline, limit=8),
        key_numbers=_clean_list(key_numbers, limit=12),
        source_titles=_source_titles(research_rows),
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
                    "请从资料线索中抽取并购案例事实包。不要写正文。资料没有披露的字段保留为空或写'公开资料未披露'。"
                    "输出JSON格式：{\"acquirer\":...,\"target\":...,\"deal_value\":...,\"deal_status\":...,\"buyer_rationale\":...,\"seller_rationale\":...,\"financial_highlights\":...,\"timeline\":[...],\"key_numbers\":[...]}。"
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
        validation_issues=[],
    )
    pack.validation_issues = validate_fact_pack(pack)
    return pack
