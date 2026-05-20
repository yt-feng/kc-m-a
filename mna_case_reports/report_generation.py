"""Article generation for M&A case analysis reports."""

from __future__ import annotations

import json
import re

from .case_selection import CaseBrief
from .config import CATEGORY_GUIDE, STYLE_RULES
from .deepseek_client import chat_json


def chinese_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def trim_article(article: dict[str, object], max_chars: int = 4000) -> dict[str, object]:
    # The prompt asks the model to stay under the limit. This is a final guardrail.
    text = json.dumps(article, ensure_ascii=False)
    if chinese_length(text) <= max_chars + 300:
        return article
    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            paragraphs = sec.get("paragraphs") or []
            if isinstance(paragraphs, list):
                sec["paragraphs"] = [str(p)[:520] for p in paragraphs[:3]]
    return article


def generate_article(brief: CaseBrief) -> dict[str, object]:
    messages = [
        {"role": "system", "content": "你是并购案例研究作者。只输出 JSON，不输出 Markdown。"},
        {
            "role": "user",
            "content": (
                "请写一篇并购案例分析报告，面向有并购需求的企业决策者。"
                "不要使用表格，不超过4000个中文字符，语气客观、中性、克制。"
                "不要出现政治化、宏观敏感或广告式表述，不给公司打广告，不使用明显负面标签。"
                f"\n案例：{brief.case_name}"
                f"\n分类：{brief.category}"
                f"\n地区：{brief.region}"
                f"\n线索标题：{brief.source_title}"
                f"\n线索链接：{brief.source_url}"
                f"\n选题理由：{brief.why}"
                f"\n分类口径：{CATEGORY_GUIDE}"
                f"\n写作规则：{STYLE_RULES}"
                "\n输出 JSON 格式：{\"case_name\":...,\"category\":...,\"title\":\"主标题：副标题\",\"intro\":...,\"sections\":[{\"heading\":\"一、...\",\"paragraphs\":[...]},...],\"sources\":[...]}。"
                "\n四个 sections 固定为：一、交易过程；二、交易逻辑；三、可复用经验；四、结论。"
                "\n事实不确定处要用限定语，不能编造具体金额、日期或监管结果。"
            ),
        },
    ]
    payload = chat_json(messages)
    article = {
        "case_name": str(payload.get("case_name") or brief.case_name),
        "category": str(payload.get("category") or brief.category),
        "title": str(payload.get("title") or brief.case_name),
        "intro": str(payload.get("intro") or ""),
        "sections": payload.get("sections") or [],
        "sources": payload.get("sources") or [brief.source_url] if brief.source_url else [],
    }
    return trim_article(article)
