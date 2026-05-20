"""Rich source orchestration with diagnostics for weekly M&A tracking."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from .config import CHINA_NEWS_SOURCES, CHINA_SOURCES, GLOBAL_QUERIES, HKEX_QUERIES, SourceConfig
from .sources_fixed import (
    RawItem,
    dedupe_items,
    fetch_bing_news,
    fetch_gdelt_doc,
    fetch_google_news,
    fetch_sogou_weixin,
    fetch_source as base_fetch_source,
    parse_datetime,
    week_window,
)

LOGGER = logging.getLogger(__name__)


def _extend_unique(target: list[RawItem], additions: Iterable[RawItem]) -> None:
    seen = {item.stable_key() for item in target}
    for item in additions:
        key = item.stable_key()
        if key in seen:
            continue
        seen.add(key)
        target.append(item)


def fetch_gdelt_with_fallback(source: SourceConfig, start: datetime, end: datetime) -> list[RawItem]:
    items: list[RawItem] = []
    for keyword in source.keywords:
        _extend_unique(items, fetch_gdelt_doc(keyword, start, end, source_name=source.name, source_url=source.url))
    if items:
        return items
    # GDELT may return non-JSON/empty responses for some Chinese Boolean queries.
    # Keep the source useful by falling back to news search with the same terms.
    fallback: list[RawItem] = []
    for keyword in source.keywords:
        _extend_unique(
            fallback,
            fetch_google_news(
                keyword,
                start,
                end,
                source_name=f"{source.name} - Google fallback",
                source_url=source.url,
                region_hint="中国/全球",
                locale="zh-CN",
                region="CN",
            ),
        )
        _extend_unique(
            fallback,
            fetch_bing_news(
                keyword,
                start,
                end,
                source_name=f"{source.name} - Bing fallback",
                source_url=source.url,
                region_hint="中国/全球",
            ),
        )
    return fallback


def fetch_sogou_with_diagnostics(source: SourceConfig, start: datetime, end: datetime) -> list[RawItem]:
    items: list[RawItem] = []
    for keyword in source.keywords:
        _extend_unique(items, fetch_sogou_weixin(keyword, start, end, source_name=source.name, source_url=source.url))
    if not items:
        LOGGER.info("Source returned 0 candidates: %s. Sogou Weixin is best-effort and may be rate-limited by the provider.", source.name)
    return items


def fetch_source(source: SourceConfig, start: datetime, end: datetime) -> list[RawItem]:
    if source.kind == "gdelt_doc":
        return fetch_gdelt_with_fallback(source, start, end)
    if source.kind == "sogou_weixin_search":
        return fetch_sogou_with_diagnostics(source, start, end)
    return base_fetch_source(source, start, end)


def fetch_all_candidates(start: datetime, end: datetime, max_items: int = 450) -> tuple[list[RawItem], list[str]]:
    errors: list[str] = []
    candidates: list[RawItem] = []
    source_rows: list[tuple[str, str, int]] = []

    for source in CHINA_SOURCES + CHINA_NEWS_SOURCES:
        try:
            items = fetch_source(source, start, end)
            source_rows.append((source.name, source.kind, len(items)))
            _extend_unique(candidates, items)
        except Exception as exc:  # noqa: BLE001
            msg = f"source failed: {source.name}: {exc}"
            LOGGER.warning(msg)
            errors.append(msg)
            source_rows.append((source.name, source.kind, 0))

    for name, kind, count in source_rows:
        LOGGER.info("Source summary: kind=%s count=%s name=%s", kind, count, name)

    for query in GLOBAL_QUERIES:
        _extend_unique(
            candidates,
            fetch_google_news(
                query,
                start,
                end,
                source_name="Google News - Global M&A",
                source_url="https://news.google.com/",
                region_hint="全球",
                locale="en-US",
                region="US",
            ),
        )
    for query in HKEX_QUERIES:
        _extend_unique(
            candidates,
            fetch_google_news(
                query,
                start,
                end,
                source_name="Google News - HKEXnews",
                source_url="https://www.hkexnews.hk/",
                region_hint="中国香港/全球",
                locale="zh-CN",
                region="CN",
            ),
        )
        _extend_unique(
            candidates,
            fetch_bing_news(
                query,
                start,
                end,
                source_name="Bing News - HKEXnews",
                source_url="https://www.hkexnews.hk/",
                region_hint="中国香港/全球",
            ),
        )

    deduped = dedupe_items(candidates)
    deduped.sort(key=lambda x: parse_datetime(x.published_at) or start, reverse=True)
    LOGGER.info("Source summary total: raw_before_cap=%s raw_after_cap=%s", len(deduped), min(len(deduped), max_items))
    return deduped[:max_items], errors
