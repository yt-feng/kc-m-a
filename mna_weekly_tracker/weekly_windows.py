"""Canonical weekly windows and continuity checks for Excel deal-flow output."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .url_validation import validate_workbook

LOGGER = logging.getLogger(__name__)
BEIJING_TZ_NAME = "Asia/Shanghai"
SCHEDULE_WEEKDAY = 4  # Friday
SCHEDULE_CUTOFF = time(hour=5)
WEEK_DAYS = 7
DEFAULT_RECOVERY_START_DATE = "2026-07-31"


@dataclass(frozen=True, order=True)
class WeeklyWindow:
    start: datetime
    end: datetime

    @property
    def filename(self) -> str:
        return f"并购案例一览_{self.start:%Y%m%d}_{self.end:%Y%m%d}.xlsx"

    def output_path(self, output_dir: Path) -> Path:
        return output_dir / self.filename


def parse_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format: {value!r}") from exc


def window_from_dates(
    start_value: str,
    end_value: str,
    *,
    tz_name: str = BEIJING_TZ_NAME,
) -> WeeklyWindow:
    start_date = parse_date(start_value, field_name="start date")
    end_date = parse_date(end_value, field_name="end date")
    if start_date.weekday() != SCHEDULE_WEEKDAY or end_date.weekday() != SCHEDULE_WEEKDAY:
        raise ValueError(
            "canonical weekly windows must start and end on Friday: "
            f"{start_date.isoformat()} -> {end_date.isoformat()}"
        )
    if end_date - start_date != timedelta(days=WEEK_DAYS):
        raise ValueError(
            f"weekly window must be exactly {WEEK_DAYS} days: "
            f"{start_date.isoformat()} -> {end_date.isoformat()}"
        )
    tz = ZoneInfo(tz_name)
    return WeeklyWindow(
        start=datetime.combine(start_date, SCHEDULE_CUTOFF, tzinfo=tz),
        end=datetime.combine(end_date, SCHEDULE_CUTOFF, tzinfo=tz),
    )


def latest_scheduled_cutoff(
    now: datetime | None = None,
    *,
    tz_name: str = BEIJING_TZ_NAME,
) -> datetime:
    tz = ZoneInfo(tz_name)
    if now is None:
        local_now = datetime.now(tz)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=tz)
    else:
        local_now = now.astimezone(tz)

    days_since_friday = (local_now.weekday() - SCHEDULE_WEEKDAY) % WEEK_DAYS
    cutoff_date = local_now.date() - timedelta(days=days_since_friday)
    cutoff = datetime.combine(cutoff_date, SCHEDULE_CUTOFF, tzinfo=tz)
    if cutoff > local_now:
        cutoff -= timedelta(days=WEEK_DAYS)
    return cutoff


def expected_weekly_windows(
    latest_end: datetime,
    *,
    recovery_start_date: str = DEFAULT_RECOVERY_START_DATE,
) -> list[WeeklyWindow]:
    start_date = parse_date(recovery_start_date, field_name="recovery start date")
    end_date = latest_end.date()
    if start_date.weekday() != SCHEDULE_WEEKDAY:
        raise ValueError(f"recovery start date must be a Friday: {start_date.isoformat()}")
    if end_date.weekday() != SCHEDULE_WEEKDAY:
        raise ValueError(f"latest scheduled cutoff must be a Friday: {end_date.isoformat()}")
    if start_date > end_date:
        raise ValueError(
            f"recovery start date {start_date.isoformat()} is after "
            f"latest scheduled cutoff {end_date.isoformat()}"
        )

    tz = latest_end.tzinfo or ZoneInfo(BEIJING_TZ_NAME)
    windows: list[WeeklyWindow] = []
    cursor = start_date
    while cursor + timedelta(days=WEEK_DAYS) <= end_date:
        window_end = cursor + timedelta(days=WEEK_DAYS)
        windows.append(
            WeeklyWindow(
                start=datetime.combine(cursor, SCHEDULE_CUTOFF, tzinfo=tz),
                end=datetime.combine(window_end, SCHEDULE_CUTOFF, tzinfo=tz),
            )
        )
        cursor = window_end
    return windows


def workbook_completion(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    try:
        result = validate_workbook(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"unreadable:{type(exc).__name__}:{exc}"
    if result["issue_count"]:
        issue_types = sorted({str(issue.get("type", "unknown")) for issue in result["issues"]})
        return False, f"validation_failed:{','.join(issue_types)}"
    if result["case_rows"] <= 0:
        return False, "no_case_rows"
    return True, f"valid:case_rows={result['case_rows']}"


def missing_weekly_windows(
    output_dir: Path,
    latest_end: datetime,
    *,
    recovery_start_date: str = DEFAULT_RECOVERY_START_DATE,
) -> list[WeeklyWindow]:
    missing: list[WeeklyWindow] = []
    for window in expected_weekly_windows(
        latest_end,
        recovery_start_date=recovery_start_date,
    ):
        path = window.output_path(output_dir)
        complete, detail = workbook_completion(path)
        LOGGER.info(
            "Weekly coverage check start=%s end=%s path=%s complete=%s detail=%s",
            window.start.date(),
            window.end.date(),
            path,
            complete,
            detail,
        )
        if not complete:
            missing.append(window)
    return missing
