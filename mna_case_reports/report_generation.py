"""Article generation for M&A case analysis reports."""

from __future__ import annotations

import json
import logging
import re

from .case_selection import CaseBrief
from .config import CATEGORY_GUIDE, REFERENCE_STYLE, STYLE_RULES, TOPIC_SELECTION_RULES
from .deepseek_client import chat_json
from .research import collect_research_context

LOGGER = logging.getLogger(__name__)

BANNED_INTRO_PATTERNS = ("本文", "本报告", "本文将", "本文认为", "本文分析", "以下将")
GENERIC_HEADINGS = ("交易过程", "交易逻辑", "可复用经验", "结论", "经验启示", "案例启示")
TIME_PATTERNS = ("2025", "2026", "2024", "2023", "交割", "完成", "签约", "公告", "过户")
CONSIDERATION_PATTERNS = ("亿元", "亿美元", "万欧元", "亿欧元", "对价", "估值", "交易金额", "作价", "价格")
FINANCIAL_PATTERNS = ("营收", "收入", "净利润", "毛利", "EBITDA", "现金流", "负债", "市值", "产能", "订单", "用户", "员工", "股权")


def chinese_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def title_length(title: str) -> int:
    return len(re.sub(r"\s+", "", title))


def article_text(article: dict[str, object]) -> str:
    parts = [str(article.get("title") or ""), str(article.get("intro") or "")]
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if isinstance(sec, dict):
                parts.append(str(sec.get("heading") or ""))
                paragraphs = sec.get("paragraphs") or []
                if isinstance(paragraphs, list):
                    parts.extend(str(p) for p in paragraphs)
    return "\n".join(parts)


def validate_article(article: dict[str, object]) -> list[str]:
    issues: list[str] = []
    text = article_text(article)
    length = chinese_length(text)
    title = str(article.get("title") or "")
    if length < 3300:
        issues.append(f"正文偏短，当前约 {length} 字，需要扩展到 3500-4000 字。")
    if length > 4300:
        issues.append(f"正文偏长，当前约 {length} 字，需要压缩到 3500-4000 字。")
    if title_length(title) > 34:
        issues.append(f"标题过长，当前约 {title_length(title)} 字，最好压缩到30字以内，且仍保留主副标题形式。")
    if "：" not in title and ":" not in title:
        issues.append("标题需要采用主副标题形式，中间使用冒号。")
    intro = str(article.get("intro") or "")[:100]
    if any(pattern in intro for pattern in BANNED_INTRO_PATTERNS):
        issues.append("引言出现'本文/本报告'等模板化表达，需要改为直接讲案例启示。")
    sections = article.get("sections") or []
    if not isinstance(sections, list) or len(sections) < 4:
        issues.append("章节不足，需要至少4个一级章节，并可根据案例写成4-7章。")
    else:
        if len(sections) > 7:
            issues.append("章节过多，需要控制在4-7章。")
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            heading = str(sec.get("heading") or "")
            if any(generic in heading for generic in GENERIC_HEADINGS):
                issues.append(f"章节标题过于机械：{heading}，需要改为概括该章结论的标题。")
        last_heading = str(sections[-1].get("heading") or "") if isinstance(sections[-1], dict) else ""
        if not last_heading.startswith("结语："):
            issues.append("最后一章标题必须固定为'结语：副标题'的形式。")
    digit_count = len(re.findall(r"\d", text))
    if digit_count < 30:
        issues.append("数据密度不足，需要补充交易对价、估值、比例、营收、净利润、时间节点等可核验数据。")
    if not any(pattern in text for pattern in TIME_PATTERNS):
        issues.append("缺少并购时间线，需要写明公告/签约/完成交割或过户时间。")
    if not any(pattern in text for pattern in CONSIDERATION_PATTERNS):
        issues.append("缺少交易对价或估值金额，需要写明交易金额、估值、作价或支付方式。")
    if sum(1 for pattern in FINANCIAL_PATTERNS if pattern in text) < 3:
        issues.append("财务和经营数据不足，需要加入买方或标的的收入、净利润、负债、现金流、产能、订单、员工、用户或股权比例等。")
    return issues


def trim_article(article: dict[str, object], max_chars: int = 4200) -> dict[str, object]:
    if chinese_length(article_text(article)) <= max_chars:
        return article
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                sec["paragraphs"] = [str(p)[:650] for p in paragraphs[:4]]
    return article


def build_prompt(brief: CaseBrief, research_rows: list[dict[str, str]], *, revision_issues: list[str] | None = None, previous_article: dict[str, object] | None = None) -> list[dict[str, str]]:
    revise_text = ""
    if revision_issues and previous_article:
        revise_text = (
            "\n需要基于上一版重写并修复以下问题："
            + json.dumps(revision_issues, ensure_ascii=False)
            + "\n上一版："
            + json.dumps(previous_article, ensure_ascii=False)
        )
    return [
        {
            "role": "system",
            "content": (
                "你是资深并购案例研究作者，读者是上市公司董事长、CEO和产业集团负责人。"
                "只输出 JSON，不输出 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请写一篇并购案例分析报告，要求像已上传案例中的研究文章，而不是短摘要。"
                "文章要服务于企业家决策：并购前如何判断标的、如何设计交易结构、哪些假设必须验证、投后如何承接。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n地区：{brief.region}"
                f"\n线索标题：{brief.source_title}"
                f"\n线索链接：{brief.source_url}"
                f"\n选题理由：{brief.why}"
                f"\n是否经典案例：{brief.is_classic}"
                f"\n分类口径：{CATEGORY_GUIDE}"
                f"\n选题规则：{TOPIC_SELECTION_RULES}"
                f"\n写作规则：{STYLE_RULES}"
                f"\n参考写法：{REFERENCE_STYLE}"
                "\n可使用的公开资料线索如下。只能把它们作为线索和事实来源；若资料不足，不要编造精确数字。"
                f"\n资料线索：{json.dumps(research_rows, ensure_ascii=False)}"
                "\n输出 JSON 格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、概括性标题\",\"paragraphs\":[...]},...],\"sources\":[...]}。"
                "\n标题总字数最好不超过30个中文字符，但必须保留冒号形式。"
                "\n章节数量不要固定为5章，可按案例写成4-7章；所有章节标题必须根据该章内容概括，不能使用'交易过程/交易逻辑/可复用经验/结论'。"
                "\n最后一章标题必须写成'结语：副标题'，副标题概括最重要的启示。"
                "\n必须在正文前半部分写清楚：公告或签约时间、完成交割/完成过户时间、交易对价或估值、支付方式、股权比例。"
                "\n必须写入一组财务和经营数据：买方或标的的营收、净利润、负债、现金流、订单、产能、员工、用户、专利、客户等，按资料实际选择。"
                "\n建议每章 2-4 段，每段 180-320 字，总字数 3500-4000 中文字。"
                "\n引言第一句不要出现'本文'或'本报告'，要直接讲此案例对CEO的借鉴价值。"
                + revise_text
            ),
        },
    ]


def normalize_article(payload: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    return {
        "case_name": str(payload.get("case_name") or brief.case_name),
        "category": str(payload.get("category") or brief.category),
        "title": str(payload.get("title") or brief.case_name),
        "intro": str(payload.get("intro") or ""),
        "sections": payload.get("sections") or [],
        "sources": payload.get("sources") or ([brief.source_url] if brief.source_url else []),
    }


def generate_article(brief: CaseBrief) -> dict[str, object]:
    research_items = collect_research_context(brief)
    research_rows = [item.to_dict() for item in research_items]
    LOGGER.info("Collected %s research items for report: %s", len(research_rows), brief.case_name)

    payload = chat_json(build_prompt(brief, research_rows), timeout=240)
    article = normalize_article(payload, brief)
    issues = validate_article(article)
    for round_idx in range(2):
        if not issues:
            break
        LOGGER.info("Revising report %s round %s due to issues: %s", brief.case_name, round_idx + 1, issues)
        payload = chat_json(build_prompt(brief, research_rows, revision_issues=issues, previous_article=article), timeout=240)
        article = normalize_article(payload, brief)
        issues = validate_article(article)
    if issues:
        LOGGER.warning("Report still has validation issues after revisions: %s %s", brief.case_name, issues)
    return trim_article(article)
