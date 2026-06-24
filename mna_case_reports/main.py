"""CLI for generating M&A case analysis DOCX reports."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import signal
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from queue import Empty
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
from .report_generation import generate_article, set_progress_queue

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class CandidateTimeoutError(TimeoutError):
    pass


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def action_notice(message: str) -> None:
    safe = str(message).replace("\n", " ")[:1000]
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::notice::{safe}", flush=True)
    else:
        print(safe, flush=True)


def env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def candidate_timeout(seconds: int, case_name: str):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handle_timeout(_signum, _frame):
        raise CandidateTimeoutError(f"Candidate timed out after {seconds}s: {case_name}")

    previous_handler = signal.signal(signal.SIGALRM, _handle_timeout)
    previous_alarm = signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_alarm:
            signal.alarm(previous_alarm)


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
    pool = max(requested_count, requested_count + extra, requested_count * multiplier)
    cap = int(os.getenv("REPORT_CANDIDATE_POOL_MAX", "0"))
    return min(pool, cap) if cap > 0 else pool


def max_generation_attempts(requested_count: int) -> int:
    configured = int(os.getenv("REPORT_MAX_GENERATION_ATTEMPTS", "0"))
    if configured > 0:
        return configured
    return max(requested_count + 3, requested_count * 3)


def per_candidate_timeout_seconds() -> int:
    return int(os.getenv("REPORT_PER_CANDIDATE_TIMEOUT_SECONDS", "900"))


def use_static_smoke_pool(args: argparse.Namespace, pool_count: int) -> bool:
    return (
        args.mode == "weekly"
        and args.count == 1
        and pool_count <= 1
        and env_flag("REPORT_ALLOW_STATIC_WEEKLY_POOL")
        and env_flag("REPORT_STATIC_SMOKE_FIRST", default=True)
    )


def _generate_article_worker(brief: CaseBrief, queue: multiprocessing.Queue) -> None:
    try:
        set_progress_queue(queue)
        article = generate_article(brief)
        queue.put({"type": "result", "ok": True, "article": article})
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(f"WORKER_EXCEPTION case={brief.case_name} error={str(exc)[:1000]}", flush=True)
        print(tb, flush=True)
        queue.put({"type": "result", "ok": False, "error": str(exc), "traceback": tb})


def generate_article_with_timeout(brief: CaseBrief, timeout_seconds: int) -> dict[str, object]:
    if timeout_seconds <= 0:
        return generate_article(brief)
    heartbeat_seconds = int(os.getenv("REPORT_WORKER_HEARTBEAT_SECONDS", "30"))
    allow_partial_draft = env_flag("REPORT_ALLOW_DRAFT_ON_VALIDATION_FAILURE")
    ctx = multiprocessing.get_context(os.getenv("REPORT_WORKER_START_METHOD", "fork"))
    result_queue: multiprocessing.Queue = ctx.Queue()
    process = ctx.Process(target=_generate_article_worker, args=(brief, result_queue), daemon=True)
    process.start()
    started = time.monotonic()
    deadline = started + timeout_seconds
    last_heartbeat = 0.0
    result: dict[str, object] | None = None
    partial_article: dict[str, object] | None = None
    partial_stage = ""
    while True:
        now = time.monotonic()
        elapsed = int(now - started)
        if elapsed - last_heartbeat >= heartbeat_seconds:
            last_heartbeat = elapsed
            action_notice(f"candidate_heartbeat case={brief.case_name} elapsed_s={elapsed} timeout_s={timeout_seconds} alive={process.is_alive()}")
        timeout = max(0.2, min(2.0, deadline - now))
        try:
            message = result_queue.get(timeout=timeout)
        except Empty:
            message = None
        if isinstance(message, dict):
            if message.get("type") == "event":
                action_notice(str(message.get("message") or "worker_event"))
            elif message.get("type") == "partial":
                article = message.get("article")
                if isinstance(article, dict):
                    partial_article = article
                    partial_stage = str(message.get("stage") or "partial")
                    action_notice(
                        f"candidate_partial_draft case={brief.case_name} stage={partial_stage} "
                        f"length={message.get('length')} hard={message.get('hard')} quality={message.get('quality')}"
                    )
            elif message.get("type") == "result":
                result = message
                break
        if not process.is_alive():
            break
        if time.monotonic() >= deadline:
            process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join(5)
            if allow_partial_draft and partial_article is not None:
                action_notice(f"candidate_timeout_returning_partial case={brief.case_name} stage={partial_stage}")
                return partial_article
            raise CandidateTimeoutError(f"Candidate worker timed out after {timeout_seconds}s: {brief.case_name}")

    process.join(5)
    while True:
        try:
            message = result_queue.get_nowait()
        except Empty:
            break
        if isinstance(message, dict) and message.get("type") == "event":
            action_notice(str(message.get("message") or "worker_event"))
        elif isinstance(message, dict) and message.get("type") == "partial":
            article = message.get("article")
            if isinstance(article, dict):
                partial_article = article
                partial_stage = str(message.get("stage") or "partial")
                action_notice(
                    f"candidate_partial_draft case={brief.case_name} stage={partial_stage} "
                    f"length={message.get('length')} hard={message.get('hard')} quality={message.get('quality')}"
                )
        elif isinstance(message, dict) and message.get("type") == "result" and result is None:
            result = message
    if result is None:
        if allow_partial_draft and partial_article is not None:
            action_notice(f"candidate_exited_returning_partial case={brief.case_name} stage={partial_stage} exitcode={process.exitcode}")
            return partial_article
        raise RuntimeError(f"Candidate worker exited without returning a result: {brief.case_name} exitcode={process.exitcode}")
    if result.get("ok"):
        article = result.get("article")
        if not isinstance(article, dict):
            raise RuntimeError(f"Candidate worker returned invalid article payload: {brief.case_name}")
        return article
    error = str(result.get("error") or "unknown worker error")
    tb = str(result.get("traceback") or "")
    action_notice(f"candidate_worker_error case={brief.case_name} error={error[:900]}")
    if tb:
        print(f"WORKER_TRACEBACK case={brief.case_name}", flush=True)
        print(tb, flush=True)
    LOGGER.error("Worker traceback for %s:\n%s", brief.case_name, tb)
    if allow_partial_draft and partial_article is not None:
        action_notice(f"candidate_error_returning_partial case={brief.case_name} stage={partial_stage} error={error[:400]}")
        return partial_article
    raise RuntimeError(error)


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
    max_attempts = max_generation_attempts(args.count)
    candidate_timeout_seconds = per_candidate_timeout_seconds()
    action_notice(
        f"report_run_start mode={args.mode} count={args.count} min_domestic={args.min_domestic} "
        f"pool_count={pool_count} max_attempts={max_attempts} candidate_timeout_s={candidate_timeout_seconds}"
    )
    allow_weak_single_candidate = args.count == 1 and os.getenv("REPORT_ALLOW_WEAK_SINGLE_CANDIDATE", "0") == "1"

    if args.mode == "backfill":
        LOGGER.info("Discovering backfill cases")
        action_notice("candidate_pool_stage=backfill_discovery_start")
        briefs = discover_backfill_cases(max((args.offset + pool_count) * 3, 30))
        action_notice(f"candidate_pool_stage=backfill_discovery_done candidates={len(briefs)}")
    elif use_static_smoke_pool(args, pool_count):
        LOGGER.info("Using static-first weekly smoke-test candidate pool")
        action_notice("candidate_pool_stage=static_smoke_start")
        briefs = extended_pool_briefs()
        briefs.extend(seed_briefs())
        action_notice(f"candidate_pool_stage=static_smoke_done candidates={len(briefs)}")
    else:
        LOGGER.info("Loading weekly report candidates from latest structured Excel")
        action_notice("candidate_pool_stage=excel_structured_start")
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
        action_notice(
            f"candidate_pool_stage=excel_structured_done total={len(briefs)} ready={ready_count} "
            f"source_ready={source_ready_count} domestic_source_ready={domestic_source_ready_count}"
        )
        if ready_count < args.count or source_ready_count < args.count or domestic_source_ready_count < required_domestic:
            LOGGER.info("Summarizing raw candidates from latest weekly Excel because structured Excel source-ready pool is below requested count")
            action_notice(f"candidate_pool_stage=excel_raw_summary_start max_items={args.max_raw_items}")
            excel_raw_items = raw_items_from_latest_weekly_workbook(Path(args.weekly_output_dir), max_items=args.max_raw_items)
            excel_raw_briefs = summarize_raw_items(excel_raw_items, target_count=max(pool_count * 3, 12))
            LOGGER.info("Raw weekly Excel candidate pool: raw=%s summarized=%s", len(excel_raw_items), len(excel_raw_briefs))
            action_notice(f"candidate_pool_stage=excel_raw_summary_done raw={len(excel_raw_items)} summarized={len(excel_raw_briefs)}")
            briefs.extend(excel_raw_briefs)
            ready_count = sum(1 for brief in briefs if is_report_ready_candidate(brief))
            source_ready_count = sum(1 for brief in briefs if is_report_ready_candidate(brief) and has_usable_source_url(brief.source_url))
            domestic_source_ready_count = sum(1 for brief in briefs if brief.is_domestic and is_report_ready_candidate(brief) and has_usable_source_url(brief.source_url))
        if ready_count < args.count or source_ready_count < args.count or domestic_source_ready_count < required_domestic:
            LOGGER.info("Collecting live weekly report candidates because Excel source-ready pool is below requested count")
            action_notice(f"candidate_pool_stage=live_weekly_start days={args.days} max_items={args.max_raw_items}")
            raw_items = lightweight_weekly_candidates(args.days, args.max_raw_items)
            live_briefs = summarize_raw_items(raw_items, target_count=max(pool_count * 3, 12))
            LOGGER.info("Live weekly candidate pool: raw=%s summarized=%s", len(raw_items), len(live_briefs))
            action_notice(f"candidate_pool_stage=live_weekly_done raw={len(raw_items)} summarized={len(live_briefs)}")
            briefs.extend(live_briefs)
        if os.getenv("REPORT_ALLOW_STATIC_WEEKLY_POOL", "0") == "1":
            LOGGER.info("Static weekly report pool is explicitly enabled")
            action_notice("candidate_pool_stage=static_append_start")
            briefs.extend(extended_pool_briefs())
            briefs.extend(seed_briefs())
            action_notice(f"candidate_pool_stage=static_append_done total={len(briefs)}")

    selected_all = choose_balanced(
        briefs,
        count=args.offset + pool_count,
        min_domestic=args.min_domestic,
        report_root=output_root,
        readiness_first=args.count <= 1,
    )
    selected = selected_all[args.offset :]
    action_notice("selected_candidates=" + " | ".join(f"{idx+1}.{brief.case_name}" for idx, brief in enumerate(selected[:max_attempts])))
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
        if attempted >= max_attempts:
            LOGGER.warning("Stopping report generation after %s attempts for requested_count=%s; written=%s", attempted, args.count, len(written))
            break
        if not should_try_candidate(brief, written_briefs=written_briefs, requested_count=args.count, min_domestic=effective_min_domestic):
            LOGGER.info("Skipping candidate because remaining slots are reserved for domestic reports: %s [%s]", brief.case_name, brief.category)
            continue
        attempted += 1
        if not is_report_ready_candidate(brief):
            if allow_weak_single_candidate:
                LOGGER.info("Proceeding with weak single-report candidate because smoke-test override is enabled: %s [%s]", brief.case_name, brief.category)
                action_notice(f"candidate_weak_override attempt={attempted} case={brief.case_name}")
            else:
                LOGGER.info("Skipping report candidate before research because preflight is weak: %s [%s]", brief.case_name, brief.category)
                action_notice(f"candidate_skip_preflight attempt={attempted} case={brief.case_name}")
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
        action_notice(f"candidate_start attempt={attempted}/{max_attempts} selected_index={index}/{len(selected)} case={brief.case_name} category={brief.category}")
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
            article = generate_article_with_timeout(brief, candidate_timeout_seconds)
            path = write_docx(article, category=brief.category, output_root=output_root, run_label=run_label)
            written.append(str(path))
            written_briefs.append(brief)
            save_manifest(manifest_path, written_briefs)
            LOGGER.info("Wrote report: %s", path)
            action_notice(f"candidate_success case={brief.case_name} path={path}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Report generation failed for %s: %s", brief.case_name, exc)
            action_notice(f"candidate_failed attempt={attempted} case={brief.case_name} error={str(exc)[:700]}")
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
