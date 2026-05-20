"""Source collection utilities for weekly M&A candidates."""

from __future__ import annotations

import hashlib
import html
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .config import CHINA_SOURCES, GLOBAL_QUERIES, HKEX_QUERIES, SourceConfig

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36"


@dataclass
class RawItem:
    title: str
    url: str
    source_name: str
    source_url: str
    published_at: str
    summary: str = ""
    region_hint: str = "中国"
    query: str = ""

    def stable_key(self) -> str:
        raw = f"{normalize_text(self.title)}|{normalize_url(self.url)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def week_window(days: int = 7, tz_name: str = "Asia/Shanghai") -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    end = datetime.now(tz).replace(microsecond=0)
    start = end - timedelta(days=days)
    return start, end


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip().lower()


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).strip()
    parsed = urllib.parse.urlsplit(value)
    query_pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, urllib.parse.urlencode(query_pairs), ""))


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return normalize_text(soup.get_text(" "))


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def in_window(value: str | None, start: datetime, end: datetime) -> bool:
    dt = parse_datetime(value)
    if dt is None:
        return True
    return start <= dt <= end


def request_with_retries(method: str, url: str, *, retries: int = 2, sleep_seconds: float = 1.0, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept", "application/json,text/html,application/xml;q=0.9,*/*;q=0.8")
    timeout = kwargs.pop("timeout", 20)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(sleep_seconds * (attempt + 1))
    raise RuntimeError(f"request failed for {url}: {last_exc}")


def fetch_cninfo(source: SourceConfig, start: datetime, end: datetime, page_size: int = 30) -> list[RawItem]:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    headers = {
        "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "Origin": "https://www.cninfo.com.cn",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    results: list[RawItem] = []
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")
    for keyword in source.keywords:
        for page_num in (1, 2):
            data = {
                "pageNum": str(page_num),
                "pageSize": str(page_size),
                "column": "szse",
                "tabName": "fulltext",
                "plate": "",
                "stock": "",
                "searchkey": keyword,
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start_s}~{end_s}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            try:
                response = request_with_retries("POST", endpoint, headers=headers, data=data)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("CNINFO fetch failed: %s %s page=%s error=%s", source.name, keyword, page_num, exc)
                break
            announcements = payload.get("announcements") or []
            if not announcements:
                break
            for ann in announcements:
                title = strip_html(ann.get("announcementTitle") or "")
                adjunct_url = ann.get("adjunctUrl") or ""
                if not title or not adjunct_url:
                    continue
                url = urllib.parse.urljoin("https://static.cninfo.com.cn/", adjunct_url)
                ts = ann.get("announcementTime")
                if isinstance(ts, (int, float)):
                    published_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(BEIJING_TZ).isoformat()
                else:
                    published_at = str(ts or "")
                sec_name = strip_html(ann.get("secName") or "")
                summary = f"证券简称：{sec_name}" if sec_name else ""
                results.append(RawItem(title, url, source.name, source.url, published_at, summary, "中国", keyword))
            if len(announcements) < page_size:
                break
    return results


def google_news_rss_url(query: str, *, locale: str = "zh-CN", region: str = "CN") -> str:
    ceid = "CN:zh-Hans" if region.upper() == "CN" else "US:en"
    params = urllib.parse.urlencode({"q": query, "hl": locale, "gl": region, "ceid": ceid})
    return f"https://news.google.com/rss/search?{params}"


def fetch_google_news(query: str, start: datetime, end: datetime, *, source_name: str = "Google News", source_url: str = "https://news.google.com/", region_hint: str = "全球", locale: str = "zh-CN", region: str = "CN") -> list[RawItem]:
    rss_query = f"{query} when:{max((end - start).days, 1)}d"
    url = google_news_rss_url(rss_query, locale=locale, region=region)
    try:
        response = request_with_retries("GET", url, headers={"Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8"})
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Google News fetch failed: query=%s error=%s", query, exc)
        return []
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        LOGGER.warning("Google News RSS parse failed: query=%s error=%s", query, exc)
        return []
    items: list[RawItem] = []
    for entry in root.findall("./channel/item"):
        title = strip_html(entry.findtext("title") or "")
        link = entry.findtext("link") or ""
        summary = strip_html(entry.findtext("description") or "")
        publisher = strip_html(entry.findtext("source") or "")
        pub_date = entry.findtext("pubDate") or ""
        published_at = ""
        if pub_date:
            try:
                published_at = parsedate_to_datetime(pub_date).astimezone(BEIJING_TZ).isoformat()
            except (TypeError, ValueError, OverflowError):
                dt = parse_datetime(pub_date)
                published_at = dt.isoformat() if dt else pub_date
        if not in_window(published_at, start, end):
            continue
        if not title or not link:
            continue
        combined = f"{source_name} / {publisher}" if publisher else source_name
        items.append(RawItem(title, link, combined, source_url, published_at, summary, region_hint, query))
    return items


def fetch_site_source(source: SourceConfig, start: datetime, end: datetime) -> list[RawItem]:
    results: list[RawItem] = []
    base = source.site_query or urllib.parse.urlparse(source.url).netloc
    for keyword in source.keywords:
        results.extend(fetch_google_news(f"{base} {keyword}", start, end, source_name=source.name, source_url=source.url, region_hint="中国", locale="zh-CN", region="CN"))
    return results


def fetch_all_candidates(start: datetime, end: datetime, max_items: int = 160) -> tuple[list[RawItem], list[str]]:
    errors: list[str] = []
    candidates: list[RawItem] = []
    for source in CHINA_SOURCES:
        try:
            if source.kind == "cninfo_api":
                candidates.extend(fetch_cninfo(source, start, end))
            elif source.kind == "google_news_site":
                candidates.extend(fetch_site_source(source, start, end))
            else:
                errors.append(f"unsupported source kind: {source.kind} ({source.name})")
        except Exception as exc:  # noqa: BLE001
            msg = f"source failed: {source.name}: {exc}"
            LOGGER.warning(msg)
            errors.append(msg)
    for query in GLOBAL_QUERIES:
        candidates.extend(fetch_google_news(query, start, end, source_name="Google News - Global M&A", source_url="https://news.google.com/", region_hint="全球", locale="en-US", region="US"))
    for query in HKEX_QUERIES:
        candidates.extend(fetch_google_news(query, start, end, source_name="Google News - HKEXnews", source_url="https://www.hkexnews.hk/", region_hint="中国香港/全球", locale="zh-CN", region="CN"))
    deduped = dedupe_items(candidates)
    deduped.sort(key=lambda x: parse_datetime(x.published_at) or start, reverse=True)
    return deduped[:max_items], errors


def dedupe_items(items: Iterable[RawItem]) -> list[RawItem]:
    seen: set[str] = set()
    deduped: list[RawItem] = []
    for item in items:
        key = item.stable_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
