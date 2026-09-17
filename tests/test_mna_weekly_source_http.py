from __future__ import annotations

import unittest
from unittest import mock

import requests

from mna_weekly_tracker import sources_fixed as sources
from mna_weekly_tracker.config import CHINA_SOURCES
from mna_weekly_tracker.weekly_windows import window_from_dates


def response(status: int) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.url = "https://example.com/announcements"
    return result


class SourceHttpTests(unittest.TestCase):
    @mock.patch.object(sources.time, "sleep")
    @mock.patch.object(sources.requests, "request")
    def test_access_denied_does_not_retry(self, request: mock.Mock, sleep: mock.Mock) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                request.reset_mock()
                request.return_value = response(status)
                with self.assertRaisesRegex(sources.SourceAccessDeniedError, f"HTTP {status}"):
                    sources.request_with_retries("GET", "https://example.com/announcements")
                request.assert_called_once()
                sleep.assert_not_called()

    @mock.patch.object(sources.time, "sleep")
    @mock.patch.object(sources.requests, "request")
    def test_transient_errors_still_retry(self, request: mock.Mock, sleep: mock.Mock) -> None:
        for status in (429, 503):
            with self.subTest(status=status):
                request.reset_mock()
                sleep.reset_mock()
                request.side_effect = [response(status), response(200)]
                self.assertEqual(sources.request_with_retries("GET", "https://example.com/announcements").status_code, 200)
                self.assertEqual(request.call_count, 2)
                sleep.assert_called_once()

    @mock.patch.object(sources, "request_with_retries")
    def test_cninfo_access_denial_stops_keyword_loop(self, request: mock.Mock) -> None:
        request.side_effect = sources.SourceAccessDeniedError("HTTP 403")
        window = window_from_dates("2026-09-11", "2026-09-18")
        with self.assertRaises(sources.SourceAccessDeniedError):
            sources.fetch_cninfo(CHINA_SOURCES[0], window.start, window.end)
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
