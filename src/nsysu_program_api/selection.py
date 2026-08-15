from __future__ import annotations

import hashlib
import re
import unicodedata

COUNT_TEXT_VALUES = {
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
COUNT_TOKEN = r"[一二三四五六七八九十兩\d]+"
CHOOSE_RATIO_PATTERN = re.compile(
    rf"(?P<options>{COUNT_TOKEN})\s*擇\s*(?P<select>{COUNT_TOKEN})"
)
CHOOSE_COUNT_PATTERN = re.compile(
    rf"(?:任選|選擇)\s*(?P<select>{COUNT_TOKEN})\s*(?:門|科)"
)
CHOOSE_ONE_PATTERN = re.compile(r"擇\s*一\s*(?:修習|門|科)?")
PROGRAM_COURSE_SELECTION_PATTERN = re.compile(
    rf"(?P<options>{COUNT_TOKEN})\s*學程科目\s*應選\s*(?P<select>{COUNT_TOKEN})\s*門"
)
NO_DOUBLE_COUNT_PATTERN = re.compile(
    r"(?:不得|不再|不可|勿再)\s*重複\s*(?:採計|計入|抵免)"
)
ONLY_ONE_PROGRAM_COURSE_PATTERN = re.compile(
    r"每一(?:學程|課程)科目\s*僅採計\s*一門課程\s*學分"
)
NAMED_GROUP_SELECTION_PATTERN = re.compile(
    rf"(?P<labels>[\u4e00-\u9fff]+(?:/[\u4e00-\u9fff]+){{1,}})"
    rf"(?P<declared>{COUNT_TOKEN})領域擇一修習至少(?P<credits>\d+(?:\.5)?)學分"
)


def normalized_key(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", value)).casefold()


def selection_text(value: str) -> str:
    """Normalize PDF wrapping while retaining punctuation meaningful to rules."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def parse_count(value: str) -> int | None:
    value = unicodedata.normalize("NFKC", value).strip()
    if value.isdigit():
        return int(value)
    return COUNT_TEXT_VALUES.get(value)


def constraint_id(kind: str, *parts: object) -> str:
    identity = "|".join([kind, *(str(part) for part in parts)])
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f"constraint_{digest}"


def split_course_names(
    name_raw: str, credit_count: int, notes: str, unit_raw: str = ""
) -> list[str]:
    """Split explicit alternative/compound course cells without guessing aliases."""
    lines = [
        unicodedata.normalize("NFKC", line).strip()
        for line in str(name_raw or "").splitlines()
        if line.strip()
    ]
    if not lines:
        return []
    if credit_count > 1 and len(lines) == credit_count:
        return lines

    joined = "".join(lines)
    numbered = [
        part.strip()
        for part in re.findall(r"(?:^|\s)\d+[.、]\s*(.*?)(?=(?:\s\d+[.、])|$)", str(name_raw), re.S)
        if part.strip()
    ]
    if len(numbered) > 1:
        return [re.sub(r"\s+", "", part) for part in numbered]
    normalized_notes = selection_text(notes)
    ratio = CHOOSE_RATIO_PATTERN.search(normalized_notes)
    choose_count = CHOOSE_COUNT_PATTERN.search(normalized_notes)
    declared_options = parse_count(ratio.group("options")) if ratio else None
    unit_parts = [
        part
        for part in re.split(r"[/、,，\n]+", unicodedata.normalize("NFKC", unit_raw))
        if part.strip()
    ]
    cell_selects_one = bool(
        (ratio and declared_options == len(lines))
        or (
            (len(unit_parts) >= len(lines) or all(len(line) >= 3 for line in lines))
            and (
                (choose_count and parse_count(choose_count.group("select")) == 1)
                or CHOOSE_ONE_PATTERN.search(normalized_notes)
            )
        )
    )
    if len(lines) > 1 and cell_selects_one:
        return lines
    if "、或" in joined:
        parts = [part.strip() for part in re.split(r"\s*、\s*(?:或\s*)?", joined) if part.strip()]
        if len(parts) > 1:
            return parts
    return [joined]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _row_entry_ids(row: dict) -> list[str]:
    return _unique([course["catalog_entry_id"] for course in row.get("courses", [])])


def _row_course_names(row: dict) -> list[str]:
    return _unique([course["course_name_snapshot"] for course in row.get("courses", [])])


def _same_scope(origin: dict, candidate: dict) -> bool:
    return (
        candidate.get("table_id") == origin.get("table_id")
        and candidate.get("requirement_section") == origin.get("requirement_section")
    )


def _has_selection_marker(text: str) -> bool:
    text = selection_text(text)
    return bool(
        CHOOSE_RATIO_PATTERN.search(text)
        or CHOOSE_COUNT_PATTERN.search(text)
        or CHOOSE_ONE_PATTERN.search(text)
    )


def _forward_course_rows(rows: list[dict], index: int, include_same_choose_one: bool) -> list[dict]:
    origin = rows[index]
    selected: list[dict] = []
    origin_text = selection_text(origin.get("rule_text", ""))
    for candidate in rows[index:]:
        if not _same_scope(origin, candidate):
            break
        candidate_text = selection_text(candidate.get("rule_text", ""))
        if candidate is not origin and _has_selection_marker(candidate_text):
            if not (
                include_same_choose_one
                and origin.get("courses")
                and candidate.get("courses")
                and CHOOSE_ONE_PATTERN.search(origin_text)
                and CHOOSE_ONE_PATTERN.search(candidate_text)
            ):
                break
        if candidate.get("is_summary"):
            break
        if candidate.get("courses"):
            selected.append(candidate)
    return selected


def _backward_course_rows(rows: list[dict], index: int) -> list[dict]:
    origin = rows[index]
    selected: list[dict] = []
    for candidate in reversed(rows[:index]):
        if not _same_scope(origin, candidate):
            break
        if candidate.get("is_summary"):
            break
        if _has_selection_marker(candidate.get("rule_text", "")):
            break
        if candidate.get("courses"):
            selected.insert(0, candidate)
    return selected


def _entry_selection_constraint(
    selected_rows: list[dict],
    select_count: int,
    declared_option_count: int | None,
    source_row: dict,
) -> dict | None:
    entry_ids = _unique(
        [entry_id for row in selected_rows for entry_id in _row_entry_ids(row)]
    )
    course_names = _unique(
        [name for row in selected_rows for name in _row_course_names(row)]
    )
    if not entry_ids or select_count < 1 or select_count > len(entry_ids):
        return None
    section = source_row.get("requirement_section", "unspecified")
    source_text = source_row.get("rule_text", "")
    return {
        "constraint_id": constraint_id("select_entries", select_count, *entry_ids),
        "kind": "select_entries",
        "catalog_entry_ids": entry_ids,
        "course_names": course_names,
        "min_entries": select_count,
        "max_entries": select_count,
        "declared_option_count": declared_option_count,
        "option_count_matches": (
            declared_option_count is None or declared_option_count == len(entry_ids)
        ),
        "requirement_group": source_row.get("requirement_group", "unspecified"),
        "requirement_section": section,
        "source_page": source_row["source_page"],
        "source_text": source_text,
        "validation_status": "source_text_match",
    }


def _course_selection_constraint(
    courses: list[dict], select_count: int, source_row: dict, source_text: str
) -> dict | None:
    names = _unique([course["course_name_snapshot"] for course in courses])
    if len(names) < 2 or select_count < 1 or select_count > len(names):
        return None
    return {
        "constraint_id": constraint_id("select_courses", select_count, *names),
        "kind": "select_courses",
        "course_names": names,
        "min_courses": select_count,
        "max_courses": select_count,
        "requirement_group": source_row.get("requirement_group", "unspecified"),
        "requirement_section": source_row.get("requirement_section", "unspecified"),
        "source_page": source_row["source_page"],
        "source_text": source_text,
        "validation_status": "source_text_match",
    }


def _dedupe(constraints: list[dict], identity_field: str) -> list[dict]:
    by_identity: dict[tuple, dict] = {}
    for item in constraints:
        identity = (
            item["kind"],
            tuple(item.get(identity_field, [])),
            item.get("min_entries"),
            item.get("max_entries"),
            item.get("min_courses"),
            item.get("max_courses"),
            item.get("program_course_name_snapshot"),
        )
        current = by_identity.get(identity)
        if current is None or len(item["source_text"]) < len(current["source_text"]):
            by_identity[identity] = item
    return sorted(
        by_identity.values(), key=lambda item: (item["source_page"], item["constraint_id"])
    )


def _drop_redundant_entry_subsets(constraints: list[dict]) -> list[dict]:
    result = []
    for constraint in constraints:
        entry_ids = set(constraint["catalog_entry_ids"])
        redundant = any(
            constraint is not other
            and constraint.get("min_entries") == other.get("min_entries")
            and constraint.get("max_entries") == other.get("max_entries")
            and normalized_key(constraint.get("source_text", ""))
            == normalized_key(other.get("source_text", ""))
            and entry_ids < set(other["catalog_entry_ids"])
            for other in constraints
        )
        if not redundant:
            result.append(constraint)
    return result


def build_selection_requirements(
    courses: list[dict], table_rows: list[dict], version_pages: list[tuple[int, str]]
) -> dict[str, list[dict]]:
    course_constraints: list[dict] = []
    entry_constraints: list[dict] = []
    program_course_constraints: list[dict] = []
    no_double_count_constraints: list[dict] = []
    named_group_constraints: list[dict] = []

    for row in table_rows:
        row_courses = row.get("courses", [])
        entry_groups: dict[str, list[dict]] = {}
        for course in row_courses:
            entry_groups.setdefault(course["catalog_entry_id"], []).append(course)
        for entry_courses in entry_groups.values():
            if len(entry_courses) > 1:
                constraint = _course_selection_constraint(
                    entry_courses, 1, row, row.get("raw_course_name", "")
                )
                if constraint:
                    constraint["kind"] = "course_equivalence"
                    course_constraints.append(constraint)

    for index, row in enumerate(table_rows):
        text = row.get("rule_text", "")
        normalized_text = selection_text(text)
        named_group = NAMED_GROUP_SELECTION_PATTERN.search(normalized_text)
        if named_group:
            labels = named_group.group("labels").split("/")
            declared = parse_count(named_group.group("declared"))
            candidate_rows = []
            for candidate in table_rows[index:]:
                if not _same_scope(row, candidate) or candidate.get("is_summary"):
                    break
                if candidate is not row and candidate.get("notes"):
                    break
                if candidate.get("courses"):
                    candidate_rows.append(candidate)
            candidate_courses = [
                candidate_course
                for candidate in candidate_rows
                for candidate_course in candidate["courses"]
            ]
            first_label = labels[0]
            for suffix_length in range(2, len(first_label) + 1):
                suffix = first_label[-suffix_length:]
                if any(
                    suffix in candidate_course["course_name_snapshot"]
                    for candidate_course in candidate_courses
                ):
                    labels[0] = suffix
                    break
            option_courses: list[list[dict]] = [[] for _ in labels]
            unassigned = []
            for candidate in candidate_rows:
                for candidate_course in candidate["courses"]:
                    matched = False
                    for option_index, label in enumerate(labels[:-1]):
                        if label in candidate_course["course_name_snapshot"]:
                            option_courses[option_index].append(candidate_course)
                            matched = True
                            break
                    if not matched:
                        unassigned.append(candidate_course)
            option_courses[-1].extend(unassigned)
            options = []
            for label, grouped_courses in zip(labels, option_courses, strict=True):
                options.append(
                    {
                        "name": label,
                        "catalog_entry_ids": _unique(
                            [course["catalog_entry_id"] for course in grouped_courses]
                        ),
                        "course_names": _unique(
                            [course["course_name_snapshot"] for course in grouped_courses]
                        ),
                    }
                )
            if declared == len(labels) and all(option["catalog_entry_ids"] for option in options):
                named_group_constraints.append(
                    {
                        "constraint_id": constraint_id(
                            "select_named_groups",
                            *labels,
                            named_group.group("credits"),
                        ),
                        "kind": "select_named_groups",
                        "options": options,
                        "min_groups": 1,
                        "max_groups": 1,
                        "minimum_credits_per_selected_group": float(
                            named_group.group("credits")
                        ),
                        "requirement_group": row.get(
                            "requirement_group", "unspecified"
                        ),
                        "requirement_section": row.get(
                            "requirement_section", "unspecified"
                        ),
                        "source_page": row["source_page"],
                        "source_text": text,
                        "validation_status": "source_text_match",
                    }
                )
            continue
        ratio = CHOOSE_RATIO_PATTERN.search(normalized_text)
        choose_count = CHOOSE_COUNT_PATTERN.search(normalized_text)
        choose_one = CHOOSE_ONE_PATTERN.search(normalized_text)
        if ratio:
            declared = parse_count(ratio.group("options"))
            selected_count = parse_count(ratio.group("select"))
            if selected_count is None:
                continue
            selected_rows = _forward_course_rows(table_rows, index, False)
            if row.get("courses") and declared:
                limited_rows = []
                entry_count = 0
                for selected_row in selected_rows:
                    limited_rows.append(selected_row)
                    entry_count += len(_row_entry_ids(selected_row))
                    if entry_count >= declared:
                        break
                selected_rows = limited_rows
            selected_entries = _unique(
                [
                    entry_id
                    for selected_row in selected_rows
                    for entry_id in _row_entry_ids(selected_row)
                ]
            )
            if len(selected_entries) == 1 and len(row.get("courses", [])) >= 2:
                constraint = _course_selection_constraint(
                    row["courses"], selected_count, row, text
                )
                if constraint:
                    course_constraints.append(constraint)
                continue
            constraint = _entry_selection_constraint(
                selected_rows, selected_count, declared, row
            )
            if constraint:
                entry_constraints.append(constraint)
        elif choose_count:
            selected_count = parse_count(choose_count.group("select"))
            if selected_count is None:
                continue
            selected_rows = (
                _forward_course_rows(table_rows, index, False)
                if row.get("courses")
                else _backward_course_rows(table_rows, index)
            )
            selected_entries = _unique(
                [
                    entry_id
                    for selected_row in selected_rows
                    for entry_id in _row_entry_ids(selected_row)
                ]
            )
            if len(selected_entries) == 1 and len(row.get("courses", [])) >= 2:
                constraint = _course_selection_constraint(
                    row["courses"], selected_count, row, text
                )
                if constraint:
                    course_constraints.append(constraint)
                continue
            constraint = _entry_selection_constraint(
                selected_rows, selected_count, None, row
            )
            if constraint:
                entry_constraints.append(constraint)
        elif choose_one:
            selected_rows = _forward_course_rows(table_rows, index, True)
            constraint = _entry_selection_constraint(selected_rows, 1, None, row)
            if constraint:
                entry_constraints.append(constraint)

        subject_selection = PROGRAM_COURSE_SELECTION_PATTERN.search(normalized_text)
        if subject_selection:
            declared = parse_count(subject_selection.group("options"))
            selected_count = parse_count(subject_selection.group("select"))
            if selected_count is None:
                continue
            section = row.get("requirement_section", "unspecified")
            subject_names = _unique(
                [
                    course.get("program_course_name_snapshot")
                    for course in courses
                    if course.get("requirement_section") == section
                ]
            )
            if subject_names and selected_count <= len(subject_names):
                program_course_constraints.append(
                    {
                        "constraint_id": constraint_id(
                            "select_program_courses", selected_count, *subject_names
                        ),
                        "kind": "select_program_courses",
                        "program_course_names": subject_names,
                        "min_program_courses": selected_count,
                        "max_program_courses": selected_count,
                        "declared_option_count": declared,
                        "option_count_matches": (
                            declared is None or declared == len(subject_names)
                        ),
                        "requirement_group": row.get(
                            "requirement_group", "unspecified"
                        ),
                        "requirement_section": section,
                        "source_page": row["source_page"],
                        "source_text": text,
                        "validation_status": "source_text_match",
                    }
                )

    table_rule_text = " ".join(row.get("rule_text", "") for row in table_rows)
    if ONLY_ONE_PROGRAM_COURSE_PATTERN.search(selection_text(table_rule_text)):
        subjects = _unique(
            [course.get("program_course_name_snapshot") for course in courses]
        )
        for subject in subjects:
            subject_courses = [
                course
                for course in courses
                if course.get("program_course_name_snapshot") == subject
            ]
            names = _unique([course["course_name_snapshot"] for course in subject_courses])
            entry_ids = _unique([course["catalog_entry_id"] for course in subject_courses])
            if len(entry_ids) < 2:
                continue
            source_page = min(course["source_page"] for course in subject_courses)
            source_text = "每一學程科目僅採計一門課程學分。"
            course_constraints.append(
                {
                    "constraint_id": constraint_id(
                        "program_course_equivalence", subject, *entry_ids
                    ),
                    "kind": "program_course_equivalence",
                    "catalog_entry_ids": entry_ids,
                    "course_names": names,
                    "min_courses": 1,
                    "max_courses": 1,
                    "requirement_group": subject_courses[0]["requirement_group"],
                    "requirement_section": subject_courses[0]["requirement_section"],
                    "program_course_name_snapshot": subject,
                    "source_page": source_page,
                    "source_text": source_text,
                    "validation_status": "source_text_match",
                }
            )

    for page_number, page_text in version_pages:
        compact = re.sub(r"\s+", " ", page_text)
        for match in NO_DOUBLE_COUNT_PATTERN.finditer(compact):
            sentence_start = max(
                compact.rfind("。", 0, match.start()),
                compact.rfind("；", 0, match.start()),
                compact.rfind(";", 0, match.start()),
            )
            endings = [
                value
                for value in (
                    compact.find("。", match.end()),
                    compact.find("；", match.end()),
                    compact.find(";", match.end()),
                )
                if value >= 0
            ]
            sentence_end = min(endings) + 1 if endings else match.end()
            source_text = compact[sentence_start + 1 : sentence_end].strip()
            no_double_count_constraints.append(
                {
                    "constraint_id": constraint_id("no_double_count", source_text),
                    "kind": "no_double_count",
                    "requirement_groups": ["core", "elective"],
                    "max_count_per_course": 1,
                    "source_page": page_number,
                    "source_text": source_text,
                    "validation_status": "source_text_match",
                }
            )

    course_constraints = _dedupe(course_constraints, "course_names")
    entry_constraints = _dedupe(entry_constraints, "catalog_entry_ids")
    entry_constraints = _drop_redundant_entry_subsets(entry_constraints)
    program_course_constraints = _dedupe(
        program_course_constraints, "program_course_names"
    )
    no_double_count_constraints = _dedupe(
        no_double_count_constraints, "requirement_groups"
    )

    for constraint in course_constraints:
        names = set(constraint["course_names"])
        for course in courses:
            if course["course_name_snapshot"] in names:
                note = constraint["source_text"]
                if note and note not in course.get("notes", ""):
                    course["notes"] = " ".join(
                        value for value in (course.get("notes", ""), note) if value
                    )
    for constraint in entry_constraints:
        entry_ids = set(constraint["catalog_entry_ids"])
        for course in courses:
            if course["catalog_entry_id"] in entry_ids:
                note = constraint["source_text"]
                if note and note not in course.get("notes", ""):
                    course["notes"] = " ".join(
                        value for value in (course.get("notes", ""), note) if value
                    )

    result = {}
    if course_constraints:
        result["course_count_constraints"] = course_constraints
    if entry_constraints:
        result["entry_selection_constraints"] = entry_constraints
    if program_course_constraints:
        result["program_course_selection_constraints"] = program_course_constraints
    if no_double_count_constraints:
        result["no_double_count_constraints"] = no_double_count_constraints
    if named_group_constraints:
        result["named_group_selection_constraints"] = sorted(
            named_group_constraints,
            key=lambda item: (item["source_page"], item["constraint_id"]),
        )
    return result
