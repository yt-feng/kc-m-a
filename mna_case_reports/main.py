"""CLI for generating M&A case analysis DOCX reports."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .case_selection import choose_balanced, candidates_from_weekly, discover_backfill_cases, save_manifest, seed_briefs, summarize_raw_items
from .config import CATEGORY_FOLDER_NAMES, CATEGORIES
from .docx_writer import write_docx
from .report_generation import generate_article

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def ensure_category_dirs(root: Path) -> None:
    for category in CATEGORIES:
        (root / CATEGORY_FOLDER_NAMES[category]).mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly M&A case analysis reports in DOCX format.")
    parser.add_argument("--mode", choices=["weekly", "backfill"], default=os.getenv("REPORT_MODE", "weekly"))
    parser.add_argument("--days", type=int, default=int(os.getenv("REPORT_LOOKBACK_DAYS", "7")))
    parser.add_argument("--count", type=int, default=int(os.getenv("REPORT_COUNT", "4")))
    parser.add_argument("--min-domestic", type=int, default=int(os.getenv("REPORT_MIN_DOMESTIC", "2")))
    parser.add_argument("--output-root", default=os.getenv("REPORT_OUTPUT_ROOT", "case_reports"))
    parser.add_argument("--max-raw-items", type=int, default=int(os.getenv("REPORT_MAX_RAW_ITEMS", "450")))
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    output_root = Path(args.output_root)
    ensure_category_dirs(output_root)

    if args.mode == "backfill":
        LOGGER.info("Discovering backfill cases")
        briefs = discover_backfill_cases(max(args.count * 4, 30))
    else:
        LOGGER.info("Collecting weekly report candidates")
        raw_items = candidates_from_weekly(args.days, args.max_raw_items)
        briefs = summarize_raw_items(raw_items, target_count=max(args.count * 3, 12))
        briefs.extend(seed_briefs())

    selected = choose_balanced(briefs, count=args.count, min_domestic=args.min_domestic, report_root=output_root)
    timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    save_manifest(output_root / "_manifests" / f"{args.mode}_{timestamp}.json", selected)

    written: list[str] = []
    for brief in selected:
        LOGGER.info("Generating report: %s [%s]", brief.case_name, brief.category)
        article = generate_article(brief)
        path = write_docx(article, category=brief.category, output_root=output_root)
        written.append(str(path))
        LOGGER.info("Wrote report: %s", path)

    print(json.dumps({"mode": args.mode, "count": len(written), "files": written}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
