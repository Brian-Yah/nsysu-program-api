from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

from .core import SCHEMA_VERSION, Fetcher, load_json, now_iso, sha256, write_json

DEPARTMENT_INDEX_URL = "https://selcrs.nsysu.edu.tw/stu_query/crs_mst_qry/crs_mst_query_top.asp"
REQUIREMENTS_URL = "https://selcrs.nsysu.edu.tw/stu_query/crs_mst_qry/crs_mst_query.asp"
GRADUATION_PARSER_VERSION = "1.0.0"

# The official selector is a global list and includes retired departments as well as
# departments that did not yet exist for an older entry year.  These ranges are
# pinned to official institutional-history evidence so a HTTP 200 skeleton page is
# never mistaken for an active curriculum.
DEPARTMENT_ENTRY_YEAR_RANGES: dict[str, tuple[int | None, int | None]] = {
    "B3080": (None, 96),  # renamed/split into B3090 and B3100 from year 97
    "B5610": (None, 101),  # merged into B5090 from year 102
    "B7020": (115, None),  # successor department to B7610
    "B7610": (None, 114),  # stopped admitting students after year 114
    "B7620": (114, None),  # established in year 114
    "B8060": (113, None),  # established in year 113
    "B8070": (114, None),  # established in year 114
}


def department_is_active_for_entry_year(department_code: str, entry_year: str) -> bool:
    """Return whether a global-selector department applies to this entry year."""
    if not re.fullmatch(r"\d{3}", entry_year):
        raise ValueError("entry_year must be a three-digit ROC academic year")
    bounds = DEPARTMENT_ENTRY_YEAR_RANGES.get(department_code)
    if bounds is None:
        return True
    first_year, last_year = bounds
    year = int(entry_year)
    return (first_year is None or year >= first_year) and (last_year is None or year <= last_year)


def active_departments_for_entry_year(
    departments: list[dict[str, str]], entry_year: str
) -> list[dict[str, str]]:
    """Filter the official global selector using verified department lifecycles."""
    return [
        department
        for department in departments
        if department_is_active_for_entry_year(department["department_code"], entry_year)
    ]


class DepartmentOptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.departments: list[dict[str, str]] = []
        self._in_department_select = False
        self._option_value: str | None = None
        self._option_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "select":
            self._in_department_select = attributes.get("name") == "DPT1"
        elif tag.lower() == "option" and self._in_department_select:
            self._option_value = attributes.get("value")
            self._option_text = []

    def handle_data(self, data: str) -> None:
        if self._option_value is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "option" and self._option_value is not None:
            code = self._option_value.strip()
            text = " ".join("".join(self._option_text).split())
            if code:
                name = text.removeprefix(code).strip()
                self.departments.append({"department_code": code, "department_name": name})
            self._option_value = None
            self._option_text = []
        elif tag.lower() == "select" and self._in_department_select:
            self._in_department_select = False


class EntryYearOptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entry_years: list[str] = []
        self._in_year_select = False
        self._option_value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "select":
            self._in_year_select = attributes.get("name") == "YY1"
        elif tag.lower() == "option" and self._in_year_select:
            self._option_value = attributes.get("value")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "option" and self._option_value is not None:
            value = self._option_value.strip()
            if re.fullmatch(r"\d{3}", value) and value not in self.entry_years:
                self.entry_years.append(value)
            self._option_value = None
        elif tag.lower() == "select" and self._in_year_select:
            self._in_year_select = False


class RequirementTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._finish_row()
            self._row = []
        elif tag in {"td", "th"}:
            self._finish_cell()
            if self._row is None:
                self._row = []
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()

    def close(self) -> None:
        super().close()
        self._finish_cell()
        self._finish_row()

    def _finish_cell(self) -> None:
        if self._cell is None:
            return
        value = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._cell).replace("\xa0", " "))
        value = "\n".join(part.strip() for part in value.splitlines() if part.strip())
        if self._row is None:
            self._row = []
        self._row.append(value)
        self._cell = None

    def _finish_row(self) -> None:
        self._finish_cell()
        if self._row and any(self._row):
            self.rows.append(self._row)
        self._row = None


def parse_department_options(html: str, degree_prefix: str = "B") -> list[dict[str, str]]:
    parser = DepartmentOptionParser()
    parser.feed(html)
    parser.close()
    return [
        department
        for department in parser.departments
        if department["department_code"].startswith(degree_prefix)
    ]


def parse_entry_year_options(html: str) -> list[str]:
    parser = EntryYearOptionParser()
    parser.feed(html)
    parser.close()
    return sorted(parser.entry_years, key=int)


def parse_graduation_requirement(html: str) -> dict[str, int | float | None] | None:
    parser = RequirementTableParser()
    parser.feed(html)
    parser.close()
    for row in parser.rows:
        if not row:
            continue
        label = re.sub(r"\s+", "", row[0])
        if "最低畢業學分數" not in label:
            continue
        credit_match = re.search(r"\d+", row[1] if len(row) > 1 else "")
        if not credit_match:
            return None
        ratio_match = re.search(r"\d+(?:\.\d+)?", row[3] if len(row) > 3 else "")
        minimum_credits = int(credit_match.group())
        if minimum_credits <= 0:
            return None
        return {
            "minimum_graduation_credits": minimum_credits,
            "required_course_ratio": float(ratio_match.group()) if ratio_match else None,
        }
    return None


def requirement_url(entry_year: str, department_code: str) -> str:
    query = urlencode({"action": "3", "YY1": entry_year, "DPT1": department_code})
    return f"{REQUIREMENTS_URL}?{query}"


def fetch_graduation_requirements(
    root: Path, entry_year: str, user_agent: str, degree_prefix: str = "B"
) -> dict:
    if not re.fullmatch(r"\d{3}", entry_year):
        raise ValueError("entry_year must be a three-digit ROC academic year")
    retrieved_at = now_iso()
    fetcher = Fetcher(user_agent)
    index_response = fetcher.get(DEPARTMENT_INDEX_URL)
    departments = active_departments_for_entry_year(
        parse_department_options(
            index_response.body.decode("utf-8", errors="replace"), degree_prefix
        ),
        entry_year,
    )
    if not departments:
        raise RuntimeError(f"no departments found with prefix {degree_prefix!r}")

    requirements = []
    unavailable = []
    fetch_failures = []
    for department in departments:
        code = department["department_code"]
        url = requirement_url(entry_year, code)
        try:
            response = fetcher.get(url)
        except RuntimeError as exc:
            fetch_failures.append({**department, "reason": str(exc)})
            continue
        parsed = parse_graduation_requirement(response.body.decode("utf-8", errors="replace"))
        if parsed is None:
            unavailable.append({**department, "reason": "no requirement table for entry year"})
            continue
        requirements.append(
            {
                **department,
                "degree_level": "bachelor",
                "entry_academic_year": entry_year,
                **parsed,
                "source": {
                    "url": url,
                    "retrieved_at": retrieved_at,
                    "binary_sha256": sha256(response.body),
                    "http_status": response.status,
                    "parser_version": GRADUATION_PARSER_VERSION,
                },
            }
        )
    if fetch_failures:
        codes = ", ".join(item["department_code"] for item in fetch_failures)
        raise RuntimeError(f"failed to fetch graduation requirements for: {codes}")
    if not requirements:
        raise RuntimeError(f"no graduation requirements found for entry year {entry_year}")

    dataset = {
        "schema_version": SCHEMA_VERSION,
        "entry_academic_year": entry_year,
        "degree_level": "bachelor",
        "retrieved_at": retrieved_at,
        "department_count": len(requirements),
        "requirements": sorted(requirements, key=lambda item: item["department_code"]),
        "unavailable_departments": sorted(unavailable, key=lambda item: item["department_code"]),
        "source": {
            "index_url": DEPARTMENT_INDEX_URL,
            "index_binary_sha256": sha256(index_response.body),
            "parser_version": GRADUATION_PARSER_VERSION,
        },
    }
    write_json(root / "data" / "graduation-requirements" / entry_year / "bachelor.json", dataset)
    return dataset


def materialize_graduation_requirements_from_rules(
    root: Path,
    entry_year: str,
    source_index_sha256: str,
) -> dict:
    """Build the compact setup API from the same pinned official rule sources."""
    if not re.fullmatch(r"\d{3}", entry_year):
        raise ValueError("entry_year must be a three-digit ROC academic year")
    if not re.fullmatch(r"[a-f0-9]{64}", source_index_sha256):
        raise ValueError("source_index_sha256 must be a SHA-256 hex digest")
    rule_paths = sorted(
        (root / "data" / "graduation-rules" / entry_year / "bachelor").glob(
            "*.json"
        )
    )
    if not rule_paths:
        raise RuntimeError(f"missing department rules for entry year {entry_year}")

    existing = load_json(
        root / "data" / "graduation-requirements" / entry_year / "bachelor.json",
        {},
    )
    existing_by_code = {
        item["department_code"]: item for item in existing.get("requirements", [])
    }
    retrieved_at = now_iso()
    requirements = []
    unavailable = []
    for path in rule_paths:
        rule = load_json(path, {})
        code = rule["department_code"]
        minimum = rule.get("credit_requirements", {}).get(
            "minimum_graduation_credits"
        )
        if minimum is None:
            unavailable.append(
                {
                    "department_code": code,
                    "department_name": rule["department_name_zh"],
                    "reason": "official rule does not provide minimum graduation credits",
                }
            )
            continue
        sources = rule.get("sources", [])
        if not sources or not sources[0].get("sha256"):
            raise RuntimeError(f"{entry_year} {code} lacks a pinned official source")
        source = sources[0]
        previous = existing_by_code.get(code, {})
        previous_ratio = (
            previous.get("required_course_ratio")
            if previous.get("minimum_graduation_credits") == minimum
            else None
        )
        requirements.append(
            {
                "department_code": code,
                "department_name": rule["department_name_zh"],
                "degree_level": "bachelor",
                "entry_academic_year": entry_year,
                "minimum_graduation_credits": int(minimum),
                "required_course_ratio": previous_ratio,
                "source": {
                    "url": source["url"],
                    "retrieved_at": retrieved_at,
                    "binary_sha256": source["sha256"],
                    "http_status": 200,
                    "parser_version": f"graduation-rules-{GRADUATION_PARSER_VERSION}",
                },
            }
        )

    dataset = {
        "schema_version": SCHEMA_VERSION,
        "entry_academic_year": entry_year,
        "degree_level": "bachelor",
        "retrieved_at": retrieved_at,
        "department_count": len(requirements),
        "requirements": requirements,
        "unavailable_departments": unavailable,
        "source": {
            "index_url": DEPARTMENT_INDEX_URL,
            "index_binary_sha256": source_index_sha256,
            "parser_version": f"graduation-rules-{GRADUATION_PARSER_VERSION}",
        },
    }
    write_json(
        root / "data" / "graduation-requirements" / entry_year / "bachelor.json",
        dataset,
    )
    return dataset


def build_graduation_api(root: Path, entry_year: str) -> dict:
    source_root = root / "data" / "graduation-requirements"
    datasets = [
        load_json(path, {})
        for path in sorted(source_root.glob("[0-9][0-9][0-9]/bachelor.json"))
    ]
    datasets = [dataset for dataset in datasets if dataset]
    if not datasets:
        raise RuntimeError(f"missing graduation requirements for entry year {entry_year}")
    by_year = {dataset["entry_academic_year"]: dataset for dataset in datasets}
    if entry_year not in by_year:
        raise RuntimeError(f"missing graduation requirements for entry year {entry_year}")
    years = sorted(by_year, key=int)
    latest_year = years[-1]
    latest = by_year[latest_year]
    api = root / "api" / "v1" / "graduation-requirements"
    for year in years:
        dataset = by_year[year]
        write_json(api / year / "bachelor.json", dataset)
        expected_codes = {item["department_code"] for item in dataset["requirements"]}
        directory = api / year / "bachelor"
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.glob("*.json"):
            if path.stem not in expected_codes:
                path.unlink()
        for requirement in dataset["requirements"]:
            write_json(
                directory / f"{requirement['department_code']}.json", requirement
            )

    write_json(api / "latest" / "bachelor.json", latest)
    latest_codes = {item["department_code"] for item in latest["requirements"]}
    latest_directory = api / "latest" / "bachelor"
    latest_directory.mkdir(parents=True, exist_ok=True)
    for path in latest_directory.glob("*.json"):
        if path.stem not in latest_codes:
            path.unlink()
    for requirement in latest["requirements"]:
        write_json(
            latest_directory / f"{requirement['department_code']}.json", requirement
        )

    index = {
        "schema_version": SCHEMA_VERSION,
        "latest_entry_academic_year": latest_year,
        "entry_academic_years": years,
        "degree_levels": ["bachelor"],
        "department_count": latest["department_count"],
        "entry_year_summary": [
            {
                "entry_academic_year": year,
                "department_count": by_year[year]["department_count"],
                "unavailable_department_count": len(
                    by_year[year].get("unavailable_departments", [])
                ),
            }
            for year in years
        ],
        "paths": {
            "latest_bachelor": "latest/bachelor.json",
            "year_bachelor_template": "{entry_year}/bachelor.json",
            "department_template": "{entry_year}/bachelor/{department_code}.json",
            "requirement_schema": "../schemas/graduation-requirement.schema.json",
            "collection_schema": "../schemas/graduation-requirements.schema.json",
        },
    }
    write_json(api / "index.json", index)
    for name in ("graduation-requirement.schema.json", "graduation-requirements.schema.json"):
        schema_src = root / "schemas" / name
        schema_dest = root / "api" / "v1" / "schemas" / name
        schema_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(schema_src, schema_dest)
    return index
