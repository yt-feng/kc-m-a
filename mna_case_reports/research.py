"""Research context collection for M&A case reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mna_weekly_tracker.sources_rich import fetch_bing_news, fetch_google_news

from .case_selection import CaseBrief

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class ResearchItem:
    title: str
    url: str
    source_name: str
    published_at: str
    summary: str
    query: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def collect_research_context(brief: CaseBrief, *, lookback_days: int = 3650, limit: int = 24) -> list[ResearchItem]:
    """Collect public snippets and source links for a case.

    The generator must treat these snippets as factual leads rather than a full
    diligence file. If data is not in the snippets, the prompt tells the model
    to avoid inventing exact numbers.
    """
    end = datetime.now(BEIJING_TZ).replace(microsecond=0)
    start = end - timedelta(days=lookback_days)
    queries = [
        brief.case_name,
        f"{brief.case_name} 交易金额 并购 收购 估值",
        f"{brief.case_name} 财务数据 营收 净利润 对价",
        f"{brief.case_name} 公告 交易结构 股权 现金 股份",
    ]
    if brief.source_title:
        queries.append(brief.source_title)

    seen: set[str] = set()
    out: list[ResearchItem] = []
    if brief.source_url:
        out.append(
            ResearchItem(
                title=brief.source_title or brief.case_name,
                url=brief.source_url,
                source_name="seed_source",
                published_at=brief.published_at,
                summary=brief.why,
                query="seed",
            )
        )
        seen.add(brief.source_url)

    for query in queries:
        for item in fetch_google_news(query, start, end, source_name="Google News - report research", source_url="https://news.google.com/", region_hint=brief.region):
            if item.url in seen:
                continue
            seen.add(item.url)
            out.append(ResearchItem(item.title, item.url, item.source_name, item.published_at, item.summary[:500], query))
            if len(out) >= limit:
                return out
        for item in fetch_bing_news(query, start, end, source_name="Bing News - report research", source_url="https://www.bing.com/news/search", region_hint=brief.region):
            if item.url in seen:
                continue
            seen.add(item.url)
            out.append(ResearchItem(item.title, item.url, item.source_name, item.published_at, item.summary[:500], query))
            if len(out) >= limit:
                return out
    return out[:limit]
