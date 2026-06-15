"""Quality checks for M&A case report depth and narrative structure.

These checks complement hard formatting/fact validation. They focus on whether
the report reads like a thoughtful case study rather than a templated summary.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .article_rules_extra import article_text, strip_heading_number

TEMPLATE_HEADINGS = (
    "关键事实与交易进程",
    "关键事实、交易进程与披露边界",
    "交易双方的业务基础与披露信息",
    "价格、支付方式与交割条件安排",
    "交割后的治理、业务与人员承接",
    "业务承接、治理安排与后续观察",
    "从公开事实回到执行关注点",
)

TEMPLATE_PHRASES = (
    "公开资料能够直接复核的事实包括",
    "这些信息决定了复盘边界",
    "从收购方角度看",
    "从出售方或被整合方角度看",
    "数据层面的复核重点包括",
    "相关安排的后续观察重点",
    "只能回到公开资料",
)

WEAK_TITLE_PHRASES = (
    "交易复盘", "案例分析", "并购案例", "交易启示", "交易观察", "并购启示", "案例研究",
    "一文看懂", "深度解析", "全面复盘", "重大交易",
)
GENERIC_CONCLUSION_PHRASES = (
    "并购不是终点",
    "整合才是开始",
    "协同不是口号",
    "时间会给出答案",
    "值得长期关注",
    "只有真正整合才能释放价值",
    "对企业具有重要启示",
    "为同类交易提供了借鉴",
    "具有参考意义",
    "未来仍需观察",
)

MEDIA_TONE_EXAMPLES = (
    "悬疑", "尖厉问号", "尖锐问号", "杠杆悬河", "治理真空", "协同的虚实",
    "陡坡", "搏杀", "资本游戏", "暗礁", "蒸发", "拉响", "抛出", "剑指",
    "闪电成立", "新壳", "杠杆入主", "撬动", "迷局", "戏码", "狂飙", "风暴",
)

INDUSTRY_TERMS = (
    "行业", "产业", "产业链", "竞争格局", "市场格局", "供需", "周期", "渗透率", "客户结构",
    "技术路线", "商业模式", "产品结构", "区域市场", "监管环境", "上市平台", "资源禀赋",
)
STRUCTURE_TERMS = (
    "对价", "估值", "作价", "支付方式", "现金支付", "股份支付", "股权比例", "控制权", "表决权",
    "交割条件", "先决条件", "业绩承诺", "锁定期", "融资安排", "并表", "治理安排", "要约",
)
METHODOLOGY_TERMS = (
    "方法论", "核验", "尽调", "估值锚", "交易执行", "交割承接", "治理边界", "整合节奏",
    "信息披露", "承接能力", "协同边界", "执行条件", "风险隔离", "条款设计", "复盘",
)
FINANCIAL_TERMS = (
    "收入", "营收", "净利润", "毛利", "EBITDA", "现金流", "负债", "资产", "市值", "估值倍数",
    "利润率", "资产负债率", "订单", "产能", "客户", "用户",
)
ORIGIN_BACKGROUND_TERMS = (
    "交易前", "此前", "原控股股东", "股权结构", "控制权状态", "无实际控制人", "经营压力",
    "业务状态", "产业位置", "主业", "上市平台", "分散", "承压", "转型",
)
ORIGIN_INITIATION_TERMS = (
    "发起", "启动", "公告", "披露", "签署", "董事会", "股东会", "决议", "要约", "协议",
    "预案", "报告书", "监管", "过户", "交割", "触发",
)
ORIGIN_OBJECTIVE_TERMS = (
    "目的", "目标", "旨在", "为了", "意在", "希望", "取得控制权", "提升持股", "内部整合",
    "产业协同", "资产注入", "退出", "变现", "优化资本结构", "补强", "并表", "治理",
)


def _sections(article: dict[str, object]) -> list[dict[str, Any]]:
    sections = article.get("sections") or []
    return [s for s in sections if isinstance(s, dict)] if isinstance(sections, list) else []


def _heading_texts(article: dict[str, object]) -> list[str]:
    return [strip_heading_number(str(sec.get("heading") or "")) for sec in _sections(article)]


def _paragraphs(article: dict[str, object]) -> list[str]:
    paras: list[str] = []
    for sec in _sections(article):
        paragraphs = sec.get("paragraphs") or []
        if isinstance(paragraphs, list):
            paras.extend(str(p) for p in paragraphs if str(p).strip())
    return paras


def _last_section_text(article: dict[str, object]) -> str:
    sections = _sections(article)
    if not sections:
        return ""
    last = sections[-1]
    parts = [str(last.get("heading") or "")]
    paragraphs = last.get("paragraphs") or []
    if isinstance(paragraphs, list):
        parts.extend(str(p) for p in paragraphs)
    return "\n".join(parts)


def _early_text(article: dict[str, object]) -> str:
    parts = [str(article.get("intro") or "")]
    for sec in _sections(article)[:2]:
        parts.append(str(sec.get("heading") or ""))
        paragraphs = sec.get("paragraphs") or []
        if isinstance(paragraphs, list):
            parts.extend(str(p) for p in paragraphs[:3])
    return "\n".join(parts)


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in text)


def _tone_hits(text: str) -> list[str]:
    return [term for term in MEDIA_TONE_EXAMPLES if term in text]


def _has_causal_analysis(text: str) -> bool:
    return any(token in text for token in ("原因在于", "这意味着", "其约束在于", "真正关键", "底层逻辑", "对应的是", "不是", "而是", "因此", "从而"))


def _has_high_acceptance_contradiction(text: str) -> bool:
    high_ratio_context = re.search(r"(?:预受|接受|受要约|要约)[\s\S]{0,180}(?:9\d(?:\.\d+)?%|100%|九成|超过九成|逾九成)", text)
    if not high_ratio_context:
        return False
    contradiction_context = re.search(r"(?:预受率|接受率|接受|预受|受要约)[\s\S]{0,260}(?:严重不足|明显不足|远低于|不被市场接受|接受不足)", text)
    return bool(contradiction_context)


def _template_heading_count(headings: list[str]) -> int:
    count = 0
    for heading in headings:
        if any(template in heading for template in TEMPLATE_HEADINGS):
            count += 1
    return count


def _paragraph_openings(paragraphs: list[str]) -> Counter[str]:
    openings = []
    for para in paragraphs:
        stripped = re.sub(r"^（?\d+[）.]?", "", para.strip())
        openings.append(stripped[:8])
    return Counter(openings)


def _title_quality_issues(article: dict[str, object]) -> list[str]:
    title = str(article.get("title") or "")
    issues: list[str] = []
    if any(phrase in title for phrase in WEAK_TITLE_PHRASES):
        issues.append("标题仍然平淡或模板化，不能只写'交易复盘/案例分析/交易启示'，应点出本案核心交易逻辑或分析重点。")
    if "：" not in title:
        issues.append("标题需要采用主副标题形式，并让副标题承担分析重点，而不是空泛概括。")
    after_colon = title.split("：", 1)[-1] if "：" in title else title
    if len(after_colon) < 6:
        issues.append("标题副标题信息量不足，需要突出对价结构、控制权、业务承接、财务影响或产业逻辑之一。")
    if not any(term in title for term in STRUCTURE_TERMS + INDUSTRY_TERMS + FINANCIAL_TERMS + ("承接", "协同", "治理", "平台", "私有化", "控制权")):
        issues.append("标题缺少核心交易逻辑关键词，需要体现交易结构、产业位置、控制权、财务影响或承接重点。")
    return issues


def assess_quality(article: dict[str, object]) -> list[str]:
    """Return issues that indicate template-like structure or shallow analysis."""
    issues: list[str] = []
    text = article_text(article)
    headings = _heading_texts(article)
    paragraphs = _paragraphs(article)

    issues.extend(_title_quality_issues(article))

    prominent_tone_hits = _tone_hits(str(article.get("title") or "") + "\n" + "\n".join(headings))
    body_tone_hits = _tone_hits(text)
    if prominent_tone_hits:
        issues.append(
            "标题或章节标题偏媒体化、戏剧化；这些词只是风格问题示例，不要机械替换，而应重写为专业克制的交易结构、产业位置、财务影响或治理安排表述："
            + "、".join(prominent_tone_hits[:8])
            + "。"
        )
    elif len(body_tone_hits) >= 3:
        issues.append(
            "正文出现较多媒体化、戏剧化表达；请在不改变事实的前提下重写相关段落，使语气接近专业并购案例研究，而不是新闻报道或评论标题："
            + "、".join(body_tone_hits[:8])
            + "。"
        )

    template_heading_count = _template_heading_count(headings)
    if template_heading_count >= 2:
        issues.append("文章结构仍像固定模板：多个章节标题来自默认框架，需要按本案例最有信息量的材料重新组织结构。")

    phrase_hits = [phrase for phrase in TEMPLATE_PHRASES if text.count(phrase) >= 1]
    if len(phrase_hits) >= 2:
        issues.append("正文出现较多模板化连接语，需要改成围绕案例事实推进的自然叙述，而不是'公开资料显示/从某角度看'式堆叠。")

    opening_counts = _paragraph_openings(paragraphs)
    repeated_openings = [opening for opening, count in opening_counts.items() if opening and count >= 3]
    if repeated_openings:
        issues.append("段落开头重复，行文模式化；需要调整段落推进方式，避免每段都用相同句式开头。")

    para_counts = [len(sec.get("paragraphs") or []) for sec in _sections(article)]
    if len(para_counts) >= 4 and len(set(para_counts[:-1])) <= 1 and (template_heading_count >= 1 or bool(repeated_openings)):
        issues.append("各章节段落数量过于整齐，结构像模板；应根据材料重点调整章节长短。")

    depth_categories = 0
    industry_count = _count_terms(text, INDUSTRY_TERMS)
    structure_count = _count_terms(text, STRUCTURE_TERMS)
    methodology_count = _count_terms(text, METHODOLOGY_TERMS)
    financial_count = _count_terms(text, FINANCIAL_TERMS)
    if industry_count >= 4:
        depth_categories += 1
    if structure_count >= 5:
        depth_categories += 1
    if methodology_count >= 4:
        depth_categories += 1
    if financial_count >= 5:
        depth_categories += 1
    if depth_categories < 3:
        issues.append("分析深度不足：需要同时展开至少三个层面，例如产业判断、交易结构、财务影响、交割承接或并购方法论意义。")
    weak_core_layers = 0
    if industry_count < 3:
        weak_core_layers += 1
    if structure_count < 4:
        weak_core_layers += 1
    if methodology_count < 3:
        weak_core_layers += 1
    if weak_core_layers >= 2:
        issues.append("产业判断、交易结构分析和并购方法论意义没有写足，需要围绕本案事实展开，而不是只描述交易过程。")

    paragraph_lengths = [len(re.sub(r"\s+", "", p)) for p in paragraphs]
    long_paragraphs = [p for p in paragraphs if len(re.sub(r"\s+", "", p)) >= 260]
    average_para_len = sum(paragraph_lengths) / len(paragraph_lengths) if paragraph_lengths else 0
    if len(long_paragraphs) < 2 and average_para_len < 210:
        issues.append("长段分析不足，文章更像摘要；需要增加若干连续论证段，解释事实之间的因果关系和方法论意义。")

    if not any(term in text for term in ("方法论", "同类并购", "执行条件", "核验", "治理边界", "交割承接", "整合节奏", "条款安排")):
        issues.append("缺少并购方法论意义，需要把案例事实提炼为同类交易可参考的资料核验、条款安排或交割承接经验。")

    if not any(term in text for term in ("产业链", "行业", "竞争格局", "客户结构", "技术路线", "商业模式", "资源禀赋", "产业位置")):
        issues.append("缺少产业层面的判断，需要结合标的所处行业、客户/产品/资源位置或竞争格局解释交易意义。")
    if not _has_causal_analysis(text):
        issues.append("文章缺少因果分析和判断句，需要解释为什么这些交易事实会影响估值、条款、交割或整合结果。")
    if _has_high_acceptance_contradiction(text):
        issues.append("正文存在数值逻辑矛盾：预受/接受比例已超过九成时，不得再写预受率严重不足、远低于预期或不被市场接受。")

    early_text = _early_text(article)
    if _count_terms(early_text, ORIGIN_BACKGROUND_TERMS) < 1:
        issues.append("文章前部没有交代交易前状态，需要说明交易发生前的股权结构、业务状态、经营压力、产业位置或控制权状态。")
    if _count_terms(early_text, ORIGIN_INITIATION_TERMS) < 2:
        issues.append("文章前部没有讲清交易如何发起，需要说明触发交易的公告、协议、要约、董事会/股东会决议、监管文件或推进路径。")
    if _count_terms(early_text, ORIGIN_OBJECTIVE_TERMS) < 1:
        issues.append("文章前部没有说明交易目标，需要解释为什么要做这笔交易，以及希望实现取得控制权、内部整合、产业协同、退出变现、优化资本结构或补强业务等哪类目标。")

    last_text = _last_section_text(article)
    if any(phrase in last_text for phrase in GENERIC_CONCLUSION_PHRASES):
        issues.append("结语出现泛泛表达，需要紧扣本案例的交易双方、对价结构、业务承接和披露事实，而不是通用口号。")
    if last_text and _count_terms(last_text, STRUCTURE_TERMS + METHODOLOGY_TERMS + FINANCIAL_TERMS + INDUSTRY_TERMS) < 5:
        issues.append("结语/启示深度不足，需要回到本案例的交易结构、财务数据、产业位置、交割承接或方法论意义。")
    if last_text and not _has_causal_analysis(last_text):
        issues.append("结语缺少判断和解释力，需要说明本案事实如何推导出同类并购的方法论启示。")

    return issues
