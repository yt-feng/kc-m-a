"""Outline generation stage for M&A case reports."""

from __future__ import annotations

import json
import re
from typing import Any

from .article_rules import HEADING_THINKING_PATTERNS, BANNED_TONE_PATTERNS, cn_number, strip_heading_number
from .case_selection import CaseBrief
from .deepseek_client import chat_json
from .fact_pack import FactPack

DEFAULT_OUTLINE = [
    "关键事实与交易进程",
    "交易双方的业务基础与披露信息",
    "价格、支付方式与交割条件安排",
    "交割后的治理、业务与人员承接",
    "结语：从公开事实回到执行关注点",
]


def _normalize_heading(text: str) -> str:
    text = strip_heading_number(text)
    text = re.sub(r"^结语[:：]?", "结语：", text).strip()
    replacements = {
        "交易动机": "双方披露的交易出发点",
        "交易背景": "交易启动前的业务与时间线",
        "并购战略考量": "收购方业务边界与资产承接条件",
        "标的筛选": "标的资产、客户与财务质量核验",
        "交易结构设计": "价格、支付方式与交割条件安排",
        "并购后整合": "交割后的治理、业务与人员承接",
        "价值释放": "收入、利润与协同事项的后续观察",
        "投后整合": "交割后的治理和业务承接",
        "窗口期如何打开": "交易启动前的业务与时间线",
        "用条款把不确定性前置": "价格、支付方式与交割条件安排",
        "交割后的第一件事是接住能力": "交割后的治理、业务与人员承接",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.strip(" ：:") or "关键事实与交易进程"


def normalize_outline(headings: list[str]) -> list[str]:
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
    if len(clean) < 4:
        clean = DEFAULT_OUTLINE[:]
    if not any("结语" in h for h in clean):
        clean.append("结语：从公开事实回到执行关注点")
    if len(clean) > 7:
        clean = clean[:6] + [clean[-1]]
    # Ensure only the last chapter is conclusion.
    non_last = [h for h in clean[:-1] if "结语" not in h]
    last = clean[-1]
    if "结语" not in last:
        last = "结语：从公开事实回到执行关注点"
    clean = non_last + [last]
    total = len(clean)
    numbered: list[str] = []
    for idx, heading in enumerate(clean, start=1):
        number = cn_number(idx)
        stripped = strip_heading_number(heading)
        if idx == total:
            suffix = stripped.split("结语", 1)[-1].lstrip("：: ") if "结语" in stripped else stripped
            suffix = suffix or "从公开事实回到执行关注点"
            numbered.append(f"{number}、结语：{suffix}")
        else:
            stripped = re.sub(r"^结语[:：]?", "", stripped).strip() or "关键事实与交易进程"
            numbered.append(f"{number}、{stripped}")
    return numbered


def validate_outline(headings: list[str]) -> list[str]:
    issues: list[str] = []
    if len(headings) < 4 or len(headings) > 7:
        issues.append("章节数量必须为4-7章。")
    for heading in headings:
        bare = strip_heading_number(heading)
        if any(pattern in bare for pattern in HEADING_THINKING_PATTERNS):
            issues.append(f"标题出现提纲词：{heading}")
        if any(pattern in bare for pattern in BANNED_TONE_PATTERNS):
            issues.append(f"标题不够客观中性：{heading}")
    expected = cn_number(len(headings))
    if headings and not headings[-1].startswith(f"{expected}、结语："):
        issues.append("最后一章需要按实际顺序编号并使用'结语：副标题'。")
    return issues


def generate_outline(brief: CaseBrief, fact_pack: FactPack) -> list[str]:
    try:
        messages = [
            {"role": "system", "content": "你是并购案例报告大纲编辑。只输出JSON，不写正文。"},
            {
                "role": "user",
                "content": (
                    "请基于事实包生成4-7个章节标题。标题要客观、中性、克制，概括事实和关注点；不要口号化、负面化或广告化。"
                    "不要直接使用'交易动机、交易背景、交易结构设计、并购战略考量、标的筛选、并购后整合、价值释放'等提纲词。"
                    "最后一章必须是'结语：副标题'，但不要编号，编号由程序处理。"
                    "输出JSON格式：{\"headings\":[...]}。"
                    f"\n案例：{brief.case_name}"
                    f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
                ),
            },
        ]
        data = chat_json(messages, timeout=150)
        raw = data.get("headings") or []
        if not isinstance(raw, list):
            raw = []
        headings = normalize_outline([str(x) for x in raw])
    except Exception:
        headings = normalize_outline(DEFAULT_OUTLINE)
    issues = validate_outline(headings)
    if issues:
        headings = normalize_outline(DEFAULT_OUTLINE)
    return headings


def outline_to_sections(headings: list[str]) -> list[dict[str, Any]]:
    return [{"heading": h, "paragraphs": []} for h in headings]
