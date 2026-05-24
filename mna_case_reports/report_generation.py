"""Article generation for M&A case analysis reports.

Pipeline:
1. collect research rows
2. build a fact pack
3. generate a neutral outline
4. generate article body from fact pack + outline
5. validate, targeted rewrite, length expansion
"""

from __future__ import annotations

import json
import logging
import os

from .article_rules import (
    MAX_CHARS,
    MIN_CHARS,
    TARGET_MIN_CHARS,
    append_until_min_length,
    article_text,
    chinese_length,
    postprocess_article,
    trim_article,
    validate_article,
)
from .case_selection import CaseBrief
from .config import CATEGORY_GUIDE, REFERENCE_STYLE, STYLE_RULES, TOPIC_SELECTION_RULES
from .deepseek_client import chat_json
from .fact_pack import FactPack, build_fact_pack
from .outline_generation import generate_outline, outline_to_sections
from .research import collect_research_context

LOGGER = logging.getLogger(__name__)


def build_prompt(
    brief: CaseBrief,
    research_rows: list[dict[str, str]],
    *,
    fact_pack: FactPack,
    outline: list[str],
    revision_issues: list[str] | None = None,
    previous_article: dict[str, object] | None = None,
    expansion_only: bool = False,
) -> list[dict[str, str]]:
    revise_text = ""
    if revision_issues and previous_article:
        revise_text = (
            "\n需要基于上一版定向修复以下问题，不要引入资料外事实："
            + json.dumps(revision_issues, ensure_ascii=False)
            + "\n上一版："
            + json.dumps(previous_article, ensure_ascii=False)
        )
    if expansion_only and previous_article:
        revise_text = (
            "\n下面是上一版文章。请只做事实性扩写和轻微润色，不改变事实、标题、章节顺序和交易主体；必须扩展到3600-3900个中文字符。"
            "正文不得出现读者对象，不要写'上市公司CEO/董事长/读者'等提示语。章节标题要客观中性，不使用口号化、负面化或广告化表述。"
            "章节标题不要出现'交易动机、交易背景、交易结构设计、并购战略考量、标的筛选、并购后整合、价值释放'这些过程词。"
            + "\n上一版："
            + json.dumps(previous_article, ensure_ascii=False)
        )
    return [
        {
            "role": "system",
            "content": (
                "你是并购案例研究作者。读者对象只用于把握专业深度，不得在正文出现。"
                "必须只基于用户给定资料和事实包写作，不联网、不编造、不推测。只输出JSON，不输出Markdown。"
                "文风客观、中性、克制，不做主观判断，不写负面化标题，不写广告化表达，不写敏感宏观表述。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请基于事实包和指定大纲写一篇并购案例分析报告。报告不是新闻摘要，要从公开事实出发，复盘交易时间、金额、双方业务、购买理由、出售或接受安排原因、条款安排和交割后承接。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n地区：{brief.region}"
                f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
                f"\n指定大纲：{json.dumps(outline, ensure_ascii=False)}"
                f"\n分类口径：{CATEGORY_GUIDE}"
                f"\n选题规则：{TOPIC_SELECTION_RULES}"
                f"\n写作规则：{STYLE_RULES}"
                f"\n参考写法：{REFERENCE_STYLE}"
                "\n可使用的公开资料线索如下。只能作为事实来源；资料没有出现的具体数据不得写成确定事实。"
                f"\n资料线索：{json.dumps(research_rows[:36], ensure_ascii=False)}"
                "\n输出JSON格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、客观概括该章事实和关注点的标题\",\"paragraphs\":[...]},...],\"sources\":[...]}。"
                "\n【硬性要求】必须使用指定大纲的章节标题和顺序；最后一章保持指定大纲中的实际编号和'结语：副标题'。"
                "\n【硬性要求】正文不得出现'上市公司CEO、上市公司董事长、读者、面向谁'等对象提示语；这些只用于写作定位。"
                "\n【硬性要求】标题必须包含并购方和标的方名称或简称，并采用主副标题形式，最好不超过30个中文字符。"
                "\n【硬性要求】全文必须客观陈述事实，不使用'假设、推测、可能是、或许、有望、如果、若能、若未'等推测表达。"
                "\n【硬性要求】正文必须涵盖：1）并购具体日期或时间线；2）交易金额/估值/支付方式/股权比例；3）并购方基本介绍；4）标的方基本介绍；5）并购方为什么买；6）标的方/出售方为什么卖或为什么接受整合。"
                "\n【硬性要求】章节标题客观、中性、克制，用一句话概括事实和关注点；不要口号化，不要负面化，不要广告化。不要把'交易动机、交易背景、交易结构设计、并购战略考量、标的筛选、并购后整合、价值释放'等分析提纲直接写进标题。"
                "\n【硬性要求】成品字数必须大于3500个中文字符、小于4000个中文字符；优先写到3600-3900字。"
                "\n【行文要求】每篇可以有不同侧重，可围绕产业位置、资产质量、价格与支付方式、条款安排、交割后承接、业务协同、财务影响等角度组织，不需要每篇固定结构。"
                "\n引言第一句不要出现'本文'或'本报告'，直接讲这个案例可复盘的关键事实和关注点。"
                + revise_text
            ),
        },
    ]


def normalize_article(payload: dict[str, object], brief: CaseBrief, outline: list[str]) -> dict[str, object]:
    article = {
        "case_name": str(payload.get("case_name") or brief.case_name),
        "category": str(payload.get("category") or brief.category),
        "title": str(payload.get("title") or brief.case_name),
        "intro": str(payload.get("intro") or ""),
        "sections": payload.get("sections") or outline_to_sections(outline),
        "sources": payload.get("sources") or ([brief.source_url] if brief.source_url else []),
    }
    # Preserve model paragraphs but force validated outline headings/order.
    original_sections = article.get("sections") or []
    forced_sections = outline_to_sections(outline)
    if isinstance(original_sections, list):
        for idx, section in enumerate(forced_sections):
            if idx < len(original_sections) and isinstance(original_sections[idx], dict):
                paragraphs = original_sections[idx].get("paragraphs") or []
                section["paragraphs"] = paragraphs if isinstance(paragraphs, list) else [str(paragraphs)]
    article["sections"] = forced_sections
    return postprocess_article(article, brief)


def expand_to_target_length(article: dict[str, object], brief: CaseBrief, research_rows: list[dict[str, str]], fact_pack: FactPack, outline: list[str]) -> dict[str, object]:
    article = postprocess_article(article, brief)
    for attempt in range(2):
        length = chinese_length(article_text(article))
        if MIN_CHARS <= length <= MAX_CHARS:
            return article
        if length < MIN_CHARS:
            LOGGER.info("Expanding report %s for hard length check, attempt %s, current=%s", brief.case_name, attempt + 1, length)
            payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, outline=outline, previous_article=article, expansion_only=True), timeout=240)
            article = normalize_article(payload, brief, outline)
        elif length > MAX_CHARS:
            article = trim_article(article)
            if chinese_length(article_text(article)) <= MAX_CHARS:
                return article
    article = append_until_min_length(article, brief, research_rows)
    if chinese_length(article_text(article)) > MAX_CHARS:
        article = trim_article(article)
    return postprocess_article(article, brief)


def generate_article(brief: CaseBrief) -> dict[str, object]:
    research_items = collect_research_context(brief)
    research_rows = [item.to_dict() for item in research_items]
    LOGGER.info("Collected %s research items for report: %s", len(research_rows), brief.case_name)

    fact_pack = build_fact_pack(brief, research_rows)
    if fact_pack.validation_issues:
        LOGGER.warning("Fact pack has validation issues for %s: %s", brief.case_name, fact_pack.validation_issues)
    outline = generate_outline(brief, fact_pack)
    LOGGER.info("Generated outline for %s: %s", brief.case_name, outline)

    payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, outline=outline), timeout=240)
    article = normalize_article(payload, brief, outline)
    issues = validate_article(article, brief)
    max_revisions = int(os.getenv("REPORT_MAX_REVISIONS", "2"))
    for round_idx in range(max_revisions):
        if not issues:
            break
        non_length_issues = [x for x in issues if "成品字数" not in x]
        if not non_length_issues and chinese_length(article_text(article)) < MIN_CHARS:
            break
        LOGGER.info("Revising report %s round %s due to issues: %s", brief.case_name, round_idx + 1, non_length_issues or issues)
        payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, outline=outline, revision_issues=non_length_issues or issues, previous_article=article), timeout=240)
        article = normalize_article(payload, brief, outline)
        issues = validate_article(article, brief)

    article = expand_to_target_length(article, brief, research_rows, fact_pack, outline)
    article = postprocess_article(article, brief)
    final_issues = validate_article(article, brief)
    if final_issues:
        LOGGER.warning("Report still has validation issues after staged pipeline: %s %s", brief.case_name, final_issues)
    else:
        LOGGER.info("Report passed hard validation: %s length=%s", brief.case_name, chinese_length(article_text(article)))
    return article
