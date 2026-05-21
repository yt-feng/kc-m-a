"""Case selection for weekly and backfill M&A analysis reports."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mna_weekly_tracker.sources_rich import RawItem, fetch_all_candidates

from .config import CASE_DISCOVERY_QUERIES, CATEGORIES, CLASSIC_CASE_SEEDS, DOMESTIC_CATEGORY_HINTS, TOPIC_SELECTION_RULES
from .deepseek_client import chat_json

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class CaseBrief:
    case_name: str
    category: str
    region: str
    source_title: str = ""
    source_url: str = ""
    published_at: str = ""
    why: str = ""
    is_domestic: bool = False
    is_classic: bool = False
    completed_year: str = ""
    is_completed: bool = False

    def key(self) -> str:
        return f"{self.case_name}|{self.category}".lower().strip()

    def is_recent_completed(self) -> bool:
        return self.is_completed and self.completed_year in {"2025", "2026"}

    def is_allowed_topic(self) -> bool:
        return self.is_recent_completed() or self.is_classic

    def to_dict(self) -> dict[str, object]:
        return {
            "case_name": self.case_name,
            "category": self.category,
            "region": self.region,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "why": self.why,
            "is_domestic": self.is_domestic,
            "is_classic": self.is_classic,
            "completed_year": self.completed_year,
            "is_completed": self.is_completed,
        }


def safe_category(value: str | None) -> str:
    if value in CATEGORIES:
        return value
    text = value or ""
    for category in CATEGORIES:
        if category in text:
            return category
    return "依托上市平台持续整合同类资产"


def is_domestic_region(region: str) -> bool:
    text = region or ""
    return any(token in text for token in ("中国", "A股", "港股", "境内", "香港", "中概"))


def infer_completed_year(*values: str) -> str:
    text = " ".join(v or "" for v in values)
    years = re.findall(r"20(?:2[0-6]|1[0-9])", text)
    for year in ("2026", "2025"):
        if year in years:
            return year
    return years[0] if years else ""


def infer_is_completed(*values: str) -> bool:
    text = " ".join(v or "" for v in values)
    return any(token in text for token in ("完成", "交割", "过户", "closing", "closed", "completed", "收购完成", "交易完成", "完成合并"))


def existing_counts(report_root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for category in CATEGORIES:
        counts[category] = 0
    if not report_root.exists():
        return counts
    for path in report_root.rglob("*.docx"):
        for category in CATEGORIES:
            if category in str(path):
                counts[category] += 1
    return counts


def seed_briefs() -> list[CaseBrief]:
    return [
        CaseBrief(
            case_name=row["case_name"],
            category=safe_category(row.get("category")),
            region=row.get("region", "中国"),
            why=row.get("why", "经典或代表性并购案例。"),
            is_domestic=is_domestic_region(row.get("region", "中国")),
            is_classic=row.get("completed_year") not in {"2025", "2026"},
            completed_year=row.get("completed_year", ""),
            is_completed=str(row.get("is_completed", "true")).lower() == "true",
        )
        for row in CLASSIC_CASE_SEEDS
    ]


def candidates_from_weekly(days: int, max_items: int) -> list[RawItem]:
    end = datetime.now(BEIJING_TZ).replace(microsecond=0)
    start = end - timedelta(days=days)
    raw, _errors = fetch_all_candidates(start, end, max_items=max_items)
    return raw


def summarize_raw_items(raw_items: list[RawItem], target_count: int) -> list[CaseBrief]:
    if not raw_items:
        return []
    sample = [item.as_dict() for item in raw_items[: min(len(raw_items), 180)]]
    messages = [
        {"role": "system", "content": "你是并购案例研究选题编辑。只输出 JSON。"},
        {
            "role": "user",
            "content": (
                "从候选新闻/公告中筛选适合写成并购案例分析报告的交易。"
                f"选题规则：{TOPIC_SELECTION_RULES}"
                "优先中国案例；交易主体、交易事件、完成时间、交易对价和启示维度要清楚；剔除纯传闻、纯政策、纯市场评论、未完成或终止交易。"
                f"最多输出 {target_count} 个。分类只能用：{json.dumps(CATEGORIES, ensure_ascii=False)}。"
                "输出格式：{\"cases\":[{\"case_name\":...,\"category\":...,\"region\":...,\"source_title\":...,\"source_url\":...,\"published_at\":...,\"why\":...,\"is_domestic\":true/false,\"completed_year\":\"2025或2026等\",\"is_completed\":true/false,\"is_classic\":true/false}]}。"
                f"候选：{json.dumps(sample, ensure_ascii=False)}"
            ),
        },
    ]
    payload = chat_json(messages)
    out: list[CaseBrief] = []
    for row in payload.get("cases", []):
        if not isinstance(row, dict):
            continue
        case_name = str(row.get("case_name") or "").strip()
        if not case_name:
            continue
        region = str(row.get("region") or "中国")
        completed_year = str(row.get("completed_year") or infer_completed_year(case_name, str(row.get("source_title") or ""), str(row.get("published_at") or "")))
        is_completed = bool(row.get("is_completed", infer_is_completed(case_name, str(row.get("source_title") or ""), str(row.get("why") or ""))))
        is_classic = bool(row.get("is_classic", False))
        brief = CaseBrief(
            case_name=case_name,
            category=safe_category(str(row.get("category") or "")),
            region=region,
            source_title=str(row.get("source_title") or ""),
            source_url=str(row.get("source_url") or ""),
            published_at=str(row.get("published_at") or ""),
            why=str(row.get("why") or ""),
            is_domestic=bool(row.get("is_domestic", is_domestic_region(region))),
            is_classic=is_classic,
            completed_year=completed_year,
            is_completed=is_completed,
        )
        if brief.is_allowed_topic():
            out.append(brief)
    return out


def discover_backfill_cases(target_count: int) -> list[CaseBrief]:
    raw: list[RawItem] = []
    end = datetime.now(BEIJING_TZ).replace(microsecond=0)
    start = end - timedelta(days=730)
    from mna_weekly_tracker.sources_rich import fetch_bing_news, fetch_google_news

    for query in CASE_DISCOVERY_QUERIES:
        raw.extend(fetch_google_news(query, start, end, source_name="Google News - backfill", source_url="https://news.google.com/", region_hint="中国"))
        raw.extend(fetch_bing_news(query, start, end, source_name="Bing News - backfill", source_url="https://www.bing.com/news/search", region_hint="中国"))
    briefs = summarize_raw_items(raw, target_count=max(target_count, 20)) if raw else []
    return briefs + seed_briefs()


def choose_balanced(briefs: list[CaseBrief], *, count: int = 4, min_domestic: int = 2, report_root: Path) -> list[CaseBrief]:
    seen: set[str] = set()
    deduped: list[CaseBrief] = []
    for brief in briefs:
        if not brief.is_allowed_topic():
            continue
        if brief.key() in seen:
            continue
        seen.add(brief.key())
        deduped.append(brief)
    counts = existing_counts(report_root)

    def score(b: CaseBrief) -> tuple[int, int, int, int]:
        topic_rank = 0 if b.is_recent_completed() else 1
        domestic_rank = 0 if b.is_domestic else 1
        classic_penalty = 1 if b.is_classic else 0
        category_count = counts[b.category] + (2 if b.category == "SPAC" else 0)
        return (topic_rank, category_count, domestic_rank, classic_penalty)

    selected: list[CaseBrief] = []
    for brief in sorted(deduped, key=score):
        if len(selected) >= count:
            break
        selected.append(brief)
        counts[brief.category] += 1

    domestic_now = sum(1 for x in selected if x.is_domestic)
    if domestic_now < min_domestic:
        selected_keys = {s.key() for s in selected}
        for brief in sorted([b for b in deduped if b.is_domestic and b.key() not in selected_keys], key=score):
            if domestic_now >= min_domestic:
                break
            if len(selected) < count:
                selected.append(brief)
            else:
                for idx in range(len(selected) - 1, -1, -1):
                    if not selected[idx].is_domestic:
                        selected[idx] = brief
                        break
            domestic_now = sum(1 for x in selected if x.is_domestic)
            selected_keys = {s.key() for s in selected}
    return selected[:count]


def save_manifest(path: Path, briefs: list[CaseBrief]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([b.to_dict() for b in briefs], ensure_ascii=False, indent=2), encoding="utf-8")
