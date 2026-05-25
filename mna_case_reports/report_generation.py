"""Article generation for M&A case analysis reports.

Pipeline:
1. collect research rows
2. build a fact pack
3. build a narrative plan / writing focus
4. let the model create material-driven 4-7 chapters
5. validate hard requirements and quality requirements
6. targeted rewrite, length expansion, final postprocess
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .article_quality import assess_quality
from .article_rules_extra import (
    MAX_CHARS,
    MIN_CHARS,
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
from .narrative_generation import NarrativePlan, build_narrative_plan, fallback_sections_from_plan
from .research import collect_research_context

LOGGER = logging.getLogger(__name__)


def build_prompt(
    brief: CaseBrief,
    research_rows: list[dict[str, str]],
    *,
    fact_pack: FactPack,
    narrative_plan: NarrativePlan,
    revision_issues: list[str] | None = None,
    previous_article: dict[str, object] | None = None,
    expansion_only: bool = False,
    quality_rewrite: bool = False,
) -> list[dict[str, str]]:
    revise_text = ""
    if revision_issues and previous_article:
        instruction = "需要基于上一版定向修复以下问题，不要引入资料外事实："
        if quality_rewrite:
            instruction = (
                "需要基于上一版做质量重写：保留已核验事实、金额、日期和交易主体，但围绕叙事重心重新组织章节和段落。"
                "减少模板感，增强产业判断、交易结构分析、财务影响和并购方法论意义。"
                "不要机械补段落，不要用'从公开资料看/从收购方角度看/数据层面'反复开头。需要让段落之间有事实推进和分析递进。需修复以下问题："
            )
        revise_text = (
            "\n" + instruction
            + json.dumps(revision_issues, ensure_ascii=False)
            + "\n上一版："
            + json.dumps(previous_article, ensure_ascii=False)
        )
    if expansion_only and previous_article:
        revise_text = (
            "\n下面是上一版文章。请只做事实性扩写和轻微润色，不改变已核验事实和交易主体；可根据叙事重心调整章节内部段落。优先扩展到3600-3900个中文字符。"
            "如为保证逻辑完整可略高于4000字，但不要明显冗长。正文不得出现读者对象，不要写'上市公司CEO/董事长/读者'等提示语。"
            "章节标题要客观中性，不使用口号化、负面化或广告化表述。章节标题不要出现'交易动机、交易背景、交易结构设计、并购战略考量、标的筛选、并购后整合、价值释放'这些过程词。"
            "扩写重点不是凑字，而是补充事实之间的逻辑：产业位置、交易结构、财务影响、交割承接和方法论意义。"
            + "\n上一版："
            + json.dumps(previous_article, ensure_ascii=False)
        )
    return [
        {
            "role": "system",
            "content": (
                "你是并购案例研究作者。读者对象只用于把握专业深度，不得在正文出现。"
                "必须只基于用户给定资料、事实包和叙事计划写作，不联网、不编造、不推测。只输出JSON，不输出Markdown。"
                "文风在学术严谨性和可读性之间保持平衡：专业、克制、有判断力，使用流畅、自然、专业的中文。"
                "不要写特别负面的描述，不写广告化表达，不写敏感宏观表述。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请基于事实包和叙事计划写一篇并购案例分析报告。报告不是新闻摘要，也不是固定模板文章。"
                "不要强行使用统一大纲；你需要围绕叙事重心，自行生成4-7个章节，章节数量和长短应根据材料信息量决定。"
                "文章要像一篇有主线的案例研究：引言提出本案例最值得复盘的问题；正文围绕事实推进分析；结语提炼同类交易可参考的方法。"
                "除拆解案例本身外，还要加入有依据的产业判断、交易结构分析和并购方法论意义，但所有判断都要回到公开资料和事实包。"
                "避免每章都用同样段落数量，避免每段都用同样句式开头，避免把'事实—分析—启示'机械重复。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n地区：{brief.region}"
                f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
                f"\n叙事计划：{json.dumps(narrative_plan.to_dict(), ensure_ascii=False)}"
                f"\n建议深入分析角度：{json.dumps(fact_pack.analysis_angles, ensure_ascii=False)}"
                f"\n分类口径：{CATEGORY_GUIDE}"
                f"\n选题规则：{TOPIC_SELECTION_RULES}"
                f"\n写作规则：{STYLE_RULES}"
                f"\n参考写法：{REFERENCE_STYLE}"
                "\n可使用的公开资料线索如下。只能作为事实来源；资料没有出现的具体数据不得写成确定事实。"
                f"\n资料线索：{json.dumps(research_rows[:36], ensure_ascii=False)}"
                "\n输出JSON格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、客观概括该章事实和分析重点的标题\",\"paragraphs\":[...]},...],\"sources\":[...]}。"
                "\n【硬性要求】正文必须自行生成4-7个章节，不要套用固定五章结构；最后一章必须带实际顺序编号并写成'N、结语：副标题'。"
                "\n【硬性要求】正文不得出现'上市公司CEO、上市公司董事长、读者、面向谁'等对象提示语；这些只用于写作定位。"
                "\n【硬性要求】标题必须准确概括文章主旨，突出核心交易逻辑或分析重点，兼顾专业性与吸引力；避免过于平淡、空泛或标题党式表达；标题必须包含并购方和标的方名称或简称。"
                "\n【硬性要求】严格区分三类信息：1）官方事实，来自公司公告、监管文件、交易所文件、反垄断审查公告等，可作为确定性事实使用；2）媒体报道或市场传闻，必须明确写成'据媒体报道'或'市场认为'，不得写成确定事实；3）合理推断，必须说明推理依据，不得包装成事实。"
                "\n【硬性要求】全文必须客观陈述事实，不使用'假设、推测、可能是、或许、有望、如果、若能、若未'等推测表达；事实、数字、信息必须基于公开权威资料，严禁编造。"
                "\n【硬性要求】正文必须涵盖：1）并购具体日期或时间线；2）交易金额/估值/支付方式/股权比例；3）并购方基本介绍；4）标的方基本介绍；5）并购方为什么买；6）标的方/出售方为什么卖或为什么接受整合。"
                "\n【硬性要求】公司名称首次出现时，必须在完整公司名称之后标注简称和股票代码（如公开资料披露）。例如：腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME）；上海喜马拉雅科技有限公司（下称“喜马拉雅”）。不得把括号插入公司全称中间。"
                "\n【硬性要求】全文使用一致的全角中文标点符号；引号必须使用全角中文引号“”；不要使用半角双引号或单引号。不要在中文字符和英文单词或数字之间添加空格；金额、数量等类型的长数字应添加千字符分隔符。"
                "\n【硬性要求】章节标题客观、中性、克制，用一句话概括事实和分析重点；不要口号化，不要负面化，不要广告化。不要把'交易动机、交易背景、交易结构设计、并购战略考量、标的筛选、并购后整合、价值释放'等分析提纲直接写进标题。"
                "\n【硬性要求】全文字数控制在3500-4000字；如果为了保证逻辑完整，可以适当超过，但不要为了凑字重复。"
                "\n【深度要求】正文至少覆盖三个层面：产业判断、交易结构、财务影响、交割承接、并购方法论意义。不能只复述新闻或公告。"
                "\n【深度要求】结语/启示部分必须紧扣本案例，回到交易双方、对价结构、业务承接和披露事实，不能写'并购不是终点，整合才是开始'等泛泛表达。"
                "\n【深度要求】各部分分析不能笼统、浮于表面。每一部分都要紧扣本案例实际情况，形成有依据、有层次、有判断的拆解。"
                "\n【行文要求】每篇可以有不同侧重，可围绕产业位置、资产质量、价格与支付方式、条款安排、交割承接、业务协同、财务影响等角度组织，不需要每篇固定结构。"
                "\n引言第一句不要出现'本文'或'本报告'，直接讲这个案例可复盘的关键事实和关注点。"
                + revise_text
            ),
        },
    ]


def normalize_article(payload: dict[str, object], brief: CaseBrief, narrative_plan: NarrativePlan) -> dict[str, object]:
    sections = payload.get("sections") or fallback_sections_from_plan(narrative_plan)
    article: dict[str, Any] = {
        "case_name": str(payload.get("case_name") or brief.case_name),
        "category": str(payload.get("category") or brief.category),
        "title": str(payload.get("title") or brief.case_name),
        "intro": str(payload.get("intro") or ""),
        "sections": sections,
        "sources": payload.get("sources") or ([brief.source_url] if brief.source_url else []),
    }
    return postprocess_article(article, brief)


def expand_to_target_length(article: dict[str, object], brief: CaseBrief, research_rows: list[dict[str, str]], fact_pack: FactPack, narrative_plan: NarrativePlan) -> dict[str, object]:
    article = postprocess_article(article, brief)
    for attempt in range(2):
        length = chinese_length(article_text(article))
        if MIN_CHARS <= length <= MAX_CHARS:
            return article
        if length < MIN_CHARS:
            LOGGER.info("Expanding report %s for hard length check, attempt %s, current=%s", brief.case_name, attempt + 1, length)
            payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan, previous_article=article, expansion_only=True), timeout=240)
            article = normalize_article(payload, brief, narrative_plan)
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
    narrative_plan = build_narrative_plan(brief, fact_pack, research_rows)
    LOGGER.info("Generated narrative plan for %s: %s", brief.case_name, narrative_plan.to_dict())

    payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan), timeout=240)
    article = normalize_article(payload, brief, narrative_plan)
    issues = validate_article(article, brief)
    quality_issues = assess_quality(article)
    max_revisions = int(os.getenv("REPORT_MAX_REVISIONS", "2"))
    for round_idx in range(max_revisions):
        combined_issues = issues + quality_issues
        if not combined_issues:
            break
        non_length_issues = [x for x in combined_issues if "成品字数" not in x]
        if not non_length_issues and chinese_length(article_text(article)) < MIN_CHARS:
            break
        is_quality_rewrite = bool(quality_issues) and not issues
        LOGGER.info("Revising report %s round %s due to issues: %s", brief.case_name, round_idx + 1, non_length_issues or combined_issues)
        payload = chat_json(
            build_prompt(
                brief,
                research_rows,
                fact_pack=fact_pack,
                narrative_plan=narrative_plan,
                revision_issues=non_length_issues or combined_issues,
                previous_article=article,
                quality_rewrite=is_quality_rewrite,
            ),
            timeout=240,
        )
        article = normalize_article(payload, brief, narrative_plan)
        issues = validate_article(article, brief)
        quality_issues = assess_quality(article)

    article = expand_to_target_length(article, brief, research_rows, fact_pack, narrative_plan)
    article = postprocess_article(article, brief)
    final_issues = validate_article(article, brief)
    final_quality_issues = assess_quality(article)
    if final_issues or final_quality_issues:
        LOGGER.warning("Report still has validation/quality issues after narrative pipeline: %s hard=%s quality=%s", brief.case_name, final_issues, final_quality_issues)
    else:
        LOGGER.info("Report passed hard validation and quality checks: %s length=%s", brief.case_name, chinese_length(article_text(article)))
    return article
