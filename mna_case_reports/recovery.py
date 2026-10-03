"""Revalidate accepted outputs from an earlier Actions artifact on the runner."""
from __future__ import annotations
import json
import shutil
from dataclasses import fields
from pathlib import Path
from docx import Document
from .article_quality import assess_quality
from .article_rules import validate_article
from .case_selection import CaseBrief, is_report_completed_candidate
from .docx_validate import HEADING_RE, validate_docx


def read_article(path: Path) -> dict:
    paragraphs = [p.text.strip() for p in Document(path).paragraphs if p.text.strip()]
    article = {"title": paragraphs[0], "intro": "", "sections": []}
    for text in paragraphs[1:]:
        if HEADING_RE.match(text):
            article["sections"].append({"heading": text, "paragraphs": []})
        elif article["sections"]:
            article["sections"][-1]["paragraphs"].append(text)
        else:
            article["intro"] += text
    return article


def restore_accepted(root: Path, output_root: Path, *, requested_count: int) -> tuple[list[str], list[CaseBrief]]:
    root = root.resolve()
    progress_files = list(root.rglob("*_progress.json"))
    if len(progress_files) != 1:
        raise ValueError("Recovery requires exactly one prior batch progress manifest")
    progress = json.loads(progress_files[0].read_text())
    paths = progress.get("files", progress.get("written", []))
    if not isinstance(paths, list) or not paths or len(paths) > requested_count or len(set(paths)) != len(paths):
        raise ValueError("Invalid prior accepted-file list")
    manifest = progress_files[0].with_name(progress_files[0].name.replace("_progress.json", ".json"))
    rows = json.loads(manifest.read_text())
    if not isinstance(rows, list) or len(rows) != len(paths):
        raise ValueError("Prior case manifest and accepted files do not agree")
    if progress.get("requested_count") != requested_count:
        raise ValueError("Recovery must retain the original requested report count")
    accepted = []
    briefs = []
    keys = set()
    for raw, row in zip(paths, rows):
        relative = Path(raw)
        if relative.is_absolute() or relative.parts[0] != output_root.name:
            raise ValueError("Prior output path is outside report scope")
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or source.suffix != ".docx" or not source.is_file():
            raise ValueError("Missing or out-of-scope prior report")
        brief = CaseBrief(**{f.name: row[f.name] for f in fields(CaseBrief) if f.name in row})
        if not is_report_completed_candidate(brief) or brief.identity_key() in keys:
            raise ValueError("Prior case is incomplete or duplicated")
        keys.add(brief.identity_key())
        article = read_article(source)
        if not validate_docx(source)["ok"] or validate_article(article, brief) or assess_quality(article):
            raise ValueError("Prior report no longer passes format, fact, and article quality gates")
        accepted.append((source, output_root / Path(*relative.parts[1:])))
        briefs.append(brief)
    # Copy only after every accepted report has passed all checks.
    for source, destination in accepted:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return [str(destination) for _, destination in accepted], briefs
