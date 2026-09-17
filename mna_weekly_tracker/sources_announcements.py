"""Independent, public announcement collection when CNINFO is unavailable.

Eastmoney's public notices page uses this paginated listing endpoint. Fetch the
entire requested date range before selecting M&A titles: category-only queries
omit purchase-asset reports that the provider classifies as issuance or other.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime

from .config import CHINA_SOURCES
from .sources_fixed import BEIJING_TZ, RawItem, parse_datetime, request_with_retries, strip_html

LOGGER = logging.getLogger(__name__)
ANNOUNCEMENTS_ENDPOINT = "https://np-anotice-stock.eastmoney.com/api/security/ann"
ANNOUNCEMENTS_SOURCE_URL = "https://data.eastmoney.com/notices/"
ANNOUNCEMENTS_SOURCE_NAME = "东方财富 - 上市公司公告公开备用源"
ANNOUNCEMENT_KEYWORDS = tuple(dict.fromkeys(
    [keyword for source in CHINA_SOURCES if source.kind == "cninfo_api" for keyword in source.keywords]
    + ["收购", "重组", "股权转让", "股份转让", "实际控制人变更", "控股股东变更", "重整", "分拆"]
))


class AnnouncementCollectionError(RuntimeError):
    """The fallback range could not be collected completely."""


def fetch_public_announcements(start: datetime, end: datetime, *, page_size: int = 100, max_pages: int = 200) -> list[RawItem]:
    """Collect the full public A-share listing and return in-window M&A items.

    The endpoint caps pages at 100 rows. A page limit is a failure boundary, not
    permission to return an incomplete week. Publication dates intentionally use
    ``notice_date``, matching the disclosed-date semantics of CNINFO.
    """
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ValueError("announcement window must contain ordered timezone-aware datetimes")
    if not 1 <= page_size <= 100 or max_pages < 1:
        raise ValueError("announcement page_size must be 1..100 and max_pages positive")
    start, end = start.astimezone(BEIJING_TZ), end.astimezone(BEIJING_TZ)
    out: list[RawItem] = []
    seen_articles: set[str] = set()
    seen_pages: set[tuple[str, ...]] = set()
    scanned = 0
    snapshot_total: int | None = None
    for page in range(1, max_pages + 1):
        params = {
            "sr": "-1", "page_size": str(page_size), "page_index": str(page),
            "ann_type": "A", "client_source": "web", "f_node": "0", "s_node": "0",
            "begin_time": start.strftime("%Y-%m-%d"), "end_time": end.strftime("%Y-%m-%d"),
        }
        response = request_with_retries("GET", ANNOUNCEMENTS_ENDPOINT, params=params, timeout=20)
        try:
            payload = response.json()
            data = payload["data"]
            rows = data["list"]
            total = int(data["total_hits"])
            returned_page = int(data["page_index"])
            returned_size = int(data["page_size"])
        except (ValueError, KeyError, TypeError) as exc:
            raise AnnouncementCollectionError(f"invalid public announcement response on page {page}") from exc
        if payload.get("success") != 1 or not isinstance(rows, list) or total < 0:
            raise AnnouncementCollectionError(f"public announcement provider rejected page {page}")
        if returned_page != page or returned_size != page_size:
            raise AnnouncementCollectionError(f"public announcement pagination mismatch on page {page}")
        if snapshot_total is not None and total != snapshot_total:
            raise AnnouncementCollectionError(f"public announcement range changed during pagination: {snapshot_total} to {total}")
        snapshot_total = total
        required_pages = max(1, math.ceil(total / page_size))
        if required_pages > max_pages:
            raise AnnouncementCollectionError(f"public announcement range needs {required_pages} pages; limit is {max_pages}")
        expected_rows = min(page_size, max(0, total - (page - 1) * page_size))
        if len(rows) != expected_rows:
            raise AnnouncementCollectionError(f"incomplete public announcement page {page}: expected {expected_rows}, got {len(rows)}")
        try:
            article_ids = tuple(str(row["art_code"]) for row in rows)
        except (KeyError, TypeError) as exc:
            raise AnnouncementCollectionError(f"invalid public announcement rows on page {page}") from exc
        if rows and article_ids in seen_pages:
            raise AnnouncementCollectionError(f"repeated public announcement page {page}")
        if len(set(article_ids)) != len(article_ids) or seen_articles.intersection(article_ids):
            raise AnnouncementCollectionError(f"overlapping public announcement records on page {page}")
        seen_pages.add(article_ids)
        scanned += len(rows)
        for row, article_id in zip(rows, article_ids):
            if not re.fullmatch(r"AN\d+", article_id):
                raise AnnouncementCollectionError(f"invalid public announcement identifier on page {page}")
            seen_articles.add(article_id)
            title = strip_html(row.get("title") or row.get("title_ch") or "")
            if not title or not any(keyword.lower() in title for keyword in ANNOUNCEMENT_KEYWORDS):
                continue
            published = parse_datetime(row.get("notice_date"))
            if published is None:
                raise AnnouncementCollectionError(f"missing public announcement date for {article_id}")
            if not start <= published <= end:
                continue
            securities = row.get("codes") or []
            # The public A-share feed also includes pending issuers (e.g.
            # A26221). Its own detail-page links use these alphanumeric codes.
            security = next((code for code in securities if re.fullmatch(r"[A-Za-z0-9]{1,12}", str(code.get("stock_code", "")))), None)
            if security is None:
                raise AnnouncementCollectionError(f"missing public announcement security for {article_id}")
            stock = security["stock_code"]
            categories = "、".join(strip_html(column.get("column_name", "")) for column in row.get("columns") or [])
            summary = f"证券简称：{strip_html(security.get('short_name', ''))}；证券代码：{stock}；公告类别：{categories}"
            out.append(RawItem(
                title=title,
                url=f"https://data.eastmoney.com/notices/detail/{stock}/{article_id}.html",
                source_name=ANNOUNCEMENTS_SOURCE_NAME,
                source_url=ANNOUNCEMENTS_SOURCE_URL,
                published_at=published.isoformat(),
                summary=summary,
                region_hint="中国",
                query="上市公司并购重组公告（完整日期范围备用采集）",
            ))
        if page == 1 or page % 10 == 0:
            LOGGER.info("Public announcement fallback progress: page=%s/%s scanned=%s candidates=%s", page, required_pages, scanned, len(out))
        if page >= required_pages:
            LOGGER.info("Public announcement fallback complete: pages=%s scanned=%s candidates=%s", page, scanned, len(out))
            return out
    raise AnnouncementCollectionError(f"public announcement pagination exceeded {max_pages} pages")
