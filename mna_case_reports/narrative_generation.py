"""Narrative planning stage for M&A case reports."""

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
    return issues


def build_narrative_plan(brief: CaseBrief, fact_pack: FactPack, research_rows: list[dict[str, str]]) -> NarrativePlan:
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
                "必须覆盖至少三个深度层面，优先包括产业判断、交易结构分析和并购方法论意义，并可结合财务影响、交割承接、治理边界继续展开。"
                "结语方向必须紧扣本案事实和交易结构，不能输出通用口号。"
                "输出JSON格式：{\"core_question\":...,\"central_thesis\":...,\"narrative_focus\":...,\"title_direction\":...,\"structure_logic\":...,\"depth_angles\":[...],\"chapter_directions\":[...],\"must_cover\":[...],\"avoid_patterns\":[...]}。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
                f"\n资料线索：{json.dumps(research_rows[:24], ensure_ascii=False)}"
            ),
        },
    ]
    data = chat_json(messages, timeout=180)
    plan = NarrativePlan(
        core_question=str(data.get("core_question") or ""),
        central_thesis=str(data.get("central_thesis") or ""),
        narrative_focus=str(data.get("narrative_focus") or ""),
        title_direction=str(data.get("title_direction") or ""),
        structure_logic=str(data.get("structure_logic") or ""),
        depth_angles=_clean_list(list(data.get("depth_angles") or []), limit=6),
        chapter_directions=_clean_list(list(data.get("chapter_directions") or []), limit=7),
        must_cover=_clean_list(list(data.get("must_cover") or []), limit=8),
        avoid_patterns=_clean_list(list(data.get("avoid_patterns") or []), limit=8),
    )
    issues = validate_narrative_plan(plan)
    if issues:
        raise RuntimeError(f"Narrative plan validation failed for {brief.case_name}: {issues}")
    return plan
