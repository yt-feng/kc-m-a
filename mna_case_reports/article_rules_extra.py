"""Additional article post-processing rules.

This module wraps article_rules with fixes that are hard to express in prompting
alone: full-width quote conversion and repair of party-name first-mention
annotations that were inserted inside a longer company name.
"""

from __future__ import annotations

import re
from typing import Any

from . import article_rules as base
from .case_selection import CaseBrief

MIN_CHARS = base.MIN_CHARS
TARGET_MIN_CHARS = base.TARGET_MIN_CHARS
TARGET_MAX_CHARS = base.TARGET_MAX_CHARS
MAX_CHARS = base.MAX_CHARS
CJK = base.CJK
article_text = base.article_text
chinese_length = base.chinese_length
title_length = base.title_length
compact_name = base.compact_name
name_in_text = base.name_in_text
infer_parties_from_case_name = base.infer_parties_from_case_name
party_names_for_title = base.party_names_for_title
cn_number = base.cn_number
strip_heading_number = base.strip_heading_number
trim_article = base.trim_article
extract_research_fact_lines = base.extract_research_fact_lines

COMPANY_SUFFIX = r"(?:股份有限公司|有限责任公司|科技有限公司|娱乐集团|有限公司|集团|公司)"
LEGAL_NAME_TAIL = r"(?:科技有限公司|股份有限公司|有限责任公司|娱乐集团|有限公司|集团|公司)"
BAD_SPLIT_NOTE_PATTERN = re.compile(rf"([{CJK}A-Za-z0-9·&]+)（下称“([^”]+)”(?:，[^）]+)?）(?!收购|并购|购买|出售|转让|受让|入股|控股|合并)([{CJK}A-Za-z0-9·&]{{0,12}}{LEGAL_NAME_TAIL})")
DUP_NOTE_PATTERN = re.compile(r"(?P<note>（下称“[^”]+”(?:，[^）]+)?）)(?P=note)+")
ANY_COMPANY_NOTE_PATTERN = re.compile(r"（下称“[^”]+”(?:，[^）]+)?）")
KNOWN_COMPANY_NOTES = {
    "腾讯音乐娱乐集团": "腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME）",
    "Tencent Music Entertainment Group": "腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME）",
    "上海喜马拉雅科技有限公司": "上海喜马拉雅科技有限公司（下称“喜马拉雅”）",
    "Advanced Micro Devices": "Advanced Micro Devices, Inc.（下称“AMD”，NASDAQ：AMD）",
    "ZT Systems": "ZT Systems（下称“ZT Systems”）",
    "Intel Corporation": "Intel Corporation（下称“Intel”，NASDAQ：INTC）",
}


def convert_halfwidth_quotes(text: str) -> str:
    """Convert straight half-width quotes to Chinese full-width quotes."""
    text = str(text or "")
    out: list[str] = []
    double_open = True
    single_open = True
    for char in text:
        if char == '"':
            out.append("“" if double_open else "”")
            double_open = not double_open
        elif char == "'":
            out.append("‘" if single_open else "’")
            single_open = not single_open
        else:
            out.append(char)
    return "".join(out)


def normalize_ticker_punctuation(text: str) -> str:
    return (
        text.replace("NYSE:TME", "NYSE：TME")
        .replace("NYSE： TME", "NYSE：TME")
        .replace("NASDAQ:AMD", "NASDAQ：AMD")
        .replace("NASDAQ： AMD", "NASDAQ：AMD")
        .replace("NASDAQ:INTC", "NASDAQ：INTC")
        .replace("NASDAQ： INTC", "NASDAQ：INTC")
    )


def _strip_all_notes_after_company(text: str, full_name: str) -> str:
    """Remove one or more immediate short-name notes after a legal name."""
    escaped = re.escape(full_name)
    return re.sub(escaped + r"(?:（下称“[^”]+”(?:，[^）]+)?）)+", full_name, text)


def _collapse_duplicate_notes(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = DUP_NOTE_PATTERN.sub(lambda m: m.group("note"), text)
    return text


def _repair_split_notes(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = BAD_SPLIT_NOTE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(3)}（下称“{m.group(2)}”）", text)
        text = _collapse_duplicate_notes(text)
    return text


def _apply_known_company_notes(text: str, *, add_known_notes: bool) -> str:
    # First remove repeated notes after known legal names. Then add one canonical
    # note only where we actually want first-mention annotations.
    for full_name in KNOWN_COMPANY_NOTES:
        if full_name in text:
            text = _strip_all_notes_after_company(text, full_name)
    if add_known_notes:
        for full_name, canonical in KNOWN_COMPANY_NOTES.items():
            if full_name in text:
                text = text.replace(full_name, canonical, 1)
    return _collapse_duplicate_notes(text)


def _apply_known_company_notes_once(text: str, seen_notes: set[str]) -> str:
    text = _apply_known_company_notes(text, add_known_notes=False)
    for full_name, canonical in KNOWN_COMPANY_NOTES.items():
        if full_name not in text:
            continue
        if full_name in seen_notes:
            continue
        text = text.replace(full_name, canonical, 1)
        seen_notes.add(full_name)
    return _collapse_duplicate_notes(text)


def strip_company_notes(text: str) -> str:
    """Remove parenthetical short-name notes, useful for titles."""
    text = convert_halfwidth_quotes(str(text or ""))
    text = normalize_ticker_punctuation(text).replace("（下文简称“", "（下称“")
    text = _repair_split_notes(text)
    text = ANY_COMPANY_NOTE_PATTERN.sub("", text)
    return base.normalize_text(text)


def repair_party_annotation_text(text: str, *, add_known_notes: bool = True) -> str:
    """Repair malformed first-mention annotations.

    Examples fixed:
    - 腾讯音乐（下文简称“腾讯音乐”）娱乐集团 -> 腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME）
    - 上海喜马拉雅（下称“喜马拉雅”）科技有限公司（下称“喜马拉雅”）
      -> 上海喜马拉雅科技有限公司（下称“喜马拉雅”）
    - 腾讯音乐娱乐集团（下称“腾讯音乐”，NYSE：TME） repeated many times
      -> one canonical note only.
    """
    text = convert_halfwidth_quotes(str(text or ""))
    text = normalize_ticker_punctuation(text).replace("（下文简称“", "（下称“")
    text = _repair_split_notes(text)
    text = _apply_known_company_notes(text, add_known_notes=add_known_notes)
    return base.normalize_text(text)


def repair_party_annotation_text_once(text: str, seen_notes: set[str]) -> str:
    text = convert_halfwidth_quotes(str(text or ""))
    text = normalize_ticker_punctuation(text).replace("（下文简称“", "（下称“")
    text = _repair_split_notes(text)
    text = _apply_known_company_notes_once(text, seen_notes)
    return base.normalize_text(text)


def _walk_text_fields(article: dict[str, object]) -> None:
    # Titles should be concise and should not carry first-mention legal notes.
    article["title"] = strip_company_notes(str(article.get("title") or ""))
    seen_notes: set[str] = set()
    article["intro"] = repair_party_annotation_text_once(str(article.get("intro") or ""), seen_notes)
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec["heading"] = strip_company_notes(str(sec.get("heading") or ""))
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                sec["paragraphs"] = [repair_party_annotation_text_once(str(p), seen_notes) for p in paragraphs]


def postprocess_article(article: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    article = base.postprocess_article(article, brief)
    _walk_text_fields(article)
    return article


def _has_malformed_party_annotation(text: str) -> bool:
    if "下文简称" in text:
        return True
    if BAD_SPLIT_NOTE_PATTERN.search(text):
        return True
    # A short name note immediately followed by company-name suffix is almost always wrong.
    if re.search(r"（下称“[^”]+”(?:，[^）]+)?）(?:娱乐集团|科技有限公司|股份有限公司|有限责任公司|有限公司)", text):
        return True
    for full_name in KNOWN_COMPANY_NOTES:
        pattern = re.escape(full_name) + r"(?:（下称“[^”]+”(?:，[^）]+)?）){2,}"
        if re.search(pattern, text):
            return True
    if DUP_NOTE_PATTERN.search(text):
        return True
    return False


def _has_halfwidth_quote(text: str) -> bool:
    return '"' in text or "'" in text


def _article_body_text(article: dict[str, object]) -> str:
    parts = [str(article.get("intro") or "")]
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            parts.append(str(sec.get("heading") or ""))
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                parts.extend(str(p) for p in paragraphs)
    return "\n".join(parts)


def _missing_required_party_note(body_text: str, brief: CaseBrief) -> bool:
    for name in (brief.acquirer, brief.target):
        name = str(name or "").strip()
        if len(name) < 3:
            continue
        if not re.search(COMPANY_SUFFIX + r"$", name, flags=re.I) and not re.search(r"\b(?:Corporation|Inc\.?|Limited|Ltd\.?|PLC)\b", name, flags=re.I):
            continue
        idx = body_text.find(name)
        if idx < 0:
            continue
        after = body_text[idx + len(name): idx + len(name) + 48]
        if not after.startswith("（下称“"):
            return True
    return False


def validate_article(article: dict[str, object], brief: CaseBrief, *, strict_length: bool = True) -> list[str]:
    issues = base.validate_article(article, brief, strict_length=strict_length)
    postprocess_article(article, brief)
    text = article_text(article)
    body_text = _article_body_text(article)
    if _has_malformed_party_annotation(text):
        issues.append("公司首次出现的简称标注位置错误或重复，应写在完整公司名称之后且只出现一次，例如“上海喜马拉雅科技有限公司（下称“喜马拉雅”）”。")
    if _missing_required_party_note(body_text, brief):
        issues.append("公司名称首次出现时，应使用完整名称并在其后标注简称和股票代码（如上市）；资料没有披露股票代码时不得编造。")
    if _has_halfwidth_quote(text):
        issues.append("文中的引号需要使用全角中文引号“”。")
    return issues


def normalize_article_text_fields(article: dict[str, Any], brief: CaseBrief) -> dict[str, Any]:
    return postprocess_article(article, brief)
