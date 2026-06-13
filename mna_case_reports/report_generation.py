"""Article generation for M&A case analysis reports."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .article_quality import assess_quality
from .article_rules_extra import (
    MAX_CHARS,
    MIN_CHARS,
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
from .narrative_generation import NarrativePlan, build_narrative_plan
from .research import collect_research_context

LOGGER = logging.getLogger(__name__)


def external_evidence_count(research_rows: list[dict[str, str]]) -> int:
    return sum(1 for row in research_rows if (row.get("evidence_type") or "") != "structured_seed")


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
        instruction = "请在不新增事实的前提下修复以下问题："
        if quality_rewrite:
            instruction = "请保留事实、金额、日期和交易主体，重写文章主线、标题和段落推进，修复以下质量问题："
        revise_text = "\n" + instruction + json.dumps(revision_issues, ensure_ascii=False) + "\n上一版：" + json.dumps(previous_article, ensure_ascii=False)
    if expansion_only and previous_article:
        revise_text = (
            "\n下面是上一版文章。请基于事实包和资料线索重新生成一版3,500至4,000字文章，不要拼接补丁段落，不要套模板。"
            "重写重点是产业位置、交易结构、财务影响、交割承接和同类并购方法；不改变交易主体和已核验事实。"
            + "\n上一版：" + json.dumps(previous_article, ensure_ascii=False)
        )

    system_prompt = (
        "你是严谨的并购案例研究作者。只能基于给定材料写作，不联网，不补充资料外事实。"
        "只输出JSON。文风专业、克制、有判断力，中文自然流畅。"
    )
    user_prompt = (
        "请写一篇并购案例研究报告，不要写新闻摘要，也不要套固定模板。"
        "必须根据材料自行生成4至7个章节，章节数量、顺序和长短由材料的信息量决定，结构服务于内容，不追求格式统一。"
        "标题要采用主标题：副标题形式，包含交易双方名称或简称，并点出本案核心交易逻辑或分析重点；不能只写交易复盘、案例分析、交易启示，也不能标题党。"
        "公司首次出现必须在完整名称之后标注简称和股票代码（如上市），例如腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME）；上海喜马拉雅科技有限公司（下称“喜马拉雅”）。资料没有披露完整名称或股票代码时不要编造。"
        "全文必须用全角中文标点和中文引号“”，不要用半角引号；中文和英文或数字之间不要加空格。"
        "金额、数量等数字必须使用千分位逗号。事实、数字、信息必须基于给定资料线索和事实包，不能编造资料外事实。"
        "全文长度控制在3,500至4,000个中文字符。"
        "文章必须覆盖交易时间、交易金额或估值、支付方式或股权比例、交易双方介绍、买方购买理由、卖方接受安排原因。"
        "深度必须覆盖至少三个层面，且优先写足产业判断、交易结构分析和并购方法论意义；可结合财务影响、交割承接、治理边界继续展开。"
        "每一部分都要贴合本案例的实际情况，至少使用本案的主体、金额/比例、业务资产、客户/产能/技术/资源、治理或交割安排之一支撑判断。"
        "结语必须回到本案的交易双方、对价或股权结构、产业位置、业务承接和披露事实，说明本案例对同类交易的方法论意义；严禁写“并购不是终点，整合才是开始”等空泛句。"
        f"\n案例：{brief.case_name}"
        f"\n分类：{brief.category}"
        f"\n地区：{brief.region}"
        f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
        f"\n叙事计划：{json.dumps(narrative_plan.to_dict(), ensure_ascii=False)}"
        f"\n分类口径：{CATEGORY_GUIDE}"
        f"\n选题规则：{TOPIC_SELECTION_RULES}"
        f"\n写作规则：{STYLE_RULES}"
        f"\n参考写法：{REFERENCE_STYLE}"
        f"\n资料线索：{json.dumps(research_rows[:36], ensure_ascii=False)}"
        "\n输出JSON格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、章节标题\",\"paragraphs\":[...]},...],\"sources\":[...]}。"
        + revise_text
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def normalize_article(payload: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    article: dict[str, Any] = {
        "case_name": str(payload.get("case_name") or brief.case_name),
        "category": str(payload.get("category") or brief.category),
        "title": str(payload.get("title") or ""),
        "intro": str(payload.get("intro") or ""),
        "sections": sections,
        "sources": payload.get("sources") or ([brief.source_url] if brief.source_url else []),
    }
    return postprocess_article(article, brief)


def expand_to_target_length(article: dict[str, object], brief: CaseBrief, research_rows: list[dict[str, str]], fact_pack: FactPack, narrative_plan: NarrativePlan) -> dict[str, object]:
    article = postprocess_article(article, brief)
    for attempt in range(3):
        length = chinese_length(article_text(article))
        if MIN_CHARS <= length <= MAX_CHARS:
            return article
        if length < MIN_CHARS:
            LOGGER.info("Regenerating report %s for hard length check, attempt %s, current=%s", brief.case_name, attempt + 1, length)
            payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan, previous_article=article, expansion_only=True), timeout=240)
            article = normalize_article(payload, brief)
        elif length > MAX_CHARS:
            article = trim_article(article)
            if chinese_length(article_text(article)) <= MAX_CHARS:
                return article
    return postprocess_article(article, brief)


def generate_article(brief: CaseBrief) -> dict[str, object]:
    research_items = collect_research_context(brief)
    research_rows = [item.to_dict() for item in research_items]
    external_count = external_evidence_count(research_rows)
    LOGGER.info("Collected %s research items for report: %s external=%s", len(research_rows), brief.case_name, external_count)
    if external_count < int(os.getenv("REPORT_MIN_EXTERNAL_RESEARCH_ITEMS", "1")):
        raise RuntimeError(f"Insufficient external research evidence for {brief.case_name}; only structured seed was found.")
    try:
        return generate_article_with_rows(brief, research_rows)
    except Exception as exc:  # noqa: BLE001
        if external_count < 2 and os.getenv("REPORT_EXPAND_WEAK_RESEARCH", "0") != "1":
            raise
        LOGGER.warning("Initial report generation failed for %s; expanding Google/Bing/page research and regenerating: %s", brief.case_name, exc)
        expanded_items = collect_research_context(brief, limit=56, expanded=True)
        expanded_rows = [item.to_dict() for item in expanded_items]
        expanded_external_count = external_evidence_count(expanded_rows)
        LOGGER.info(
            "Expanded research context to %s items for report: %s external=%s",
            len(expanded_rows),
            brief.case_name,
            expanded_external_count,
        )
        if len(expanded_rows) <= len(research_rows) or expanded_external_count < 2:
            raise
        return generate_article_with_rows(brief, expanded_rows)


def generate_article_with_rows(brief: CaseBrief, research_rows: list[dict[str, str]]) -> dict[str, object]:
    fact_pack = build_fact_pack(brief, research_rows)
    if fact_pack.validation_issues:
        raise RuntimeError(f"Fact pack validation failed for {brief.case_name}: {fact_pack.validation_issues}")
    narrative_plan = build_narrative_plan(brief, fact_pack, research_rows)
    LOGGER.info("Generated narrative plan for %s: %s", brief.case_name, narrative_plan.to_dict())

    payload = chat_json(build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan), timeout=240)
    article = normalize_article(payload, brief)
    issues = validate_article(article, brief)
    quality_issues = assess_quality(article)
    max_revisions = int(os.getenv("REPORT_MAX_REVISIONS", "3"))
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
        article = normalize_article(payload, brief)
        issues = validate_article(article, brief)
        quality_issues = assess_quality(article)

    article = expand_to_target_length(article, brief, research_rows, fact_pack, narrative_plan)
    article = postprocess_article(article, brief)
    final_issues = validate_article(article, brief)
    final_quality_issues = assess_quality(article)
    if final_issues or final_quality_issues:
        LOGGER.warning("Report still has validation/quality issues after narrative pipeline: %s hard=%s quality=%s", brief.case_name, final_issues, final_quality_issues)
        raise RuntimeError(f"Report quality validation failed for {brief.case_name}: hard={final_issues} quality={final_quality_issues}")
    else:
        LOGGER.info("Report passed hard validation and quality checks: %s length=%s", brief.case_name, chinese_length(article_text(article)))
    return article
