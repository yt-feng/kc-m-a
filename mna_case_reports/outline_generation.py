"""Outline generation stage for M&A case reports."""

from __future__ import annotations

import json
import re
from typing import Any

from .article_rules import HEADING_THINKING_PATTERNS, BANNED_TONE_PATTERNS, cn_number, strip_heading_number
from .case_selection import CaseBrief
from .deepseek_client import chat_json
from .fact_pack import FactPack


def _normalize_heading(text: str) -> str:
    text = strip_heading_number(text)
    text = re.sub(r"^结语[:：]?", "结语：", text).strip()
    return text.strip(" ：:")


def normalize_outline(headings: list[str], *, brief: CaseBrief | None = None, fact_pack: FactPack | None = None) -> list[str]:
    clean: list[str] = []
    for item in headings:
        heading = _normalize_heading(str(item or ""))
        if not heading:
            continue
        if heading in clean:
            continue
        clean.append(heading)
        if len(clean) >= 7:
            break
    if len(clean) > 7:
        clean = clean[:6] + [clean[-1]]
    total = len(clean)
    numbered: list[str] = []
    for idx, heading in enumerate(clean, start=1):
        number = cn_number(idx)
        stripped = strip_heading_number(heading)
        if idx == total and "结语" in stripped:
            suffix = stripped.split("结语", 1)[-1].lstrip("：: ") if "结语" in stripped else stripped
            numbered.append(f"{number}、结语：{suffix}")
        else:
            stripped = re.sub(r"^结语[:：]?", "", stripped).strip()
            numbered.append(f"{number}、{stripped}")
    return numbered


def validate_outline(headings: list[str]) -> list[str]:
    issues: list[str] = []
    if len(headings) < 4 or len(headings) > 7:
        issues.append("章节数量必须为4-7章。")
    if len(set(strip_heading_number(h) for h in headings)) != len(headings):
        issues.append("章节标题存在重复。")
    for heading in headings:
        bare = strip_heading_number(heading)
        if any(pattern in bare for pattern in HEADING_THINKING_PATTERNS):
            issues.append(f"标题出现提纲词：{heading}")
        if any(pattern in bare for pattern in BANNED_TONE_PATTERNS):
            issues.append(f"标题不够客观中性：{heading}")
        if len(bare) < 8:
            issues.append(f"标题过短、信息量不足：{heading}")
    expected = cn_number(len(headings))
    if headings and not headings[-1].startswith(f"{expected}、结语："):
        issues.append("最后一章需要按实际顺序编号并使用'结语：副标题'。")
    return issues


def generate_outline(brief: CaseBrief, fact_pack: FactPack) -> list[str]:
    messages = [
        {"role": "system", "content": "你是并购案例报告大纲编辑。只输出JSON，不写正文。"},
        {
            "role": "user",
            "content": (
                "请基于事实包生成4-7个章节标题。不要套用固定模板，要根据该案例材料决定结构和叙述重点。"
                "标题要客观、中性、克制，概括事实、核心交易逻辑和分析重点；兼顾专业性与可读性，但不要口号化、负面化或广告化。"
                "除案例拆解外，大纲中应体现至少一个深入分析角度，例如产业判断、交易结构、控制权/治理安排、财务影响、交割承接或并购方法论意义。"
                "不要直接使用'交易动机、交易背景、交易结构设计、并购战略考量、标的筛选、并购后整合、价值释放'等提纲词。"
                "最后一章必须是'结语：副标题'，但不要编号，编号由程序处理。"
                "输出JSON格式：{\"headings\":[...]}。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
                f"\n建议分析角度：{json.dumps(fact_pack.analysis_angles, ensure_ascii=False)}"
            ),
        },
    ]
    data = chat_json(messages, timeout=150)
    raw = data.get("headings") or []
    if not isinstance(raw, list):
        raw = []
    headings = normalize_outline([str(x) for x in raw], brief=brief, fact_pack=fact_pack)
    issues = validate_outline(headings)
    if issues:
        raise RuntimeError(f"Outline validation failed for {brief.case_name}: {issues}")
    return headings


def outline_to_sections(headings: list[str]) -> list[dict[str, Any]]:
    return [{"heading": h, "paragraphs": []} for h in headings]
