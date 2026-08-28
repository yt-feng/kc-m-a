from __future__ import annotations

import os
import unittest
from unittest import mock

import requests

from mna_weekly_tracker.deepseek import DeepSeekError, deepseek_chat


def successful_response(content: str = '{"cases":[]}') -> mock.Mock:
    response = mock.Mock(spec=requests.Response)
    response.status_code = 200
    response.headers = {}
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def error_response(status_code: int, detail: str, *, retry_after: str = "") -> mock.Mock:
    response = mock.Mock(spec=requests.Response)
    response.status_code = status_code
    response.headers = {"Retry-After": retry_after} if retry_after else {}
    response.text = detail
    return response


class WeeklyDeepSeekRetryTests(unittest.TestCase):
    env = {
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_HTTP_ATTEMPTS": "4",
        "DEEPSEEK_RETRY_BASE_SECONDS": "2",
        "DEEPSEEK_RETRY_MAX_SECONDS": "30",
    }

    @mock.patch.dict(os.environ, env, clear=False)
    @mock.patch("mna_weekly_tracker.deepseek.random.uniform", return_value=0.0)
    @mock.patch("mna_weekly_tracker.deepseek.time.sleep")
    @mock.patch("mna_weekly_tracker.deepseek.requests.post")
    def test_premature_chunked_response_is_retried(
        self,
        post: mock.Mock,
        sleep: mock.Mock,
        _uniform: mock.Mock,
    ) -> None:
        post.side_effect = [
            requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
            successful_response(),
        ]

        content = deepseek_chat([{"role": "user", "content": "return JSON"}])

        self.assertEqual(content, '{"cases":[]}')
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2.0)

    @mock.patch.dict(os.environ, env, clear=False)
    @mock.patch("mna_weekly_tracker.deepseek.random.uniform", return_value=0.0)
    @mock.patch("mna_weekly_tracker.deepseek.time.sleep")
    @mock.patch("mna_weekly_tracker.deepseek.requests.post")
    def test_rate_limit_honors_retry_after(
        self,
        post: mock.Mock,
        sleep: mock.Mock,
        _uniform: mock.Mock,
    ) -> None:
        post.side_effect = [
            error_response(429, "rate limited", retry_after="7"),
            successful_response(),
        ]

        deepseek_chat([{"role": "user", "content": "return JSON"}])

        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(7.0)

    @mock.patch.dict(os.environ, env, clear=False)
    @mock.patch("mna_weekly_tracker.deepseek.random.uniform", return_value=0.0)
    @mock.patch("mna_weekly_tracker.deepseek.time.sleep")
    @mock.patch("mna_weekly_tracker.deepseek.requests.post")
    def test_server_error_uses_exponential_backoff(
        self,
        post: mock.Mock,
        sleep: mock.Mock,
        _uniform: mock.Mock,
    ) -> None:
        post.side_effect = [
            error_response(503, "temporarily unavailable"),
            error_response(503, "temporarily unavailable"),
            successful_response(),
        ]

        deepseek_chat([{"role": "user", "content": "return JSON"}])

        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(2.0), mock.call(4.0)])

    @mock.patch.dict(os.environ, env, clear=False)
    @mock.patch("mna_weekly_tracker.deepseek.time.sleep")
    @mock.patch("mna_weekly_tracker.deepseek.requests.post")
    def test_payment_error_fails_without_retry(self, post: mock.Mock, sleep: mock.Mock) -> None:
        post.return_value = error_response(402, "Insufficient Balance")

        with self.assertRaisesRegex(DeepSeekError, "API error 402.*Insufficient Balance"):
            deepseek_chat([{"role": "user", "content": "return JSON"}])

        post.assert_called_once()
        sleep.assert_not_called()

    @mock.patch.dict(os.environ, {**env, "DEEPSEEK_HTTP_ATTEMPTS": "3"}, clear=False)
    @mock.patch("mna_weekly_tracker.deepseek.random.uniform", return_value=0.0)
    @mock.patch("mna_weekly_tracker.deepseek.time.sleep")
    @mock.patch("mna_weekly_tracker.deepseek.requests.post")
    def test_exhausted_transport_retries_report_attempt_count(
        self,
        post: mock.Mock,
        _sleep: mock.Mock,
        _uniform: mock.Mock,
    ) -> None:
        post.side_effect = requests.exceptions.Timeout("timed out")

        with self.assertRaisesRegex(DeepSeekError, "failed after 3 attempts.*timed out"):
            deepseek_chat([{"role": "user", "content": "return JSON"}])

        self.assertEqual(post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
