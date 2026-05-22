"""Article generation for M&A case analysis reports."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .case_selection import CaseBrief
from .config import CATEGORY_GUIDE, REFERENCE_STYLE, STYLE_RULES, TOPIC_SELECTION_RULES
from .deepseek_client import chat_json
from .research import collect_research_context

LOGGER = logging.getLogger(__name__)

BANNED_INTRO_PATTERNS = ("本文", "本报告", "本文将", "本文认为", "本文分析", "以下将")
GENERIC_HEADINGS = ("交易过程", "交易逻辑", "可复用经验", "结论", "经验启示", "案例启示")
HYPOTHESIS_PATTERNS = (
    "假设", "推测", "猜测", "可能是", "或许", "大概", "预计将", "有望", "如果", "若未", "若能", "可能会", "不排除",
    "could", "may ", "might", "possibly",
)
TIME_PATTERNS = ("2025", "2026", "2024", "2023", "交割", "完成", "签约", "公告", "过户", "协议", "closing", "closed")
CONSIDERATION_PATTERNS = ("亿元", "亿美元", "万欧元", "亿欧元", "万元", "对价", "估值", "交易金额", "作价", "价格", "现金", "股份")
FINANCIAL_PATTERNS = ("营收", "收入", "营业收入", "净利润", "毛利", "EBITDA", "现金流", "负债", "市值", "产能", "订单", "用户", "员工", "股权", "资源量", "储量")
BUYER_MOTIVE_PATTERNS = ("买方动机", "收购动因", "并购方", "收购方", "愿意买", "收购目的", "战略目的", "为什么买", "选择收购")
SELLER_MOTIVE_PATTERNS = ("卖方动机", "出售原因", "愿意卖", "为什么卖", "出售方", "标的方", "转让方", "退出", "出售股权", "出让")
INTRO_PATTERNS = ("基本介绍", "主营", "主营业务", "业务", "收入", "净利润", "成立", "上市", "资产", "产品", "客户")
CN_NUMS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


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


def compact_name(value: str) -> str:
    value = re.sub(r"（.*?）|\(.*?\)", "", value or "")
    value = re.sub(r"股份有限公司|有限责任公司|有限公司|集团|控股|公司|Corporation|Inc\.|Inc|Ltd\.|Ltd|Limited|PLC", "", value, flags=re.I)
    return value.strip()


def name_in_text(name: str, text: str) -> bool:
    if not name:
        return False
    candidates = {name, compact_name(name)}
    candidates = {x for x in candidates if len(x) >= 2}
    return any(x in text for x in candidates)


def infer_parties_from_case_name(case_name: str) -> tuple[str, str]:
    parts = re.split(r"收购|并购|入主|吸收合并|私有化|合并|出售", case_name or "", maxsplit=1)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


def party_names_for_title(brief: CaseBrief) -> tuple[str, str]:
    acquirer = brief.acquirer or ""
    target = brief.target or ""
    if not acquirer or not target:
        inferred_a, inferred_t = infer_parties_from_case_name(brief.case_name)
        acquirer = acquirer or inferred_a
        target = target or inferred_t
    return compact_name(acquirer) or acquirer, compact_name(target) or target


def cn_number(index: int) -> str:
    return CN_NUMS[index] if 0 <= index < len(CN_NUMS) else str(index)


def strip_heading_number(heading: str) -> str:
    heading = str(heading or "").strip()
    heading = re.sub(r"^(第[一二三四五六七八九十0-9]+章\s*)", "", heading)
    heading = re.sub(r"^[一二三四五六七八九十0-9]+[、.．]\s*", "", heading)
    return heading.strip()


def ensure_title(article: dict[str, object], brief: CaseBrief) -> None:
    title = str(article.get("title") or brief.case_name or "并购案例研究").strip()
    acquirer, target = party_names_for_title(brief)
    if "：" not in title and ":" not in title:
        title = f"{title}：交易启示"
    missing_acquirer = acquirer and not name_in_text(acquirer, title)
    missing_target = target and not name_in_text(target, title)
    if missing_acquirer or missing_target:
        left = f"{acquirer or '并购方'}收购{target or '标的'}"
        right = "事实复盘"
        title = f"{left}：{right}"
    if title_length(title) > 36 and acquirer and target:
        title = f"{acquirer}收购{target}：交易启示"
    article["title"] = title


def ensure_sections(article: dict[str, object]) -> None:
    sections = article.get("sections") or []
    normalized: list[dict[str, Any]] = []
    if isinstance(sections, list):
        for sec in sections[:7]:
            if not isinstance(sec, dict):
                continue
            heading = str(sec.get("heading") or "").strip()
            paragraphs = sec.get("paragraphs") or []
            if not isinstance(paragraphs, list):
                paragraphs = [str(paragraphs)] if paragraphs else []
            clean_paras = [str(p).strip() for p in paragraphs if str(p).strip()]
            if heading and clean_paras:
                normalized.append({"heading": heading, "paragraphs": clean_paras})
    if len(normalized) < 4:
        normalized.append({"heading": "补充事实：交易信息仍需围绕公告核验", "paragraphs": ["本节保留用于承接交易日期、交易金额、交易双方基本情况与交易动机等关键事实，后续生成会继续依据公开资料补足。"]})
    total = len(normalized)
    for idx, sec in enumerate(normalized, start=1):
        body = strip_heading_number(str(sec.get("heading") or ""))
        number = cn_number(idx)
        if idx == total:
            if "结语" in body:
                suffix = body.split("结语", 1)[-1].lstrip("：: 　") or "交易启示回到事实与承接"
            else:
                suffix = "交易启示回到事实与承接"
            sec["heading"] = f"{number}、结语：{suffix}"
        else:
            body = re.sub(r"^结语[:：]?", "", body).strip() or "围绕事实展开交易复盘"
            sec["heading"] = f"{number}、{body}"
    article["sections"] = normalized


def sanitize_fact_language(article: dict[str, object]) -> None:
    replacements = {
        "本文": "本案例",
        "本报告": "本案例",
        "本文将": "本案例围绕公开资料",
        "本文认为": "公开资料显示",
        "有望": "相关安排指向",
        "或许": "公开资料未进一步披露",
        "大概": "约",
        "可能是": "公开资料显示为",
        "可能会": "相关安排指向",
    }
    def clean(value: str) -> str:
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    article["intro"] = clean(str(article.get("intro") or ""))
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if isinstance(sec, dict):
                sec["heading"] = clean(str(sec.get("heading") or ""))
                paragraphs = sec.get("paragraphs") or []
                if isinstance(paragraphs, list):
                    sec["paragraphs"] = [clean(str(p)) for p in paragraphs]


def postprocess_article(article: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    ensure_title(article, brief)
    ensure_sections(article)
    sanitize_fact_language(article)
    return article


def validate_article(article: dict[str, object], brief: CaseBrief) -> list[str]:
    article = postprocess_article(article, brief)
    issues: list[str] = []
    text = article_text(article)
    length = chinese_length(text)
    title = str(article.get("title") or "")
    acquirer = brief.acquirer or ""
    target = brief.target or ""
    inferred_a, inferred_t = infer_parties_from_case_name(brief.case_name)
    title_acquirer = acquirer or inferred_a
    title_target = target or inferred_t

    if length <= 3500:
        issues.append(f"成品字数不足，当前约 {length} 字，必须大于3500个中文字符。")
    if length >= 4000:
        issues.append(f"成品字数过长，当前约 {length} 字，必须小于4000个中文字符。")
    if title_length(title) > 36:
        issues.append(f"标题过长，当前约 {title_length(title)} 字，需压缩并保留交易双方。")
    if "：" not in title and ":" not in title:
        issues.append("标题需要采用主副标题形式，中间使用冒号。")
    if title_acquirer and not name_in_text(title_acquirer, title):
        issues.append(f"主副标题必须包含并购方名称或简称：{title_acquirer}。")
    if title_target and not name_in_text(title_target, title):
        issues.append(f"主副标题必须包含标的方名称或简称：{title_target}。")

    intro = str(article.get("intro") or "")[:120]
    if any(pattern in intro for pattern in BANNED_INTRO_PATTERNS):
        issues.append("引言出现'本文/本报告'等模板化表达，需要改为直接讲案例启示。")

    hypothesis_hits = [p for p in HYPOTHESIS_PATTERNS if p in text]
    if hypothesis_hits:
        issues.append("全文必须基于事实客观陈述，不得使用假设或推测性表述；需删除或改写这些词：" + "、".join(hypothesis_hits[:8]))

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
            if any(generic in strip_heading_number(heading) for generic in GENERIC_HEADINGS):
                issues.append(f"章节标题过于机械：{heading}，需要改为概括该章结论的标题。")
        expected_num = cn_number(len(sections))
        last_heading = str(sections[-1].get("heading") or "") if isinstance(sections[-1], dict) else ""
        if not last_heading.startswith(f"{expected_num}、结语：") and not last_heading.startswith(f"第{expected_num}章"):
            issues.append(f"最后一章标题必须按实际顺序编号为'{expected_num}、结语：副标题'或'第{expected_num}章 结语：副标题'。")

    digit_count = len(re.findall(r"\d", text))
    if digit_count < 35:
        issues.append("数据密度不足，需要补充交易对价、估值、比例、营收、净利润、时间节点等可核验数据。")
    if not any(pattern in text for pattern in TIME_PATTERNS):
        issues.append("缺少并购时间线，需要写明公告/签约/完成交割或过户时间。")
    if not any(pattern in text for pattern in CONSIDERATION_PATTERNS):
        issues.append("缺少交易对价或估值金额，需要写明交易金额、估值、作价或支付方式。")
    if sum(1 for pattern in FINANCIAL_PATTERNS if pattern in text) < 4:
        issues.append("财务和经营数据不足，需要加入买方或标的的收入、净利润、负债、现金流、产能、订单、员工、用户、资源量或股权比例等。")
    if sum(1 for pattern in BUYER_MOTIVE_PATTERNS if pattern in text) < 2:
        issues.append("缺少并购方/买方交易动机，需要明确写出并购方为什么愿意买。")
    if sum(1 for pattern in SELLER_MOTIVE_PATTERNS if pattern in text) < 2:
        issues.append("缺少标的方/卖方交易动机，需要明确写出被并购方或转让方为什么愿意卖。")
    if acquirer and not name_in_text(acquirer, text):
        issues.append(f"正文必须包含并购方基本介绍：{acquirer}。")
    if target and not name_in_text(target, text):
        issues.append(f"正文必须包含标的方基本介绍：{target}。")
    if sum(1 for pattern in INTRO_PATTERNS if pattern in text) < 5:
        issues.append("交易双方基本介绍不足，需要写清并购方和标的方的主营业务、资产/产品、财务或经营规模。")
    return issues


def trim_article(article: dict[str, object], max_chars: int = 3990) -> dict[str, object]:
    if chinese_length(article_text(article)) < max_chars:
        return article
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections[1:-1]:
            if not isinstance(sec, dict):
                continue
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                sec["paragraphs"] = [str(p)[:520] for p in paragraphs[:3]]
    return article


def build_prompt(brief: CaseBrief, research_rows: list[dict[str, str]], *, revision_issues: list[str] | None = None, previous_article: dict[str, object] | None = None) -> list[dict[str, str]]:
    revise_text = ""
    if revision_issues and previous_article:
        revise_text = (
            "\n需要基于上一版重写并逐条修复以下问题："
            + json.dumps(revision_issues, ensure_ascii=False)
            + "\n上一版："
            + json.dumps(previous_article, ensure_ascii=False)
        )
    return [
        {
            "role": "system",
            "content": (
                "你是资深并购案例研究作者，读者是上市公司董事长、CEO和产业集团负责人。"
                "必须只基于用户给定资料写作，不联网、不编造、不推测。只输出JSON，不输出Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请写一篇并购案例分析报告，要求像已上传案例中的研究文章，而不是短摘要。"
                "文章要服务于企业家决策：并购前如何判断标的、如何设计交易结构、哪些事实需要核验、投后如何承接。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n地区：{brief.region}"
                f"\n并购方：{brief.acquirer or '-'}"
                f"\n并购标的/出售方：{brief.target or '-'}"
                f"\n交易金额/估值：{brief.deal_value or '-'}"
                f"\n交易状态：{brief.deal_status or ('已完成，' + brief.completed_year if brief.completed_year else '-')}"
                f"\n买方动机：{brief.buyer_motivation or '-'}"
                f"\n卖方动机：{brief.seller_motivation or '-'}"
                f"\n财务和经营数据：{brief.financial_highlights or '-'}"
                f"\n线索标题：{brief.source_title}"
                f"\n线索链接：{brief.source_url}"
                f"\n选题理由：{brief.why}"
                f"\n是否经典案例：{brief.is_classic}"
                f"\n分类口径：{CATEGORY_GUIDE}"
                f"\n选题规则：{TOPIC_SELECTION_RULES}"
                f"\n写作规则：{STYLE_RULES}"
                f"\n参考写法：{REFERENCE_STYLE}"
                "\n可使用的公开资料线索如下。只能把它们作为事实来源；资料没有出现的具体数据不得写成确定事实。"
                f"\n资料线索：{json.dumps(research_rows, ensure_ascii=False)}"
                "\n输出JSON格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、概括性标题\",\"paragraphs\":[...]},...],\"sources\":[...]}。"
                "\n【硬性要求】标题必须包含并购方和标的方名称或简称，并采用主副标题形式，最好不超过30个中文字符。"
                "\n【硬性要求】全文必须客观陈述事实，不使用'假设、推测、可能是、或许、有望、如果、若能、若未'等推测表达。"
                "\n【硬性要求】正文必须涵盖：1）并购具体日期或时间线；2）交易金额/估值/支付方式/股权比例；3）并购方基本介绍；4）标的方基本介绍；5）并购方为什么买；6）标的方/卖方为什么卖。"
                "\n【硬性要求】章节数量4-7章，最后一章必须带实际顺序编号：若全文5章写'五、结语：副标题'，6章写'六、结语：副标题'，7章写'七、结语：副标题'。不要固定写五。"
                "\n【硬性要求】成品字数必须大于3500个中文字符、小于4000个中文字符。"
                "\n引言第一句不要出现'本文'或'本报告'，直接讲这个案例对CEO的借鉴价值。"
                + revise_text
            ),
        },
    ]


def normalize_article(payload: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    return postprocess_article({
        "case_name": str(payload.get("case_name") or brief.case_name),
        "category": str(payload.get("category") or brief.category),
        "title": str(payload.get("title") or brief.case_name),
        "intro": str(payload.get("intro") or ""),
        "sections": payload.get("sections") or [],
        "sources": payload.get("sources") or ([brief.source_url] if brief.source_url else []),
    }, brief)


def generate_article(brief: CaseBrief) -> dict[str, object]:
    research_items = collect_research_context(brief)
    research_rows = [item.to_dict() for item in research_items]
    LOGGER.info("Collected %s research items for report: %s", len(research_rows), brief.case_name)

    payload = chat_json(build_prompt(brief, research_rows), timeout=240)
    article = normalize_article(payload, brief)
    issues = validate_article(article, brief)
    for round_idx in range(4):
        if not issues:
            break
        LOGGER.info("Revising report %s round %s due to issues: %s", brief.case_name, round_idx + 1, issues)
        payload = chat_json(build_prompt(brief, research_rows, revision_issues=issues, previous_article=article), timeout=240)
        article = normalize_article(payload, brief)
        issues = validate_article(article, brief)
    article = postprocess_article(trim_article(article), brief)
    final_issues = validate_article(article, brief)
    if final_issues:
        LOGGER.warning("Report still has validation issues after robust postprocess: %s %s", brief.case_name, final_issues)
    return article
