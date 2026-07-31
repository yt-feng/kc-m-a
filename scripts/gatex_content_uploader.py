#!/usr/bin/env python3
"""Import generated DOCX reports into the private GateX review queue.

The bridge intentionally uses only the Python standard library so it can run as
the final step of a GitHub Actions generation job without adding dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree


DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_XML_BYTES = 20 * 1024 * 1024
MAX_SECTIONS = 40
MAX_SECTION_CHARS = 19_000
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "bearer_token",
    "token",
    "cookie",
)
MANIFEST_AUDIT_KEYS = {
    "generated_at",
    "period",
    "raw_count",
    "collection_warnings",
    "warnings",
    "errors",
    "factcheck",
    "mode",
    "run_label",
    "requested_count",
    "selected_count",
    "count",
    "file",
    "source_path",
    "sourcePath",
    "case_name",
    "category",
    "source_title",
    "source_url",
    "published_at",
    "deal_status",
    "ok",
    "issues",
    "heading_count",
    "body_count",
    "half_width_quote_count",
}


def validated_callback_base(value: str) -> str:
    """Accept only the production GateX HTTPS origins before sending a secret."""
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(raw)
    allowed_hosts = {
        host.strip().casefold()
        for host in os.getenv(
            "GATEX_CALLBACK_ALLOWED_HOSTS",
            "gatex.fund,www.gatex.fund",
        ).split(",")
        if host.strip()
    }
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("GATEX_CALLBACK_BASE contains an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.casefold() not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GATEX_CALLBACK_BASE must be an allowed GateX HTTPS origin")
    return f"https://{parsed.hostname.casefold()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_relative(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"Report must be inside the repository: {path}") from exc


def clean_docx_text(value: str, repo_root: Path) -> str:
    """Remove control characters and any accidental local-machine paths."""
    text = " ".join(value.replace("\u00a0", " ").split())
    text = text.replace(str(repo_root.resolve()), "[repository]")
    text = re.sub(r"file://\S+", "[local-path-redacted]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?:(?:/Users|/home|/tmp|/private/tmp)/|[A-Za-z]:\\(?:Users|Temp)\\)\S+",
        "[local-path-redacted]",
        text,
    )
    return text.strip()


def paragraph_style(paragraph: ElementTree.Element) -> str:
    node = paragraph.find(f"{{{WORD_NS}}}pPr/{{{WORD_NS}}}pStyle")
    if node is None:
        return ""
    return str(node.attrib.get(f"{{{WORD_NS}}}val", "")).strip()


def paragraph_text(paragraph: ElementTree.Element, repo_root: Path) -> str:
    instructions = " ".join(
        (node.text or "") for node in paragraph.findall(f".//{{{WORD_NS}}}instrText")
    ).upper()
    if " TOC " in f" {instructions} " or "PAGEREF" in instructions:
        return ""
    raw = "".join((node.text or "") for node in paragraph.findall(f".//{{{WORD_NS}}}t"))
    return clean_docx_text(raw, repo_root)


def is_title_style(style: str) -> bool:
    normalized = style.casefold().replace(" ", "")
    return normalized in {"title", "标题", "题目"} or normalized.startswith("maintitle")


def is_contents_label(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text).casefold()
    return normalized in {"目录", "目次", "tableofcontents", "contents"}


def is_heading(text: str, style: str) -> bool:
    normalized = style.casefold().replace(" ", "")
    if normalized.startswith("heading") or normalized.startswith("标题"):
        return True
    if len(text) > 100:
        return False
    return bool(
        re.match(r"^(?:【[^】]{1,60}】|第[一二三四五六七八九十百\d]+[章节篇]|[一二三四五六七八九十\d]+[、.．]\s*\S+)", text)
    )


def section_kind(heading: str) -> str:
    lowered = heading.casefold()
    if any(marker in heading for marker in ("摘要", "要点", "结论")) or "summary" in lowered:
        return "executive_summary"
    if any(marker in heading for marker in ("来源", "参考", "方法")) or "method" in lowered:
        return "methodology"
    return "analysis"


def chunk_paragraphs(paragraphs: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if current and current_size + len(paragraph) + 2 > MAX_SECTION_CHARS:
            chunks.append("\n\n".join(current))
            current = []
            current_size = 0
        if len(paragraph) > MAX_SECTION_CHARS:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_size = 0
            for offset in range(0, len(paragraph), MAX_SECTION_CHARS):
                chunks.append(paragraph[offset : offset + MAX_SECTION_CHARS])
            continue
        current.append(paragraph)
        current_size += len(paragraph) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def extract_docx(path: Path, repo_root: Path) -> tuple[str, str, list[dict[str, str]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo("word/document.xml").file_size > MAX_DOCUMENT_XML_BYTES:
                raise ValueError(f"DOCX document XML is unexpectedly large: {path}")
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Not a valid DOCX report: {path}") from exc

    root = ElementTree.fromstring(document_xml)
    paragraphs: list[tuple[str, str]] = []
    for node in root.findall(f".//{{{WORD_NS}}}p"):
        text = paragraph_text(node, repo_root)
        if text:
            paragraphs.append((text, paragraph_style(node)))
    if not paragraphs:
        raise ValueError(f"DOCX contains no readable report text: {path}")

    title_index = next(
        (
            index
            for index, (text, style) in enumerate(paragraphs)
            if is_title_style(style) and not is_contents_label(text)
        ),
        0,
    )
    title = paragraphs[title_index][0][:300]
    body_start = title_index + 1
    if is_contents_label(title):
        title = path.stem.replace("_", " ")[:300]
        first_real_heading = next(
            (
                index
                for index, (_, style) in enumerate(paragraphs)
                if style.casefold().replace(" ", "").startswith(("heading", "标题"))
            ),
            body_start,
        )
        body_start = first_real_heading
    body_paragraphs = paragraphs[body_start:]

    grouped: list[tuple[str, list[str]]] = []
    current_heading = "Executive brief"
    current_body: list[str] = []
    for text, style in body_paragraphs:
        if is_heading(text, style):
            if current_body:
                grouped.append((current_heading, current_body))
            current_heading = text[:300]
            current_body = []
        else:
            current_body.append(text)
    if current_body:
        grouped.append((current_heading, current_body))

    sections: list[dict[str, str]] = []
    for heading, section_paragraphs in grouped:
        for chunk_index, body in enumerate(chunk_paragraphs(section_paragraphs), start=1):
            display_heading = heading if chunk_index == 1 else f"{heading}（续）"
            sections.append(
                {
                    "id": f"section-{len(sections) + 1}",
                    "kind": section_kind(heading),
                    "heading": display_heading,
                    "body": body,
                }
            )
            if len(sections) >= MAX_SECTIONS:
                break
        if len(sections) >= MAX_SECTIONS:
            break
    if not sections:
        sections = [
            {
                "id": "section-1",
                "kind": "analysis",
                "heading": "Report",
                "body": title,
            }
        ]

    summary_candidates = [
        paragraph
        for _, section_paragraphs in grouped
        for paragraph in section_paragraphs
        if len(paragraph) >= 20
    ]
    summary = " ".join(summary_candidates[:2])[:1000] or sections[0]["body"][:1000]
    return title, summary, sections


def sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def sanitize_manifest(value: Any, repo_root: Path, depth: int = 0) -> Any:
    if depth > 14:
        return "[maximum-depth]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in list(value.items())[:1000]:
            if sensitive_key(key):
                continue
            result[str(key)] = sanitize_manifest(child, repo_root, depth + 1)
        return result
    if isinstance(value, list):
        return [sanitize_manifest(item, repo_root, depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        cleaned = value.replace(str(repo_root.resolve()), "[repository]")
        cleaned = re.sub(r"\b(?:Bearer\s+)?(?:sk|ds)-[A-Za-z0-9_-]{16,}\b", "[credential-redacted]", cleaned)
        cleaned = re.sub(
            r"(?:(?:/Users|/home|/tmp|/private/tmp)/|[A-Za-z]:\\(?:Users|Temp)\\)\S+",
            "[local-path-redacted]",
            cleaned,
        )
        return cleaned[:50_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:10_000]


def compact_manifest_record(record: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Keep audit metadata while excluding duplicated report/research bodies."""
    compact = {
        key: value
        for key, value in record.items()
        if str(key) in MANIFEST_AUDIT_KEYS
    }
    return sanitize_manifest(compact, repo_root)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_match_text(value: object) -> str:
    return "".join(re.findall(r"[0-9a-z\u3400-\u9fff]+", str(value).casefold()))


def record_matches_docx(record: dict[str, Any], source_path: str, stem: str) -> bool:
    path_values = [
        record.get("file"),
        record.get("source_path"),
        record.get("sourcePath"),
    ]
    outputs = record.get("outputs")
    if isinstance(outputs, dict):
        path_values.extend((outputs.get("docx"), outputs.get("report")))
    for value in path_values:
        if value and (str(value).replace("\\", "/") == source_path or Path(str(value)).name == Path(source_path).name):
            return True
    case_name = normalized_match_text(record.get("case_name") or record.get("title") or "")
    return bool(case_name and case_name in normalized_match_text(stem))


def select_manifest(paths: Iterable[Path], source_path: str, repo_root: Path) -> Any:
    matches: list[Any] = []
    for path in paths:
        if not path.is_file():
            continue
        data = load_json(path)
        if isinstance(data, dict) and record_matches_docx(data, source_path, Path(source_path).stem):
            matches.append(compact_manifest_record(data, repo_root))
            continue
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            selected = [
                item
                for item in data["results"]
                if isinstance(item, dict) and record_matches_docx(item, source_path, Path(source_path).stem)
            ]
            if selected:
                matches.append(
                    {
                        "validation": [
                            compact_manifest_record(item, repo_root)
                            for item in selected
                        ]
                    }
                )
            continue
        if isinstance(data, list):
            selected = [
                item
                for item in data
                if isinstance(item, dict) and record_matches_docx(item, source_path, Path(source_path).stem)
            ]
            if selected:
                matches.append(
                    {
                        "records": [
                            compact_manifest_record(item, repo_root)
                            for item in selected
                        ]
                    }
                )
    if not matches:
        return {}
    selected_manifest = matches[0] if len(matches) == 1 else {"sources": matches}
    return selected_manifest


def read_path_list(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    return [Path(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def http_request(url: str, method: str, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
    for attempt in range(1, 4):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt == 3:
                raise RuntimeError(f"GateX returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt == 3:
                raise RuntimeError(f"GateX callback failed: {exc.reason}") from exc
        time.sleep(2 ** (attempt - 1))
    raise RuntimeError("GateX callback failed after retries")


def external_key(source_module: str, source_path: str, checksum: str) -> str:
    path_digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:16]
    return f"{source_module.replace('_', '-')}-{path_digest}-{checksum[:16]}"


def upload_report(
    *,
    docx: Path,
    manifests: list[Path],
    repo_root: Path,
    callback_base: str,
    secret: str,
    source_module: str,
    source_repo: str,
    report_type: str,
) -> dict[str, Any]:
    callback_base = validated_callback_base(callback_base)
    if not docx.is_file():
        raise FileNotFoundError(f"Generated DOCX not found: {docx}")
    if docx.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"DOCX exceeds the 50 MB GateX limit: {docx}")
    source_path = repo_relative(docx, repo_root)
    checksum = sha256_file(docx)
    key = external_key(source_module, source_path, checksum)
    title, summary, sections = extract_docx(docx, repo_root)
    manifest = select_manifest(manifests, source_path, repo_root)
    endpoint = f"{callback_base.rstrip('/')}/api/integrations/content/{urllib.parse.quote(key, safe='')}"
    auth_headers = {"Authorization": f"Bearer {secret}"}

    archive_headers = {
        **auth_headers,
        "Content-Type": DOCX_CONTENT_TYPE,
        "x-source-module": source_module,
        "x-source-repo": source_repo,
        "x-file-name": urllib.parse.quote(docx.name, safe=""),
        "x-checksum": checksum,
    }
    http_request(f"{endpoint}/archive", "PUT", docx.read_bytes(), archive_headers)

    payload = {
        "sourceModule": source_module,
        "sourceRepo": source_repo,
        "sourcePath": source_path,
        "title": title,
        "summary": summary,
        "language": "zh",
        "reportType": report_type,
        "contentSections": sections,
        "manifest": manifest,
        "checksum": checksum,
    }
    complete_response = http_request(
        f"{endpoint}/complete",
        "POST",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        {**auth_headers, "Content-Type": "application/json; charset=utf-8"},
    )
    print(f"Imported {source_path} into GateX review ({key}).")
    return complete_response


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload private weekly reports to GateX review.")
    parser.add_argument("--source-module", choices=("kc_c_wk", "kc-m-a"), required=True)
    parser.add_argument("--source-repo", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--report-type")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--docx", type=Path, action="append", default=[])
    parser.add_argument("--docx-list", type=Path, action="append", default=[])
    parser.add_argument("--manifest", type=Path, action="append", default=[])
    parser.add_argument("--manifest-list", type=Path, action="append", default=[])
    parser.add_argument("--callback-base", default=os.getenv("GATEX_CALLBACK_BASE", "https://gatex.fund"))
    parser.add_argument("--secret", default=os.getenv("GATEX_GENERATION_CALLBACK_SECRET", ""))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.secret:
        raise ValueError("GATEX_GENERATION_CALLBACK_SECRET is required")
    callback_base = validated_callback_base(args.callback_base)
    repo_root = args.repo_root.resolve()
    docx_paths = list(args.docx)
    for list_path in args.docx_list:
        docx_paths.extend(read_path_list(list_path))
    manifest_paths = list(args.manifest)
    for list_path in args.manifest_list:
        manifest_paths.extend(read_path_list(list_path))
    unique_docx = list(dict.fromkeys(path.resolve() for path in docx_paths))
    if not unique_docx:
        raise ValueError("At least one generated DOCX is required")
    report_type = args.report_type or (
        "Digital Assets" if args.source_module == "kc_c_wk" else "Transactions & M&A"
    )
    source_repo = args.source_repo or args.source_module
    for docx in unique_docx:
        upload_report(
            docx=docx,
            manifests=[path.resolve() for path in manifest_paths],
            repo_root=repo_root,
            callback_base=callback_base,
            secret=args.secret,
            source_module=args.source_module,
            source_repo=source_repo,
            report_type=report_type,
        )
    print(f"GateX import complete for {len(unique_docx)} report(s).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"GateX import failed: {exc}", file=sys.stderr)
        sys.exit(1)
