from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

from .core import Fetcher, load_json, now_iso, sha256, write_json
from .graduation import (
    DEPARTMENT_INDEX_URL,
    RequirementTableParser,
    materialize_graduation_requirements_from_rules,
    parse_department_options,
    parse_entry_year_options,
    parse_graduation_requirement,
    requirement_url,
)

GRADUATION_RULE_PARSER_VERSION = "1.0.0"
MINIMUM_DEPARTMENT_COVERAGE_RATIO = 0.75
SEMESTERS = ("fall", "spring", "summer")
COMMON_CATEGORY_MARKERS = (
    "通識",
    "共同",
    "語文",
    "跨院",
    "博雅",
    "體驗",
    "服務學習",
    "運動與健康",
    "國防",
)
PROFESSIONAL_CATEGORY_MARKERS = (
    "專業",
    "系必",
    "院必",
    "核心",
    "專門",
    "必修",
    "選修",
)


def _compact(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).replace("\xa0", " ").split())


def _match_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _identifier(department_code: str, value: str) -> str:
    digest = hashlib.sha256(_compact(value).casefold().encode("utf-8")).hexdigest()[:12]
    return f"{department_code.casefold()}_{digest}"


def _number(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", value)
    return float(match.group(1)) if match else None


def _integer(value: str) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None and parsed.is_integer() else None


def _unique_credit_match(value: str, patterns: tuple[str, ...]) -> float | None:
    matches: set[float] = set()
    for pattern in patterns:
        matches.update(float(match.group(1)) for match in re.finditer(pattern, value))
    return next(iter(matches)) if len(matches) == 1 else None


def _numbered_clauses(value: str) -> list[str]:
    compact = _compact(value)
    starts = [
        match.start("number")
        for match in re.finditer(r"(?:^|[\s。；;])(?P<number>\d+)\.", compact)
    ]
    if not starts:
        return [compact] if compact else []
    clauses: list[str] = []
    if starts[0] > 0 and compact[: starts[0]].strip():
        clauses.append(compact[: starts[0]].strip())
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(compact)
        clause = compact[start:end].strip()
        if clause:
            clauses.append(clause)
    return clauses


def _department_specific_clauses(value: str) -> list[str]:
    clauses: list[str] = []
    seen: set[str] = set()
    for clause in _numbered_clauses(value):
        normalized = _match_text(clause)
        normalized_without_number = re.sub(r"^\d+(?:\.\d+)?[.、]?", "", normalized)
        if "通識教育課程必修28學分" in normalized:
            continue
        if "修習通識教育各類課程" in normalized:
            continue
        if normalized_without_number in seen:
            continue
        seen.add(normalized_without_number)
        clauses.append(clause)
    return clauses


def _is_common_international_remark(value: str) -> bool:
    normalized = _match_text(value)
    return (
        "109學年度起入學學士班學生" in normalized
        and "國際或跨域學習" in normalized
        and "出國交換或研修" in normalized
        and "輔系、雙主修或教育學程" in normalized
    )


def _looks_like_category(value: str) -> bool:
    compact = _match_text(value)
    markers = (*COMMON_CATEGORY_MARKERS, *PROFESSIONAL_CATEGORY_MARKERS)
    return any(marker in compact for marker in markers)


def _is_professional_category(value: str) -> bool:
    compact = _match_text(value)
    if any(marker in compact for marker in COMMON_CATEGORY_MARKERS):
        return False
    return any(marker in compact for marker in PROFESSIONAL_CATEGORY_MARKERS)


def _course_placement(
    semester_cells: list[str],
) -> tuple[float | None, int | None, str | None, list[str], bool]:
    placements = [
        (index, parsed)
        for index, value in enumerate(semester_cells)
        if (parsed := _number(value)) is not None
    ]
    if not placements:
        return None, None, None, ["官方表未在年級學期欄標示學分。"], True

    values = {value for _, value in placements}
    years = {index // 3 + 1 for index, _ in placements}
    semester_names = {SEMESTERS[index % 3] for index, _ in placements}
    notes: list[str] = []
    manual = False

    if values == {0.0}:
        credits = None
        manual = True
        notes.append("官方表列為0學分畢業條件，credits保留null。")
    elif len(values) == 1 and len(placements) == 1:
        credits = next(iter(values))
    else:
        credits = None
        manual = True
        rendered = "、".join(
            f"{index // 3 + 1}年{SEMESTERS[index % 3]}={value:g}"
            for index, value in placements
        )
        notes.append(f"官方同一列有多個學期／學分配置（{rendered}），不得合併猜測單科學分。")

    recommended_year = next(iter(years)) if len(years) == 1 else None
    if len(semester_names) == 1:
        recommended_semester = next(iter(semester_names))
    elif semester_names <= {"fall", "spring"}:
        recommended_semester = "either"
    else:
        recommended_semester = None
    if len(placements) > 1:
        manual = True
    return credits, recommended_year, recommended_semester, notes, manual


def _course_requirement(category: str, subgroup: str, group_code: str) -> str:
    scope = _match_text(f"{category} {subgroup}")
    if group_code or "分組" in scope or "任選" in scope:
        return "department_group_elective"
    if "選修" in scope and "必選" not in scope:
        return "department_elective"
    if "核心" in scope:
        return "department_core"
    return "department_required"


def _manual_rule(
    department_code: str,
    suffix: str,
    description: str,
    reason: str,
    source_id: str,
    resolution: str,
) -> dict[str, str]:
    return {
        "rule_id": f"{department_code.casefold()}_{suffix}",
        "description": description,
        "reason": reason,
        "source_document": source_id,
        "resolution": resolution,
    }


def graduation_rule_regression_errors(previous: dict, candidate: dict) -> list[str]:
    """Reject suspicious source regressions while allowing ordinary official edits."""
    errors: list[str] = []
    previous_courses = len(previous.get("courses", []))
    candidate_courses = len(candidate.get("courses", []))
    if previous_courses and not candidate_courses:
        errors.append("course table changed from non-empty to empty")
    elif previous_courses >= 10 and candidate_courses < math.ceil(previous_courses * 0.5):
        errors.append(
            f"course row count dropped from {previous_courses} to {candidate_courses}"
        )
    previous_minimum = previous.get("credit_requirements", {}).get(
        "minimum_graduation_credits"
    )
    candidate_minimum = candidate.get("credit_requirements", {}).get(
        "minimum_graduation_credits"
    )
    if previous_minimum is not None and candidate_minimum is None:
        errors.append("minimum graduation credits disappeared")
    return errors


def graduation_rule_coverage(rules: list[dict[str, Any]]) -> dict[str, int | float]:
    department_count = len(rules)
    with_courses = sum(bool(rule.get("courses")) for rule in rules)
    with_minimum = sum(
        rule.get("credit_requirements", {}).get("minimum_graduation_credits")
        is not None
        for rule in rules
    )
    return {
        "department_count": department_count,
        "with_course_table_count": with_courses,
        "with_minimum_graduation_credits_count": with_minimum,
        "course_table_coverage_ratio": (
            with_courses / department_count if department_count else 0.0
        ),
        "minimum_credit_coverage_ratio": (
            with_minimum / department_count if department_count else 0.0
        ),
    }


def parse_official_department_rule(
    html: str,
    *,
    entry_year: str,
    department_code: str,
    department_name: str,
    source_url: str,
    source_hash: str,
    reviewed_at: str,
) -> dict[str, Any]:
    parser = RequirementTableParser()
    parser.feed(html)
    parser.close()
    rows = parser.rows
    source_id = f"official-required-subjects-{entry_year}-{department_code.casefold()}"
    parsed_minimum = parse_graduation_requirement(html)
    minimum_graduation_credits = (
        parsed_minimum["minimum_graduation_credits"] if parsed_minimum else None
    )

    current_category = ""
    current_subgroup = ""
    group_summaries: dict[str, str] = {}
    group_members: dict[str, dict[str, Any]] = {}
    courses_by_name: dict[str, dict[str, Any]] = {}
    course_order: list[str] = []
    regulations: list[str] = []
    remarks: list[str] = []
    parse_warnings: list[str] = []

    for row in rows:
        if not row:
            continue
        label = re.sub(r"\s+", "", row[0])
        if "最低畢業學分數" in label:
            continue
        if label == "修課規定" and len(row) > 1:
            regulations.append(row[1])
            continue
        if label == "備註" and len(row) > 1:
            remarks.append(row[1])
            continue

        if len(row) == 2 and re.search(r"任選|應選|擇", row[1]):
            current_subgroup = _compact(row[0])
            code_match = re.search(r"[【\[]\s*([A-Za-z0-9]+)", row[1])
            if code_match:
                group_summaries[code_match.group(1).upper()] = _compact(row[1])
            continue

        # Official course rows end with 12 year/semester cells and three group cells.
        if len(row) < 16:
            continue
        prefix = row[:-15]
        semester_cells = row[-15:-3]
        group_code = _compact(row[-3]).upper()
        declared_total = _integer(row[-2])
        declared_required = _integer(row[-1])
        if not prefix or not any(_number(value) is not None for value in semester_cells):
            continue

        if len(prefix) >= 3:
            current_category = _compact(prefix[0])
            current_subgroup = _compact(" ".join(prefix[1:-1]))
            course_name = _compact(prefix[-1])
        elif len(prefix) == 2:
            if _looks_like_category(prefix[0]):
                current_category = _compact(prefix[0])
                current_subgroup = ""
            else:
                current_subgroup = _compact(prefix[0])
            course_name = _compact(prefix[1])
        else:
            course_name = _compact(prefix[0])

        if not course_name or not _is_professional_category(current_category):
            continue
        credits, year, semester, placement_notes, placement_manual = _course_placement(
            semester_cells
        )
        course_id = _identifier(department_code, course_name)
        requirement = _course_requirement(current_category, current_subgroup, group_code)
        course = courses_by_name.get(course_name)
        if course is None:
            course = {
                "course_id": course_id,
                "canonical_name_zh": course_name,
                "canonical_name_en": None,
                "known_aliases": [],
                "credits": credits,
                "curriculumRequirement": requirement,
                "recommendedYear": year,
                "recommendedSemester": semester,
                "source_document": source_id,
                "notes": [
                    "官方必修科目表未提供英文課名，故不自行翻譯。",
                    *placement_notes,
                ],
                "alternatives": [],
                "manual_review_required": placement_manual,
            }
            courses_by_name[course_name] = course
            course_order.append(course_name)
        else:
            differences = (
                course["credits"] != credits
                or course["recommendedYear"] != year
                or course["recommendedSemester"] != semester
                or course["curriculumRequirement"] != requirement
            )
            if differences:
                course["manual_review_required"] = True
                course["notes"].append(
                    "同名課程在官方表出現不同學分、年級學期或課程屬性，需人工確認採計方式。"
                )

        if group_code:
            group = group_members.setdefault(
                group_code,
                {
                    "course_ids": [],
                    "declared_totals": set(),
                    "declared_required": set(),
                    "subgroups": set(),
                },
            )
            if course_id not in group["course_ids"]:
                group["course_ids"].append(course_id)
            if declared_total is not None:
                group["declared_totals"].add(declared_total)
            if declared_required is not None:
                group["declared_required"].add(declared_required)
            if current_subgroup:
                group["subgroups"].add(current_subgroup)

    courses = [courses_by_name[name] for name in course_order]
    groups: list[dict[str, Any]] = []
    for code, group in sorted(group_members.items()):
        required_values = group["declared_required"]
        if len(required_values) != 1:
            parse_warnings.append(
                f"分組{code}的應選數不唯一或缺漏：{sorted(required_values)}"
            )
            continue
        minimum_courses = next(iter(required_values))
        course_ids = group["course_ids"]
        if minimum_courses > len(course_ids):
            parse_warnings.append(
                f"分組{code}要求{minimum_courses}門，但僅解析到{len(course_ids)}門。"
            )
            continue
        declared_totals = group["declared_totals"]
        group_notes = [group_summaries[code]] if code in group_summaries else []
        if len(declared_totals) == 1 and next(iter(declared_totals)) != len(course_ids):
            group_notes.append(
                f"官方宣告{next(iter(declared_totals))}科，表列解析為{len(course_ids)}科；保留全部表列課程並人工確認。"
            )
        name = " / ".join(sorted(group["subgroups"])) or f"分組必修 {code}"
        groups.append(
            {
                "group_id": f"{department_code.casefold()}_group_{code.casefold()}",
                "name_zh": f"{name}（{code}組）",
                "name_en": None,
                "rule_kind": "choose_n_from_m",
                "course_ids": course_ids,
                "minimum_courses": minimum_courses,
                "maximum_courses": None,
                "minimum_credits": None,
                "maximum_counted_credits": None,
                "category_requirements": [],
                "counts_toward": "required",
                "source_document": source_id,
                "manual_review_required": bool(group_notes and "人工確認" in group_notes[-1]),
                "notes": group_notes,
            }
        )

    regulation_text = "\n".join(_compact(value) for value in regulations if _compact(value))
    remarks_text = "\n".join(_compact(value) for value in remarks if _compact(value))
    professional_credits = _unique_credit_match(
        regulation_text,
        (
            r"(?:本系|財務管理)?專業必修(?:科目|課程|學分數|學分)?"
            r"\s*(?:共|計|為|[:：,，]?\s*至少(?:需)?修畢)?\s*(\d+(?:\.\d+)?)\s*學分",
        ),
    )
    department_elective_credits = _unique_credit_match(
        regulation_text,
        (
            r"(?:本系|中文系|後醫系|系上|本學程)(?:開設之?)?(?:專業)?選修"
            r"(?:課程|科目)?\s*(?:至少(?:須|需)?(?:包含|修習)?|應至少修習|"
            r"不得(?:低於|少於)|達)?\s*(\d+(?:\.\d+)?)\s*學分",
            r"至少(?:須|需)?包含(?:本系|中文系|後醫系)(?:專業)?選修課程"
            r"\s*(\d+(?:\.\d+)?)\s*學分",
            r"專業選修(?:課程|科目)?\s*[:：]?\s*(?:至少|不得(?:低於|少於))?"
            r"\s*(\d+(?:\.\d+)?)\s*學分",
        ),
    )
    total_elective_credits = _unique_credit_match(
        regulation_text,
        (r"皆須修滿選修\s*(\d+(?:\.\d+)?)\s*學分",),
    )

    track_names: dict[str, str] = {}
    if "一般組" in regulation_text and "量子科技組" in regulation_text:
        track_names = {"a": "一般組", "b": "量子科技組"}
    elif "社統組" in regulation_text and "質性組" in regulation_text:
        track_names = {"a": "社統組", "b": "質性組"}
    if track_names:
        for group in groups:
            code = group["group_id"].rsplit("_", 1)[-1]
            if code not in track_names:
                continue
            group["name_zh"] = f"{track_names[code]}（{code.upper()}組）"
            group["manual_review_required"] = True
            group["notes"].append(
                "此組為學籍軌道別必修，只能套用學生所屬組別；不得同時強制所有組別。"
            )

    manual_rules: list[dict[str, str]] = []
    if courses:
        manual_rules.append(
            _manual_rule(
                department_code,
                "official_table_review_pending",
                "官方必修科目表已轉為結構化候選，但尚未完成逐列雙人覆核。",
                "自動解析可保存表格內容，不能單獨證明跨欄、附註與特殊身分條件已完整建模。",
                source_id,
                "逐列對照官方表，確認課名、學分、建議學期、分組與所有附註後再改為reviewed。",
            )
        )
    for index, clause in enumerate(_department_specific_clauses(regulation_text), start=1):
        if re.search(r"應增加\s*12\s*學分", clause):
            # This clause is represented once in additional_credit_rules below.
            continue
        manual_rules.append(
            _manual_rule(
                department_code,
                f"course_regulation_{index:02d}",
                clause,
                "這是系所特有條款，可能包含組別、領域、授課語言、檢定、抵免或審查條件，現有課程完成狀態不足以自動證明。",
                source_id,
                "依本條所述身分與課程屬性逐項查核；在ClearGrad尚未支援對應屬性前，不可自動標示已完成。",
            )
        )
    if remarks_text and not _is_common_international_remark(remarks_text):
        manual_rules.append(
            _manual_rule(
                department_code,
                "official_remarks",
                remarks_text,
                "備註可能含國際／跨域學習及學年例外；共同規則與系所加嚴條件須人工區分。",
                source_id,
                "與common 113+規則比對，僅將系所特有或更嚴格條件加入系所層。",
            )
        )
    if track_names:
        manual_rules.append(
            _manual_rule(
                department_code,
                "curriculum_track_selection",
                "官方表同時列出多個學籍軌道，各軌道的必修課與學分數不同。",
                "未知學生所屬組別時，同時套用A、B組會虛增必修要求。",
                source_id,
                "先由學生選擇或從學籍取得組別，只套用對應軌道的course_group。",
            )
        )
    if parse_warnings:
        manual_rules.append(
            _manual_rule(
                department_code,
                "parser_warnings",
                "；".join(parse_warnings),
                "官方表格宣告與可安全解析的欄位不一致。",
                source_id,
                "人工檢視原表合併欄位與分組列，在確認前不得自動判定該分組。",
            )
        )
    if not courses:
        manual_rules.append(
            _manual_rule(
                department_code,
                "course_table_unavailable",
                f"官方查詢頁未提供{entry_year}學年度可解析的系所專業課程表。",
                "沒有正式課程列時不得從當學期開課目錄推測畢業規則。",
                source_id,
                "向系辦取得該入學年度正式必修科目表或修業規定後補建。",
            )
        )

    additional_credit_rules: list[dict[str, Any]] = []
    if re.search(r"增加\s*12\s*學分", regulation_text):
        additional_credit_rules.append(
            {
                "rule_id": f"{department_code.casefold()}_foreign_secondary_grade2_equivalent",
                "admission_statuses": [
                    "國外或香港澳門地區同級同類學校畢業年級相當於國內高級中等學校二年級，並以同等學力入學"
                ],
                "additional_credits": 12,
                "source_document": source_id,
                "notes": ["官方修課規定明載在原訂畢業應修學分之外增加12學分。"],
            }
        )

    return {
        "schema_version": "1.0",
        "rule_set_id": f"nsysu-{entry_year}-bachelor-{department_code}",
        "rule_type": "department",
        "entry_year": entry_year,
        "degree_level": "bachelor",
        "department_code": department_code,
        "department_name_zh": department_name,
        "department_name_en": None,
        "common_rule_ref": (
            "../../common/113-plus.json" if int(entry_year) >= 113 else None
        ),
        "review_status": "manual_review_required",
        "reviewed_at": reviewed_at,
        "coverage": "partial",
        "credit_requirements": {
            "minimum_graduation_credits": minimum_graduation_credits,
            "minimum_required_credits": None,
            "minimum_elective_credits": total_elective_credits,
            "minimum_department_elective_credits": department_elective_credits,
            "minimum_department_professional_credits": professional_credits,
        },
        "sources": [
            {
                "source_id": source_id,
                "title": (
                    f"國立中山大學必修科目表（{entry_year}學年度入學新生適用）"
                    f"－{department_name}"
                ),
                "url": source_url,
                "document_type": "html",
                "reviewed_at": reviewed_at,
                "sha256": source_hash,
            }
        ],
        "courses": courses,
        "course_groups": groups,
        "prerequisites": [],
        "non_duplicated_counting_groups": [],
        "manual_review_rules": manual_rules,
        "additional_credit_rules": additional_credit_rules,
    }


def fetch_department_graduation_rules(
    root: Path,
    entry_year: str,
    user_agent: str,
    *,
    preserve_existing: bool = True,
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{3}", entry_year):
        raise ValueError("entry_year must be a three-digit ROC academic year")
    fetcher = Fetcher(user_agent)
    index_response = fetcher.get(DEPARTMENT_INDEX_URL)
    departments = parse_department_options(
        index_response.body.decode("utf-8", errors="strict")
    )
    if not departments:
        raise RuntimeError("official department index returned no bachelor departments")
    codes = [item["department_code"] for item in departments]
    if len(codes) != len(set(codes)):
        raise RuntimeError("official department index contains duplicate bachelor codes")

    destination = root / "data" / "graduation-rules" / entry_year / "bachelor"
    generated: list[tuple[Path, dict[str, Any]]] = []
    preserved: list[str] = []
    unchanged: list[str] = []
    failures: list[str] = []
    reviewed_at = now_iso()[:10]
    for department in departments:
        code = department["department_code"]
        path = destination / f"{code}.json"
        if preserve_existing and path.is_file():
            current = load_json(path, {})
            generated_source = any(
                str(source.get("source_id", "")).startswith("official-required-subjects-")
                for source in current.get("sources", [])
            )
            if not generated_source:
                preserved.append(code)
                continue
        url = requirement_url(entry_year, code)
        try:
            response = fetcher.get(url)
            response_hash = sha256(response.body)
            if path.is_file():
                current = load_json(path, {})
                current_hashes = {
                    source.get("sha256") for source in current.get("sources", [])
                }
                if current_hashes == {response_hash}:
                    unchanged.append(code)
                    continue
            html = response.body.decode("utf-8", errors="strict")
            rule = parse_official_department_rule(
                html,
                entry_year=entry_year,
                department_code=code,
                department_name=department["department_name"],
                source_url=response.url,
                source_hash=response_hash,
                reviewed_at=reviewed_at,
            )
            if path.is_file():
                current = load_json(path, {})
                regressions = graduation_rule_regression_errors(current, rule)
                if regressions:
                    failures.append(f"{code}: " + "; ".join(regressions))
                    continue
            generated.append((path, rule))
        except (RuntimeError, UnicodeDecodeError, ValueError) as error:
            failures.append(f"{code}: {error}")
    if failures:
        raise RuntimeError("failed to fetch detailed graduation rules: " + "; ".join(failures))
    generated_by_code = {rule["department_code"]: rule for _, rule in generated}
    candidate_rules = []
    for department in departments:
        code = department["department_code"]
        candidate = generated_by_code.get(code)
        if candidate is None:
            candidate = load_json(destination / f"{code}.json", {})
        if not candidate:
            raise RuntimeError(f"missing candidate graduation rule for {entry_year} {code}")
        candidate_rules.append(candidate)
    coverage = graduation_rule_coverage(candidate_rules)
    required_coverage = math.ceil(
        coverage["department_count"] * MINIMUM_DEPARTMENT_COVERAGE_RATIO
    )
    if coverage["with_course_table_count"] < required_coverage:
        raise RuntimeError(
            f"{entry_year} course-table coverage fell below safety floor: "
            f"{coverage['with_course_table_count']}/{coverage['department_count']}"
        )
    if coverage["with_minimum_graduation_credits_count"] < required_coverage:
        raise RuntimeError(
            f"{entry_year} minimum-credit coverage fell below safety floor: "
            f"{coverage['with_minimum_graduation_credits_count']}/"
            f"{coverage['department_count']}"
        )
    for path, rule in generated:
        write_json(path, rule)
    return {
        "entry_year": entry_year,
        "department_count": len(departments),
        "generated_count": len(generated),
        "preserved_count": len(preserved),
        "unchanged_count": len(unchanged),
        "preserved_departments": preserved,
        "unchanged_departments": unchanged,
        "parser_version": GRADUATION_RULE_PARSER_VERSION,
        "source_index_url": DEPARTMENT_INDEX_URL,
        "source_index_sha256": sha256(index_response.body),
        "quality": coverage,
    }


def sync_department_graduation_rules(
    root: Path,
    user_agent: str,
    *,
    start_entry_year: str = "112",
    end_entry_year: str | None = None,
    preserve_existing: bool = True,
) -> dict[str, Any]:
    if not re.fullmatch(r"\d{3}", start_entry_year):
        raise ValueError("start_entry_year must be a three-digit ROC academic year")
    if end_entry_year is not None and not re.fullmatch(r"\d{3}", end_entry_year):
        raise ValueError("end_entry_year must be a three-digit ROC academic year")
    if end_entry_year is not None and int(end_entry_year) < int(start_entry_year):
        raise ValueError("end_entry_year cannot be earlier than start_entry_year")

    index_response = Fetcher(user_agent).get(DEPARTMENT_INDEX_URL)
    official_years = parse_entry_year_options(
        index_response.body.decode("utf-8", errors="strict")
    )
    selected_years = [
        year
        for year in official_years
        if int(year) >= int(start_entry_year)
        and (end_entry_year is None or int(year) <= int(end_entry_year))
    ]
    if not selected_years:
        raise RuntimeError(
            f"official index has no entry years from {start_entry_year}"
            + (f" through {end_entry_year}" if end_entry_year else "")
        )

    results = []
    for year in selected_years:
        results.append(
            fetch_department_graduation_rules(
                root,
                year,
                user_agent,
                preserve_existing=preserve_existing,
            )
        )
    for result in results:
        materialize_graduation_requirements_from_rules(
            root,
            result["entry_year"],
            result["source_index_sha256"],
        )
    return {
        "start_entry_year": start_entry_year,
        "end_entry_year": end_entry_year,
        "official_entry_years": official_years,
        "synced_entry_years": selected_years,
        "latest_entry_year": selected_years[-1],
        "years": results,
        "source_index_url": DEPARTMENT_INDEX_URL,
        "source_index_sha256": sha256(index_response.body),
    }
