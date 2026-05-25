"""Narrative planning stage for M&A case reports.

This stage replaces a fixed outline-first workflow.  It asks the model to decide
what the article is really about: the core question, narrative focus, depth
angles, and flexible structure logic.  The article generator may then create
its own 4-7 chapters around that narrative rather than being forced into a
standard outline.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .case_selection import CaseBrief
from .deepseek_client import chat_json
from .fact_pack import FactPack


@dataclass
class NarrativePlan:
    core_question: str
    central_thesis: str
    narrative_focus: str
    title_direction: str
    structure_logic: str
    depth_angles: list[str]
    chapter_directions: list[str]
    must_cover: list[str]
    avoid_patterns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_list(values: list[Any], limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:220])
        if len(out) >= limit:
            break
    return out


def _fallback_plan(brief: CaseBrief, fact_pack: FactPack) -> NarrativePlan:
    angles = fact_pack.analysis_angles or ["交易事实、资产质量、价格安排和交割承接的连续复盘"]
    return NarrativePlan(
        core_question=f"{brief.case_name}最值得复盘的是交易双方如何围绕资产质量、价格安排和交割承接形成可执行的交易路径。",
        central_thesis="并购案例的分析重点不只是交易是否完成，而是交易前的业务关系、交易中的条款安排和交割后的承接条件是否形成连续闭环。",
        narrative_focus="围绕交易事实、双方业务基础、对价与控制权安排、交割承接和同类交易方法论展开。",
        title_direction="标题应包含交易双方简称，并突出本案最核心的交易逻辑或执行关注点。",
        structure_logic="根据材料信息量安排4-7章：事实密集处展开，资料较少处合并，不追求章节长度一致。",
        depth_angles=_clean_list(angles, limit=5),
        chapter_directions=_clean_list([
            "先交代交易时间、金额、状态和披露边界，建立事实基础",
            "再解释交易双方业务基础、资产关系和接受交易安排的原因",
            "围绕本案最有信息量的交易结构、估值、支付或控制权安排展开分析",
            "结合财务、客户、产能、技术、资源或治理资料说明交易承接条件",
            "结语提炼同类并购在资料核验、条款安排和交割承接上的方法论意义",
        ], limit=6),
        must_cover=[
            "交易日期或时间线", "交易金额、估值、支付方式或股权比例", "并购方和标的方基本介绍",
            "买方购买理由", "出售方或被整合方接受安排的原因", "产业、交易结构、财务影响或交割承接中的至少三个层面",
        ],
        avoid_patterns=[
            "不要套用固定五章结构", "不要每章段落数量一致", "不要反复使用相同段落开头",
            "不要把交易动机、交易背景、交易结构设计等提纲词直接写成标题",
        ],
    )


def validate_narrative_plan(plan: NarrativePlan) -> list[str]:
    issues: list[str] = []
    if len(plan.core_question) < 20:
        issues.append("core_question过短，未形成明确复盘问题。")
    if len(plan.central_thesis) < 24:
        issues.append("central_thesis过短，未形成文章主线。")
    if len(plan.depth_angles) < 3:
        issues.append("depth_angles不足，至少需要三个深入分析角度。")
    if len(plan.chapter_directions) < 4:
        issues.append("chapter_directions不足，无法支撑4-7章灵活结构。")
    template_words = ("交易动机", "交易背景", "交易结构设计", "并购战略考量", "标的筛选", "并购后整合", "价值释放")
    if any(any(word in item for word in template_words) for item in plan.chapter_directions):
        issues.append("chapter_directions包含提纲词，容易导致模板化标题。")
    return issues


def build_narrative_plan(brief: CaseBrief, fact_pack: FactPack, research_rows: list[dict[str, str]]) -> NarrativePlan:
    fallback = _fallback_plan(brief, fact_pack)
    try:
        messages = [
            {
                "role": "system",
                "content": "你是并购案例研究的主编，负责先确定文章主线。只输出JSON，不写正文，不编造事实。",
            },
            {
                "role": "user",
                "content": (
                    "请基于事实包为并购案例文章制定'叙事重心'，不要生成固定大纲。"
                    "目标是让文章结构服务于材料，而不是套用模板。请判断本案例最值得复盘的问题、文章主线、标题方向、结构逻辑和深度分析角度。"
                    "chapter_directions只是写作方向，不是章节标题；应允许正文阶段根据材料生成4-7个章节。"
                    "必须覆盖至少三个深度层面：产业判断、交易结构、财务影响、交割承接、并购方法论意义。"
                    "输出JSON格式：{\"core_question\":...,\"central_thesis\":...,\"narrative_focus\":...,\"title_direction\":...,\"structure_logic\":...,\"depth_angles\":[...],\"chapter_directions\":[...],\"must_cover\":[...],\"avoid_patterns\":[...]}。"
                    f"\n案例：{brief.case_name}"
                    f"\n分类：{brief.category}"
                    f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
                    f"\n资料线索：{json.dumps(research_rows[:18], ensure_ascii=False)}"
                ),
            },
        ]
        data = chat_json(messages, timeout=180)
        plan = NarrativePlan(
            core_question=str(data.get("core_question") or fallback.core_question),
            central_thesis=str(data.get("central_thesis") or fallback.central_thesis),
            narrative_focus=str(data.get("narrative_focus") or fallback.narrative_focus),
            title_direction=str(data.get("title_direction") or fallback.title_direction),
            structure_logic=str(data.get("structure_logic") or fallback.structure_logic),
            depth_angles=_clean_list(list(data.get("depth_angles") or []) + fallback.depth_angles, limit=6),
            chapter_directions=_clean_list(list(data.get("chapter_directions") or []) + fallback.chapter_directions, limit=7),
            must_cover=_clean_list(list(data.get("must_cover") or []) + fallback.must_cover, limit=8),
            avoid_patterns=_clean_list(list(data.get("avoid_patterns") or []) + fallback.avoid_patterns, limit=8),
        )
    except Exception:
        return fallback
    if validate_narrative_plan(plan):
        return fallback
    return plan


def fallback_sections_from_plan(plan: NarrativePlan) -> list[dict[str, Any]]:
    directions = plan.chapter_directions[:5] or _fallback_plan(CaseBrief(case_name="并购案例", category="", region=""), FactPack(  # type: ignore[arg-type]
        case_name="并购案例", category="", region="", acquirer="", target="", deal_value="", deal_status="",
        buyer_rationale="", seller_rationale="", financial_highlights="", timeline=[], key_numbers=[], source_titles=[],
        source_refs=[], authoritative_source_count=0, analysis_angles=[], validation_issues=[],
    )).chapter_directions
    headings: list[str] = []
    for idx, direction in enumerate(directions, start=1):
        if idx == len(directions):
            headings.append(f"{idx}、结语：{plan.central_thesis[:22] or '从公开事实回到执行关注点'}")
        else:
            headings.append(f"{idx}、{direction[:28]}")
    if len(headings) < 4:
        headings = [
            "一、交易事实与披露边界",
            "二、业务基础与资产关系",
            "三、价格安排与承接条件",
            "四、结语：从公开事实回到执行关注点",
        ]
    cn = ["零", "一", "二", "三", "四", "五", "六", "七"]
    out = []
    for i, heading in enumerate(headings, start=1):
        bare = heading.split("、", 1)[-1]
        if i == len(headings):
            bare = bare.split("结语", 1)[-1].lstrip("：: ") if "结语" in bare else bare
            out.append({"heading": f"{cn[i]}、结语：{bare}", "paragraphs": []})
        else:
            out.append({"heading": f"{cn[i]}、{bare}", "paragraphs": []})
    return out
