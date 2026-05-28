"""Case selection for weekly and backfill M&A analysis reports."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mna_weekly_tracker.sources_rich import RawItem, fetch_all_candidates

from .case_pool import EXTENDED_CASE_POOL
from .config import CASE_DISCOVERY_QUERIES, CATEGORIES, CLASSIC_CASE_SEEDS, TOPIC_SELECTION_RULES
from .deepseek_client import chat_json

LOGGER = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
VAGUE_PARTY_TERMS = ("未披露", "未知", "不详", "某标的", "标的资产", "标的公司", "相关资产", "部分资产")


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
    acquirer: str = ""
    target: str = ""
    deal_value: str = ""
    deal_status: str = ""
    buyer_motivation: str = ""
    seller_motivation: str = ""
    financial_highlights: str = ""

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
            "acquirer": self.acquirer,
            "target": self.target,
            "deal_value": self.deal_value,
            "deal_status": self.deal_status,
            "buyer_motivation": self.buyer_motivation,
            "seller_motivation": self.seller_motivation,
            "financial_highlights": self.financial_highlights,
        }


def excluded_terms() -> list[str]:
    raw = os.getenv("REPORT_EXCLUDE_CASE_TERMS", "")
    return [term.strip().lower() for term in re.split(r"[,，;；\n]+", raw) if term.strip()]


def infer_parties_from_name(case_name: str) -> tuple[str, str]:
    parts = re.split(r"收购|并购|入主|吸收合并|私有化|合并|出售|控股|取得", case_name or "", maxsplit=1)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


def is_vague_party(value: str) -> bool:
    text = (value or "").strip()
    if len(text) < 2:
        return True
    return any(term in text for term in VAGUE_PARTY_TERMS)


def has_explicit_parties(brief: CaseBrief) -> bool:
    acquirer = brief.acquirer
    target = brief.target
    inferred_a, inferred_t = infer_parties_from_name(brief.case_name)
    acquirer = acquirer or inferred_a
    target = target or inferred_t
    if is_vague_party(acquirer) or is_vague_party(target):
        return False
    combined = "\n".join([brief.case_name, brief.acquirer, brief.target, brief.source_title, brief.why])
    return not any(term in combined for term in VAGUE_PARTY_TERMS)


def is_excluded_case(brief: CaseBrief) -> bool:
    terms = excluded_terms()
    if not terms:
        return False
    text = "\n".join([
        brief.case_name,
        brief.acquirer,
        brief.target,
        brief.source_title,
        brief.why,
    ]).lower()
    return any(term in text for term in terms)


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


def rows_to_briefs(rows: list[dict[str, str]], *, default_classic: bool = False) -> list[CaseBrief]:
    briefs: list[CaseBrief] = []
    for row in rows:
        case_name = str(row.get("case_name") or "").strip()
        if not case_name:
            continue
        inferred_a, inferred_t = infer_parties_from_name(case_name)
        region = str(row.get("region") or "中国")
        completed_year = str(row.get("completed_year") or infer_completed_year(case_name, str(row.get("why") or "")))
        is_completed = str(row.get("is_completed", "true")).lower() == "true"
        is_classic_raw = row.get("is_classic")
        if is_classic_raw is None:
            is_classic = default_classic or completed_year not in {"2025", "2026"}
        else:
            is_classic = str(is_classic_raw).lower() == "true"
        brief = CaseBrief(
            case_name=case_name,
            category=safe_category(row.get("category")),
            region=region,
            source_title=str(row.get("source_title") or case_name),
            source_url=str(row.get("source_url") or ""),
            published_at=str(row.get("published_at") or ""),
            why=str(row.get("why") or "经典或代表性并购案例。"),
            is_domestic=is_domestic_region(region),
            is_classic=is_classic,
            completed_year=completed_year,
            is_completed=is_completed,
            acquirer=str(row.get("acquirer") or inferred_a),
            target=str(row.get("target") or inferred_t),
            deal_value=str(row.get("deal_value") or ""),
            deal_status=str(row.get("deal_status") or ""),
            buyer_motivation=str(row.get("buyer_motivation") or ""),
            seller_motivation=str(row.get("seller_motivation") or ""),
            financial_highlights=str(row.get("financial_highlights") or ""),
        )
        if brief.is_allowed_topic() and has_explicit_parties(brief) and not is_excluded_case(brief):
            briefs.append(brief)
    return briefs


def seed_briefs() -> list[CaseBrief]:
    return rows_to_briefs(CLASSIC_CASE_SEEDS, default_classic=True)


def extended_pool_briefs() -> list[CaseBrief]:
    return rows_to_briefs(EXTENDED_CASE_POOL, default_classic=False)


def candidates_from_weekly(days: int, max_items: int) -> list[RawItem]:
    end = datetime.now(BEIJING_TZ).replace(microsecond=0)
    start = end - timedelta(days=days)
    raw, _errors = fetch_all_candidates(start, end, max_items=max_items)
    return raw


def summarize_raw_items(raw_items: list[RawItem], target_count: int) -> list[CaseBrief]:
    if not raw_items:
        return []
    sample = [item.as_dict() for item in raw_items[: min(len(raw_items), 220)]]
    exclude = "、".join(excluded_terms())
    prompt_parts = [
        "从候选新闻/公告中筛选适合写成并购案例分析报告的交易。",
        f"选题规则：{TOPIC_SELECTION_RULES}",
        "优先选择并购方和并购标的名称明确、交易金额或估值线索明确的案例。",
        "严禁选择标的名称为未披露、未知、不详、某标的、标的资产、标的公司的案例。",
        "优先中国案例；交易主体、交易事件、完成时间、交易对价和启示维度要清楚；剔除纯传闻、纯政策、纯市场评论、未完成或终止交易。",
    ]
    if exclude:
        prompt_parts.append(f"不要选择包含这些主体或关键词的案例：{exclude}。")
    prompt_parts.extend([
        f"最多输出 {target_count} 个。分类只能用：{json.dumps(CATEGORIES, ensure_ascii=False)}。",
        "输出格式：{\"cases\":[{\"case_name\":...,\"category\":...,\"region\":...,\"source_title\":...,\"source_url\":...,\"published_at\":...,\"why\":...,\"is_domestic\":true/false,\"completed_year\":\"2025或2026等\",\"is_completed\":true/false,\"is_classic\":true/false,\"acquirer\":...,\"target\":...,\"deal_value\":...,\"deal_status\":...,\"buyer_motivation\":...,\"seller_motivation\":...,\"financial_highlights\":...}]}。",
        f"候选：{json.dumps(sample, ensure_ascii=False)}",
    ])
    messages = [
        {"role": "system", "content": "你是并购案例研究选题编辑。只输出 JSON。"},
        {"role": "user", "content": "".join(prompt_parts)},
    ]
    payload = chat_json(messages)
    rows: list[dict[str, str]] = []
    for row in payload.get("cases", []):
        if not isinstance(row, dict):
            continue
        case_name = str(row.get("case_name") or "").strip()
        if not case_name:
            continue
        completed_year = str(row.get("completed_year") or infer_completed_year(case_name, str(row.get("source_title") or ""), str(row.get("published_at") or "")))
        is_completed = row.get("is_completed", infer_is_completed(case_name, str(row.get("source_title") or ""), str(row.get("why") or "")))
        row["completed_year"] = completed_year
        row["is_completed"] = str(bool(is_completed)).lower()
        rows.append(row)  # type: ignore[arg-type]
    return rows_to_briefs(rows)


def dedupe_briefs(briefs: list[CaseBrief]) -> list[CaseBrief]:
    seen: set[str] = set()
    out: list[CaseBrief] = []
    for brief in briefs:
        if not brief.is_allowed_topic() or not has_explicit_parties(brief) or is_excluded_case(brief):
            continue
        key = brief.key()
        if key in seen:
            continue
        seen.add(key)
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
    live_briefs = summarize_raw_items(raw, target_count=max(target_count, 20)) if raw else []
    pooled = live_briefs + extended_pool_briefs() + seed_briefs()
    deduped = dedupe_briefs(pooled)
    LOGGER.info("Backfill candidate pool: live=%s pool=%s seeds=%s deduped=%s target=%s", len(live_briefs), len(extended_pool_briefs()), len(seed_briefs()), len(deduped), target_count)
    return deduped


def choose_balanced(briefs: list[CaseBrief], *, count: int = 4, min_domestic: int = 2, report_root: Path) -> list[CaseBrief]:
    deduped = dedupe_briefs(briefs)
    counts = existing_counts(report_root)
    selected: list[CaseBrief] = []
    selected_keys: set[str] = set()
    selected_category_counts: Counter[str] = Counter()

    def score(b: CaseBrief) -> tuple[int, int, int, int, int]:
        selected_category_penalty = selected_category_counts[b.category] * 100
        existing_category_penalty = counts[b.category] + (2 if b.category == "SPAC" else 0)
        topic_rank = 0 if b.is_recent_completed() else 1
        domestic_rank = 0 if b.is_domestic else 1
        classic_penalty = 1 if b.is_classic else 0
        return (selected_category_penalty, existing_category_penalty, topic_rank, domestic_rank, classic_penalty)

    # First pass: prefer one case per category, starting from historically underrepresented folders.
    for category in sorted(CATEGORIES, key=lambda c: counts[c] + (2 if c == "SPAC" else 0)):
        if len(selected) >= count:
            break
        category_candidates = [b for b in deduped if b.category == category and b.key() not in selected_keys]
        if not category_candidates:
            continue
        brief = sorted(category_candidates, key=score)[0]
        selected.append(brief)
        selected_keys.add(brief.key())
        selected_category_counts[brief.category] += 1
        counts[brief.category] += 1

    # Second pass: fill any remaining slots, still penalizing categories already selected in this run.
    for brief in sorted([b for b in deduped if b.key() not in selected_keys], key=score):
        if len(selected) >= count:
            break
        selected.append(brief)
        selected_keys.add(brief.key())
        selected_category_counts[brief.category] += 1
        counts[brief.category] += 1

    domestic_now = sum(1 for x in selected if x.is_domestic)
    if domestic_now < min_domestic:
        for brief in sorted([b for b in deduped if b.is_domestic and b.key() not in selected_keys], key=score):
            if domestic_now >= min_domestic:
                break
            replace_indexes = sorted(
                [i for i, item in enumerate(selected) if not item.is_domestic],
                key=lambda i: selected_category_counts[selected[i].category],
                reverse=True,
            )
            if not replace_indexes:
                break
            idx = replace_indexes[0]
            old = selected[idx]
            selected_category_counts[old.category] -= 1
            selected_keys.discard(old.key())
            selected[idx] = brief
            selected_keys.add(brief.key())
            selected_category_counts[brief.category] += 1
            domestic_now = sum(1 for x in selected if x.is_domestic)

    LOGGER.info(
        "Selected %s report cases, including %s domestic cases, categories=%s, requested count=%s min_domestic=%s",
        len(selected[:count]),
        sum(1 for x in selected[:count] if x.is_domestic),
        dict(Counter(x.category for x in selected[:count])),
        count,
        min_domestic,
    )
    return selected[:count]


def save_manifest(path: Path, briefs: list[CaseBrief]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([b.to_dict() for b in briefs], ensure_ascii=False, indent=2), encoding="utf-8")
