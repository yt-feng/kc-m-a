"""CLI entrypoint for weekly M&A case tracking."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from .deepseek import structure_cases
from .excel import build_workbook, save_workbook
from .sources_rich import RawItem, fetch_all_candidates, week_window

LOGGER = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def label(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def date_label(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def parse_raw_json(path: str | Path) -> list[RawItem]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RawItem(**row) for row in data]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect and structure weekly global M&A cases into Excel.")
    parser.add_argument("--days", type=int, default=int(os.getenv("MNA_LOOKBACK_DAYS", "7")), help="Lookback window in days. Default: 7.")
    parser.add_argument("--output-dir", default=os.getenv("MNA_OUTPUT_DIR", "outputs"), help="Directory to write weekly Excel files.")
    parser.add_argument("--max-raw-items", type=int, default=int(os.getenv("MAX_RAW_ITEMS", "450")), help="Maximum raw candidates to keep. Default: 450.")
    parser.add_argument("--max-cases", type=int, default=int(os.getenv("MAX_STRUCTURED_CASES", "120")), help="Maximum structured cases in the Excel. Default: 120.")
    parser.add_argument("--raw-json", default="", help="Optional local raw candidates JSON for debugging without network collection.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    start, end = week_window(args.days, "Asia/Shanghai")
    start_label = label(start)
    end_label = label(end)
    if args.raw_json:
        LOGGER.info("Loading raw candidates from %s", args.raw_json)
        raw_items = parse_raw_json(args.raw_json)
        errors: list[str] = []
    else:
        LOGGER.info("Collecting candidates for %s to %s", start_label, end_label)
        raw_items, errors = fetch_all_candidates(start, end, max_items=args.max_raw_items)
    LOGGER.info("Collected %s raw candidates", len(raw_items))
    cases = structure_cases(raw_items, start_label=start_label, end_label=end_label, max_cases=args.max_cases)
    LOGGER.info("Structured %s cases", len(cases))
    output_dir = Path(args.output_dir)
    filename = f"并购案例一览_{date_label(start)}_{date_label(end)}.xlsx"
    output_path = output_dir / filename
    wb = build_workbook(cases, raw_items, errors, start_label=start_label, end_label=end_label)
    save_workbook(wb, output_path)
    LOGGER.info("Wrote %s", output_path)
    print(output_path)


if __name__ == "__main__":
    main()
