"""Additional article post-processing rules.

This module wraps :mod:`article_rules` with fixes that are hard to express in
prompting alone: full-width quote conversion and repair of party-name first
mention annotations that were inserted inside a longer company name.
"""

from __future__ import annotations

import re
from typing import Any

from . import article_rules as base
from .case_selection import CaseBrief

# Re-export constants and helpers used by other modules.
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
append_until_min_length = base.append_until_min_length
trim_article = base.trim_article
extract_research_fact_lines = base.extract_research_fact_lines

COMPANY_SUFFIX = r"(?:股份有限公司|有限责任公司|科技有限公司|娱乐集团|有限公司|集团|公司)"
BAD_SPLIT_NOTE_PATTERN = re.compile(rf"([{CJK}A-Za-z0-9·]+)（下称“([^”]+)”）([{CJK}A-Za-z0-9·]+{COMPANY_SUFFIX})")
DUP_NOTE_PATTERN = re.compile(r"（下称“([^”]+)”）（下称“\1”）")


def convert_halfwidth_quotes(text: str) -> str:
    """Convert half-width straight quotes in generated Chinese prose to full-width quotes."""
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


def repair_party_annotation_text(text: str) -> str:
    """Repair malformed first-mention annotations.

    Examples fixed:
    - 腾讯音乐（下文简称“腾讯音乐”）娱乐集团 -> 腾讯音乐娱乐集团（下称“腾讯音乐”）
    - 上海喜马拉雅（下称“喜马拉雅”）科技有限公司（下称“喜马拉雅”）
      -> 上海喜马拉雅科技有限公司（下称“喜马拉雅”）
    """
    text = convert_halfwidth_quotes(str(text or ""))
    text = text.replace("（下文简称“", "（下称“")
    previous = None
    while previous != text:
        previous = text
        text = BAD_SPLIT_NOTE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(3)}（下称“{m.group(2)}”）", text)
        text = DUP_NOTE_PATTERN.sub(lambda m: f"（下称“{m.group(1)}”）", text)
    return base.normalize_text(text)


def _walk_text_fields(article: dict[str, object]) -> None:
    article["title"] = repair_party_annotation_text(str(article.get("title") or ""))
    article["intro"] = repair_party_annotation_text(str(article.get("intro") or ""))
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec["heading"] = repair_party_annotation_text(str(sec.get("heading") or ""))
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                sec["paragraphs"] = [repair_party_annotation_text(str(p)) for p in paragraphs]


def postprocess_article(article: dict[str, object], brief: CaseBrief) -> dict[str, object]:
    article = base.postprocess_article(article, brief)
    _walk_text_fields(article)
    return article


def _has_malformed_party_annotation(text: str) -> bool:
    if "下文简称" in text:
        return True
    # Detect an annotation followed immediately by more company-name text.
    if BAD_SPLIT_NOTE_PATTERN.search(text):
        return True
    if DUP_NOTE_PATTERN.search(text):
        return True
    return False


def _has_halfwidth_quote(text: str) -> bool:
    return '"' in text or "'" in text


def validate_article(article: dict[str, object], brief: CaseBrief, *, strict_length: bool = True) -> list[str]:
    issues = base.validate_article(article, brief, strict_length=strict_length)
    postprocess_article(article, brief)
    text = article_text(article)
    if _has_malformed_party_annotation(text):
        issues.append("公司首次出现的简称标注位置错误，应写在完整公司名称之后，例如'上海喜马拉雅科技有限公司（下称“喜马拉雅”）'。")
    if _has_halfwidth_quote(text):
        issues.append("文中的引号需要使用全角中文引号“”。")
    return issues


def normalize_article_text_fields(article: dict[str, Any], brief: CaseBrief) -> dict[str, Any]:
    """Public helper for tests or future migration."""
    return postprocess_article(article, brief)
