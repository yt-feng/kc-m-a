"""Robust source collectors for weekly M&A candidates.

This module replaces the earlier experimental RSS-only collectors.  It keeps
Bing/GDELT/Sogou failures quiet, falls back to Google News where useful, and
keeps the raw candidate cap high enough for China-heavy weekly deal flow.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import base64
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .config import GLOBAL_QUERIES, HKEX_QUERIES, MIDDLE_EAST_BUYER_KEYWORDS, MIDDLE_EAST_QUERIES, TRACKED_FETCH_SOURCES, SourceConfig

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36"
GOOGLE_NEWS_HOSTS = {"news.google.com", "news.url.google.com"}
BING_NEWS_HOSTS = {"www.bing.com", "bing.com", "cn.bing.com"}
WRAPPER_HOSTS = (
    "news.google.com",
    "news.url.google.com",
    "www.google.com",
    "google.com",
    "www.bing.com",
    "bing.com",
    "cn.bing.com",
    "weixin.sogou.com",
)
URL_RESOLVE_CACHE: dict[str, str] = {}
MAX_TITLE_URL_RESOLVES = int(os.getenv("MNA_MAX_TITLE_URL_RESOLVES", "10"))
_TITLE_URL_RESOLVE_CACHE: dict[str, str] = {}
_TITLE_URL_RESOLVE_ATTEMPTS = 0


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
    return end - timedelta(days=days), end


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip().lower()


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(str(value).strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, urllib.parse.urlencode(query), ""))


def _url_host(url: str) -> str:
    return urllib.parse.urlsplit(url or "").netloc.lower()


def _first_query_url(url: str, names: tuple[str, ...]) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    query = urllib.parse.parse_qs(parsed.query)
    for name in names:
        for value in query.get(name, []):
            value = urllib.parse.unquote(value or "").strip()
            if value.startswith(("http://", "https://")):
                return value
    return ""


def _decode_google_news_token(token: str) -> str:
    token = urllib.parse.unquote(token or "").split("?", 1)[0].strip("/")
    if not token:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:  # noqa: BLE001
        return ""
    text = decoded.decode("latin-1", errors="ignore")
    urls = re.findall(r"https?://[^\x00-\x20\"'<>]+", text)
    for candidate in urls:
        candidate = urllib.parse.unquote(candidate).strip()
        if candidate and _url_host(candidate) not in GOOGLE_NEWS_HOSTS:
            return candidate
    return ""


def is_aggregator_url(url: str) -> bool:
    host = _url_host(url)
    return host in GOOGLE_NEWS_HOSTS or (host in BING_NEWS_HOSTS and "news" in urllib.parse.urlsplit(url or "").path.lower())


def unwrap_news_url(url: str, *, publisher_url: str = "") -> str:
    """Return the publisher/original URL for known news aggregator links.

    Google News RSS often stores the publisher article URL inside a base64-ish
    article token. Bing News can expose the original link in query parameters.
    Keeping the raw aggregator URLs makes the Excel links brittle or unusable,
    so unwrap them before candidates enter the workbook/model pipeline.
    """
    url = str(url or "").strip()
    if not url:
        return ""
    direct = _first_query_url(url, ("url", "u", "target", "r"))
    if direct:
        return direct

    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    if host in GOOGLE_NEWS_HOSTS and "/articles/" in parsed.path:
        token = parsed.path.rsplit("/articles/", 1)[-1].split("/", 1)[0]
        decoded = _decode_google_news_token(token)
        if decoded:
            return decoded
    if host in BING_NEWS_HOSTS and "news" in parsed.path.lower():
        direct = _first_query_url(url, ("url", "u"))
        if direct:
            return direct
    if host in GOOGLE_NEWS_HOSTS and publisher_url:
        # This is a last-resort fallback. Google RSS source@url is usually the
        # publisher homepage rather than the article, but it is still preferable
        # to a non-openable Google RSS article wrapper.
        return publisher_url
    return url


def resolve_news_link(link: str, *, title: str = "", publisher_url: str = "") -> str:
    global _TITLE_URL_RESOLVE_ATTEMPTS
    unwrapped = unwrap_news_url(link, publisher_url="")
    if unwrapped and not is_aggregator_url(unwrapped):
        return unwrapped
    if title:
        cache_key = normalize_text(title)[:220]
        if cache_key in _TITLE_URL_RESOLVE_CACHE:
            cached = _TITLE_URL_RESOLVE_CACHE[cache_key]
            return cached or publisher_url or unwrapped or link
        if _TITLE_URL_RESOLVE_ATTEMPTS >= MAX_TITLE_URL_RESOLVES:
            _TITLE_URL_RESOLVE_CACHE[cache_key] = ""
            return publisher_url or unwrapped or link
        _TITLE_URL_RESOLVE_ATTEMPTS += 1
        try:
            for candidate in rss_items(
                bing_news_url(title),
                title,
                datetime.now(BEIJING_TZ) - timedelta(days=30),
                datetime.now(BEIJING_TZ) + timedelta(days=1),
                source_name="Bing News - URL resolver",
                source_url="https://www.bing.com/news/search",
                region_hint="全球",
                resolve_links=False,
            ):
                candidate_url = unwrap_news_url(candidate.url, publisher_url="")
                if candidate_url and not is_aggregator_url(candidate_url):
                    _TITLE_URL_RESOLVE_CACHE[cache_key] = candidate_url
                    return candidate_url
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("News URL title resolution failed: title=%s error=%s", title[:120], exc)
        _TITLE_URL_RESOLVE_CACHE[cache_key] = ""
    if publisher_url:
        return publisher_url
    return unwrapped or link


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return normalize_text(BeautifulSoup(value, "html.parser").get_text(" "))


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


def parse_relative_time(text: str | None, *, now: datetime) -> datetime | None:
    if not text:
        return None
    text = normalize_text(text)
    specs = (
        (r"(\d+)\s*分钟前", "minutes"), (r"(\d+)\s*小时前", "hours"),
        (r"(\d+)\s*天前", "days"), (r"(\d+)\s*周前", "weeks"),
        (r"(\d+)\s*minutes? ago", "minutes"), (r"(\d+)\s*hours? ago", "hours"),
        (r"(\d+)\s*days? ago", "days"), (r"(\d+)\s*weeks? ago", "weeks"),
    )
    for pattern, unit in specs:
        m = re.search(pattern, text)
        if not m:
            continue
        n = int(m.group(1))
        if unit == "minutes":
            return now - timedelta(minutes=n)
        if unit == "hours":
            return now - timedelta(hours=n)
        if unit == "days":
            return now - timedelta(days=n)
        return now - timedelta(weeks=n)
    return parse_datetime(text)


def in_window(value: str | None, start: datetime, end: datetime) -> bool:
    dt = parse_datetime(value)
    return True if dt is None else start <= dt <= end


def source_region_hint(source: SourceConfig, default: str = "中国") -> str:
    if source.region_hint:
        return source.region_hint
    text = f"{source.name} {source.coverage}"
    if any(token in text for token in ("中东", "沙特", "阿联酋", "卡塔尔", "Mubadala", "PIF", "QIA", "ADQ")):
        return "中东/全球"
    return default


def source_search_locale(source: SourceConfig) -> tuple[str, str, str, str]:
    hint = source_region_hint(source)
    if hint.startswith("中东/全球") or hint.startswith("中东/海外"):
        return "en-US", "US", "en-US", "en"
    return "zh-CN", "CN", "zh-CN", "zh-cn"


def raw_item_priority(item: RawItem) -> int:
    text = f"{item.region_hint} {item.source_name} {item.query} {item.title}"
    if any(token in text for token in ("中东", "沙特", "阿联酋", "卡塔尔", "阿布扎比", "穆巴达拉")):
        return 2
    text_n = normalize_text(text)
    for keyword in MIDDLE_EAST_BUYER_KEYWORDS:
        keyword_n = normalize_text(keyword)
        if len(keyword_n) <= 4:
            if re.search(rf"\b{re.escape(keyword_n)}\b", text_n):
                return 2
        elif keyword_n in text_n:
            return 2
    if "全球" in item.region_hint:
        return 1
    return 0


def candidate_sort_key(item: RawItem, fallback_dt: datetime) -> tuple[int, datetime]:
    return raw_item_priority(item), parse_datetime(item.published_at) or fallback_dt


def request_with_retries(method: str, url: str, *, retries: int = 1, sleep_seconds: float = 1.0, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept", "application/rss+xml,application/xml,text/html,application/json;q=0.9,*/*;q=0.8")
    timeout = kwargs.pop("timeout", 20)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(sleep_seconds * (attempt + 1))
    raise RuntimeError(f"request failed for {url}: {last_exc}")


def is_wrapper_url(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return False
    return any(host == wrapper or host.endswith("." + wrapper) for wrapper in WRAPPER_HOSTS)


def url_from_query_params(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key in ("url", "u", "q", "target", "to"):
        for value in params.get(key, []):
            candidate = urllib.parse.unquote(value).strip()
            if candidate.startswith(("http://", "https://")) and not is_wrapper_url(candidate):
                return candidate
    return ""


def url_from_html(html_text: str) -> str:
    soup = BeautifulSoup(html_text or "", "html.parser")
    for selector, attr in (("link[rel=canonical]", "href"), ("meta[property='og:url']", "content")):
        node = soup.select_one(selector)
        if node:
            candidate = (node.get(attr) or "").strip()
            if candidate.startswith(("http://", "https://")) and not is_wrapper_url(candidate):
                return candidate
    for a in soup.select("a[href]"):
        candidate = urllib.parse.unquote(a.get("href") or "").strip()
        if candidate.startswith(("http://", "https://")) and not is_wrapper_url(candidate):
            return candidate
    return ""


def resolve_original_url(url: str) -> str:
    """Best-effort conversion of Google/Bing/Sogou wrapper URLs to original URLs."""
    url = (url or "").strip()
    if not url:
        return url
    if url in URL_RESOLVE_CACHE:
        return URL_RESOLVE_CACHE[url]
    direct = url_from_query_params(url)
    if direct:
        URL_RESOLVE_CACHE[url] = direct
        return direct
    if not is_wrapper_url(url):
        URL_RESOLVE_CACHE[url] = url
        return url
    resolved = url
    try:
        response = request_with_retries("GET", url, retries=0, timeout=8, allow_redirects=True)
        final_url = response.url or url
        if final_url.startswith(("http://", "https://")) and not is_wrapper_url(final_url):
            resolved = final_url
        else:
            html_candidate = url_from_html(response.text)
            if html_candidate:
                resolved = html_candidate
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("URL resolve skipped: url=%s error=%s", url, exc)
    URL_RESOLVE_CACHE[url] = resolved
    return resolved


def fetch_cninfo(source: SourceConfig, start: datetime, end: datetime, page_size: int = 50) -> list[RawItem]:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    headers = {
        "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "Origin": "https://www.cninfo.com.cn",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    results: list[RawItem] = []
    for keyword in source.keywords:
        for page_num in range(1, 5):
            data = {
                "pageNum": str(page_num), "pageSize": str(page_size), "column": "szse", "tabName": "fulltext",
                "plate": "", "stock": "", "searchkey": keyword, "secid": "", "category": "", "trade": "",
                "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}", "sortName": "", "sortType": "", "isHLtitle": "true",
            }
            try:
                payload = request_with_retries("POST", endpoint, headers=headers, data=data, timeout=20).json()
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
                ts = ann.get("announcementTime")
                if isinstance(ts, (int, float)):
                    published = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(BEIJING_TZ).isoformat()
                else:
                    published = str(ts or "")
                sec_name = strip_html(ann.get("secName") or "")
                results.append(RawItem(title, urllib.parse.urljoin("https://static.cninfo.com.cn/", adjunct_url), source.name, source.url, published, f"证券简称：{sec_name}" if sec_name else "", "中国", keyword))
            if len(announcements) < page_size:
                break
    return results


def rss_items(url: str, query: str, start: datetime, end: datetime, *, source_name: str, source_url: str, region_hint: str, resolve_links: bool = True) -> list[RawItem]:
    try:
        response = request_with_retries("GET", url, retries=1, timeout=15)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("RSS fetch skipped: source=%s query=%s error=%s", source_name, query, exc)
        return []
    prefix = (response.content or b"")[:100].decode("utf-8", errors="ignore").lstrip().lower()
    if not (prefix.startswith("<?xml") or prefix.startswith("<rss") or prefix.startswith("<feed")):
        LOGGER.debug("RSS non-XML skipped: source=%s query=%s prefix=%s", source_name, query, prefix[:50])
        return []
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        LOGGER.debug("RSS parse skipped: source=%s query=%s error=%s", source_name, query, exc)
        return []
    out: list[RawItem] = []
    for entry in root.findall("./channel/item"):
        title = strip_html(entry.findtext("title") or "")
        link = entry.findtext("link") or ""
        publisher_node = entry.find("source")
        publisher_url = publisher_node.attrib.get("url", "") if publisher_node is not None else ""
        summary = strip_html(entry.findtext("description") or "")
        publisher = strip_html(entry.findtext("source") or "")
        link = resolve_news_link(link, title=title, publisher_url=publisher_url) if resolve_links else unwrap_news_url(link, publisher_url=publisher_url)
        pub_date = entry.findtext("pubDate") or ""
        published = ""
        if pub_date:
            try:
                published = parsedate_to_datetime(pub_date).astimezone(BEIJING_TZ).isoformat()
            except (TypeError, ValueError, OverflowError):
                dt = parse_datetime(pub_date)
                published = dt.isoformat() if dt else pub_date
        if title and link and in_window(published, start, end):
            source = f"{source_name} / {publisher}" if publisher else source_name
            out.append(RawItem(title, resolve_original_url(link), source, source_url, published, summary, region_hint, query))
    return out


def google_news_url(query: str, *, locale: str = "zh-CN", region: str = "CN") -> str:
    ceid = "CN:zh-Hans" if region.upper() == "CN" else "US:en"
    params = urllib.parse.urlencode({"q": query, "hl": locale, "gl": region, "ceid": ceid})
    return f"https://news.google.com/rss/search?{params}"


def fetch_google_news(query: str, start: datetime, end: datetime, *, source_name: str = "Google News", source_url: str = "https://news.google.com/", region_hint: str = "全球", locale: str = "zh-CN", region: str = "CN") -> list[RawItem]:
    rss_query = f"{query} when:{max((end - start).days, 1)}d"
    return rss_items(google_news_url(rss_query, locale=locale, region=region), query, start, end, source_name=source_name, source_url=source_url, region_hint=region_hint)


def bing_news_url(query: str, *, market: str = "zh-CN", language: str = "zh-cn") -> str:
    return "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": query, "format": "rss", "mkt": market, "setlang": language})


def fetch_bing_news(
    query: str,
    start: datetime,
    end: datetime,
    *,
    source_name: str = "Bing News",
    source_url: str = "https://www.bing.com/news/search",
    region_hint: str = "中国",
    market: str = "zh-CN",
    language: str = "zh-cn",
    fallback_locale: str | None = None,
    fallback_region: str | None = None,
) -> list[RawItem]:
    items = rss_items(bing_news_url(query, market=market, language=language), query, start, end, source_name=f"{source_name} RSS", source_url=source_url, region_hint=region_hint)
    if items:
        return items
    locale = fallback_locale or ("en-US" if market.lower().startswith("en") else "zh-CN")
    region = fallback_region or ("US" if market.lower().endswith("us") else "CN")
    return fetch_google_news(query, start, end, source_name=f"{source_name} via Google News", source_url=source_url, region_hint=region_hint, locale=locale, region=region)


def fetch_sogou_weixin(query: str, start: datetime, end: datetime, *, source_name: str = "搜狗微信", source_url: str = "https://weixin.sogou.com/weixin", limit: int = 10) -> list[RawItem]:
    url = "https://weixin.sogou.com/weixin?" + urllib.parse.urlencode({"type": "2", "query": query, "ie": "utf8"})
    try:
        response = request_with_retries("GET", url, headers={"Referer": "https://weixin.sogou.com/"}, retries=1, timeout=15)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Sogou Weixin skipped: query=%s error=%s", query, exc)
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    out: list[RawItem] = []
    for node in soup.select("li[id^=sogou_vr_]")[:limit]:
        a = node.select_one("h3 a") or node.select_one("a")
        if not a:
            continue
        title = strip_html(a.get_text(" "))
        link = urllib.parse.urljoin("https://weixin.sogou.com/", a.get("href") or "")
        summary = strip_html(" ".join(x.get_text(" ") for x in node.select("p.txt-info, .txt-info")))
        time_text = strip_html(" ".join(x.get_text(" ") for x in node.select("span.s2, .s2")))
        dt = parse_relative_time(time_text, now=end)
        published = dt.isoformat() if dt else ""
        if title and link and in_window(published, start, end):
            out.append(RawItem(title, resolve_original_url(link), source_name, source_url, published, summary, "中国/微信", query))
    return out


def fetch_gdelt_doc(query: str, start: datetime, end: datetime, *, source_name: str = "GDELT DOC", source_url: str = "https://api.gdeltproject.org/api/v2/doc/doc", region_hint: str = "中国/全球") -> list[RawItem]:
    params = urllib.parse.urlencode({
        "query": query, "mode": "artlist", "format": "json", "maxrecords": "50", "sort": "hybridrel",
        "startdatetime": start.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
    })
    try:
        response = request_with_retries("GET", f"{source_url}?{params}", headers={"Accept": "application/json"}, retries=0, timeout=12)
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("GDELT skipped: query=%s error=%s", query, exc)
        return []
    out: list[RawItem] = []
    for article in payload.get("articles") or []:
        title = strip_html(article.get("title") or "")
        link = article.get("url") or ""
        dt = parse_datetime(article.get("seendate"))
        published = dt.isoformat() if dt else ""
        domain = strip_html(article.get("domain") or "")
        if title and link and in_window(published, start, end):
            source = f"{source_name} / {domain}" if domain else source_name
            out.append(RawItem(title, link, source, source_url, published, domain, region_hint, query))
    return out


def fetch_source(source: SourceConfig, start: datetime, end: datetime) -> list[RawItem]:
    region_hint = source_region_hint(source)
    locale, region, market, language = source_search_locale(source)
    if source.kind == "cninfo_api":
        return fetch_cninfo(source, start, end)
    if source.kind == "google_news_site":
        out: list[RawItem] = []
        base = source.site_query or urllib.parse.urlparse(source.url).netloc
        for kw in source.keywords:
            out.extend(fetch_google_news(f"{base} {kw}", start, end, source_name=source.name, source_url=source.url, region_hint=region_hint, locale=locale, region=region))
            out.extend(fetch_bing_news(f"{base} {kw}", start, end, source_name=f"{source.name} - Bing补充", source_url=source.url, region_hint=region_hint, market=market, language=language, fallback_locale=locale, fallback_region=region))
        return out
    if source.kind == "google_news_search":
        out = []
        for kw in source.keywords:
            out.extend(fetch_google_news(kw, start, end, source_name=source.name, source_url=source.url, region_hint=region_hint, locale=locale, region=region))
        return out
    if source.kind == "bing_news_search":
        out = []
        for kw in source.keywords:
            out.extend(fetch_bing_news(kw, start, end, source_name=source.name, source_url=source.url, region_hint=region_hint, market=market, language=language, fallback_locale=locale, fallback_region=region))
        return out
    if source.kind == "sogou_weixin_search":
        out = []
        for kw in source.keywords:
            out.extend(fetch_sogou_weixin(kw, start, end, source_name=source.name, source_url=source.url))
        return out
    if source.kind == "gdelt_doc":
        out = []
        for kw in source.keywords:
            out.extend(fetch_gdelt_doc(kw, start, end, source_name=source.name, source_url=source.url, region_hint=region_hint))
        return out
    raise ValueError(f"unsupported source kind: {source.kind} ({source.name})")


def dedupe_items(items: Iterable[RawItem]) -> list[RawItem]:
    seen: set[str] = set()
    out: list[RawItem] = []
    for item in items:
        key = item.stable_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def fetch_all_candidates(start: datetime, end: datetime, max_items: int = 450) -> tuple[list[RawItem], list[str]]:
    errors: list[str] = []
    candidates: list[RawItem] = []
    for source in TRACKED_FETCH_SOURCES:
        try:
            candidates.extend(fetch_source(source, start, end))
        except Exception as exc:  # noqa: BLE001
            msg = f"source failed: {source.name}: {exc}"
            LOGGER.warning(msg)
            errors.append(msg)
    for query in GLOBAL_QUERIES:
        candidates.extend(fetch_google_news(query, start, end, source_name="Google News - Global M&A", source_url="https://news.google.com/", region_hint="全球", locale="en-US", region="US"))
    for query in MIDDLE_EAST_QUERIES:
        candidates.extend(fetch_google_news(query, start, end, source_name="Google News - Middle East outbound M&A", source_url="https://news.google.com/", region_hint="中东/全球", locale="en-US", region="US"))
        candidates.extend(fetch_bing_news(query, start, end, source_name="Bing News - Middle East outbound M&A", source_url="https://www.bing.com/news/search?format=rss", region_hint="中东/全球", market="en-US", language="en", fallback_locale="en-US", fallback_region="US"))
    for query in HKEX_QUERIES:
        candidates.extend(fetch_google_news(query, start, end, source_name="Google News - HKEXnews", source_url="https://www.hkexnews.hk/", region_hint="中国香港/全球", locale="zh-CN", region="CN"))
        candidates.extend(fetch_bing_news(query, start, end, source_name="Bing News - HKEXnews", source_url="https://www.hkexnews.hk/", region_hint="中国香港/全球"))
    deduped = dedupe_items(candidates)
    deduped.sort(key=lambda x: candidate_sort_key(x, start), reverse=True)
    LOGGER.info("Collected candidates: raw=%s deduped=%s capped=%s", len(candidates), len(deduped), min(len(deduped), max_items))
    return deduped[:max_items], errors
