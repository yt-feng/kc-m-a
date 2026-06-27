"""Article generation for M&A case analysis reports."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
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
    cn_number,
    compact_name,
    postprocess_article,
    strip_heading_number,
    title_length,
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
DISABLED_ARTICLE_PROVIDERS: set[str] = set()
GENERIC_REPAIR_HEADINGS = (
    "交易过程", "交易逻辑", "可复用经验", "结论", "经验启示", "案例启示",
    "交易背景", "交易结构设计", "并购后整合", "买方动机", "卖方动机",
)
GENERIC_CONCLUSION_REPAIR_PHRASES = (
    "并购不是终点", "整合才是开始", "协同不是口号", "时间会给出答案",
    "值得长期关注", "对企业具有重要启示", "为同类交易提供了借鉴", "具有参考意义",
)


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


def _provider_has_api_key(provider: str) -> bool:
    return bool(provider and provider not in {"none", "off", "0"} and os.getenv(_article_api_key_env(provider)))


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
    fallback_provider = (os.getenv("REPORT_ARTICLE_FALLBACK_PROVIDER") or "deepseek-pro").strip().lower()
    if provider in DISABLED_ARTICLE_PROVIDERS and fallback_provider not in {"", "none", "off", "0", provider} and _provider_has_api_key(fallback_provider):
        action_notice(f"report_stage stage=article_model_primary_disabled provider={provider} using={fallback_provider}")
        return _article_chat_json_provider(messages, provider=fallback_provider, timeout=timeout)
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

    if fallback_provider and fallback_provider not in {"none", "off", "0", provider} and _provider_has_api_key(fallback_provider):
        action_notice(f"report_stage stage=article_model_fallback from={provider} to={fallback_provider} reason={str(last_exc)[:220]}")
        DISABLED_ARTICLE_PROVIDERS.add(provider)
        return _article_chat_json_provider(messages, provider=fallback_provider, timeout=timeout)
    if last_exc:
        raise last_exc
    return chat_json(messages, timeout=timeout or model_timeout(180))


def external_evidence_count(research_rows: list[dict[str, str]]) -> int:
    return sum(1 for row in research_rows if (row.get("evidence_type") or "") != "structured_seed")


def _allow_fact_pack_smoke_test() -> bool:
    return os.getenv("REPORT_ALLOW_FACT_PACK_VALIDATION_FAILURE", "0") == "1"


def _allow_draft_on_validation_failure() -> bool:
    return os.getenv("REPORT_ALLOW_DRAFT_ON_VALIDATION_FAILURE", "0") == "1"


def _expand_on_failure_enabled() -> bool:
    return os.getenv("REPORT_EXPAND_ON_FAILURE", "0") == "1"


def _should_expand_after_failure(exc: Exception) -> bool:
    text = str(exc)
    return any(token in text for token in ("Insufficient external research evidence", "Fact pack validation failed", "缺少可引用"))


def publish_partial_article(
    brief: CaseBrief,
    article: dict[str, object],
    *,
    stage: str,
    hard_issues: list[str] | None = None,
    quality_issues: list[str] | None = None,
) -> None:
    if PROGRESS_QUEUE is None or not _allow_draft_on_validation_failure():
        return
    partial = postprocess_article(copy.deepcopy(article), brief)
    if hard_issues:
        partial["validation_issues"] = hard_issues
    if quality_issues:
        partial["quality_issues"] = quality_issues
    PROGRESS_QUEUE.put({
        "type": "partial",
        "stage": stage,
        "article": partial,
        "length": chinese_length(article_text(partial)),
        "hard": len(hard_issues or []),
        "quality": len(quality_issues or []),
    })


def _clip_text_piece(text: str, target_chinese_len: int, *, min_len: int = 120) -> str:
    text = str(text or "").strip()
    if chinese_length(text) <= target_chinese_len:
        return text
    target_chinese_len = max(min_len, target_chinese_len)
    low = min(min_len, len(text))
    high = len(text)
    best = text[:low]
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip("，；、：: ")
        if chinese_length(candidate) <= target_chinese_len:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    window_start = max(0, len(best) - 80)
    for punct in ("。", "；", "，", "、"):
        idx = best.rfind(punct, window_start)
        if idx > max(10, min_len // 2):
            best = best[: idx + 1]
            break
    best = best.rstrip("，；、：: ")
    if best and best[-1] not in "。！？；":
        best += "。"
    return best


def _force_trim_article_to_max(article: dict[str, object], brief: CaseBrief, max_chars: int) -> dict[str, object]:
    """Last-resort deterministic trim for small post-repair overages."""
    article = postprocess_article(article, brief)
    sections = article.get("sections") or []
    if not isinstance(sections, list):
        return article
    for _ in range(40):
        length = chinese_length(article_text(article))
        if length <= max_chars:
            return postprocess_article(article, brief)
        overage = length - max_chars
        candidates: list[tuple[int, int, int, int, str, bool]] = []
        intro = str(article.get("intro") or "")
        if chinese_length(intro) > 140:
            candidates.append((2, chinese_length(intro), -1, -1, intro, False))
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            paragraphs = section.get("paragraphs") or []
            if not isinstance(paragraphs, list):
                continue
            priority = 1 if section_index == 0 else (3 if section_index == len(sections) - 1 else 0)
            for paragraph_index, paragraph in enumerate(paragraphs):
                text = str(paragraph or "")
                paragraph_len = chinese_length(text)
                if paragraph_len > 80:
                    can_delete = priority == 0 and len([p for p in paragraphs if str(p).strip()]) > 1
                    candidates.append((priority, paragraph_len, section_index, paragraph_index, text, can_delete))
        if not candidates:
            return article
        priority, piece_len, section_index, paragraph_index, text, can_delete = sorted(candidates, key=lambda item: (item[0], not item[5], -item[1]))[0]
        if can_delete and (overage > 180 or piece_len < overage + 80):
            section = sections[section_index]
            paragraphs = section.get("paragraphs") if isinstance(section, dict) else []
            if isinstance(paragraphs, list) and 0 <= paragraph_index < len(paragraphs):
                del paragraphs[paragraph_index]
                article = postprocess_article(article, brief)
                continue
        min_keep = 70 if priority != 3 else 120
        target_len = max(min_keep, piece_len - overage - 70)
        clipped = _clip_text_piece(text, target_len, min_len=min_keep)
        if chinese_length(clipped) >= piece_len and piece_len > min_keep:
            clipped = _clip_text_piece(text, max(min_keep, piece_len - max(overage + 120, 180)), min_len=min_keep)
        if section_index < 0:
            article["intro"] = clipped
        else:
            sections[section_index]["paragraphs"][paragraph_index] = clipped
        article = postprocess_article(article, brief)
    return article


def enforce_hard_length(article: dict[str, object], brief: CaseBrief, *, stage: str) -> dict[str, object]:
    article = postprocess_article(article, brief)
    length = chinese_length(article_text(article))
    safety_margin = max(0, int(os.getenv("REPORT_MAX_CHARS_SAFETY_MARGIN", "60")))
    safe_max_chars = max(MIN_CHARS, MAX_CHARS - safety_margin)
    if length <= safe_max_chars:
        return article
    action_notice(f"report_stage case={brief.case_name} stage={stage}_hard_trim_start length={length}")
    trimmed = trim_article(article, safe_max_chars)
    trimmed = postprocess_article(trimmed, brief)
    if chinese_length(article_text(trimmed)) > safe_max_chars:
        trimmed = _force_trim_article_to_max(trimmed, brief, safe_max_chars)
    trimmed_length = chinese_length(article_text(trimmed))
    action_notice(f"report_stage case={brief.case_name} stage={stage}_hard_trim_done length={trimmed_length}")
    if trimmed_length <= MAX_CHARS:
        return trimmed
    trimmed = _force_trim_article_to_max(trimmed, brief, MAX_CHARS - 20)
    trimmed_length = chinese_length(article_text(trimmed))
    action_notice(f"report_stage case={brief.case_name} stage={stage}_hard_trim_forced_done length={trimmed_length}")
    if trimmed_length <= MAX_CHARS:
        return trimmed
    LOGGER.warning(
        "Hard trim still above max for %s stage=%s length=%s",
        brief.case_name,
        stage,
        trimmed_length,
    )
    return trimmed


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
            instruction = "请保留事实、金额、日期和交易主体，完整重写文章主线、标题和段落推进，文章净长度必须保持在3,550至3,800字，超过4,000字无效，修复以下质量问题："
        revise_text = "\n" + instruction + json.dumps(revision_issues, ensure_ascii=False) + "\n上一版：" + json.dumps(previous_article, ensure_ascii=False)
    if expansion_only and previous_article:
        revise_text = (
            "\n下面是上一版文章。请基于事实包和资料线索完整重写一版3,550至3,800字文章，低于3,500字或超过4,000字都无效；不要拼接补丁段落，不要套模板。"
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
        "标题、章节标题和正文语气必须专业、克制，像并购案例研究或投委会备忘录，而不是媒体报道；不要使用悬疑、问号、搏杀、资本游戏、暗礁、拉响、剑指、闪电成立、新壳入主等戏剧化表达，相关词只是风格示例，真正要求是用交易结构、产业位置、财务影响或治理安排来概括判断。"
        "公司首次出现必须在完整名称之后标注简称和股票代码（如上市），例如腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME）；上海喜马拉雅科技有限公司（下称“喜马拉雅”）。资料没有披露完整名称或股票代码时不要编造。"
        "全文必须用全角中文标点和中文引号“”，不要用半角引号；中文和英文或数字之间不要加空格。"
        "金额、数量等数字必须使用千分位逗号。事实、数字、信息必须基于给定资料线索和事实包，不能编造资料外事实。"
        "全文长度控制在3,500至4,000个中文字符，这是硬性要求；最稳妥的目标是3,550至3,800字，超过4,000字无效；不要写成2,000多字的摘要。"
        "不要输出兜底模板段落，不要每章都写成相同段数；至少3个段落要形成超过260字的连续论证。"
        "正文开头和前1至2个章节必须先讲清楚本案的前因后果：交易发生前的股权结构、业务状态、经营压力、产业位置或控制权状态；触发交易的公告、协议、要约、董事会/股东会决议、监管文件或市场事件；交易由谁发起、通过什么路径发起；交易希望实现的目标，例如取得控制权、内部整合、产业协同、资产注入、退出变现、优化资本结构或补强业务。"
        "不要一开篇就只罗列交易结果和数字；必须解释为什么会有这笔交易、它是怎样被推进出来的、这种交易结构为什么服务于目标。"
        "文章必须覆盖交易时间、交易金额或估值、支付方式或股权比例、交易双方介绍、买方购买理由、卖方接受安排原因或可由披露条款支撑的客观安排依据。"
        "若公开资料没有披露卖方或被整合方的主观动机，必须直接说明未披露，不得编造；但要结合本案已披露的现金要约、预受要约、协议转让价格、股份支付、控制权变化或资产置换等条款分析其接受安排的现实机制。"
        "若公开资料没有披露交易发起过程或买方主观目的，必须直接说明未披露，并用已披露的条款、持股变化、控制权状态、资金来源、交割条件或程序安排解释客观发起机制。"
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
        "标题采用主标题：副标题，正文3,500至4,000字，最稳妥目标是3,550至3,800字，4至6个章节。"
        "不要写模板化套话，不要写“并购不是终点，整合才是开始”。语气专业、克制，不用悬疑、问号、资本游戏、暗礁、剑指、新壳入主等媒体化表达；这些词只是风格示例，具体写法要改成交易结构、产业位置、财务影响或治理安排判断。"
        "正文前部必须讲清本案前因后果：交易前是什么状态，什么事项触发或启动交易，谁通过什么路径发起交易，交易希望实现什么目标；资料未披露时要明确写明未披露并转向已披露条款的客观机制。"
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


def _replace_article_text_fields(article: dict[str, object], replace_fn: Any) -> dict[str, object]:
    article["title"] = replace_fn(str(article.get("title") or ""))
    article["intro"] = replace_fn(str(article.get("intro") or ""))
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec["heading"] = replace_fn(str(sec.get("heading") or ""))
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                sec["paragraphs"] = [replace_fn(str(p)) for p in paragraphs]
    return article


def repair_article_against_fact_pack(article: dict[str, object], brief: CaseBrief, fact_pack: FactPack) -> dict[str, object]:
    fact_text = json.dumps(fact_pack.to_dict(), ensure_ascii=False)
    percentages = re.findall(r"\d{1,3}(?:\.\d+)?%", fact_text)
    per_share_prices = re.findall(r"\d{1,3}(?:\.\d{1,4})?(?:元/股|港元/股|美元/股)", fact_text)
    amount_limits = re.findall(r"金额上限约(\d{1,3}(?:,\d{3})+元)", fact_text)

    def replace_placeholder(match: re.Match[str]) -> str:
        prefix = match.group(1)
        for pct in percentages:
            if pct.startswith(prefix + ".") or pct == prefix + "%":
                return pct
        return match.group(0)

    def replace_text(text: str) -> str:
        text = re.sub(r"(\d{1,3})\.x+%", replace_placeholder, text, flags=re.I)
        if per_share_prices:
            verified_price = per_share_prices[0]
            price_number_match = re.match(r"(\d{1,3}(?:\.\d{1,4})?)", verified_price)
            text = re.sub(r"\d{1,4}(?:\.\d+)?(?:元/股|港元/股|美元/股)", verified_price, text, count=1)
            if price_number_match:
                text = re.sub(r"每股\d{1,4}(?:\.\d+)?元", f"每股{price_number_match.group(1)}元", text, count=1)
        if amount_limits:
            text = re.sub(r"总金额约?\d{1,3}(?:,\d{3})+元", f"总金额上限约{amount_limits[0]}", text, count=1)
        return text

    article = _replace_article_text_fields(article, replace_text)
    return postprocess_article(article, brief)


def _compact_title_alias(value: str, *, max_len: int) -> str:
    original = str(value or "").strip()
    suffix_only = re.sub(r"(股份有限公司|有限责任公司|有限公司|公司)$", "", original, flags=re.I).strip()
    alias = suffix_only or compact_name(original) or original
    alias = re.sub(r"[（）()\"“”'‘’]", "", alias)
    alias = alias.replace("上海市", "上海").replace("深圳市", "深圳").replace("杭州市", "杭州")
    alias = alias.strip()
    if len(alias) <= max_len:
        return alias
    return alias[:max_len]


def _deal_focus_phrase(brief: CaseBrief, fact_pack: FactPack) -> str:
    text = " ".join([
        brief.category or "",
        brief.deal_value or "",
        fact_pack.deal_value or "",
        fact_pack.buyer_rationale or "",
        fact_pack.seller_rationale or "",
    ])
    if "要约" in text:
        return "要约与控制权安排"
    if "控制权" in text or "控股权" in text or "表决权" in text:
        return "控制权与治理安排"
    if "现金" in text:
        return "现金支付与业务承接"
    if "协议转让" in text or "受让" in text:
        return "协议转让与交割条件"
    if "股权" in text or "股份" in text or "%" in text or "％" in text:
        return "股权比例与治理安排"
    return "披露边界下的交易结构"


def _safe_article_title(brief: CaseBrief, fact_pack: FactPack) -> str:
    focus = _deal_focus_phrase(brief, fact_pack)
    for max_len in (10, 8, 6, 4):
        acquirer = _compact_title_alias(brief.acquirer or fact_pack.acquirer or brief.case_name, max_len=max_len)
        target = _compact_title_alias(brief.target or fact_pack.target or brief.case_name, max_len=max_len)
        title = f"{acquirer}收购{target}：{focus}"
        if title_length(title) <= 40:
            return title
    acquirer = _compact_title_alias(brief.acquirer or fact_pack.acquirer or brief.case_name, max_len=4)
    target = _compact_title_alias(brief.target or fact_pack.target or brief.case_name, max_len=4)
    return f"{acquirer}收购{target}：控制权安排"


def _clean_fact_values(values: list[object], *, limit: int = 3) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" ；;，,。")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:120])
        if len(out) >= limit:
            break
    return "；".join(out)


def _ensure_intro_context(article: dict[str, object], brief: CaseBrief, fact_pack: FactPack) -> None:
    full_acquirer = brief.acquirer or fact_pack.acquirer
    full_target = brief.target or fact_pack.target
    if not full_acquirer or not full_target:
        return
    intro = str(article.get("intro") or "")
    if full_acquirer in intro and full_target in intro and "交易前" in intro:
        return
    trigger = "公告、协议、要约或监管披露"
    source_hint = _clean_fact_values(fact_pack.source_titles, limit=1)
    if source_hint:
        trigger = source_hint
    objective = fact_pack.buyer_rationale or brief.buyer_motivation or "公开资料未完整披露主观目的，需回到已披露条款理解交易目标"
    anchor = (
        f"本案例涉及{full_acquirer}收购{full_target}的交易安排。"
        f"交易前，本文先从股权结构、控制权状态、业务状态和产业位置理解双方处境；"
        f"交易的触发路径来自{trigger}，目标线索为{objective[:180]}。"
    )
    article["intro"] = f"{anchor}{intro}" if intro else anchor


def _ensure_first_section(article: dict[str, object]) -> dict[str, object]:
    sections = article.get("sections")
    if not isinstance(sections, list):
        sections = []
        article["sections"] = sections
    if not sections:
        sections.append({"heading": "一、交易前状态与披露触发", "paragraphs": []})
    first = sections[0]
    if not isinstance(first, dict):
        first = {"heading": "一、交易前状态与披露触发", "paragraphs": []}
        sections[0] = first
    if not isinstance(first.get("paragraphs"), list):
        first["paragraphs"] = []
    return first


def _ensure_fact_boundary(article: dict[str, object], brief: CaseBrief, fact_pack: FactPack) -> None:
    text = article_text(article)
    financial_terms = ("收入", "营收", "净利润", "现金流", "负债", "产能", "订单", "客户", "用户", "员工", "资源量", "股权")
    financial_term_count = sum(1 for term in financial_terms if term in text)
    if "披露边界" in text and "收入、净利润、现金流" in text and financial_term_count >= 5:
        return
    deal_value = fact_pack.deal_value or brief.deal_value or "公开资料未披露具体金额，但保留了支付方式、股权比例或交易结构线索"
    timeline = _clean_fact_values(fact_pack.timeline or [brief.deal_status], limit=2) or "公告、签署、交割或过户节点以公开披露为准"
    key_numbers = _clean_fact_values(fact_pack.key_numbers, limit=4) or "关键数字集中在股权比例、支付方式、交易价格、资产或控制权安排"
    financial = fact_pack.financial_highlights or brief.financial_highlights
    if financial:
        financial_clause = f"已抓取资料中的财务和经营线索为{financial[:220]}"
    else:
        financial_clause = "收入、净利润、现金流、负债、产能、订单、客户、用户、员工等口径在已抓取资料中未完整披露，正文不得补编"
    seller = fact_pack.seller_rationale or "公开资料未单独披露出售方或股东主观动机，需从协议转让、要约、支付方式、控制权或表决权安排理解接受机制"
    paragraph = (
        f"事实披露边界上，本案可引用的交易对价、估值或支付口径为{deal_value}；"
        f"时间线包括{timeline}；关键数字包括{key_numbers}。"
        f"{financial_clause}。收购方、买方和并购方的购买理由需要回到{(fact_pack.buyer_rationale or brief.buyer_motivation or '已披露交易目标')[:180]}；"
        f"出售方、转让方或预受要约股东的接受安排依据为{seller[:220]}。"
        "因此，后文只围绕已披露的股权比例、支付方式、交易价格、资产、客户、产能、技术、治理边界、交割条件、现金流、负债、收入和净利润影响展开，未披露事项作为资料核验边界处理。"
    )
    first = _ensure_first_section(article)
    paragraphs = first["paragraphs"]
    if isinstance(paragraphs, list):
        paragraphs.insert(0, paragraph)


def _ensure_long_analysis_paragraphs(article: dict[str, object]) -> None:
    sections = article.get("sections")
    if not isinstance(sections, list):
        return

    def paragraph_lengths() -> list[int]:
        lengths: list[int] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            paragraphs = section.get("paragraphs") or []
            if isinstance(paragraphs, list):
                lengths.extend(chinese_length(str(paragraph)) for paragraph in paragraphs if str(paragraph).strip())
        return lengths

    lengths = paragraph_lengths()
    long_count = sum(1 for length in lengths if length >= 260)
    if long_count >= 2:
        return
    for section in sections[:-1] or sections:
        if long_count >= 2:
            break
        if not isinstance(section, dict):
            continue
        paragraphs = section.get("paragraphs") or []
        if not isinstance(paragraphs, list) or len(paragraphs) < 2:
            continue
        merged: list[str] = []
        index = 0
        while index < len(paragraphs):
            current = str(paragraphs[index] or "").strip()
            if (
                current
                and chinese_length(current) < 260
                and index + 1 < len(paragraphs)
                and chinese_length(current + str(paragraphs[index + 1] or "")) <= 520
            ):
                combined = current.rstrip("。；") + "；" + str(paragraphs[index + 1] or "").strip()
                merged.append(combined)
                if chinese_length(combined) >= 260:
                    long_count += 1
                index += 2
                continue
            merged.append(current)
            if chinese_length(current) >= 260:
                long_count += 1
            index += 1
        section["paragraphs"] = [paragraph for paragraph in merged if paragraph]


def _ensure_conclusion(article: dict[str, object], brief: CaseBrief, fact_pack: FactPack) -> None:
    sections = article.get("sections")
    if not isinstance(sections, list):
        sections = []
        article["sections"] = sections
    focus = _deal_focus_phrase(brief, fact_pack)
    if not sections:
        sections.append({"heading": f"一、结语：{focus}的执行边界", "paragraphs": []})
    last = sections[-1]
    if not isinstance(last, dict):
        last = {"heading": f"{cn_number(len(sections))}、结语：{focus}的执行边界", "paragraphs": []}
        sections[-1] = last
    if "结语" not in str(last.get("heading") or ""):
        if len(sections) < 7:
            last = {"heading": f"{cn_number(len(sections) + 1)}、结语：{focus}的执行边界", "paragraphs": []}
            sections.append(last)
        else:
            last["heading"] = f"{cn_number(len(sections))}、结语：{focus}的执行边界"
    if not isinstance(last.get("paragraphs"), list):
        last["paragraphs"] = []
    last["paragraphs"] = [
        str(p) for p in last["paragraphs"]
        if not any(phrase in str(p) for phrase in GENERIC_CONCLUSION_REPAIR_PHRASES)
    ]
    conclusion_text = "\n".join(str(p) for p in last["paragraphs"])
    acquirer = _compact_title_alias(brief.acquirer or fact_pack.acquirer or brief.case_name, max_len=10)
    target = _compact_title_alias(brief.target or fact_pack.target or brief.case_name, max_len=10)
    if acquirer in conclusion_text and target in conclusion_text and "方法论" in conclusion_text and "核验" in conclusion_text:
        return
    deal_value = fact_pack.deal_value or brief.deal_value or "已披露交易结构"
    key_numbers = _clean_fact_values(list(fact_pack.key_numbers or []) + list(fact_pack.timeline or []), limit=4) or "公开资料未披露更多量化口径"
    paragraph = (
        f"回到{acquirer}与{target}，本案的方法论意义不在于把交易结果写成通用并购总结，而在于把{deal_value}、{key_numbers}、控制权、股权比例、支付方式和交割条件放在同一披露链条中核验。"
        "因此，同类并购首先要确认信息披露是否足以支撑估值锚、条款安排、治理边界和交易执行；其次要把产业链位置、客户结构、产能、技术路线、资源禀赋和商业模式放回标的业务承接中判断；"
        "最后才评估收入、净利润、现金流、负债、资产、市值、订单、客户和用户等财务影响。"
        f"对{acquirer}而言，关键是交割承接和整合节奏能否与已披露安排匹配；对{target}而言，关键是控制权或股东接受机制能否在公开资料边界内被持续验证。"
    )
    last["paragraphs"].append(paragraph)


def _repair_section_headings(article: dict[str, object], brief: CaseBrief, fact_pack: FactPack) -> None:
    sections = article.get("sections")
    if not isinstance(sections, list):
        return
    focus = _deal_focus_phrase(brief, fact_pack)
    replacements = [
        "交易前状态、披露触发与目标边界",
        f"{focus}的条款基础",
        "业务基础、产业位置与财务边界",
        "交割条件、治理边界与承接风险",
        "同类并购的资料核验与执行方法",
        "信息披露、估值锚与整合节奏",
    ]
    total = len(sections)
    for index, sec in enumerate(sections, start=1):
        if not isinstance(sec, dict):
            continue
        if index == total:
            sec["heading"] = f"{cn_number(index)}、结语：{focus}的核验方法"
            continue
        heading = strip_heading_number(str(sec.get("heading") or ""))
        if len(re.sub(r"\s+", "", heading)) < 8 or any(token in heading for token in GENERIC_REPAIR_HEADINGS):
            sec["heading"] = f"{cn_number(index)}、{replacements[min(index - 1, len(replacements) - 1)]}"


def deterministic_article_repair(article: dict[str, object], brief: CaseBrief, fact_pack: FactPack) -> dict[str, object]:
    article = postprocess_article(article, brief)
    article["title"] = _safe_article_title(brief, fact_pack)
    _ensure_intro_context(article, brief, fact_pack)
    _ensure_fact_boundary(article, brief, fact_pack)
    _ensure_conclusion(article, brief, fact_pack)
    _repair_section_headings(article, brief, fact_pack)
    _ensure_long_analysis_paragraphs(article)
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
            try:
                payload = article_chat_json(
                    build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan, previous_article=article, expansion_only=True),
                    timeout=article_model_timeout(240),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Length rewrite model failed for %s; keeping best available article: %s", brief.case_name, exc)
                action_notice(f"report_stage case={brief.case_name} stage=length_rewrite_model_failed error={str(exc)[:220]}")
                break
            candidate = repair_article_against_fact_pack(normalize_article(payload, brief), brief, fact_pack)
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
    article = deterministic_article_repair(article, brief, fact_pack)
    for attempt in range(max_repairs):
        hard_issues = validate_article(article, brief)
        quality_issues = assess_quality(article)
        if not hard_issues and not quality_issues:
            return article
        LOGGER.info("Final report repair %s for %s due to hard=%s quality=%s", attempt + 1, brief.case_name, hard_issues, quality_issues)
        action_notice(f"report_stage case={brief.case_name} stage=final_repair attempt={attempt + 1} hard={len(hard_issues)} quality={len(quality_issues)}")
        current_score = _article_validation_score(article, brief)
        try:
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
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Final repair model failed for %s; falling back to deterministic repairs: %s", brief.case_name, exc)
            action_notice(f"report_stage case={brief.case_name} stage=final_repair_model_failed error={str(exc)[:220]}")
            break
        candidate = repair_article_against_fact_pack(normalize_article(payload, brief), brief, fact_pack)
        candidate = expand_to_target_length(candidate, brief, research_rows, fact_pack, narrative_plan)
        candidate = deterministic_article_repair(candidate, brief, fact_pack)
        candidate_score = _article_validation_score(candidate, brief)
        if candidate_score >= current_score:
            article = candidate
        else:
            LOGGER.info("Final repair candidate was worse for %s; keeping previous version", brief.case_name)
            break
    return deterministic_article_repair(article, brief, fact_pack)


def generate_article(brief: CaseBrief) -> dict[str, object]:
    action_notice(f"report_stage case={brief.case_name} stage=collect_research_start")
    research_items = collect_research_context(brief)
    research_rows = [item.to_dict() for item in research_items]
    external_count = external_evidence_count(research_rows)
    LOGGER.info("Collected %s research items for report: %s external=%s", len(research_rows), brief.case_name, external_count)
    action_notice(f"report_stage case={brief.case_name} stage=collect_research_done items={len(research_rows)} external={external_count}")
    min_external = int(os.getenv("REPORT_MIN_EXTERNAL_RESEARCH_ITEMS", "1"))
    if external_count < min_external:
        if _expand_on_failure_enabled():
            LOGGER.warning("Initial research evidence is thin for %s; expanding source collection before failing.", brief.case_name)
            action_notice(f"report_stage case={brief.case_name} stage=expanded_research_start reason=thin_external_evidence")
            expanded_items = collect_research_context(brief, limit=56, expanded=True)
            expanded_rows = [item.to_dict() for item in expanded_items]
            expanded_external_count = external_evidence_count(expanded_rows)
            action_notice(f"report_stage case={brief.case_name} stage=expanded_research_done items={len(expanded_rows)} external={expanded_external_count}")
            if expanded_external_count >= min_external:
                return generate_article_with_rows(brief, expanded_rows)
        raise RuntimeError(f"Insufficient external research evidence for {brief.case_name}; only structured seed was found.")
    try:
        return generate_article_with_rows(brief, research_rows)
    except Exception as exc:  # noqa: BLE001
        if not _expand_on_failure_enabled() or not _should_expand_after_failure(exc):
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
        if not _allow_fact_pack_smoke_test():
            raise RuntimeError(f"Fact pack validation failed for {brief.case_name}: {fact_pack.validation_issues}")
        LOGGER.warning("Proceeding with weak fact pack because smoke-test override is enabled: %s issues=%s", brief.case_name, fact_pack.validation_issues)
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
        article = repair_article_against_fact_pack(normalize_article(payload, brief), brief, fact_pack)
        action_notice(f"report_stage case={brief.case_name} stage=fast_draft_done length={chinese_length(article_text(article))}")
        article = enforce_hard_length(article, brief, stage="fast_draft")
        final_issues = validate_article(article, brief)
        final_quality_issues = assess_quality(article)
        publish_partial_article(
            brief,
            article,
            stage="fast_draft",
            hard_issues=final_issues,
            quality_issues=final_quality_issues,
        )
        if final_issues or final_quality_issues:
            LOGGER.warning("Fast draft has validation/quality issues: %s hard=%s quality=%s", brief.case_name, final_issues, final_quality_issues)
            if os.getenv("REPORT_ALLOW_DRAFT_ON_VALIDATION_FAILURE", "0") != "1":
                raise RuntimeError(f"Report quality validation failed for {brief.case_name}: hard={final_issues} quality={final_quality_issues}")
            article["validation_issues"] = final_issues
            article["quality_issues"] = final_quality_issues
        return enforce_hard_length(article, brief, stage="fast_draft_return")

    action_notice(f"report_stage case={brief.case_name} stage=article_draft_start")
    try:
        payload = article_chat_json(
            build_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan),
            timeout=article_model_timeout(240),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Primary article draft model failed for %s; trying fast draft rescue: %s", brief.case_name, exc)
        action_notice(f"report_stage case={brief.case_name} stage=article_draft_model_failed fallback=fast_draft error={str(exc)[:220]}")
        payload = chat_json(
            build_fast_draft_prompt(brief, research_rows, fact_pack=fact_pack, narrative_plan=narrative_plan),
            timeout=model_timeout(180),
        )
    article = repair_article_against_fact_pack(normalize_article(payload, brief), brief, fact_pack)
    action_notice(f"report_stage case={brief.case_name} stage=article_draft_done length={chinese_length(article_text(article))}")
    article = enforce_hard_length(article, brief, stage="article_draft")
    issues = validate_article(article, brief)
    quality_issues = assess_quality(article)
    publish_partial_article(brief, article, stage="article_draft", hard_issues=issues, quality_issues=quality_issues)
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
        try:
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
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Revision model failed for %s round %s; entering final deterministic repair: %s", brief.case_name, round_idx + 1, exc)
            action_notice(f"report_stage case={brief.case_name} stage=revision_model_failed round={round_idx + 1} error={str(exc)[:220]}")
            break
        article = repair_article_against_fact_pack(normalize_article(payload, brief), brief, fact_pack)
        article = enforce_hard_length(article, brief, stage=f"revision_{round_idx + 1}")
        action_notice(f"report_stage case={brief.case_name} stage=revision_done round={round_idx + 1} length={chinese_length(article_text(article))}")
        issues = validate_article(article, brief)
        quality_issues = assess_quality(article)
        publish_partial_article(
            brief,
            article,
            stage=f"revision_{round_idx + 1}",
            hard_issues=issues,
            quality_issues=quality_issues,
        )

    action_notice(f"report_stage case={brief.case_name} stage=final_repair_pipeline_start length={chinese_length(article_text(article))}")
    article = final_repair_article(article, brief, research_rows, fact_pack, narrative_plan)
    article = enforce_hard_length(article, brief, stage="final_repair")
    article = deterministic_article_repair(article, brief, fact_pack)
    article = enforce_hard_length(article, brief, stage="final_deterministic_repair")
    final_issues = validate_article(article, brief)
    final_quality_issues = assess_quality(article)
    publish_partial_article(
        brief,
        article,
        stage="final_repair",
        hard_issues=final_issues,
        quality_issues=final_quality_issues,
    )
    if final_issues or final_quality_issues:
        action_notice(
            f"report_stage case={brief.case_name} stage=final_validation_failed length={chinese_length(article_text(article))} "
            f"hard={len(final_issues)} quality={len(final_quality_issues)} hard_sample={';'.join(final_issues[:2])[:260]} "
            f"quality_sample={';'.join(final_quality_issues[:2])[:260]}"
        )
        LOGGER.warning("Report still has validation/quality issues after narrative pipeline: %s hard=%s quality=%s", brief.case_name, final_issues, final_quality_issues)
        if os.getenv("REPORT_ALLOW_DRAFT_ON_VALIDATION_FAILURE", "0") == "1":
            action_notice(
                f"report_stage case={brief.case_name} stage=validation_failed_returning_draft "
                f"hard={len(final_issues)} quality={len(final_quality_issues)}"
            )
            article["validation_issues"] = final_issues
            article["quality_issues"] = final_quality_issues
            return enforce_hard_length(article, brief, stage="validation_failed_return")
        raise RuntimeError(f"Report quality validation failed for {brief.case_name}: hard={final_issues} quality={final_quality_issues}")
    else:
        action_notice(f"report_stage case={brief.case_name} stage=final_validation_passed length={chinese_length(article_text(article))}")
        LOGGER.info("Report passed hard validation and quality checks: %s length=%s", brief.case_name, chinese_length(article_text(article)))
    return enforce_hard_length(article, brief, stage="final_return")
