from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from mna_weekly_tracker import sources_announcements as announcements
from mna_weekly_tracker import sources_rich as sources
from mna_weekly_tracker.config import CHINA_SOURCES
from mna_weekly_tracker.sources_fixed import RawItem, SourceAccessDeniedError

TZ = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 9, 11, 5, tzinfo=TZ)
END = datetime(2026, 9, 18, 5, tzinfo=TZ)


def row(ident: int, title: str = "测试公司:关于收购目标公司股权的公告", date: str = "2026-09-17 00:00:00") -> dict:
    return {"art_code": f"AN{ident}", "title": title, "notice_date": date,
            "codes": [{"stock_code": "000001", "short_name": "测试公司"}],
            "columns": [{"column_name": "其他"}]}


def response(rows: list[dict], *, page: int = 1, size: int = 2, total: int | None = None) -> mock.Mock:
    return mock.Mock(json=mock.Mock(return_value={"success": 1, "data": {
        "list": rows, "page_index": page, "page_size": size,
        "total_hits": len(rows) if total is None else total,
    }}))


class PublicAnnouncementsTests(unittest.TestCase):
    @mock.patch.object(announcements, "request_with_retries")
    def test_full_range_pagination_filters_dates_and_irrelevant_notices(self, request: mock.Mock) -> None:
        request.side_effect = [
            response([row(1), row(2, "测试公司:股东会通知")], total=5),
            response([row(3, date="2026-09-11 00:00:00"), row(4, date="2026-09-18 06:00:00")], page=2, total=5),
            response([row(5, "测试公司:发行股份购买资产报告书", "2026-09-18 00:00:00")], page=3, total=5),
        ]
        items = announcements.fetch_public_announcements(START, END, page_size=2)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[1].url.endswith("/000001/AN5.html"))
        self.assertIn("公告类别：其他", items[1].summary)
        self.assertEqual(request.call_count, 3)
        for index, call in enumerate(request.call_args_list, 1):
            params = call.kwargs["params"]
            self.assertEqual(params["page_index"], str(index))
            self.assertEqual(params["begin_time"], "2026-09-11")
            self.assertEqual(params["end_time"], "2026-09-18")
            self.assertEqual(params["f_node"], "0")

    @mock.patch.object(announcements, "request_with_retries")
    def test_declared_empty_range_is_valid(self, request: mock.Mock) -> None:
        request.return_value = response([])
        self.assertEqual(announcements.fetch_public_announcements(START, END, page_size=2), [])

    @mock.patch.object(announcements, "request_with_retries")
    def test_pending_issuer_alphanumeric_security_has_valid_detail_url(self, request: mock.Mock) -> None:
        pending = row(1)
        pending["codes"][0]["stock_code"] = "A26221"
        request.return_value = response([pending])
        items = announcements.fetch_public_announcements(START, END, page_size=2)
        self.assertTrue(items[0].url.endswith("/A26221/AN1.html"))

    @mock.patch.object(announcements, "request_with_retries")
    def test_page_limit_fails_instead_of_truncating(self, request: mock.Mock) -> None:
        request.return_value = response([row(1), row(2)], total=5)
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "needs 3 pages"):
            announcements.fetch_public_announcements(START, END, page_size=2, max_pages=2)

    @mock.patch.object(announcements, "request_with_retries")
    def test_short_page_is_not_accepted_as_complete(self, request: mock.Mock) -> None:
        request.return_value = response([row(1)], total=5)
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "incomplete"):
            announcements.fetch_public_announcements(START, END, page_size=2)

    @mock.patch.object(announcements, "request_with_retries")
    def test_repeated_page_is_rejected(self, request: mock.Mock) -> None:
        request.side_effect = [response([row(1), row(2)], total=4), response([row(1), row(2)], page=2, total=4)]
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "repeated"):
            announcements.fetch_public_announcements(START, END, page_size=2)

    @mock.patch.object(announcements, "request_with_retries")
    def test_provider_error_is_rejected(self, request: mock.Mock) -> None:
        request.return_value = mock.Mock(json=mock.Mock(return_value={"success": 0, "data": None}))
        with self.assertRaises(announcements.AnnouncementCollectionError):
            announcements.fetch_public_announcements(START, END, page_size=2)

    @mock.patch.object(announcements, "request_with_retries")
    def test_changed_total_does_not_claim_complete_range(self, request: mock.Mock) -> None:
        request.side_effect = [response([row(1), row(2)], total=4), response([row(3), row(4)], page=2, total=5)]
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "changed during pagination"):
            announcements.fetch_public_announcements(START, END, page_size=2)

    @mock.patch.object(announcements, "request_with_retries")
    def test_overlapping_pages_cannot_hide_missing_records(self, request: mock.Mock) -> None:
        request.side_effect = [response([row(1), row(2)], total=4), response([row(2), row(3)], page=2, total=4)]
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "overlapping"):
            announcements.fetch_public_announcements(START, END, page_size=2)

    @mock.patch.object(announcements, "request_with_retries")
    def test_missing_date_cannot_bypass_window(self, request: mock.Mock) -> None:
        request.return_value = response([row(1, date="")])
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "missing public announcement date"):
            announcements.fetch_public_announcements(START, END, page_size=2)


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patches = [mock.patch.object(sources, name, ()) for name in ("GLOBAL_QUERIES", "MIDDLE_EAST_QUERIES", "HKEX_QUERIES")]
        self.patches.append(mock.patch.object(sources, "TRACKED_FETCH_SOURCES", CHINA_SOURCES[:3]))
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.item = RawItem("收购目标公司", "https://example.com/a.html", "source", "https://example.com", "2026-09-17T00:00:00+08:00")

    @mock.patch.object(sources, "fetch_public_announcements")
    @mock.patch.object(sources, "fetch_source")
    def test_endpoint_denial_skips_equivalent_sources_and_recovers(self, fetch: mock.Mock, fallback: mock.Mock) -> None:
        fetch.side_effect = SourceAccessDeniedError("HTTP 403")
        fallback.return_value = [self.item]
        items, errors = sources.fetch_all_candidates(START, END)
        self.assertEqual(items, [self.item])
        self.assertEqual(len(errors), 1)
        fetch.assert_called_once()
        fallback.assert_called_once_with(START, END)

    @mock.patch.object(sources, "fetch_public_announcements")
    @mock.patch.object(sources, "fetch_source")
    def test_healthy_primary_does_not_call_fallback(self, fetch: mock.Mock, fallback: mock.Mock) -> None:
        fetch.return_value = [self.item]
        items, errors = sources.fetch_all_candidates(START, END)
        self.assertEqual(items, [self.item])
        self.assertEqual(errors, [])
        fallback.assert_not_called()

    @mock.patch.object(sources, "fetch_public_announcements")
    @mock.patch.object(sources, "fetch_source")
    def test_partial_primary_then_denial_still_recovers_complete_range(self, fetch: mock.Mock, fallback: mock.Mock) -> None:
        fetch.side_effect = [[self.item], SourceAccessDeniedError("HTTP 403")]
        fallback.return_value = [self.item]
        items, errors = sources.fetch_all_candidates(START, END)
        self.assertEqual(items, [self.item])
        self.assertEqual(len(errors), 1)
        self.assertEqual(fetch.call_count, 2)
        fallback.assert_called_once_with(START, END)

    @mock.patch.object(sources, "fetch_public_announcements")
    @mock.patch.object(sources, "fetch_source")
    def test_empty_primary_also_recovers(self, fetch: mock.Mock, fallback: mock.Mock) -> None:
        fetch.return_value = []
        fallback.return_value = [self.item]
        items, _ = sources.fetch_all_candidates(START, END)
        self.assertEqual(items, [self.item])
        self.assertEqual(fetch.call_count, 3)
        fallback.assert_called_once()

    @mock.patch.object(sources, "fetch_public_announcements")
    @mock.patch.object(sources, "fetch_source")
    def test_incomplete_fallback_propagates_failure(self, fetch: mock.Mock, fallback: mock.Mock) -> None:
        fetch.return_value = []
        fallback.side_effect = announcements.AnnouncementCollectionError("incomplete")
        with self.assertRaisesRegex(announcements.AnnouncementCollectionError, "incomplete"):
            sources.fetch_all_candidates(START, END)


if __name__ == "__main__":
    unittest.main()
