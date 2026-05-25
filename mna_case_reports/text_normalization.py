"""Text normalization helpers for Chinese M&A reports."""

from __future__ import annotations

import re

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


def normalize_fullwidth_punctuation(text: str) -> str:
    """Use full-width Chinese punctuation in running Chinese text."""
    text = str(text or "")
    text = text.replace("...", "……")
    text = text.replace("--", "——")
    text = text.translate(ASCII_TO_FULLWIDTH)
    # Convert ASCII period only when it is not a decimal point or part of an English abbreviation.
    text = re.sub(r"(?<!\d)\.(?!\d)", "。", text)
    return text


def remove_cn_alnum_spaces(text: str) -> str:
    """Remove spaces between Chinese characters and English words or digits."""
    text = re.sub(r"([\u4e00-\u9fff])\s+([A-Za-z0-9])", r"\1\2", text)
    text = re.sub(r"([A-Za-z0-9])\s+([\u4e00-\u9fff])", r"\1\2", text)
    return text


def add_thousands_separators(text: str) -> str:
    """Add commas to long integer amounts and quantities.

    Avoid decimal numbers, years, stock codes and already-separated numbers.
    The function runs before punctuation is converted, so commas are later
    converted to full-width Chinese commas in normal prose.
    """
    pattern = re.compile(r"(?<![\d,\.])\d{5,}(?![\d,\.])")

    def repl(match: re.Match[str]) -> str:
        value = match.group(0)
        # Do not format likely stock codes or dates.
        if len(value) in {6, 8}:
            return value
        try:
            return f"{int(value):,}"
        except ValueError:
            return value

    return pattern.sub(repl, text)


def normalize_report_text(text: str) -> str:
    text = str(text or "")
    text = add_thousands_separators(text)
    text = remove_cn_alnum_spaces(text)
    text = normalize_fullwidth_punctuation(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def normalize_article_text_fields(article: dict[str, object]) -> dict[str, object]:
    article["title"] = normalize_report_text(str(article.get("title") or ""))
    article["intro"] = normalize_report_text(str(article.get("intro") or ""))
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            section["heading"] = normalize_report_text(str(section.get("heading") or ""))
            paragraphs = section.get("paragraphs") or []
            if isinstance(paragraphs, list):
                section["paragraphs"] = [normalize_report_text(str(p)) for p in paragraphs if str(p).strip()]
    return article
