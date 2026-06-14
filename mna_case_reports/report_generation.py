"""Article generation for M&A case analysis reports."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .article_quality import assess_quality
from .article_rules_extra import (
    MAX_CHARS,
    MIN_CHARS,
    TARGET_MAX_CHARS,
    TARGET_MIN_CHARS,
    article_text,
    chinese_length,
    postprocess_article,
    trim_article,
    validate_article,
)
from .case_selection import CaseBrief
from .config import CATEGORY_GUIDE, REFERENCE_STYLE, STYLE_RULES, TOPIC_SELECTION_RULES
from .deepseek_client import DeepSeekError, chat_json
from .fact_pack import FactPack, build_fact_pack
from .narrative_generation import NarrativePlan, build_narrative_plan
from .research import collect_research_context

LOGGER = logging.getLogger(__name__)
PROGRESS_QUEUE: Any = None


def set_progress_queue(queue: Any | None) -> None:
    global PROGRESS_QUEUE
    PROGRESS_QUEUE = queue


def action_notice(message: str) -> None:
    safe = str(message).replace("\n", " ")[:1000]
    if PROGRESS_QUEUE is not None:
        print(f"WORKER_EVENT {safe}", flush=True)
        PROGRESS_QUEUE.put({"type": "event", "message": safe})
        return
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::notice::{safe}", flush=True)
    else:
        LOGGER.info(safe)


def model_timeout(default: int = 180) -> int:
    return int(os.getenv("REPORT_MODEL_TIMEOUT_SECONDS", str(default)))


def article_model_timeout(default: int = 240) -> int:
    return int(os.getenv("REPORT_ARTICLE_MODEL_TIMEOUT_SECONDS") or os.getenv("REPORT_MODEL_TIMEOUT_SECONDS", str(default)))


def _article_provider() -> str:
    return (os.getenv("REPORT_ARTICLE_MODEL_PROVIDER") or "").strip().lower()


def _article_model_name(provider: str | None = None) -> str:
    provider_name = (provider or _article_provider()).strip().lower()
    if provider_name.startswith("deepseek"):
        return (os.getenv("REPORT_ARTICLE_DEEPSEEK_MODEL") or "deepseek-v4-pro").strip()
    return (os.getenv("REPORT_ARTICLE_MODEL") or "gpt-5.5").strip()


def _use_article_model() -> bool:
    provider = _article_provider()
    if provider in ("", "default", "none", "off", "0"):
        return False
    api_key_env = _article_api_key_env(provider)
    if not os.getenv(api_key_env):
        action_notice(f"report_stage stage=article_model_unavailable provider={provider} missing={api_key_env} fallback=deepseek")
        return False
    return True


def _article_api_key_env(provider: str) -> str:
    if provider.startswith("deepseek"):
        return "DEEPSEEK_API_KEY"
    return "REPORT_ARTICLE_API_KEY"


def _article_base_url_env(provider: str) -> str:
    if provider.startswith("deepseek"):
        return "DEEPSEEK_BASE_URL"
    return "REPORT_ARTICLE_BASE_URL"


def _article_default_base_url(provider: str) -> str:
    if provider.startswith("deepseek"):
        return "https://api.deepseek.com"
    return "https://rkapi.com/v1"


def _article_reasoning_env(provider: str) -> str | None:
    if provider.startswith("deepseek"):
        return None
    return "REPORT_ARTICLE_REASONING_EFFORT"


def _is_transient_model_error(exc: Exception) -> bool:
    text = str(exc)
    return any(token in text for token in (" 408:", " 429:", " 500:", " 502:", " 503:", " 504:", "Gateway time-out", "timeout"))


def _article_chat_json_provider(messages: list[dict[str, str]], *, provider: str, timeout: int | None = None) -> dict[str, Any]:
    model_name = _article_model_name(provider)
    reasoning = os.getenv("REPORT_ARTICLE_REASONING_EFFORT", "").strip()
    reasoning_hint = f" reasoning={reasoning}" if reasoning and _article_reasoning_env(provider) else ""
    action_notice(f"report_stage stage=article_model_call provider={provider} model={model_name}{reasoning_hint}")
    return chat_json(
        messages,
        model=model_name,
        timeout=timeout or article_model_timeout(240),
        api_key_env=_article_api_key_env(provider),
        base_url_env=_article_base_url_env(provider),
        model_env="REPORT_ARTICLE_MODEL",
        default_base_url=_article_default_base_url(provider),
        default_model=model_name,
        provider_label=f"Article LLM ({provider})",
        reasoning_effort_env=_article_reasoning_env(provider),
        max_tokens_env="REPORT_ARTICLE_MAX_TOKENS",
        max_completion_tokens_env="REPORT_ARTICLE_MAX_COMPLETION_TOKENS",
    )


def article_chat_json(messages: list[dict[str, str]], *, timeout: int | None = None) -> dict[str, Any]:
    """Route expensive long-form article calls to the configured strong model."""
    if not _use_article_model():
        return chat_json(messages, timeout=timeout or model_timeout(180))
    provider = _article_provider()
    retries = max(0, int(os.getenv("REPORT_ARTICLE_MODEL_RETRIES", "1")))
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _article_chat_json_provider(messages, provider=provider, timeout=timeout)
        except DeepSeekError as exc:
            last_exc = exc
            if attempt >= retries or not _is_transient_model_error(exc):
                break
            delay = min(15 * (attempt + 1), 45)
            action_notice(f"report_stage stage=article_model_retry provider={provider} attempt={attempt + 1} delay_s={delay} error={str(exc)[:220]}")
            time.sleep(delay)

    fallback_provider = (os.getenv("REPORT_ARTICLE_FALLBACK_PROVIDER") or "deepseek-pro").strip().lower()
    if fallback_provider and fallback_provider not in {"none", "off", "0", provider} and os.getenv(_article_api_key_env(fallback_provider)):
        action_notice(f"report_stage stage=article_model_fallback from={provider} to={fallback_provider} reason={str(last_exc)[:220]}")
        return _article_chat_json_provider(messages, provider=fallback_provider, timeout=timeout)
    if last_exc:
        raise last_exc
    return chat_json(messages, timeout=timeout or model_timeout(180))


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
            instruction = "请保留事实、金额、日期和交易主体，完整重写文章主线、标题和段落推进，文章净长度必须保持在3,600至3,900字，修复以下质量问题："
        revise_text = "\n" + instruction + json.dumps(revision_issues, ensure_ascii=False) + "\n上一版：" + json.dumps(previous_article, ensure_ascii=False)
    if expansion_only and previous_article:
        revise_text = (
            "\n下面是上一版文章。请基于事实包和资料线索完整重写一版3,600至3,900字文章，低于3,500字无效；不要拼接补丁段落，不要套模板。"
            "重写重点是产业位置、交易结构、财务影响、交割承接和同类并购方法；每章长短根据材料安排，至少有3个超过260字的连续分析段；不改变交易主体和已核验事实。"
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
        "全文长度控制在3,500至4,000个中文字符，最稳妥的目标是3,600至3,900字；不要写成2,000多字的摘要。"
        "不要输出兜底模板段落，不要每章都写成相同段数；至少3个段落要形成超过260字的连续论证。"
        "文章必须覆盖交易时间、交易金额或估值、支付方式或股权比例、交易双方介绍、买方购买理由、卖方接受安排原因或可由披露条款支撑的客观安排依据。"
        "若公开资料没有披露卖方或被整合方的主观动机，必须直接说明未披露，不得编造；但要结合本案已披露的现金要约、预受要约、协议转让价格、股份支付、控制权变化或资产置换等条款分析其接受安排的现实机制。"
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


def build_fast_draft_prompt(
    brief: CaseBrief,
    research_rows: list[dict[str, str]],
    *,
    fact_pack: FactPack,
    narrative_plan: NarrativePlan,
) -> list[dict[str, str]]:
    compact_rows: list[dict[str, str]] = []
    for row in research_rows[:8]:
        compact_rows.append({
            "title": str(row.get("title") or "")[:160],
            "url": str(row.get("url") or "")[:300],
            "summary": str(row.get("summary") or "")[:700],
            "numeric_facts": str(row.get("numeric_facts") or "")[:900],
            "evidence_type": str(row.get("evidence_type") or "")[:60],
        })
    system_prompt = "你是严谨的并购案例研究作者。只输出JSON，不编造事实。"
    user_prompt = (
        "请基于事实包快速生成一篇可直接写入Word的中文并购案例分析稿。"
        "标题采用主标题：副标题，正文3,500至4,000字，4至6个章节。"
        "不要写模板化套话，不要写“并购不是终点，整合才是开始”。"
        "每章必须紧扣本案事实，覆盖产业判断、交易结构、交易条款、买方逻辑、卖方或股东接受机制、并购方法论意义。"
        "中文和英文或数字之间不要加空格；金额、股份数、比例等数字使用千分位逗号；公司首次出现按完整名称标注简称和股票代码（如披露）。"
        "资料未披露的内容必须写明未披露，不能补编。"
        f"\n案例：{brief.case_name}"
        f"\n分类：{brief.category}"
        f"\n事实包：{json.dumps(fact_pack.to_dict(), ensure_ascii=False)}"
        f"\n叙事计划：{json.dumps(narrative_plan.to_dict(), ensure_ascii=False)}"
        f"\n资料线索：{json.dumps(compact_rows, ensure_ascii=False)}"
        "\n输出JSON格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、章节标题\",\"paragraphs\":[\"自然段\",...]},...],\"sources\":[...]}。"
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


def _length_score(article: dict[str, object]) -> tuple[int, int]:
    length = chinese_length(article_text(article))
    midpoint = (TARGET_MIN_CHARS + TARGET_MAX_CHARS) // 2
    if TARGET_MIN_CHARS <= length <= TARGET_MAX_CHARS:
        return (4, -abs(length - midpoint))
    if MIN_CHARS <= length <= MAX_CHARS:
        return (3, -abs(length - midpoint))
    if length < MIN_CHARS:
        return (1, length)
    return (2, -abs(length - MAX_CHARS))


def expand_to_target_length(article: dict[str, object], brief: CaseBrief, research_rows: list[dict[str, str]], fact_pack: FactPack, narrative_plan: NarrativePlan) -> dict[str, object]:
    article = postprocess_article(article, brief)
    best_article = article
    best_score = _length_score(article)
    max_length_rewrites = int(os.getenv("REPORT_LENGTH_REWRITE_REVISIONS", "2"))
    for attempt in range(max_length_rewrites):
        length = chinese_length(article_text(article))
        if MIN_CHARS <= length <= MAX_CHARS:
            return article
        if length < MIN_CHARS:
            LOGGER.info("Regenerating report %s for hard length check, attempt %s, current=%s", brief.case_name, attempt + 1, length)
            action_notice(f"report_stage case={brief.case_name} stage=length_rewrite attempt={attempt + 1} current_length={length}")
            payload = article_chat_json(
                build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan, previous_article=article, expansion_only=True),
                timeout=article_model_timeout(240),
            )
            candidate = normalize_article(payload, brief)
            candidate_score = _length_score(candidate)
            if candidate_score > best_score:
                best_article = candidate
                best_score = candidate_score
            if candidate_score >= _length_score(article):
                article = candidate
            else:
                article = best_article
        elif length > MAX_CHARS:
            article = trim_article(article)
            if chinese_length(article_text(article)) <= MAX_CHARS:
                return article
            score = _length_score(article)
            if score > best_score:
                best_article = article
                best_score = score
    return postprocess_article(best_article, brief)


def _article_validation_score(article: dict[str, object], brief: CaseBrief) -> tuple[int, tuple[int, int]]:
    hard_issues = validate_article(article, brief)
    quality_issues = assess_quality(article)
    return (-len(hard_issues) * 100 - len(quality_issues) * 10, _length_score(article))


def final_repair_article(article: dict[str, object], brief: CaseBrief, research_rows: list[dict[str, str]], fact_pack: FactPack, narrative_plan: NarrativePlan) -> dict[str, object]:
    max_repairs = int(os.getenv("REPORT_FINAL_REPAIR_REVISIONS", "1"))
    article = expand_to_target_length(article, brief, research_rows, fact_pack, narrative_plan)
    article = postprocess_article(article, brief)
    for attempt in range(max_repairs):
        hard_issues = validate_article(article, brief)
        quality_issues = assess_quality(article)
        if not hard_issues and not quality_issues:
            return article
        LOGGER.info("Final report repair %s for %s due to hard=%s quality=%s", attempt + 1, brief.case_name, hard_issues, quality_issues)
        action_notice(f"report_stage case={brief.case_name} stage=final_repair attempt={attempt + 1} hard={len(hard_issues)} quality={len(quality_issues)}")
        current_score = _article_validation_score(article, brief)
        payload = article_chat_json(
            build_prompt(
                brief,
                research_rows,
                fact_pack=fact_pack,
                narrative_plan=narrative_plan,
                revision_issues=hard_issues + quality_issues,
                previous_article=article,
                quality_rewrite=bool(quality_issues),
            ),
            timeout=article_model_timeout(240),
        )
        candidate = normalize_article(payload, brief)
        candidate = expand_to_target_length(candidate, brief, research_rows, fact_pack, narrative_plan)
        candidate = postprocess_article(candidate, brief)
        candidate_score = _article_validation_score(candidate, brief)
        if candidate_score >= current_score:
            article = candidate
        else:
            LOGGER.info("Final repair candidate was worse for %s; keeping previous version", brief.case_name)
            break
    return postprocess_article(article, brief)


def generate_article(brief: CaseBrief) -> dict[str, object]:
    action_notice(f"report_stage case={brief.case_name} stage=collect_research_start")
    research_items = collect_research_context(brief)
    research_rows = [item.to_dict() for item in research_items]
    external_count = external_evidence_count(research_rows)
    LOGGER.info("Collected %s research items for report: %s external=%s", len(research_rows), brief.case_name, external_count)
    action_notice(f"report_stage case={brief.case_name} stage=collect_research_done items={len(research_rows)} external={external_count}")
    if external_count < int(os.getenv("REPORT_MIN_EXTERNAL_RESEARCH_ITEMS", "1")):
        raise RuntimeError(f"Insufficient external research evidence for {brief.case_name}; only structured seed was found.")
    try:
        return generate_article_with_rows(brief, research_rows)
    except Exception as exc:  # noqa: BLE001
        if os.getenv("REPORT_EXPAND_ON_FAILURE", "0") != "1":
            raise
        LOGGER.warning("Initial report generation failed for %s; expanding Google/Bing/page research and regenerating: %s", brief.case_name, exc)
        action_notice(f"report_stage case={brief.case_name} stage=expanded_research_start reason={str(exc)[:300]}")
        expanded_items = collect_research_context(brief, limit=56, expanded=True)
        expanded_rows = [item.to_dict() for item in expanded_items]
        expanded_external_count = external_evidence_count(expanded_rows)
        LOGGER.info(
            "Expanded research context to %s items for report: %s external=%s",
            len(expanded_rows),
            brief.case_name,
            expanded_external_count,
        )
        action_notice(f"report_stage case={brief.case_name} stage=expanded_research_done items={len(expanded_rows)} external={expanded_external_count}")
        if len(expanded_rows) <= len(research_rows) or expanded_external_count < 2:
            raise
        return generate_article_with_rows(brief, expanded_rows)


def generate_article_with_rows(brief: CaseBrief, research_rows: list[dict[str, str]]) -> dict[str, object]:
    action_notice(
        f"report_stage case={brief.case_name} stage=model_routing "
        f"light_model={os.getenv('DEEPSEEK_MODEL', 'deepseek-v4-flash')} "
        f"article_provider={_article_provider() or 'deepseek'} article_model={_article_model_name()}"
    )
    action_notice(f"report_stage case={brief.case_name} stage=fact_pack_start")
    fact_pack = build_fact_pack(brief, research_rows)
    if fact_pack.validation_issues:
        action_notice(f"report_stage case={brief.case_name} stage=fact_pack_failed issues={fact_pack.validation_issues}")
        raise RuntimeError(f"Fact pack validation failed for {brief.case_name}: {fact_pack.validation_issues}")
    action_notice(f"report_stage case={brief.case_name} stage=fact_pack_done deal_value={fact_pack.deal_value[:120]}")
    action_notice(f"report_stage case={brief.case_name} stage=narrative_plan_start")
    narrative_plan = build_narrative_plan(brief, fact_pack, research_rows)
    LOGGER.info("Generated narrative plan for %s: %s", brief.case_name, narrative_plan.to_dict())
    action_notice(f"report_stage case={brief.case_name} stage=narrative_plan_done")

    if os.getenv("REPORT_FAST_DRAFT_MODE", "0") == "1":
        action_notice(f"report_stage case={brief.case_name} stage=fast_draft_start")
        payload = article_chat_json(
            build_fast_draft_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan),
            timeout=article_model_timeout(240),
        )
        article = normalize_article(payload, brief)
        action_notice(f"report_stage case={brief.case_name} stage=fast_draft_done length={chinese_length(article_text(article))}")
        final_issues = validate_article(article, brief)
        final_quality_issues = assess_quality(article)
        if final_issues or final_quality_issues:
            LOGGER.warning("Fast draft has validation/quality issues: %s hard=%s quality=%s", brief.case_name, final_issues, final_quality_issues)
            if os.getenv("REPORT_ALLOW_DRAFT_ON_VALIDATION_FAILURE", "0") != "1":
                raise RuntimeError(f"Report quality validation failed for {brief.case_name}: hard={final_issues} quality={final_quality_issues}")
            article["validation_issues"] = final_issues
            article["quality_issues"] = final_quality_issues
        return postprocess_article(article, brief)

    action_notice(f"report_stage case={brief.case_name} stage=article_draft_start")
    payload = article_chat_json(
        build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan),
        timeout=article_model_timeout(240),
    )
    article = normalize_article(payload, brief)
    action_notice(f"report_stage case={brief.case_name} stage=article_draft_done length={chinese_length(article_text(article))}")
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
        action_notice(f"report_stage case={brief.case_name} stage=revision_start round={round_idx + 1} hard={len(issues)} quality={len(quality_issues)}")
        payload = article_chat_json(
            build_prompt(
                brief,
                research_rows,
                fact_pack=fact_pack,
                narrative_plan=narrative_plan,
                revision_issues=non_length_issues or combined_issues,
                previous_article=article,
                quality_rewrite=is_quality_rewrite,
            ),
            timeout=article_model_timeout(240),
        )
        article = normalize_article(payload, brief)
        action_notice(f"report_stage case={brief.case_name} stage=revision_done round={round_idx + 1} length={chinese_length(article_text(article))}")
        issues = validate_article(article, brief)
        quality_issues = assess_quality(article)

    action_notice(f"report_stage case={brief.case_name} stage=final_repair_pipeline_start length={chinese_length(article_text(article))}")
    article = final_repair_article(article, brief, research_rows, fact_pack, narrative_plan)
    final_issues = validate_article(article, brief)
    final_quality_issues = assess_quality(article)
    if final_issues or final_quality_issues:
        LOGGER.warning("Report still has validation/quality issues after narrative pipeline: %s hard=%s quality=%s", brief.case_name, final_issues, final_quality_issues)
        if os.getenv("REPORT_ALLOW_DRAFT_ON_VALIDATION_FAILURE", "0") == "1":
            action_notice(
                f"report_stage case={brief.case_name} stage=validation_failed_returning_draft "
                f"hard={len(final_issues)} quality={len(final_quality_issues)}"
            )
            article["validation_issues"] = final_issues
            article["quality_issues"] = final_quality_issues
            return postprocess_article(article, brief)
        raise RuntimeError(f"Report quality validation failed for {brief.case_name}: hard={final_issues} quality={final_quality_issues}")
    else:
        LOGGER.info("Report passed hard validation and quality checks: %s length=%s", brief.case_name, chinese_length(article_text(article)))
    return article
