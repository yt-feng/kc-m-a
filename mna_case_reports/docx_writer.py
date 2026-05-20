"""DOCX writer for M&A case analysis reports."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from .config import CATEGORY_FOLDER_NAMES


def sanitize_filename(value: str, max_len: int = 80) -> str:
    value = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", value).strip(" ._")
    value = re.sub(r"\s+", "", value)
    return value[:max_len] or "case_report"


def set_run_font(run, *, east_asia: str = "仿宋", latin: str = "Times New Roman", size_pt: float = 14.0, bold: bool = False) -> None:
    run.font.name = latin
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    r_pr = run._element.get_or_add_rPr()
    fonts = r_pr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.append(fonts)
    fonts.set(qn("w:ascii"), latin)
    fonts.set(qn("w:hAnsi"), latin)
    fonts.set(qn("w:eastAsia"), east_asia)
    fonts.set(qn("w:cs"), latin)


def set_doc_defaults(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.175)
    section.right_margin = Cm(3.175)
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(14)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.first_line_indent = Pt(28)


def add_title(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(12)
    run = p.add_run(text)
    set_run_font(run, east_asia="黑体", size_pt=15, bold=False)


def add_heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Pt(10)
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    set_run_font(run, east_asia="仿宋", size_pt=14, bold=True)


def add_body(doc: Document, text: str, *, bold_prefix: bool = False) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Pt(28)
    p.paragraph_format.line_spacing = 1.0
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if bold_prefix and "。" in text:
        prefix, rest = text.split("。", 1)
        run = p.add_run(prefix + "。")
        set_run_font(run, size_pt=14, bold=True)
        if rest:
            run2 = p.add_run(rest)
            set_run_font(run2, size_pt=14)
    else:
        run = p.add_run(text)
        set_run_font(run, size_pt=14)


def normalize_sections(article: dict[str, object]) -> list[tuple[str, list[str]]]:
    sections = article.get("sections") or []
    out: list[tuple[str, list[str]]] = []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            heading = str(sec.get("heading") or "").strip()
            paragraphs = sec.get("paragraphs") or []
            if heading and isinstance(paragraphs, list):
                out.append((heading, [str(x).strip() for x in paragraphs if str(x).strip()]))
    return out


def write_docx(article: dict[str, object], *, category: str, output_root: Path) -> Path:
    doc = Document()
    set_doc_defaults(doc)
    title = str(article.get("title") or article.get("case_name") or "并购案例研究")
    add_title(doc, title)

    intro = str(article.get("intro") or "").strip()
    if intro:
        add_body(doc, intro)

    for heading, paragraphs in normalize_sections(article):
        add_heading(doc, heading)
        for para in paragraphs:
            add_body(doc, para, bold_prefix=para.startswith(("其一", "其二", "其三", "第一", "第二", "第三")))

    folder = CATEGORY_FOLDER_NAMES.get(category, sanitize_filename(category))
    output_dir = output_root / folder
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(title) + ".docx"
    path = output_dir / filename
    doc.save(path)
    return path
