"""Post-processing and validation rules for M&A case report articles."""

from __future__ import annotations

import re
from typing import Any

from .case_selection import CaseBrief

MIN_CHARS = 3500
TARGET_MIN_CHARS = 3550
TARGET_MAX_CHARS = 3800
MAX_CHARS = 4000

CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
BANNED_INTRO_PATTERNS = ("本文", "本报告", "本文将", "本文认为", "本文分析", "以下将")
BANNED_AUDIENCE_PATTERNS = (
    "上市公司CEO", "上市公司ceo", "上市公司的CEO", "上市公司的ceo",
    "上市公司董事长", "董事长和CEO", "董事长/CEO", "CEO和董事长", "CEO/董事长",
    "对CEO而言", "对董事长而言", "对上市公司管理者而言", "读者是", "面向上市公司",
)
GENERIC_HEADINGS = ("交易过程", "交易逻辑", "可复用经验", "结论", "经验启示", "案例启示")
GENERIC_CONCLUSION_PHRASES = (
    "并购不是终点",
    "整合才是开始",
    "协同不是口号",
    "时间会给出答案",
    "值得长期关注",
    "只有真正整合才能释放价值",
    "对企业具有重要启示",
    "为同类交易提供了借鉴",
)
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
CONSIDERATION_PATTERNS = ("亿元", "亿美元", "万欧元", "亿欧元", "万元", "元/股", "港元/股", "美元/股", "对价", "估值", "交易金额", "作价", "价格", "现金", "股份")
FINANCIAL_PATTERNS = ("营收", "收入", "营业收入", "净利润", "毛利", "EBITDA", "现金流", "负债", "市值", "产能", "订单", "用户", "员工", "股权", "资源量", "储量")
BUYER_MOTIVE_PATTERNS = ("买方", "收购方", "并购方", "购买", "收购目的", "战略目的", "补强", "整合", "协同", "控股", "并表")
SELLER_MOTIVE_PATTERNS = ("卖方", "出售方", "标的方", "转让方", "被整合方", "退出", "出售股权", "出让", "接受", "承接", "私有化", "预受要约", "接受要约", "现金要约", "协议转让", "减持", "股东账户")
INTRO_PATTERNS = ("基本介绍", "主营", "主营业务", "业务", "收入", "净利润", "成立", "上市", "资产", "产品", "客户")
CN_NUMS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
LOCATION_PREFIXES = (
    "北京市", "上海市", "天津市", "重庆市", "深圳市", "广州市", "苏州市", "长沙市", "杭州市", "南京市",
    "成都市", "武汉市", "宁波市", "厦门市", "青岛市", "合肥市", "无锡市", "常州市", "北京市", "上海",
    "北京", "深圳", "广州", "苏州", "长沙", "杭州", "南京", "成都", "武汉", "宁波", "厦门", "青岛", "合肥",
    "无锡", "常州",
)
GENERIC_ALIAS_PARTS = (
    "科技发展", "管理咨询", "数码科技", "物流技术", "测试技术", "食品包装", "国际控股", "科技集团",
    "科技", "技术", "发展", "投资", "控股", "集团",
)

ASCII_TO_FULLWIDTH = str.maketrans({
    ",": "，",
    ";": "；",
    ":": "：",
    "?": "？",
    "!": "！",
    "(": "（",
    ")": "）",
    "[": "【",
    "]": "】",
})


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
    value = re.sub(
        r"科技发展合伙企业|管理咨询合伙企业|合伙企业|有限合伙|股份有限公司|有限责任公司|有限公司|集团|控股|公司|"
        r"Corporation|Inc\.|Inc|Ltd\.|Ltd|Limited|PLC",
        "",
        value,
        flags=re.I,
    )
    return value.strip()


def name_aliases(name: str) -> set[str]:
    compact = compact_name(name)
    aliases = {name, compact}
    suffix_only = re.sub(
        r"(股份有限公司|有限责任公司|有限公司|公司)$",
        "",
        name or "",
        flags=re.I,
    ).strip()
    if suffix_only:
        aliases.add(suffix_only)
    for part in re.split(r"[、,，/和及与]+", compact):
        part = part.strip()
        if len(part) >= 3:
            aliases.add(part)
    location_stripped = compact
    for prefix in LOCATION_PREFIXES:
        if location_stripped.startswith(prefix) and len(location_stripped) - len(prefix) >= 3:
            location_stripped = location_stripped[len(prefix):]
            aliases.add(location_stripped)
            break
    suffix_location_stripped = suffix_only
    for prefix in LOCATION_PREFIXES:
        if suffix_location_stripped.startswith(prefix) and len(suffix_location_stripped) - len(prefix) >= 3:
            aliases.add(suffix_location_stripped[len(prefix):])
            break
    for base in {compact, location_stripped}:
        shortened = base
        for generic in GENERIC_ALIAS_PARTS:
            shortened = shortened.replace(generic, "")
        if len(shortened) >= 3:
            aliases.add(shortened)
        if "食品包装" in base and len(base.replace("食品", "")) >= 3:
            aliases.add(base.replace("食品", ""))
    if len(compact) >= 4:
        aliases.add(compact[:4])
        aliases.add(compact[-4:])
        aliases.add(compact[:2] + compact[-2:])
    if len(location_stripped) >= 4:
        aliases.add(location_stripped[:4])
        aliases.add(location_stripped[-4:])
    if len(compact) >= 5:
        aliases.add(compact[:5])
        aliases.add(compact[-5:])
    if len(compact) >= 6:
        aliases.add(compact[:6])
        aliases.add(compact[-6:])
    return {alias for alias in aliases if len(alias) >= 3}


def name_in_text(name: str, text: str) -> bool:
    if not name:
        return False
    return any(alias in text for alias in name_aliases(name))


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


def remove_cjk_alnum_spaces(text: str) -> str:
    text = re.sub(rf"([{CJK}])\s+([A-Za-z0-9])", r"\1\2", text)
    text = re.sub(rf"([A-Za-z0-9])\s+([{CJK}])", r"\1\2", text)
    text = re.sub(rf"([{CJK}])\s+([{CJK}])", r"\1\2", text)
    return text


def remove_spaces_around_cjk_punctuation(text: str) -> str:
    text = re.sub(r"\s+([，。；：？！、）】》])", r"\1", text)
    text = re.sub(r"([（【《])\s+", r"\1", text)
    text = re.sub(rf"([）】》])\s+([{CJK}A-Za-z0-9])", r"\1\2", text)
    text = re.sub(rf"([{CJK}A-Za-z0-9])\s+([（【《])", r"\1\2", text)
    text = re.sub(r"(\d(?:,\d{3})*(?:\.\d+)?)\s+([%％])", r"\1\2", text)
    return text


def normalize_fullwidth_punctuation(text: str) -> str:
    text = text.replace("“ ", "“").replace(" ”", "”").replace("‘ ", "‘").replace(" ’", "’")
    text = re.sub(rf"([{CJK}%％）】]),(?!\d)", r"\1，", text)
    text = re.sub(rf"(?<!\d),([{CJK}])", r"，\1", text)
    for ascii_p, full_p in ((";", "；"), (":", "："), ("?", "？"), ("!", "！")):
        text = re.sub(rf"([{CJK}0-9%％）】])\{ascii_p}", rf"\1{full_p}", text)
        text = re.sub(rf"\{ascii_p}([{CJK}])", rf"{full_p}\1", text)
    text = re.sub(rf"([{CJK}])\(", r"\1（", text)
    text = re.sub(rf"\)([{CJK}])", r"）\1", text)
    text = re.sub(rf"([{CJK}])\[", r"\1【", text)
    text = re.sub(rf"\]([{CJK}])", r"】\1", text)
    text = text.replace(" ,", "，").replace(" ;", "；")
    return text


def _format_number_with_commas(match: re.Match[str]) -> str:
    raw = match.group(0)
    if len(raw) <= 3 or raw.startswith("0"):
        return raw
    return f"{int(raw):,}"


QUANTITY_UNITS = (
    "元", "美元", "港元", "欧元", "人民币", "万元", "亿元", "亿美元", "亿港元", "亿欧元", "万欧元",
    "股", "万股", "亿股", "人", "名", "户", "家", "个", "项", "台", "辆", "吨", "平方米", "平方英尺",
    "MW", "GW", "GWh", "MWh", "million", "billion", "bn", "mn",
)
QUANTITY_UNIT_RE = "|".join(re.escape(unit) for unit in sorted(QUANTITY_UNITS, key=len, reverse=True))
UNFORMATTED_QUANTITY_RE = re.compile(rf"(?<![\d.,])\d{{4,}}(?![\d.,])(?=\s*(?:{QUANTITY_UNIT_RE}))")


def format_thousands(text: str) -> str:
    text = UNFORMATTED_QUANTITY_RE.sub(_format_number_with_commas, text)
    return text


def _trim_text_piece(text: str, target_len: int, *, min_len: int = 180) -> str:
    text = str(text or "")
    text = normalize_text(text)
    if len(text) <= target_len:
        return text
    target_len = max(min_len, target_len)
    cut = target_len
    window_start = max(min_len, target_len - 60)
    for punct in ("。", "；", "，", "、"):
        idx = text.rfind(punct, window_start, target_len)
        if idx >= window_start:
            cut = idx + 1
            break
    trimmed = text[:cut].rstrip("，；、：: ")
    if trimmed and trimmed[-1] not in "。！？；":
        trimmed += "。"
    return normalize_text(trimmed)


def normalize_text(text: str) -> str:
    text = re.sub(r"[\s\u3000]+", " ", str(text or "")).strip()
    previous = None
    while previous != text:
        previous = text
        text = remove_cjk_alnum_spaces(text)
        text = remove_spaces_around_cjk_punctuation(text)
    text = normalize_fullwidth_punctuation(text)
    text = format_thousands(text)
    return text


def ensure_title(article: dict[str, object], brief: CaseBrief) -> None:
    article["title"] = normalize_text(str(article.get("title") or "").strip())


def improve_heading_text(body: str, *, is_last: bool = False) -> str:
    return normalize_text(body.strip(" ：:"))


def ensure_sections(article: dict[str, object]) -> None:
    sections = article.get("sections") or []
    normalized: list[dict[str, Any]] = []
    if isinstance(sections, list):
        for sec in sections[:7]:
            if not isinstance(sec, dict):
                continue
            heading = normalize_text(str(sec.get("heading") or "").strip())
            paragraphs = sec.get("paragraphs") or []
            if not isinstance(paragraphs, list):
                paragraphs = [str(paragraphs)] if paragraphs else []
            clean_paras = [normalize_text(str(p)) for p in paragraphs if str(p).strip()]
            if heading and clean_paras:
                normalized.append({"heading": heading, "paragraphs": clean_paras})
    total = len(normalized)
    for idx, sec in enumerate(normalized, start=1):
        body = improve_heading_text(strip_heading_number(str(sec.get("heading") or "")), is_last=(idx == total))
        number = cn_number(idx)
        if idx == total and "结语" in body:
            suffix = body.split("结语", 1)[-1].lstrip("：: 　")
            sec["heading"] = f"{number}、结语：{suffix}"
        else:
            sec["heading"] = f"{number}、{body}"
    article["sections"] = normalized


def _replace_all(value: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def sanitize_fact_language(article: dict[str, object]) -> None:
    replacements = {
        "本文将": "本案例围绕公开资料",
        "本文认为": "公开资料显示",
        "本文分析": "本案例复盘",
        "本文": "本案例",
        "本报告": "本案例",
        "假设": "情形",
        "推测": "判断",
        "猜测": "判断",
        "有望": "相关安排指向",
        "或许": "公开资料未进一步披露",
        "大概": "约",
        "预计将": "计划",
        "不排除": "存在",
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
        value = _replace_all(value, audience_replacements)
        value = _replace_all(value, replacements)
        value = re.sub(r"\b(?:could|might|possibly)\b", "", value, flags=re.I)
        value = re.sub(r"\bmay\b", "", value, flags=re.I)
        return normalize_text(value)

    article["intro"] = clean(str(article.get("intro") or ""))
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if isinstance(sec, dict):
                sec["heading"] = clean(str(sec.get("heading") or ""))
                paragraphs = sec.get("paragraphs") or []
                if isinstance(paragraphs, list):
                    sec["paragraphs"] = [clean(str(p)) for p in paragraphs]


def annotate_party_first_mentions(article: dict[str, object], brief: CaseBrief) -> None:
    parties: list[tuple[str, str]] = []
    for name in (brief.acquirer, brief.target):
        if not name:
            continue
        short = compact_name(name) or name
        if len(short) >= 2:
            parties.append((name, short))

    def annotate_text(text: str, name: str, short: str) -> tuple[str, bool]:
        idx = text.find(name)
        if idx < 0:
            return text, False
        after = text[idx + len(name): idx + len(name) + 1]
        if after == "（":
            return text, True
        note = f"{name}（下称“{short}”）"
        return text[:idx] + note + text[idx + len(name):], True

    for name, short in parties:
        done = False
        intro = str(article.get("intro") or "")
        intro, done = annotate_text(intro, name, short)
        article["intro"] = intro
        if done:
            continue
        sections = article.get("sections") or []
        if isinstance(sections, list):
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                paragraphs = sec.get("paragraphs") or []
                if not isinstance(paragraphs, list):
                    continue
                for i, para in enumerate(paragraphs):
                    new_para, done = annotate_text(str(para), name, short)
                    paragraphs[i] = new_para
                    if done:
                        break
                if done:
                    break


def postprocess_article(article: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    ensure_title(article, brief)
    ensure_sections(article)
    sanitize_fact_language(article)
    annotate_party_first_mentions(article, brief)
    ensure_sections(article)
    return article


def has_cjk_alnum_space(text: str) -> bool:
    return bool(re.search(rf"([{CJK}])\s+([A-Za-z0-9])|([A-Za-z0-9])\s+([{CJK}])", text))


def has_ascii_punct_near_cjk(text: str) -> bool:
    return bool(re.search(rf"([{CJK}])[,;:!?()\[\]]|[,;:!?()\[\]]([{CJK}])", text))


def has_unformatted_quantity_number(text: str) -> bool:
    return bool(UNFORMATTED_QUANTITY_RE.search(text))


def section_concreteness_score(text: str, brief: CaseBrief) -> int:
    score = 0
    if re.search(r"\d", text):
        score += 1
    acquirer, target = party_names_for_title(brief)
    if name_in_text(acquirer, text) or name_in_text(target, text):
        score += 1
    concrete_terms = (
        "对价", "估值", "支付", "股权", "控制权", "并表", "交割", "过户", "公告", "协议",
        "收入", "营收", "净利润", "现金流", "负债", "产能", "订单", "客户", "用户", "员工",
        "技术", "专利", "资源", "门店", "平台", "监管", "审批", "锁定期", "业绩承诺",
    )
    if sum(1 for term in concrete_terms if term in text) >= 3:
        score += 1
    return score


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
        issues.append(f"成品字数不足，当前约 {length} 字，必须不少于3500个中文字符。")
    if strict_length and length > MAX_CHARS:
        issues.append(f"成品字数过长，当前约 {length} 字，必须小于4000个中文字符。")
    if title_length(title) > 40:
        issues.append(f"标题过长，当前约 {title_length(title)} 字，需压缩并保留交易双方。")
    if "：" not in title and ":" not in title:
        issues.append("标题需要采用主副标题形式，中间使用冒号。")
    if title_acquirer and not name_in_text(title_acquirer, title):
        issues.append(f"主副标题必须包含并购方名称或简称：{title_acquirer}。")
    if title_target and not name_in_text(title_target, title):
        issues.append(f"主副标题必须包含标的方名称或简称：{title_target}。")

    if has_cjk_alnum_space(text):
        issues.append("中文字符与英文单词或数字之间不应添加空格。")
    if has_ascii_punct_near_cjk(text):
        issues.append("全文应使用一致的全角中文标点。")
    if has_unformatted_quantity_number(text):
        issues.append("金额、数量等数字应添加千分位逗号，例如1,100名员工、12,500万元。")

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
            paragraphs = sec.get("paragraphs") or []
            paragraph_text = "\n".join(str(p) for p in paragraphs) if isinstance(paragraphs, list) else str(paragraphs or "")
            sec_text = heading + "\n" + paragraph_text
            if any(generic in heading for generic in GENERIC_HEADINGS):
                issues.append(f"章节标题过于机械：{heading}，需要改为客观概括该章事实和关注点的标题。")
            if any(pattern in heading for pattern in HEADING_THINKING_PATTERNS):
                issues.append(f"章节标题出现思考过程词汇：{heading}，不要把交易动机/交易背景/交易结构设计等直接写进标题。")
            if any(pattern in heading for pattern in BANNED_TONE_PATTERNS):
                issues.append(f"章节标题表达不够客观中性：{heading}。")
            if len(re.sub(r"\s+", "", sec_text)) >= 260 and section_concreteness_score(sec_text, brief) < 2:
                issues.append(f"章节分析过于泛化，需要把判断落回本案例的主体、金额/比例、资产、客户、治理或交割事实：{heading[:24]}。")
        expected_num = cn_number(len(sections))
        last_heading = str(sections[-1].get("heading") or "") if isinstance(sections[-1], dict) else ""
        if not last_heading.startswith(f"{expected_num}、结语：") and not last_heading.startswith(f"第{expected_num}章"):
            issues.append(f"最后一章标题必须按实际顺序编号为'{expected_num}、结语：副标题'或'第{expected_num}章 结语：副标题'。")
        else:
            suffix = strip_heading_number(last_heading).split("结语", 1)[-1].lstrip("：: 　")
            if len(re.sub(r"\s+", "", suffix)) < 6:
                issues.append("结语标题必须采用'结语：副标题'，副标题要概括本案特有的方法论启示，不能只写'结语'。")
        if isinstance(sections[-1], dict):
            last_paragraphs = sections[-1].get("paragraphs") or []
            last_paragraph_text = "\n".join(str(p) for p in last_paragraphs) if isinstance(last_paragraphs, list) else str(last_paragraphs or "")
            last_text = last_heading + "\n" + last_paragraph_text
            if any(phrase in last_text for phrase in GENERIC_CONCLUSION_PHRASES):
                issues.append("结语/启示部分存在泛泛口号，必须紧扣本案例的交易双方、对价结构、产业位置、业务承接和披露事实。")
            if section_concreteness_score(last_text, brief) < 3:
                issues.append("结语/启示深度不足，需要回到本案例的交易结构、财务数据、产业位置、交割承接或并购方法论意义。")
            if (title_acquirer and not name_in_text(title_acquirer, last_text)) or (title_target and not name_in_text(title_target, last_text)):
                issues.append("结语需要点名回到本案例交易双方，而不是写成通用并购总结。")

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
        issues.append("缺少标的方/出售方接受交易安排的原因或客观安排依据，需要明确写出被并购方、转让方或预受要约股东为什么愿意卖、接受整合，或公开资料能够支撑的交易机制。")
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
        current_length = chinese_length(article_text(article))
        overage = current_length - max_chars
        candidates: list[tuple[int, int, int, int, str]] = []
        intro = str(article.get("intro") or "")
        if intro.strip():
            candidates.append((2, len(intro), -1, -1, intro))
        for si, sec in enumerate(sections):
            if not isinstance(sec, dict):
                continue
            paragraphs = sec.get("paragraphs") or []
            if not isinstance(paragraphs, list):
                continue
            if si == 0:
                priority = 1
            elif si == len(sections) - 1:
                priority = 3
            else:
                priority = 0
            for pi, para in enumerate(paragraphs):
                para_text = str(para)
                if para_text.strip():
                    candidates.append((priority, len(para_text), si, pi, para_text))
        candidates.sort(key=lambda item: (item[0], -item[1]))
        chosen: tuple[int, int, int, int, str] | None = None
        for priority, para_len, si, pi, para_text in candidates:
            min_keep = 140 if si < 0 else (180 if priority != 2 else 140)
            if para_len <= min_keep + 40:
                continue
            chosen = (priority, para_len, si, pi, para_text)
            break
        if chosen is None:
            break
        priority, para_len, si, pi, para = chosen
        min_keep = 140 if si < 0 else (180 if priority != 2 else 140)
        trim_by = min(max(40, overage + 20), para_len - min_keep)
        target_len = para_len - trim_by
        if si < 0:
            article["intro"] = _trim_text_piece(para, target_len, min_len=min_keep)
        else:
            sections[si]["paragraphs"][pi] = _trim_text_piece(para, target_len, min_len=min_keep)
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
                part = normalize_text(part.strip(" -•\t "))
                if len(part) < 20 or part in seen:
                    continue
                if not re.search(r"\d", part) and not any(token in part for token in ("亿元", "亿美元", "完成", "收购", "股权", "收入", "净利润")):
                    continue
                seen.add(part)
                lines.append(part[:180])
                if len(lines) >= limit:
                    return lines
    return lines
