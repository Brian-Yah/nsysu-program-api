from __future__ import annotations

import hashlib
import json
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

CATALOG_URL = "https://ctdr.nsysu.edu.tw/class2.php"
SCHEMA_VERSION = "1.0"
PARSER_VERSION = "0.1.0"
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
    def __init__(self, user_agent: str, timeout: int = 30, delay: float = 0.35):
        self.user_agent, self.timeout, self.delay = user_agent, timeout, delay

    def get(self, url: str, attempts: int = 3) -> Response:
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


def extract_pdf_tables(pdf_path: Path) -> tuple[list[dict], list[str]]:
    """Extract versioned course tables from native PDF geometry."""
    warnings: list[str] = []
    versions: dict[str, dict] = {}
    current_version = "unknown"
    current_group: str | None = None
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as document:
            for page_number, page in enumerate(document.pages, 1):
                page_text = page.extract_text() or ""
                detected = pdf_academic_version(page_text)
                if detected:
                    current_version = detected
                    current_group = None
                version = versions.setdefault(
                    current_version,
                    {
                        "pdf_academic_version": None
                        if current_version == "unknown"
                        else current_version,
                        "courses": [],
                        "requirements": {},
                        "source_pages": [],
                        "audit": {
                            "compound_rows_needing_review": [],
                            "duplicates_removed": 0,
                        },
                    },
                )
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
                for table in page.extract_tables():
                    unit_index, name_index, credit_index, note_index = 1, 2, 3, 4
                    group_index: int | None = 0
                    for header in table:
                        header_cells = [_clean_table_value(value) for value in header]
                        if not any(
                            "開課" in cell.replace(" ", "")
                            and any(label in cell.replace(" ", "") for label in ("單位", "系所"))
                            for cell in header_cells
                        ):
                            continue
                        for index, cell in enumerate(header_cells):
                            compact = cell.replace(" ", "")
                            if "開課" in compact and any(
                                label in compact for label in ("單位", "系所")
                            ):
                                unit_index = index
                            elif "課程名稱" in compact:
                                name_index = index
                            elif "學分" in compact:
                                credit_index = index
                            elif "備註" in compact:
                                note_index = index
                        break
                    for row in table:
                        if not row or len(row) < 4:
                            continue
                        cells = [_clean_table_value(value) for value in row]
                        joined = " ".join(cells)
                        if "開課單位" in joined and "課" in joined and "學分" in joined:
                            continue
                        marker = (
                            cells[group_index].replace(" ", "")
                            if group_index is not None and group_index < len(cells)
                            else ""
                        )
                        if marker in {"核心課程", "核心", "必修"}:
                            current_group = "core"
                        elif marker in {"選修", "選修課程"}:
                            current_group = "elective"
                        credit_raw = str(row[credit_index] or "") if credit_index < len(row) else ""
                        credit_values = re.findall(r"(?<!\d)(\d+(?:\.5)?)(?!\d)", credit_raw)
                        unit_raw = str(row[unit_index] or "") if unit_index < len(row) else ""
                        name_raw = str(row[name_index] or "") if name_index < len(row) else ""
                        if not credit_values or not unit_raw.strip() or not name_raw.strip():
                            continue
                        if len(credit_values) > 1:
                            name_lines = [
                                normalize_text(line)
                                for line in name_raw.splitlines()
                                if normalize_text(line)
                            ]
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
                            course_names = [_join_wrapped_text(name_raw)]
                        units = _split_units(unit_raw)
                        notes = _join_wrapped_text(row[note_index]) if note_index < len(row) else ""
                        for course_name, credit_value in zip(
                            course_names, credit_values, strict=True
                        ):
                            record = {
                                "course_code": None,
                                "opening_units": units,
                                "opening_unit_snapshot": " / ".join(units),
                                "course_name_snapshot": course_name,
                                "credits_snapshot": float(credit_value),
                                "requirement_group": current_group or "unspecified",
                                "notes": notes,
                                "source_page": page_number,
                                "evidence_match": _evidence_key(course_name) in page_evidence,
                                "validation_status": "needs_course_code_verification",
                                "uncertainty_reason": (
                                    "Official PDF table does not provide a course code"
                                ),
                            }
                            version["courses"].append(record)
    except Exception as exc:
        return [], [f"pdf_table_extract_error: {type(exc).__name__}: {exc}"]
    result = list(versions.values())
    for version in result:
        unique = []
        seen = set()
        for course in version["courses"]:
            key = (
                tuple(course["opening_units"]),
                course["course_name_snapshot"],
                course["credits_snapshot"],
                course["requirement_group"],
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
