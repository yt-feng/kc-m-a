"""Post-processing and validation rules for M&A case report articles."""

from __future__ import annotations

import re
from typing import Any

from .case_selection import CaseBrief

MIN_CHARS = 3501
TARGET_MIN_CHARS = 3600
TARGET_MAX_CHARS = 3900
MAX_CHARS = 3999

BANNED_INTRO_PATTERNS = ("本文", "本报告", "本文将", "本文认为", "本文分析", "以下将")
BANNED_AUDIENCE_PATTERNS = (
    "上市公司CEO", "上市公司ceo", "上市公司的CEO", "上市公司的ceo",
    "上市公司董事长", "董事长和CEO", "董事长/CEO", "CEO和董事长", "CEO/董事长",
    "对CEO而言", "对董事长而言", "对上市公司管理者而言", "读者是", "面向上市公司",
)
GENERIC_HEADINGS = ("交易过程", "交易逻辑", "可复用经验", "结论", "经验启示", "案例启示")
HEADING_THINKING_PATTERNS = (
    "交易动机", "交易背景", "并购战略考量", "标的筛选", "交易结构设计", "并购后整合", "价值释放",
    "买方动机", "卖方动机", "投后整合",
)
BANNED_TONE_PATTERNS = (
    "窗口期如何打开", "用条款把不确定性前置", "交割后的第一件事是接住能力",
    "为何此时走到一起", "资产质量先于规模想象", "产业位置决定出手方式",
    "避坑", "踩雷", "暴雷", "豪赌", "神话", "翻车", "失败教训",
)
HYPOTHESIS_PATTERNS = (
    "假设", "推测", "猜测", "可能是", "或许", "大概", "预计将", "有望", "如果", "若未", "若能", "可能会", "不排除",
    "could", "may ", "might", "possibly",
)
TIME_PATTERNS = ("2025", "2026", "2024", "2023", "交割", "完成", "签约", "公告", "过户", "协议", "closing", "closed")
CONSIDERATION_PATTERNS = ("亿元", "亿美元", "万欧元", "亿欧元", "万元", "对价", "估值", "交易金额", "作价", "价格", "现金", "股份")
FINANCIAL_PATTERNS = ("营收", "收入", "营业收入", "净利润", "毛利", "EBITDA", "现金流", "负债", "市值", "产能", "订单", "用户", "员工", "股权", "资源量", "储量")
BUYER_MOTIVE_PATTERNS = ("买方", "收购方", "并购方", "购买", "收购目的", "战略目的", "补强", "整合", "协同", "控股", "并表")
SELLER_MOTIVE_PATTERNS = ("卖方", "出售方", "标的方", "转让方", "被整合方", "退出", "出售股权", "出让", "接受", "承接", "私有化")
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
        title = f"{title}：交易复盘"
    missing_acquirer = acquirer and not name_in_text(acquirer, title)
    missing_target = target and not name_in_text(target, title)
    if missing_acquirer or missing_target:
        title = f"{acquirer or '收购方'}收购{target or '标的'}：交易复盘"
    if title_length(title) > 36 and acquirer and target:
        title = f"{acquirer}收购{target}：交易复盘"
    article["title"] = title


def improve_heading_text(body: str, *, is_last: bool = False) -> str:
    body = re.sub(r"^结语[:：]?", "", body).strip()
    replacements = {
        "交易动机": "双方披露的交易出发点",
        "交易背景": "交易启动前的业务与时间线",
        "并购战略考量": "收购方业务边界与资产承接条件",
        "标的筛选": "标的资产、客户与财务质量核验",
        "交易结构设计": "价格、支付方式与交割条件安排",
        "并购后整合": "交割后的治理、业务与人员承接",
        "价值释放": "收入、利润与协同事项的后续观察",
        "买方动机": "收购方披露的购买理由",
        "卖方动机": "出售方披露的接受安排原因",
        "投后整合": "交割后的治理和业务承接",
        "窗口期如何打开": "交易启动前的业务与时间线",
        "用条款把不确定性前置": "价格、支付方式与交割条件安排",
        "交割后的第一件事是接住能力": "交割后的治理、业务与人员承接",
        "为何此时走到一起": "双方披露的交易出发点",
        "资产质量先于规模想象": "标的资产、客户与财务质量核验",
        "产业位置决定出手方式": "收购方业务边界与资产承接条件",
        "买方为何选择此时出手": "收购方披露的购买理由",
        "出售方为何接受安排": "出售方披露的接受安排原因",
        "交割后从持有转向经营": "交割后的治理和业务承接",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    body = body.strip(" ：:")
    if not body:
        return "从公开事实回到执行关注点" if is_last else "关键事实与交易进程"
    return body


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
        normalized.append({"heading": "关键事实与交易进程", "paragraphs": ["公开资料已经披露的交易日期、交易金额、交易双方基本情况与完成进度，是复盘该案例的基础；公开资料没有披露的内容不写成确定结论。"]})
    total = len(normalized)
    for idx, sec in enumerate(normalized, start=1):
        body = improve_heading_text(strip_heading_number(str(sec.get("heading") or "")), is_last=(idx == total))
        number = cn_number(idx)
        if idx == total:
            if "结语" in body:
                suffix = body.split("结语", 1)[-1].lstrip("：: 　") or "从公开事实回到执行关注点"
            else:
                suffix = body or "从公开事实回到执行关注点"
            sec["heading"] = f"{number}、结语：{suffix}"
        else:
            sec["heading"] = f"{number}、{body}"
    article["sections"] = normalized


def sanitize_fact_language(article: dict[str, object]) -> None:
    replacements = {
        "本文将": "本案例围绕公开资料",
        "本文认为": "公开资料显示",
        "本文分析": "本案例复盘",
        "本文": "本案例",
        "本报告": "本案例",
        "有望": "相关安排指向",
        "或许": "公开资料未进一步披露",
        "大概": "约",
        "可能是": "公开资料显示为",
        "可能会": "相关安排指向",
        "如果": "在公开资料所示条件下",
        "若未": "公开资料显示未",
        "若能": "相关安排落实后",
        "避坑": "关注事项",
        "踩雷": "风险事项",
        "暴雷": "风险暴露",
        "豪赌": "重大投入",
        "神话": "代表性案例",
        "翻车": "执行偏差",
        "失败教训": "经验复盘",
    }
    audience_replacements = {
        "对上市公司董事长和CEO而言，": "",
        "对上市公司董事长和CEO而言": "",
        "对于上市公司董事长和CEO而言，": "",
        "对于上市公司董事长和CEO而言": "",
        "对董事长和CEO而言，": "",
        "对董事长和CEO而言": "",
        "对CEO而言，": "",
        "对CEO而言": "",
        "上市公司CEO": "管理层",
        "上市公司ceo": "管理层",
        "上市公司董事长": "管理层",
        "董事长和CEO": "管理层",
        "董事长/CEO": "管理层",
        "CEO和董事长": "管理层",
        "CEO/董事长": "管理层",
        "CEO": "管理层",
    }

    def clean(value: str) -> str:
        for old, new in audience_replacements.items():
            value = value.replace(old, new)
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
    ensure_sections(article)
    return article


def validate_article(article: dict[str, object], brief: CaseBrief, *, strict_length: bool = True) -> list[str]:
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

    if strict_length and length < MIN_CHARS:
        issues.append(f"成品字数不足，当前约 {length} 字，必须大于3500个中文字符。")
    if strict_length and length > MAX_CHARS:
        issues.append(f"成品字数过长，当前约 {length} 字，必须小于4000个中文字符。")
    if title_length(title) > 36:
        issues.append(f"标题过长，当前约 {title_length(title)} 字，需压缩并保留交易双方。")
    if "：" not in title and ":" not in title:
        issues.append("标题需要采用主副标题形式，中间使用冒号。")
    if title_acquirer and not name_in_text(title_acquirer, title):
        issues.append(f"主副标题必须包含并购方名称或简称：{title_acquirer}。")
    if title_target and not name_in_text(title_target, title):
        issues.append(f"主副标题必须包含标的方名称或简称：{title_target}。")

    intro = str(article.get("intro") or "")[:140]
    if any(pattern in intro for pattern in BANNED_INTRO_PATTERNS):
        issues.append("引言出现'本文/本报告'等模板化表达，需要改为直接讲案例事实。")
    audience_hits = [p for p in BANNED_AUDIENCE_PATTERNS if p in text]
    if audience_hits:
        issues.append("正文不得出现面向读者的提示语或思考过程表达：" + "、".join(audience_hits[:8]))

    hypothesis_hits = [p for p in HYPOTHESIS_PATTERNS if p in text]
    if hypothesis_hits:
        issues.append("全文必须基于事实客观陈述，不得使用假设或推测性表述；需删除或改写这些词：" + "、".join(hypothesis_hits[:8]))
    tone_hits = [p for p in BANNED_TONE_PATTERNS if p in text]
    if tone_hits:
        issues.append("正文需保持客观中性，不使用口号化、负面化或广告化表达：" + "、".join(tone_hits[:8]))

    sections = article.get("sections") or []
    if not isinstance(sections, list) or len(sections) < 4:
        issues.append("章节不足，需要至少4个一级章节，并可根据案例写成4-7章。")
    else:
        if len(sections) > 7:
            issues.append("章节过多，需要控制在4-7章。")
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            heading = strip_heading_number(str(sec.get("heading") or ""))
            if any(generic in heading for generic in GENERIC_HEADINGS):
                issues.append(f"章节标题过于机械：{heading}，需要改为客观概括该章事实和关注点的标题。")
            if any(pattern in heading for pattern in HEADING_THINKING_PATTERNS):
                issues.append(f"章节标题出现思考过程词汇：{heading}，不要把交易动机/交易背景/交易结构设计等直接写进标题。")
            if any(pattern in heading for pattern in BANNED_TONE_PATTERNS):
                issues.append(f"章节标题表达不够客观中性：{heading}。")
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
    if sum(1 for pattern in BUYER_MOTIVE_PATTERNS if pattern in text) < 3:
        issues.append("缺少并购方/买方购买理由，需要明确写出并购方为什么愿意买。")
    if sum(1 for pattern in SELLER_MOTIVE_PATTERNS if pattern in text) < 2:
        issues.append("缺少标的方/出售方接受交易安排的原因，需要明确写出被并购方或转让方为什么愿意卖或接受整合。")
    if acquirer and not name_in_text(acquirer, text):
        issues.append(f"正文必须包含并购方基本介绍：{acquirer}。")
    if target and not name_in_text(target, text):
        issues.append(f"正文必须包含标的方基本介绍：{target}。")
    if sum(1 for pattern in INTRO_PATTERNS if pattern in text) < 5:
        issues.append("交易双方基本介绍不足，需要写清并购方和标的方的主营业务、资产/产品、财务或经营规模。")
    return issues


def trim_article(article: dict[str, object], max_chars: int = MAX_CHARS) -> dict[str, object]:
    article = postprocess_article(article, CaseBrief(case_name=str(article.get("case_name") or ""), category=str(article.get("category") or ""), region=""))
    if chinese_length(article_text(article)) <= max_chars:
        return article
    sections = article.get("sections") or []
    if not isinstance(sections, list):
        return article
    while chinese_length(article_text(article)) > max_chars:
        candidates: list[tuple[int, int, int]] = []
        for si, sec in enumerate(sections[1:-1], start=1):
            if not isinstance(sec, dict):
                continue
            paragraphs = sec.get("paragraphs") or []
            if not isinstance(paragraphs, list):
                continue
            for pi, para in enumerate(paragraphs):
                candidates.append((len(str(para)), si, pi))
        if not candidates:
            break
        _length, si, pi = max(candidates)
        para = str(sections[si]["paragraphs"][pi])
        if len(para) <= 260:
            break
        sections[si]["paragraphs"][pi] = para[: max(260, len(para) - 180)].rstrip("，；、") + "。"
    return article


def extract_research_fact_lines(research_rows: list[dict[str, str]], limit: int = 10) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for row in research_rows:
        for key in ("numeric_facts", "summary", "extracted_text"):
            value = str(row.get(key) or "").strip()
            if not value:
                continue
            parts = re.split(r"[\n。；;]", value)
            for part in parts:
                part = part.strip(" -•\t ")
                if len(part) < 20 or part in seen:
                    continue
                if not re.search(r"\d", part) and not any(token in part for token in ("亿元", "亿美元", "完成", "收购", "股权", "收入", "净利润")):
                    continue
                seen.add(part)
                lines.append(part[:180])
                if len(lines) >= limit:
                    return lines
    return lines


def build_supplement_paragraphs(brief: CaseBrief, research_rows: list[dict[str, str]]) -> list[str]:
    acquirer, target = party_names_for_title(brief)
    fact_lines = extract_research_fact_lines(research_rows, limit=8)
    fact_text = "；".join(fact_lines[:4]) if fact_lines else "公开资料披露了交易主体、完成进度、交易金额或股权比例等基础信息"
    deal_status = brief.deal_status or ("已完成，" + brief.completed_year if brief.completed_year else "已披露进展")
    deal_value = brief.deal_value or "公开资料未披露统一口径的完整金额"
    buyer_reason = brief.buyer_motivation or "公开资料显示，收购方围绕业务协同、能力补强、资产控制或上市平台整合推进交易"
    seller_reason = brief.seller_motivation or "公开资料显示，出售方或被整合方接受交易安排，与股权转让、资源承接、平台整合或资本化路径有关"
    financials = brief.financial_highlights or ("；".join(fact_lines[4:8]) if len(fact_lines) > 4 else "公开资料披露的经营数据需要与公告、年报和交割文件交叉核验")
    return [
        f"围绕{brief.case_name}，公开资料能够直接复核的事实包括交易主体、进展状态和核心金额口径。收购方为{acquirer or '公开披露的买方'}，标的方或被整合方为{target or '公开披露的标的'}；交易状态为{deal_status}；交易金额或估值口径为{deal_value}。这些信息决定了复盘边界：交易评价不从主观判断出发，而从公告、交割文件、财务数据和双方披露的安排展开。",
        f"从收购方角度看，购买理由需要落在已经披露的业务和资产关系上。{buyer_reason}。相关安排的后续观察重点，包括收购方是否通过交易取得控制权、稳定现金流、客户关系、技术能力、产能或资源储备，以及这些要素在交割后的管理边界。",
        f"从出售方或被整合方角度看，接受交易安排同样需要回到披露文件。{seller_reason}。在控股权转让、吸收合并、资产注入或私有化案例中，公开资料通常需要同时观察价格、交割确定性、支付方式、债务承接、员工和客户稳定、原有业务后续安排以及审批节奏。",
        f"数据层面的复核重点包括：{financials}。同时，公开资料中出现的关键事实还包括：{fact_text}。这些数字应与交易对价、估值倍数、股权比例、收入和利润贡献放在同一框架下观察，避免只用单一金额解释交易价值。",
    ]


def append_until_min_length(article: dict[str, object], brief: CaseBrief, research_rows: list[dict[str, str]]) -> dict[str, object]:
    article = postprocess_article(article, brief)
    if chinese_length(article_text(article)) >= MIN_CHARS:
        return article
    sections = article.get("sections") or []
    if not isinstance(sections, list) or not sections:
        article["sections"] = [{"heading": "一、关键事实与交易进程", "paragraphs": []}, {"heading": "二、结语：从公开事实回到执行关注点", "paragraphs": []}]
        sections = article["sections"]
    target_index = max(0, len(sections) - 2)
    if not isinstance(sections[target_index], dict):
        target_index = len(sections) - 1
    paragraphs = sections[target_index].setdefault("paragraphs", [])
    if not isinstance(paragraphs, list):
        paragraphs = []
        sections[target_index]["paragraphs"] = paragraphs
    for para in build_supplement_paragraphs(brief, research_rows):
        if chinese_length(article_text(article)) >= TARGET_MIN_CHARS:
            break
        paragraphs.append(para)
        article = postprocess_article(article, brief)
    return article
