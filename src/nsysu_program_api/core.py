from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .selection import build_selection_requirements, constraint_id, split_course_names

CATALOG_URL = "https://ctdr.nsysu.edu.tw/class2.php"
SCHEMA_VERSION = "1.0"
PARSER_VERSION = "0.2.3"
NAMESPACE = uuid.UUID("a441fd7d-a05f-4f28-8bb7-7ccbdd0a6cab")
TYPE_NAMES = {
    0: "integrated_program",
    1: "department_professional_program",
    2: "micro_program",
    3: "discontinued_program",
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extractor_versions() -> dict[str, str | None]:
    """Record exact PDF toolchain versions used to produce normalized output."""
    versions: dict[str, str | None] = {}
    for package in ("pypdf", "pdfplumber"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def stable_id(name: str, program_type: str, registry: dict[str, str]) -> str:
    """Use the checked-in registry first; deterministic UUID is only initial assignment."""
    key = f"{program_type}:{normalize_text(name)}"
    if key not in registry:
        registry[key] = f"prog_{uuid.uuid5(NAMESPACE, key).hex[:16]}"
    return registry[key]


@dataclass
class Response:
    body: bytes
    url: str
    status: int
    headers: dict[str, str]


class Fetcher:
    def __init__(
        self,
        user_agent: str,
        timeout: int | None = None,
        delay: float = 0.35,
        attempts: int | None = None,
    ):
        self.user_agent = user_agent
        self.timeout = timeout or _env_int("NSYSU_API_TIMEOUT", 30, 5, 180)
        self.delay = delay
        self.attempts = attempts or _env_int("NSYSU_API_ATTEMPTS", 3, 1, 8)

    def get(self, url: str, attempts: int | None = None) -> Response:
        attempts = attempts or self.attempts
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read()
                    time.sleep(self.delay)
                    return Response(
                        body, response.geturl(), response.status, dict(response.headers)
                    )
            except (OSError, urllib.error.URLError) as exc:
                last = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"fetch failed after {attempts} attempts: {url}: {last}")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


class CatalogParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[dict[str, Any]]] = []
        self._table: list[dict[str, Any]] | None = None
        self._row: dict[str, Any] | None = None
        self._cell: dict[str, Any] | None = None
        self._anchor: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = {"cells": [], "links": []}
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {"text": [], "links": []}
        elif tag == "a" and self._cell is not None:
            self._anchor = attrs_dict.get("href")
            if self._anchor:
                self._cell["links"].append(self._anchor)
                self._row["links"].append(self._anchor)
        elif tag == "br" and self._cell is not None:
            self._cell["text"].append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._cell["text"] = normalize_text("".join(self._cell["text"]))
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row["cells"]:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def parse_catalog(body: bytes, retrieved_at: str, registry: dict[str, str]) -> list[dict]:
    parser = CatalogParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    programs: list[dict] = []
    for table_index, table in enumerate(parser.tables[:4]):
        program_type = TYPE_NAMES[table_index]
        for row in table[1:]:
            cells = row["cells"]
            if len(cells) < 5 or not cells[0]["text"]:
                continue
            name = cells[0]["text"]
            all_links = [urljoin(CATALOG_URL, link) for link in row["links"]]
            pdfs = [link for link in all_links if ".pdf" in link.lower()]
            websites = [link for link in all_links if link not in pdfs]
            status_text = " ".join(cell["text"] for cell in cells)
            status = "discontinued" if table_index == 3 or "停" in status_text else "active"
            unit, coordinator = split_responsible(cells[2]["text"])
            programs.append(
                {
                    "program_id": stable_id(name, program_type, registry),
                    "name_zh": name,
                    "name_en": None,
                    "previous_names": [],
                    "type": program_type,
                    "status": status,
                    "description": cells[1]["text"],
                    "responsible_unit": unit,
                    "coordinator": coordinator,
                    "program_website": websites[0] if websites else None,
                    "source_pdf": pdfs[0] if pdfs else None,
                    "academic_version": "115-1",
                    "source": {
                        "catalog_url": CATALOG_URL,
                        "pdf_url": pdfs[0] if pdfs else None,
                        "retrieved_at": retrieved_at,
                        "pdf_binary_sha256": None,
                        "normalized_text_sha256": None,
                        "http": {},
                        "parser_version": PARSER_VERSION,
                    },
                    "review_status": "source_only",
                    "rules": {"kind": "manual_review", "reason": "PDF not processed"},
                    "warnings": [],
                }
            )
    return programs


def split_responsible(value: str) -> tuple[str | None, str | None]:
    lines = [part.strip() for part in value.splitlines() if part.strip()]
    if len(lines) >= 2:
        return (" ".join(lines[:-1]), lines[-1])
    parts = value.rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (value or None, None)


def extract_pdf_text(pdf_path: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path), strict=False)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # damaged PDFs are retained as review items
        return "", [f"pdf_extract_error: {type(exc).__name__}: {exc}"]
    text = normalize_text(text)
    if len(text) < 100:
        warnings.append("text_quality_low: OCR required")
    return text, warnings


PDF_VERSION_PATTERN = re.compile(r"【\s*(\d{3})\s*學年度第\s*([12])\s*學期起適用\s*】")


def pdf_academic_version(text: str) -> str | None:
    match = PDF_VERSION_PATTERN.search(text)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _clean_table_value(value: Any) -> str:
    if value is None:
        return ""
    return normalize_text(str(value)).replace("\n", " ")


def _join_wrapped_text(value: Any) -> str:
    """Undo visual PDF wrapping while keeping spaces between Latin words."""
    lines = [normalize_text(line) for line in str(value or "").splitlines() if normalize_text(line)]
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        needs_space = bool(
            result
            and line
            and result[-1].isascii()
            and result[-1].isalnum()
            and line[0].isascii()
            and line[0].isalnum()
        )
        result += (" " if needs_space else "") + line
    return result


UNIT_ENDING = re.compile(
    r"(?:系|所|碩|院|中心|學程|聯盟|向度[一二三四五六七八九十]|選修\([^)]+\))$"
)


def _split_units(value: str) -> list[str]:
    """Preserve every explicitly listed opening unit without inventing mappings."""
    lines = [normalize_text(item) for item in re.split(r"\n|/|、", value) if normalize_text(item)]
    units: list[str] = []
    pending = ""
    for line in lines:
        pending = _join_wrapped_text(f"{pending}\n{line}") if pending else line
        if UNIT_ENDING.search(pending) or len(lines) == 1:
            units.append(pending)
            pending = ""
    if pending:
        if units and not UNIT_ENDING.search(pending):
            units[-1] = _join_wrapped_text(f"{units[-1]}\n{pending}")
        else:
            units.append(pending)
    return list(dict.fromkeys(units))


def _evidence_key(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", value)).casefold()


MAX_COURSE_COUNT_PATTERN = re.compile(
    r"至多\s*採認\s*(?P<count>[一二三四五六七八九十\d]+)\s*科"
)
COUNT_TEXT_VALUES = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _course_count(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return COUNT_TEXT_VALUES.get(value)


def extract_course_count_constraints(
    page_text: str, courses: list[dict], source_page: int
) -> list[dict]:
    """Extract explicit 'at most N courses count' rules from nearby PDF text."""
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    constraints: list[dict] = []
    seen: set[tuple[tuple[str, ...], int]] = set()
    for end_index in range(len(lines)):
        marker_window = "".join(lines[max(0, end_index - 1) : end_index + 1])
        if not MAX_COURSE_COUNT_PATTERN.search(marker_window):
            continue
        if end_index > 0 and MAX_COURSE_COUNT_PATTERN.search(lines[end_index - 1]):
            continue
        marker_start = (
            end_index
            if MAX_COURSE_COUNT_PATTERN.search(lines[end_index])
            else end_index - 1
        )
        fragments = lines[marker_start : end_index + 1]
        for index in range(marker_start - 1, max(-1, marker_start - 13), -1):
            previous = lines[index]
            if "開課單位" in previous.replace(" ", "") or re.fullmatch(r"\d+", previous):
                break
            previous_marker_window = "".join(lines[index : min(len(lines), index + 2)])
            if (
                index + 1 < marker_start
                and MAX_COURSE_COUNT_PATTERN.search(previous_marker_window)
            ):
                break
            row_match = re.match(
                r"^.*?\s\d+(?:\.5)?(?:\s+(?P<note>.+))?$", previous
            )
            if row_match:
                trailing_note = row_match.group("note")
                if trailing_note:
                    fragments.insert(0, trailing_note)
                continue
            fragments.insert(0, previous)
        note_fragments = []
        for fragment in fragments:
            row_match = re.match(
                r"^.*?\s\d+(?:\.5)?(?:\s+(?P<note>.+))?$", fragment
            )
            if not row_match:
                note_fragments.append(fragment)
            elif row_match.group("note"):
                note_fragments.append(row_match.group("note"))
        source_text = _join_wrapped_text("\n".join(note_fragments))
        marker = MAX_COURSE_COUNT_PATTERN.search(source_text)
        if not marker:
            continue
        source_text = source_text[: marker.end()].rstrip("。") + "。"
        max_courses = _course_count(marker.group("count"))
        if max_courses is None:
            continue

        source_key = _evidence_key(source_text)
        course_names = list(
            dict.fromkeys(
                course["course_name_snapshot"]
                for course in courses
                if _evidence_key(course["course_name_snapshot"]) in source_key
            )
        )
        if len(course_names) < 2:
            continue
        dedupe_key = (tuple(course_names), max_courses)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        positions = [source_text.find(name) for name in course_names if name in source_text]
        if positions:
            source_text = source_text[min(positions) :]

        groups = {
            course.get("requirement_group", "unspecified")
            for course in courses
            if course["course_name_snapshot"] in course_names
        }
        requirement_group = groups.pop() if len(groups) == 1 else "unspecified"
        identity = "|".join([str(source_page), str(max_courses), *course_names])
        constraints.append(
            {
                "constraint_id": f"constraint_{sha256(identity.encode())[:16]}",
                "kind": "max_courses",
                "course_names": course_names,
                "max_courses": max_courses,
                "requirement_group": requirement_group,
                "source_page": source_page,
                "source_text": source_text,
                "validation_status": "source_text_match",
            }
        )
    return constraints


def _consolidate_course_count_constraints(constraints: list[dict]) -> list[dict]:
    ordered = sorted(
        constraints,
        key=lambda item: (
            -len(item["course_names"]),
            len(item["source_text"]),
            item["source_page"],
        ),
    )
    kept: list[dict] = []
    for constraint in ordered:
        names = set(constraint["course_names"])
        if any(
            names <= set(existing["course_names"])
            and constraint["max_courses"] == existing["max_courses"]
            and constraint["requirement_group"] == existing["requirement_group"]
            for existing in kept
        ):
            continue
        kept.append(constraint)
    return sorted(kept, key=lambda item: (item["source_page"], item["constraint_id"]))


def _attach_constraint_notes(courses: list[dict], constraints: list[dict]) -> None:
    for constraint in constraints:
        names = set(constraint["course_names"])
        note = constraint["source_text"]
        for course in courses:
            if (
                course.get("course_name_snapshot") in names
                and note not in course.get("notes", "")
            ):
                course["notes"] = " ".join(
                    value for value in (course.get("notes", ""), note) if value
                )


def apply_course_count_constraints(
    page_text: str, courses: list[dict], requirements: dict, source_page: int
) -> list[dict]:
    """Add normalized constraints and preserve their source text on affected courses."""
    constraints = extract_course_count_constraints(page_text, courses, source_page)
    if not constraints:
        return []
    merged = _consolidate_course_count_constraints(
        [*requirements.get("course_count_constraints", []), *constraints]
    )
    requirements["course_count_constraints"] = merged
    _attach_constraint_notes(courses, merged)
    return constraints


def extract_pdf_tables(pdf_path: Path) -> tuple[list[dict], list[str]]:
    """Extract versioned course tables from native PDF geometry."""
    warnings: list[str] = []
    versions: dict[str, dict] = {}
    version_pages: dict[str, list[tuple[int, str]]] = {}
    current_version = "unknown"
    current_group: str | None = None
    current_section = "unspecified"
    current_program_course: str | None = None
    current_layout: dict[str, int | None] = {
        "group": 0,
        "unit": 1,
        "name": 2,
        "credit": 3,
        "note": 4,
        "program_course": None,
    }
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as document:
            for page_number, page in enumerate(document.pages, 1):
                page_text = page.extract_text() or ""
                detected = pdf_academic_version(page_text)
                if detected and detected != current_version:
                    current_version = detected
                    current_group = None
                    current_section = "unspecified"
                    current_program_course = None
                    current_layout = {
                        "group": 0,
                        "unit": 1,
                        "name": 2,
                        "credit": 3,
                        "note": 4,
                        "program_course": None,
                    }
                version = versions.setdefault(
                    current_version,
                    {
                        "pdf_academic_version": None
                        if current_version == "unknown"
                        else current_version,
                        "courses": [],
                        "requirements": {},
                        "_table_rows": [],
                        "source_pages": [],
                        "audit": {
                            "compound_rows_needing_review": [],
                            "duplicates_removed": 0,
                        },
                    },
                )
                version_pages.setdefault(current_version, []).append((page_number, page_text))
                version["source_pages"].append(page_number)
                totals = [
                    int(value)
                    for value in re.findall(r"總學分數[^\n]{0,30}?(\d{1,2})\s*學分", page_text)
                ]
                core = [
                    int(value)
                    for value in re.findall(r"核心課程[^\n]{0,20}?(\d{1,2})\s*學分", page_text)
                ]
                cross = [
                    int(value)
                    for value in re.findall(
                        r"至少應有\s*(\d{1,2})\s*學分不屬於學生本系所", page_text
                    )
                ]
                if totals:
                    version["requirements"]["minimum_total_credits"] = totals[-1]
                if core:
                    version["requirements"]["core_credits_text_value"] = core[-1]
                if cross:
                    version["requirements"]["minimum_outside_home_department_credits"] = cross[-1]
                page_evidence = _evidence_key(page_text)
                for table_index, table in enumerate(page.extract_tables()):
                    layout = dict(current_layout)
                    header_row_index: int | None = None
                    for candidate_index, header in enumerate(table):
                        header_cells = [_clean_table_value(value) for value in header]
                        compact_header = [cell.replace(" ", "") for cell in header_cells]
                        if not any(
                            cell in {"學分", "學分數"} for cell in compact_header
                        ) or not any(
                            "課程名稱" in cell
                            or "學程科目名稱" in cell
                            or cell in {"課程", "學程科目"}
                            for cell in compact_header
                        ):
                            continue
                        layout = {
                            "group": 0,
                            "unit": 1,
                            "name": 2,
                            "credit": 3,
                            "note": 4,
                            "program_course": None,
                        }
                        for index, cell in enumerate(header_cells):
                            compact = cell.replace(" ", "")
                            if "開課" in compact and any(
                                label in compact for label in ("單位", "系所")
                            ):
                                layout["unit"] = index
                            elif "學程科目" in compact:
                                layout["program_course"] = index
                            elif "採認課程名稱" in compact or "課程名稱" in compact:
                                layout["name"] = index
                            elif "學分" in compact:
                                layout["credit"] = index
                            elif "備註" in compact:
                                layout["note"] = index
                        header_row_index = candidate_index
                        current_layout = dict(layout)
                        if layout["program_course"] is None:
                            current_program_course = None
                        break
                    column_count = max((len(row) for row in table if row), default=0)
                    note_index = layout.get("note")
                    if (
                        header_row_index is None
                        and layout.get("program_course") is None
                        and isinstance(note_index, int)
                        and note_index >= column_count
                        and column_count == 5
                    ):
                        layout = {
                            "group": 0,
                            "unit": 1,
                            "name": 2,
                            "credit": 3,
                            "note": 4,
                            "program_course": None,
                        }
                        current_layout = dict(layout)
                    for row_index, row in enumerate(table):
                        if not row or len(row) < 4:
                            continue
                        if row_index == header_row_index:
                            continue
                        cells = [_clean_table_value(value) for value in row]
                        joined = " ".join(cells)
                        group_index = layout["group"]
                        marker = (
                            re.sub(r"\s+", "", cells[group_index])
                            if isinstance(group_index, int) and group_index < len(cells)
                            else ""
                        )
                        row_rule_key = re.sub(r"\s+", "", joined)
                        if "核心(必選修)" in row_rule_key:
                            current_group = "core"
                            current_section = "core_selective"
                        elif "核心(必修)" in row_rule_key:
                            current_group = "core"
                            current_section = "core_required"
                        elif marker.startswith("核心") or marker == "必修":
                            current_group = "core"
                            if "必選修" in marker:
                                current_section = "core_selective"
                            elif "必修" in marker:
                                current_section = "core_required"
                            else:
                                current_section = "core"
                        elif marker.startswith("選修") or marker == "選修課程":
                            current_group = "elective"
                            current_section = "elective"
                        program_course_index = layout["program_course"]
                        if (
                            isinstance(program_course_index, int)
                            and program_course_index < len(row)
                        ):
                            program_course_raw = str(row[program_course_index] or "")
                            program_course_value = _join_wrapped_text(program_course_raw)
                            if program_course_value and "學分數" not in program_course_value:
                                current_program_course = program_course_value
                        credit_index = layout["credit"]
                        unit_index = layout["unit"]
                        name_index = layout["name"]
                        note_index = layout["note"]
                        credit_raw = (
                            str(row[credit_index] or "")
                            if isinstance(credit_index, int) and credit_index < len(row)
                            else ""
                        )
                        credit_values = re.findall(r"(?<!\d)(\d+(?:\.5)?)(?!\d)", credit_raw)
                        unit_raw = (
                            str(row[unit_index] or "")
                            if isinstance(unit_index, int) and unit_index < len(row)
                            else ""
                        )
                        name_raw = (
                            str(row[name_index] or "")
                            if isinstance(name_index, int) and name_index < len(row)
                            else ""
                        )
                        notes = (
                            _join_wrapped_text(row[note_index])
                            if isinstance(note_index, int) and note_index < len(row)
                            else ""
                        )
                        row_metadata = {
                            "table_id": f"{page_number}:{table_index}",
                            "row_index": row_index,
                            "source_page": page_number,
                            "requirement_group": current_group or "unspecified",
                            "requirement_section": current_section,
                            "rule_text": notes or joined,
                            "notes": notes,
                            "raw_course_name": _join_wrapped_text(name_raw),
                            "is_summary": bool(
                                re.search(r"(?:學分數|總學分|總計)", joined)
                            ),
                            "courses": [],
                        }
                        version["_table_rows"].append(row_metadata)
                        if not credit_values or not unit_raw.strip() or not name_raw.strip():
                            continue
                        if (
                            len(credit_values) > 1
                            and len(set(credit_values)) == 1
                            and "/" in credit_raw
                            and "&" not in name_raw
                        ):
                            credit_values = credit_values[:1]
                        if len(credit_values) > 1:
                            name_lines = [
                                normalize_text(line)
                                for line in name_raw.splitlines()
                                if normalize_text(line)
                            ]
                            if (
                                len(name_lines) == len(credit_values) + 1
                                and min(map(len, name_lines)) <= 4
                            ):
                                shortest = min(
                                    range(len(name_lines)), key=lambda index: len(name_lines[index])
                                )
                                if shortest > 0:
                                    name_lines[shortest - 1] += name_lines.pop(shortest)
                                else:
                                    name_lines[0] += name_lines.pop(1)
                            if len(name_lines) != len(credit_values):
                                version["audit"]["compound_rows_needing_review"].append(
                                    {
                                        "page": page_number,
                                        "opening_unit_raw": _clean_table_value(unit_raw),
                                        "course_name_raw": _clean_table_value(name_raw),
                                        "credits_raw": _clean_table_value(credit_raw),
                                    }
                                )
                                continue
                            course_names = name_lines
                        else:
                            course_names = split_course_names(
                                name_raw, len(credit_values), notes, unit_raw
                            )
                        if not course_names:
                            continue
                        expanded_credits = (
                            credit_values
                            if len(credit_values) == len(course_names)
                            else [credit_values[0]] * len(course_names)
                        )
                        units = _split_units(unit_raw)
                        base_entry_id = constraint_id(
                            "entry",
                            pdf_path.stem,
                            current_version,
                            page_number,
                            table_index,
                            row_index,
                        ).replace("constraint_", "entry_")
                        for course_name, credit_value in zip(
                            course_names, expanded_credits, strict=True
                        ):
                            course_index = len(row_metadata["courses"])
                            entry_id = (
                                f"{base_entry_id}_{course_index + 1}"
                                if len(credit_values) > 1
                                else base_entry_id
                            )
                            record = {
                                "course_code": None,
                                "catalog_entry_id": entry_id,
                                "opening_units": units,
                                "opening_unit_snapshot": " / ".join(units),
                                "course_name_snapshot": course_name,
                                "credits_snapshot": float(credit_value),
                                "requirement_group": current_group or "unspecified",
                                "requirement_section": current_section,
                                "program_course_name_snapshot": current_program_course,
                                "notes": notes,
                                "source_page": page_number,
                                "evidence_match": _evidence_key(course_name) in page_evidence,
                                "validation_status": "needs_course_code_verification",
                                "uncertainty_reason": (
                                    "Official PDF table does not provide a course code"
                                ),
                            }
                            version["courses"].append(record)
                            row_metadata["courses"].append(record)
    except Exception as exc:
        return [], [f"pdf_table_extract_error: {type(exc).__name__}: {exc}"]
    result = list(versions.values())
    for version_key, version in versions.items():
        table_rows = version.pop("_table_rows", [])
        extracted_constraints = []
        for page_number, page_text in version_pages.get(version_key, []):
            extracted_constraints.extend(
                extract_course_count_constraints(page_text, version["courses"], page_number)
            )
        selection_requirements = build_selection_requirements(
            version["courses"], table_rows, version_pages.get(version_key, [])
        )
        extracted_constraints.extend(
            selection_requirements.pop("course_count_constraints", [])
        )
        if extracted_constraints:
            consolidated = _consolidate_course_count_constraints(extracted_constraints)
            version["requirements"]["course_count_constraints"] = consolidated
            _attach_constraint_notes(version["courses"], consolidated)
        version["requirements"].update(selection_requirements)
        unique = []
        seen = set()
        for course in version["courses"]:
            key = (
                tuple(course["opening_units"]),
                course["course_name_snapshot"],
                course["credits_snapshot"],
                course["requirement_group"],
                course.get("requirement_section"),
                course.get("program_course_name_snapshot"),
            )
            if key in seen:
                version["audit"]["duplicates_removed"] += 1
                continue
            seen.add(key)
            unique.append(course)
        version["courses"] = unique
        version["audit"].update(
            {
                "course_count": len(unique),
                "core_count": sum(c["requirement_group"] == "core" for c in unique),
                "elective_count": sum(c["requirement_group"] == "elective" for c in unique),
                "evidence_matched_count": sum(c["evidence_match"] for c in unique),
            }
        )
        constraint_count = len(version["requirements"].get("course_count_constraints", []))
        if constraint_count:
            version["audit"]["course_count_constraints_extracted"] = constraint_count
        selection_count = sum(
            len(version["requirements"].get(key, []))
            for key in (
                "entry_selection_constraints",
                "program_course_selection_constraints",
                "no_double_count_constraints",
                "named_group_selection_constraints",
            )
        )
        if selection_count:
            version["audit"]["selection_constraints_extracted"] = selection_count
    if not any(version["courses"] for version in result):
        warnings.append("No structured course rows extracted from PDF tables")
    return result, warnings


COURSE_PATTERN = re.compile(
    r"(?P<code>[A-Z]{1,5}\s*[-_]?[A-Z0-9]{2,8})\s+(?P<name>[^\n]{2,45}?)\s+(?P<credits>[0-9](?:\.[05])?)\s*(?:學分)?",
    re.IGNORECASE,
)


def extract_candidate(text: str) -> dict:
    courses = []
    seen = set()
    for match in COURSE_PATTERN.finditer(text):
        code = re.sub(r"\s+", "", match.group("code")).upper()
        if code in seen:
            continue
        seen.add(code)
        courses.append(
            {
                "course_code": code,
                "name_snapshot": normalize_text(match.group("name")),
                "credits_snapshot": float(match.group("credits")),
                "aliases": [],
                "validation_status": "unverified",
                "uncertainty_reason": "machine extracted; human review required",
            }
        )
    credit_mentions = [
        int(x) for x in re.findall(r"(?:至少|最低|應修|須修)[^\n]{0,12}?(\d{1,2})\s*學分", text)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "courses": courses,
        "credit_mentions": credit_mentions,
        "ai_interpretation": None,
        "confidence": "low" if not courses else "medium",
        "warnings": ["Candidate only; not approved for publishing as executable rules"],
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
