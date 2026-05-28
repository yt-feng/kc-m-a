"""Validate generated DOCX formatting for M&A case reports.

The validator reads DOCX XML directly. It is intentionally lightweight and can be
run in GitHub Actions without opening Word.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
HALF_WIDTH_QUOTE_RE = re.compile(r"[\"']")
FULL_WIDTH_QUOTE_CHARS = set("“”‘’")


def wval(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    return element.attrib.get(W + name)


def para_text(p: ET.Element) -> str:
    return "".join(t.text or "" for t in p.findall(".//w:t", NS)).strip()


def run_text(r: ET.Element) -> str:
    return "".join(t.text or "" for t in r.findall(".//w:t", NS))


def ppr(p: ET.Element) -> ET.Element | None:
    return p.find("w:pPr", NS)


def has_spacing_zero(p: ET.Element) -> bool:
    spacing = p.find("w:pPr/w:spacing", NS)
    if spacing is None:
        return False
    return (wval(spacing, "before") in {None, "0"} and wval(spacing, "after") in {None, "0"})


def has_first_line_chars(p: ET.Element, chars: str = "200") -> bool:
    ind = p.find("w:pPr/w:ind", NS)
    return ind is not None and wval(ind, "firstLineChars") == chars and wval(ind, "firstLine") is None


def has_no_first_line(p: ET.Element) -> bool:
    ind = p.find("w:pPr/w:ind", NS)
    if ind is None:
        return True
    return wval(ind, "firstLine") is None and wval(ind, "firstLineChars") is None


def has_alignment(p: ET.Element, expected: str) -> bool:
    jc = p.find("w:pPr/w:jc", NS)
    return jc is not None and wval(jc, "val") == expected


def is_fullwidth_quote_run(text: str) -> bool:
    return bool(text) and all(char in FULL_WIDTH_QUOTE_CHARS for char in text)


def run_has_font(p: ET.Element, east_asia: str | None = None, size_half_points: str | None = None, bold: bool | None = None) -> bool:
    runs = [r for r in p.findall("w:r", NS) if run_text(r)]
    if not runs:
        runs = p.findall("w:r", NS)
    for r in runs:
        text = run_text(r)
        rpr = r.find("w:rPr", NS)
        if rpr is None:
            return False
        if east_asia is not None:
            fonts = rpr.find("w:rFonts", NS)
            actual_east_asia = wval(fonts, "eastAsia") if fonts is not None else None
            if is_fullwidth_quote_run(text):
                if actual_east_asia != "Times New Roman":
                    return False
            elif actual_east_asia != east_asia:
                return False
        if size_half_points is not None:
            sz = rpr.find("w:sz", NS)
            if sz is None or wval(sz, "val") != size_half_points:
                return False
        if bold is not None:
            b = rpr.find("w:b", NS)
            is_bold = b is not None and wval(b, "val") not in {"0", "false", "False"}
            if is_bold != bold:
                return False
    return True


def half_width_quote_snippets(text: str, limit: int = 5) -> list[str]:
    snippets: list[str] = []
    for match in HALF_WIDTH_QUOTE_RE.finditer(text):
        start = max(0, match.start() - 18)
        end = min(len(text), match.end() + 18)
        snippet = text[start:end].replace("\n", " ")
        snippets.append(snippet)
        if len(snippets) >= limit:
            break
    return snippets


def validate_docx(path: Path) -> dict[str, object]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            document_xml = zf.read("word/document.xml")
    except Exception as exc:  # noqa: BLE001
        return {"file": str(path), "ok": False, "issues": [f"Cannot read DOCX XML: {exc}"]}

    root = ET.fromstring(document_xml)
    doc_grid = root.find(".//w:sectPr/w:docGrid", NS)
    if doc_grid is None or wval(doc_grid, "type") != "lines" or wval(doc_grid, "linePitch") != "312":
        issues.append("文档网格应为 <w:docGrid w:type=\"lines\" w:linePitch=\"312\"/>。")

    paragraphs = [p for p in root.findall(".//w:body/w:p", NS) if para_text(p)]
    if not paragraphs:
        issues.append("文档没有可检测正文段落。")
        return {"file": str(path), "ok": False, "issues": issues}

    full_text = "\n".join(para_text(p) for p in paragraphs)
    quote_snippets = half_width_quote_snippets(full_text)
    if quote_snippets:
        issues.append("正文仍存在半角引号，需要全部改为全角中文引号“”：" + " | ".join(quote_snippets))

    title = paragraphs[0]
    if not has_alignment(title, "center"):
        issues.append("一级标题应居中对齐。")
    if not has_no_first_line(title):
        issues.append("一级标题应无首行缩进。")
    if not has_spacing_zero(title):
        issues.append("一级标题段前/段后应为0。")
    if not run_has_font(title, east_asia="黑体", size_half_points="30", bold=False):
        issues.append("一级标题应为黑体、小三、非加粗；全角引号可单独使用Times New Roman。")

    heading_count = 0
    body_count = 0
    for p in paragraphs[1:]:
        text = para_text(p)
        is_heading = bool(HEADING_RE.match(text))
        if is_heading:
            heading_count += 1
            if not has_alignment(p, "left"):
                issues.append(f"章标题应左对齐：{text[:30]}")
            if not has_first_line_chars(p):
                issues.append(f"章标题应首行缩进2字符：{text[:30]}")
            if not has_spacing_zero(p):
                issues.append(f"章标题段前/段后应为0：{text[:30]}")
            if not run_has_font(p, east_asia="仿宋", size_half_points="28", bold=True):
                issues.append(f"章标题应为仿宋、加粗、四号；全角引号可单独使用Times New Roman：{text[:30]}")
        else:
            body_count += 1
            if not has_first_line_chars(p):
                issues.append(f"正文段落应首行缩进2字符：{text[:30]}")
            if not has_spacing_zero(p):
                issues.append(f"正文段落段前/段后应为0：{text[:30]}")
            if not run_has_font(p, east_asia="仿宋", size_half_points="28", bold=None):
                issues.append(f"正文应为仿宋、四号；全角引号可单独使用Times New Roman：{text[:30]}")

    if heading_count < 4:
        issues.append(f"章标题数量偏少，检测到{heading_count}个。")
    if body_count < 8:
        issues.append(f"正文段落数量偏少，检测到{body_count}个。")

    return {"file": str(path), "ok": not issues, "issues": issues[:80], "heading_count": heading_count, "body_count": body_count, "half_width_quote_count": len(HALF_WIDTH_QUOTE_RE.findall(full_text))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated DOCX XML format.")
    parser.add_argument("paths", nargs="+", help="DOCX files or directories containing DOCX files")
    parser.add_argument("--write-json", default="", help="Optional JSON output path")
    parser.add_argument("--warn-only", action="store_true", help="Do not fail process when validation issues exist")
    args = parser.parse_args()

    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.docx")))
        elif path.suffix.lower() == ".docx":
            files.append(path)

    results = [validate_docx(path) for path in files]
    summary = {"count": len(results), "failed": sum(1 for r in results if not r.get("ok")), "results": results}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.write_json:
        out = Path(args.write_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if summary["failed"] and not args.warn_only:
        sys.exit(1)


if __name__ == "__main__":
    main()
