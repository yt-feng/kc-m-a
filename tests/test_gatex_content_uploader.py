from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gatex_content_uploader.py"
SPEC = importlib.util.spec_from_file_location("gatex_content_uploader", SCRIPT_PATH)
assert SPEC and SPEC.loader
UPLOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPLOADER)


class GatexProgressManifestTests(unittest.TestCase):
    def write_manifest(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "progress.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_complete_progress_manifest_returns_docx_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_manifest(
                Path(tmp),
                {
                    "requested_count": 2,
                    "count": 2,
                    "files": ["case_reports/one.docx", "case_reports/two.docx"],
                },
            )

            result = UPLOADER.read_progress_docx_paths(path)

        self.assertEqual(result, [Path("case_reports/one.docx"), Path("case_reports/two.docx")])

    def test_partial_progress_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_manifest(
                Path(tmp),
                {
                    "requested_count": 4,
                    "count": 3,
                    "files": ["case_reports/one.docx", "case_reports/two.docx", "case_reports/three.docx"],
                },
            )

            with self.assertRaisesRegex(ValueError, r"incomplete \(3/4\)"):
                UPLOADER.read_progress_docx_paths(path)

    def test_mismatched_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_manifest(
                Path(tmp),
                {
                    "requested_count": 2,
                    "count": 2,
                    "files": ["case_reports/one.docx"],
                },
            )

            with self.assertRaisesRegex(ValueError, "count does not match"):
                UPLOADER.read_progress_docx_paths(path)

    def test_schema_cache_error_gets_actionable_hint(self) -> None:
        detail = '{"ok":false,"error":"Could not find the \'cover_art_direction\' column of \'report_review_items\' in the schema cache"}'

        hint = UPLOADER.gatex_error_hint(detail)

        self.assertIn("schema cache", hint)
        self.assertIn("Supabase migration", hint)


if __name__ == "__main__":
    unittest.main()
