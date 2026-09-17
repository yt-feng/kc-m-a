from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mna_weekly_tracker.config import OUTPUT_COLUMNS
from mna_weekly_tracker.main import generate_window, main, parse_raw_json
from mna_weekly_tracker.sources_rich import RawItem
from mna_weekly_tracker.weekly_windows import window_from_dates


class WeeklyGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output_dir = Path(self.temp.name) / "outputs"
        self.diagnostic_dir = Path(self.temp.name) / "diagnostics"
        self.window = window_from_dates("2026-09-11", "2026-09-18")
        self.args = argparse.Namespace(
            output_dir=str(self.output_dir),
            diagnostic_dir=str(self.diagnostic_dir),
            raw_json="",
            max_raw_items=450,
            max_cases=120,
        )
        self.raw = RawItem(
            title="Example Buyer acquires Example Target",
            url="https://example.com/news/acquisition",
            source_name="Example disclosure",
            source_url="https://example.com/",
            published_at="2026-09-15T10:00:00+08:00",
            summary="Example Buyer announced its acquisition of Example Target.",
        )
        self.collect = patch(
            "mna_weekly_tracker.main.fetch_all_candidates",
            return_value=([self.raw], ["One source unavailable"]),
        )
        self.collect.start()
        self.addCleanup(self.collect.stop)

    def diagnostic(self) -> dict:
        paths = list(self.diagnostic_dir.glob("*.diagnostic.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_raw_checkpoint_precedes_model_and_survives_failure(self) -> None:
        error = RuntimeError("model service unavailable")

        def fail_structuring(*args, **kwargs):
            progress = self.diagnostic()
            self.assertEqual(progress["status"], "running")
            self.assertEqual(progress["stage"], "structuring")
            self.assertEqual(parse_raw_json(progress["raw_checkpoint_path"]), [self.raw])
            raise error

        with patch("mna_weekly_tracker.main.structure_cases", side_effect=fail_structuring):
            with self.assertRaises(RuntimeError) as caught:
                generate_window(self.args, self.window)

        self.assertIs(caught.exception, error)
        result = self.diagnostic()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "structuring")
        self.assertEqual(result["raw_count"], 1)
        self.assertEqual(result["source_errors"], ["One source unavailable"])
        self.assertEqual(result["failure"], {"type": "RuntimeError", "message": str(error)})
        self.assertIsNone(result["validation"])
        self.assertEqual(result["window"]["start"], self.window.start.isoformat())
        self.assertEqual(parse_raw_json(result["raw_checkpoint_path"]), [self.raw])

    def test_zero_cases_remain_a_failure_with_validation_details(self) -> None:
        with patch("mna_weekly_tracker.main.structure_cases", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "case_rows=0"):
                generate_window(self.args, self.window)

        result = self.diagnostic()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "validating")
        self.assertEqual(result["structured_count"], 0)
        self.assertEqual(result["case_count"], 0)
        self.assertIn("no_case_rows", {issue["type"] for issue in result["validation"]["issues"]})

    def test_success_records_real_workbook_validation(self) -> None:
        case = {column: "已披露" for column in OUTPUT_COLUMNS}
        case.update({"并购方": "Example Buyer", "目标方": "Example Target", "URL": self.raw.url})
        with patch("mna_weekly_tracker.main.structure_cases", return_value=[case]):
            output_path = generate_window(self.args, self.window)

        self.assertTrue(output_path.is_file())
        result = self.diagnostic()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["stage"], "complete")
        self.assertEqual(result["structured_count"], 1)
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["validation"]["case_rows"], 1)
        self.assertEqual(result["validation"]["issue_count"], 0)
        self.assertIsNone(result["failure"])

    def test_diagnostics_redact_credential_values(self) -> None:
        credential = "test-private-credential-value"
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": credential}):
            with patch("mna_weekly_tracker.main.structure_cases", side_effect=RuntimeError(f"Bearer {credential}")):
                with self.assertRaises(RuntimeError):
                    generate_window(self.args, self.window)

        result = self.diagnostic()
        serialized = json.dumps(result)
        self.assertNotIn(credential, serialized)
        self.assertNotIn("DEEPSEEK_API_KEY", serialized)
        self.assertIn("[REDACTED]", result["failure"]["message"])

    def test_recovery_windows_have_distinct_checkpoints(self) -> None:
        prior_window = window_from_dates("2026-09-04", "2026-09-11")
        with patch("mna_weekly_tracker.main.structure_cases", side_effect=RuntimeError("failed")):
            for window in (prior_window, self.window):
                with self.assertRaises(RuntimeError):
                    generate_window(self.args, window)

        self.assertEqual(len(list(self.diagnostic_dir.glob("*.raw.json"))), 2)
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in self.diagnostic_dir.glob("*.diagnostic.json")]
        self.assertEqual({report["window"]["start"] for report in reports}, {
            prior_window.start.isoformat(), self.window.start.isoformat(),
        })

    def test_main_lists_only_completed_workbooks_when_later_window_fails(self) -> None:
        prior_window = window_from_dates("2026-09-04", "2026-09-11")
        prior_path = prior_window.output_path(self.output_dir)
        self.args.write_generated_list = str(Path(self.temp.name) / "generated.txt")
        self.args.verbose = False
        self.args.recover_missing_weeks = False
        with patch("mna_weekly_tracker.main.parse_args", return_value=self.args):
            with patch("mna_weekly_tracker.main.resolve_target_windows", return_value=[prior_window, self.window]):
                with patch("mna_weekly_tracker.main.generate_window", side_effect=[prior_path, RuntimeError("failed")]):
                    with self.assertRaisesRegex(RuntimeError, "failed"):
                        main()

        paths = Path(self.args.write_generated_list).read_text(encoding="utf-8").splitlines()
        self.assertEqual(paths, [str(prior_path)])


if __name__ == "__main__":
    unittest.main()
