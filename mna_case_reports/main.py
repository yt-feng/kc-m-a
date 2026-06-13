"""CLI for generating M&A case analysis DOCX reports."""

from __future__ import annotations

import argparse
import json
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .case_selection import (
    CaseBrief,
    briefs_from_latest_weekly_workbook,
    choose_balanced,
    discover_backfill_cases,
    extended_pool_briefs,
    has_usable_source_url,
    is_report_ready_candidate,
    lightweight_weekly_candidates,
    raw_items_from_latest_weekly_workbook,
    save_manifest,
    seed_briefs,
    summarize_raw_items,
)
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
    parser.add_argument("--offset", type=int, default=int(os.getenv("REPORT_OFFSET", "0")), help="Skip this many selected cases before generation; useful for batched backfills.")
    parser.add_argument("--continue-on-error", action="store_true", default=os.getenv("REPORT_CONTINUE_ON_ERROR", "1") == "1", help="Write partial outputs instead of failing the whole batch on one bad report.")
    parser.add_argument("--weekly-output-dir", default=os.getenv("REPORT_WEEKLY_OUTPUT_DIR", "outputs"), help="Directory containing weekly Excel candidate workbooks.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def write_progress(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_pool_count(requested_count: int) -> int:
    extra = int(os.getenv("REPORT_EXTRA_CANDIDATES", "8"))
    multiplier = int(os.getenv("REPORT_CANDIDATE_MULTIPLIER", "3"))
    return max(requested_count, requested_count + extra, requested_count * multiplier)


def domestic_written(briefs: list[CaseBrief]) -> int:
    return sum(1 for brief in briefs if brief.is_domestic)


def should_try_candidate(brief: CaseBrief, *, written_briefs: list[CaseBrief], requested_count: int, min_domestic: int) -> bool:
    remaining_slots = requested_count - len(written_briefs)
    if remaining_slots <= 0:
        return False
    domestic_needed = max(min(min_domestic, requested_count) - domestic_written(written_briefs), 0)
    if domestic_needed >= remaining_slots and not brief.is_domestic:
        return False
    return True


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    output_root = Path(args.output_root)
    ensure_category_dirs(output_root)
    pool_count = candidate_pool_count(args.count)

    if args.mode == "backfill":
        LOGGER.info("Discovering backfill cases")
        briefs = discover_backfill_cases(max((args.offset + pool_count) * 3, 30))
    else:
        LOGGER.info("Loading weekly report candidates from latest structured Excel")
        briefs = briefs_from_latest_weekly_workbook(Path(args.weekly_output_dir))
        required_domestic = min(args.min_domestic, args.count)
        ready_count = sum(1 for brief in briefs if is_report_ready_candidate(brief))
        source_ready_count = sum(1 for brief in briefs if is_report_ready_candidate(brief) and has_usable_source_url(brief.source_url))
        domestic_ready_count = sum(1 for brief in briefs if brief.is_domestic and is_report_ready_candidate(brief))
        domestic_source_ready_count = sum(1 for brief in briefs if brief.is_domestic and is_report_ready_candidate(brief) and has_usable_source_url(brief.source_url))
        LOGGER.info(
            "Weekly Excel candidate pool: total=%s ready=%s source_ready=%s domestic_ready=%s domestic_source_ready=%s",
            len(briefs),
            ready_count,
            source_ready_count,
            domestic_ready_count,
            domestic_source_ready_count,
        )
        if ready_count < args.count or source_ready_count < args.count or domestic_source_ready_count < required_domestic:
            LOGGER.info("Summarizing raw candidates from latest weekly Excel because structured Excel source-ready pool is below requested count")
            excel_raw_items = raw_items_from_latest_weekly_workbook(Path(args.weekly_output_dir), max_items=args.max_raw_items)
            excel_raw_briefs = summarize_raw_items(excel_raw_items, target_count=max(pool_count * 3, 12))
            LOGGER.info("Raw weekly Excel candidate pool: raw=%s summarized=%s", len(excel_raw_items), len(excel_raw_briefs))
            briefs.extend(excel_raw_briefs)
            ready_count = sum(1 for brief in briefs if is_report_ready_candidate(brief))
            source_ready_count = sum(1 for brief in briefs if is_report_ready_candidate(brief) and has_usable_source_url(brief.source_url))
            domestic_source_ready_count = sum(1 for brief in briefs if brief.is_domestic and is_report_ready_candidate(brief) and has_usable_source_url(brief.source_url))
        if ready_count < args.count or source_ready_count < args.count or domestic_source_ready_count < required_domestic:
            LOGGER.info("Collecting live weekly report candidates because Excel source-ready pool is below requested count")
            raw_items = lightweight_weekly_candidates(args.days, args.max_raw_items)
            live_briefs = summarize_raw_items(raw_items, target_count=max(pool_count * 3, 12))
            LOGGER.info("Live weekly candidate pool: raw=%s summarized=%s", len(raw_items), len(live_briefs))
            briefs.extend(live_briefs)
        if os.getenv("REPORT_ALLOW_STATIC_WEEKLY_POOL", "0") == "1":
            LOGGER.info("Static weekly report pool is explicitly enabled")
            briefs.extend(extended_pool_briefs())
            briefs.extend(seed_briefs())

    selected_all = choose_balanced(briefs, count=args.offset + pool_count, min_domestic=args.min_domestic, report_root=output_root)
    selected = selected_all[args.offset :]
    effective_min_domestic = min(args.min_domestic, sum(1 for brief in selected if brief.is_domestic))
    if effective_min_domestic < args.min_domestic:
        LOGGER.info("Lowering domestic quota for this run because selected candidate pool has only %s domestic candidates", effective_min_domestic)
    timestamp = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    run_label = f"{args.mode}_{timestamp}"
    manifest_path = output_root / "_manifests" / f"{run_label}.json"
    progress_path = output_root / "_manifests" / f"{run_label}_progress.json"

    written: list[str] = []
    written_briefs: list[CaseBrief] = []
    failures: list[dict[str, str]] = []
    attempted = 0
    for index, brief in enumerate(selected, start=1):
        if len(written) >= args.count:
            break
        if not should_try_candidate(brief, written_briefs=written_briefs, requested_count=args.count, min_domestic=effective_min_domestic):
            LOGGER.info("Skipping candidate because remaining slots are reserved for domestic reports: %s [%s]", brief.case_name, brief.category)
            continue
        attempted += 1
        if not is_report_ready_candidate(brief):
            LOGGER.info("Skipping report candidate before research because preflight is weak: %s [%s]", brief.case_name, brief.category)
            failures.append({"case_name": brief.case_name, "category": brief.category, "error": "preflight rejected weak report candidate"})
            write_progress(progress_path, {
                "mode": args.mode,
                "run_label": run_label,
                "requested_count": args.count,
                "candidate_count": len(selected),
                "attempted_count": attempted,
                "offset": args.offset,
                "written": written,
                "failures": failures,
            })
            continue
        LOGGER.info("Generating report attempt %s from candidate %s/%s: %s [%s]", attempted, index, len(selected), brief.case_name, brief.category)
        write_progress(progress_path, {
            "mode": args.mode,
            "run_label": run_label,
            "requested_count": args.count,
            "candidate_count": len(selected),
            "attempted_count": attempted,
            "offset": args.offset,
            "current_case": brief.to_dict(),
            "written": written,
            "failures": failures,
        })
        try:
            article = generate_article(brief)
            path = write_docx(article, category=brief.category, output_root=output_root, run_label=run_label)
            written.append(str(path))
            written_briefs.append(brief)
            save_manifest(manifest_path, written_briefs)
            LOGGER.info("Wrote report: %s", path)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Report generation failed for %s: %s", brief.case_name, exc)
            LOGGER.debug("Traceback for %s:\n%s", brief.case_name, traceback.format_exc())
            failures.append({"case_name": brief.case_name, "category": brief.category, "error": str(exc)[:2000]})
            if not args.continue_on_error:
                raise
        finally:
            write_progress(progress_path, {
                "mode": args.mode,
                "run_label": run_label,
                "requested_count": args.count,
                "candidate_count": len(selected),
                "attempted_count": attempted,
                "offset": args.offset,
                "written": written,
                "failures": failures,
            })

    result = {
        "mode": args.mode,
        "run_label": run_label,
        "requested_count": args.count,
        "candidate_count": len(selected),
        "attempted_count": attempted,
        "count": len(written),
        "files": written,
        "failures": failures,
    }
    write_progress(progress_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not written:
        raise RuntimeError("All report generations failed; see progress manifest for details.")


if __name__ == "__main__":
    main()
