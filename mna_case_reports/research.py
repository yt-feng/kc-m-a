"""Research context collection for M&A case reports.

DeepSeek does not browse the web during generation, so this module collects the
raw facts it needs beforehand: search snippets, official PDF text, HTML page
snippets, dates, transaction values, and financial/operating data.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from mna_weekly_tracker.sources_fixed import unwrap_news_url
from mna_weekly_tracker.sources_rich import fetch_bing_news, fetch_google_news

from .case_selection import CaseBrief

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36"
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024

DATA_KEYWORDS = (
    "交易", "对价", "作价", "估值", "金额", "收购", "购买", "出售", "股权", "股份", "现金", "支付", "交割", "过户",
    "完成", "公告", "签署", "协议", "营收", "收入", "营业收入", "净利润", "EBITDA", "毛利", "现金流", "负债",
    "资产", "产能", "订单", "客户", "员工", "用户", "专利", "市值", "估值倍数", "承诺", "业绩承诺",
    "consideration", "valuation", "purchase price", "revenue", "net income", "ebitda", "closing", "closed",
)

MONEY_PATTERN = re.compile(r"(?:人民币|RMB|US\$|USD|HK\$|港币|美元|欧元|亿元|亿美元|亿欧元|万欧元|万元|million|billion|bn|mn)")
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|亿元|亿美元|亿欧元|万欧元|万元|万股|亿股|股|倍|年|月|日|人|名|家|项|GWh|MW|GW|million|billion|bn|mn)?")


@dataclass
class ResearchItem:
    title: str
    url: str
    source_name: str
    published_at: str
    summary: str
    query: str
    evidence_type: str = "search_snippet"
    numeric_facts: str = ""
    extracted_text: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def safe_get(url: str, *, timeout: int = 20) -> requests.Response | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8"},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        return response
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Research fetch failed: url=%s error=%s", url, exc)
        return None


def read_limited_content(response: requests.Response, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def is_pdf_url(url: str, content_type: str = "") -> bool:
    return url.lower().split("?", 1)[0].endswith(".pdf") or "pdf" in content_type.lower()


def extract_pdf_text(url: str, *, max_pages: int = 10, max_chars: int = 12000) -> str:
    response = safe_get(url, timeout=30)
    if response is None:
        return ""
    content_type = response.headers.get("content-type", "")
    if not is_pdf_url(response.url, content_type):
        return ""
    content = read_limited_content(response)
    if not content.startswith(b"%PDF") and b"%PDF" not in content[:1024]:
        return ""
    try:
        reader = PdfReader(BytesIO(content))
        texts: list[str] = []
        for page in reader.pages[:max_pages]:
            try:
                texts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return normalize_space("\n".join(texts))[:max_chars]
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("PDF parse failed: url=%s error=%s", url, exc)
        return ""


def extract_html_text(url: str, *, max_chars: int = 10000) -> str:
    response = safe_get(url, timeout=20)
    if response is None:
        return ""
    content_type = response.headers.get("content-type", "")
    if is_pdf_url(response.url, content_type):
        return extract_pdf_text(response.url)
    content = read_limited_content(response, max_bytes=3 * 1024 * 1024)
    if not content:
        return ""
    try:
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        main_text = soup.get_text(" ")
        return normalize_space(main_text)[:max_chars]
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("HTML parse failed: url=%s error=%s", url, exc)
        return ""


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；;.!?])\s*|\n+", text)
    return [p.strip() for p in parts if p and len(p.strip()) >= 12]


def score_sentence(sentence: str, case_terms: list[str]) -> int:
    score = 0
    if NUMBER_PATTERN.search(sentence):
        score += 4
    if MONEY_PATTERN.search(sentence):
        score += 4
    score += sum(2 for kw in DATA_KEYWORDS if kw.lower() in sentence.lower())
    score += sum(3 for term in case_terms if term and term.lower() in sentence.lower())
    if len(sentence) > 260:
        score -= 2
    return score


def extract_data_snippets(text: str, brief: CaseBrief, *, max_snippets: int = 12, max_chars: int = 2600) -> tuple[str, str]:
    case_terms = [brief.case_name, brief.acquirer, brief.target]
    scored = [(score_sentence(s, case_terms), s) for s in split_sentences(text)]
    chosen = [s for score, s in sorted(scored, key=lambda x: x[0], reverse=True) if score >= 4]
    snippets = []
    seen: set[str] = set()
    for sent in chosen:
        key = sent[:80]
        if key in seen:
            continue
        seen.add(key)
        snippets.append(sent)
        if len(snippets) >= max_snippets:
            break
    snippet_text = "\n".join(f"- {s}" for s in snippets)[:max_chars]
    facts = []
    for sent in snippets:
        if MONEY_PATTERN.search(sent) or NUMBER_PATTERN.search(sent):
            facts.append(sent)
    numeric_facts = "\n".join(f"- {s}" for s in facts[:8])[:1800]
    return snippet_text, numeric_facts


def bing_web_url(query: str) -> str:
    return "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "mkt": "zh-CN", "setlang": "zh-cn"})


def fetch_bing_web_results(query: str, *, limit: int = 8) -> list[ResearchItem]:
    response = safe_get(bing_web_url(query), timeout=15)
    if response is None:
        return []
    content = read_limited_content(response, max_bytes=2 * 1024 * 1024)
    soup = BeautifulSoup(content, "html.parser")
    items: list[ResearchItem] = []
    for node in soup.select("li.b_algo")[:limit]:
        a = node.select_one("h2 a") or node.select_one("a")
        if not a:
            continue
        title = normalize_space(a.get_text(" "))
        url = a.get("href") or ""
        if not title or not url or not url.startswith("http"):
            continue
        snippet = normalize_space(" ".join(p.get_text(" ") for p in node.select("p")))
        items.append(ResearchItem(title, url, "Bing Web - report research", "", snippet[:500], query, "web_search"))
    return items


def seed_research_item(brief: CaseBrief) -> ResearchItem:
    seed_lines = [
        f"案例：{brief.case_name}",
        f"并购方：{brief.acquirer or '-'}",
        f"并购标的：{brief.target or '-'}",
        f"交易金额/估值：{brief.deal_value or '-'}",
        f"交易状态/完成年份：{brief.deal_status or ('已完成，' + brief.completed_year if brief.completed_year else '-')}",
        f"买方动机：{brief.buyer_motivation or '-'}",
        f"卖方动机：{brief.seller_motivation or '-'}",
        f"财务和经营数据：{brief.financial_highlights or '-'}",
        f"选题理由：{brief.why or '-'}",
    ]
    seed_text = "\n".join(seed_lines)
    return ResearchItem(
        title=brief.source_title or brief.case_name,
        url=unwrap_news_url(brief.source_url),
        source_name="case_brief_structured_seed",
        published_at=brief.published_at,
        summary=seed_text,
        query="structured_seed",
        evidence_type="structured_seed",
        numeric_facts=seed_text,
        extracted_text=seed_text,
    )


def enrich_research_items(items: list[ResearchItem], brief: CaseBrief, *, max_fetches: int = 10) -> list[ResearchItem]:
    enriched: list[ResearchItem] = []
    fetched = 0
    for item in items:
        enriched.append(item)
        if fetched >= max_fetches or not item.url:
            continue
        url_lower = item.url.lower()
        should_fetch = any(token in url_lower for token in ("pdf", "cninfo", "static.cninfo", "hkexnews", "sec.gov", "sse.com.cn", "szse.cn"))
        should_fetch = should_fetch or any(kw in item.title + item.summary for kw in ("公告", "报告书", "交易", "收购", "财务", "估值", "对价"))
        if not should_fetch:
            continue
        fetched += 1
        extracted = extract_pdf_text(item.url) or extract_html_text(item.url)
        if not extracted:
            continue
        snippets, numeric_facts = extract_data_snippets(extracted, brief)
        if not snippets and not numeric_facts:
            continue
        evidence_type = "pdf_extract" if is_pdf_url(item.url) else "page_extract"
        enriched.append(
            ResearchItem(
                title=f"{item.title} - 原文抽取",
                url=item.url,
                source_name=f"{item.source_name} / {evidence_type}",
                published_at=item.published_at,
                summary=snippets or numeric_facts,
                query=item.query,
                evidence_type=evidence_type,
                numeric_facts=numeric_facts,
                extracted_text=snippets,
            )
        )
    return enriched


def collect_research_context(brief: CaseBrief, *, lookback_days: int = 3650, limit: int = 36, expanded: bool = False) -> list[ResearchItem]:
    """Collect public snippets, full-page extracts, and PDF excerpts for a case."""
    end = datetime.now(BEIJING_TZ).replace(microsecond=0)
    start = end - timedelta(days=lookback_days)
    parties = " ".join(x for x in (brief.acquirer, brief.target) if x)
    base = f"{brief.case_name} {parties}".strip()
    queries = [
        base,
        f"{base} 交易金额 对价 估值 支付方式 股权比例",
        f"{base} 完成交割 完成过户 公告 签约时间",
        f"{base} 财务数据 营收 净利润 EBITDA 负债 现金流",
        f"{base} 报告书 PDF 公告 收购 重大资产重组",
        f"{base} annual report revenue net income deal value",
        f"{base} filetype:pdf acquisition merger consideration financials",
    ]
    if expanded:
        queries.extend([
            f"{brief.acquirer} {brief.target} 交易结构 估值 对价",
            f"{brief.acquirer} {brief.target} 产业链 客户 产能 技术 订单",
            f"{brief.acquirer} {brief.target} 管理层 留任 交割 整合",
            f"{brief.acquirer} {brief.target} annual report investor presentation acquisition consideration",
            f"{brief.acquirer} {brief.target} SEC filing HKEX announcement cninfo 重组报告书",
        ])
    if brief.source_title:
        queries.append(brief.source_title)

    seen: set[str] = set()
    out: list[ResearchItem] = [seed_research_item(brief)]
    if brief.source_url:
        seen.add(unwrap_news_url(brief.source_url))

    for query in queries:
        per_query_limit = 10 if expanded else 6
        for item in fetch_bing_web_results(query, limit=per_query_limit):
            if item.url in seen:
                continue
            seen.add(item.url)
            out.append(item)
        for item in fetch_google_news(query, start, end, source_name="Google News - report research", source_url="https://news.google.com/", region_hint=brief.region):
            if item.url in seen:
                continue
            seen.add(item.url)
            out.append(ResearchItem(item.title, item.url, item.source_name, item.published_at, item.summary[:500], query, "news_search"))
        for item in fetch_bing_news(query, start, end, source_name="Bing News - report research", source_url="https://www.bing.com/news/search", region_hint=brief.region):
            if item.url in seen:
                continue
            seen.add(item.url)
            out.append(ResearchItem(item.title, item.url, item.source_name, item.published_at, item.summary[:500], query, "news_search"))
        if len(out) >= limit:
            break

    enriched = enrich_research_items(out[:limit], brief, max_fetches=16 if expanded else 10)
    enriched.sort(key=lambda x: 0 if x.evidence_type in {"structured_seed", "pdf_extract", "page_extract"} else 1)
    LOGGER.info(
        "Research context ready: case=%s items=%s extracted=%s",
        brief.case_name,
        len(enriched[:limit]),
        sum(1 for x in enriched if x.evidence_type in {"pdf_extract", "page_extract"}),
    )
    return enriched[:limit]
