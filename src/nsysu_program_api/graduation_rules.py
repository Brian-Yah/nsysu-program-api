from __future__ import annotations

import shutil
from pathlib import Path

from .core import load_json, write_json
from .graduation_ai_review import (
    PENDING_REVIEW_SUFFIX,
    apply_graduation_ai_review_audit,
    graduation_rule_disqualifiers,
)

SCHEMA_VERSION = "1.0"
COMMON_SCHEMA = "graduation-common-rule.schema.json"
DEPARTMENT_SCHEMA = "graduation-department-rule.schema.json"


def validate_department_references(rule: dict) -> list[str]:
    """Validate relationships that JSON Schema cannot express."""
    errors: list[str] = []
    source_ids = [source.get("source_id") for source in rule.get("sources", [])]
    course_ids = [course.get("course_id") for course in rule.get("courses", [])]
    group_ids = [group.get("group_id") for group in rule.get("course_groups", [])]
    manual_ids = [item.get("rule_id") for item in rule.get("manual_review_rules", [])]
    additional_ids = [item.get("rule_id") for item in rule.get("additional_credit_rules", [])]
    source_id_set = set(source_ids)
    course_id_set = set(course_ids)

    minimum_graduation_credits = rule.get("credit_requirements", {}).get(
        "minimum_graduation_credits"
    )
    if minimum_graduation_credits is None and (
        rule.get("review_status") != "manual_review_required"
        or rule.get("coverage") != "partial"
        or not rule.get("manual_review_rules")
    ):
        errors.append(
            "unknown minimum_graduation_credits requires partial manual review rules"
        )
    if not rule.get("courses") and (
        rule.get("review_status") != "manual_review_required"
        or rule.get("coverage") != "partial"
        or not rule.get("manual_review_rules")
    ):
        errors.append("empty course rules require partial manual review rules")

    for label, values in (
        ("source_id", source_ids),
        ("course_id", course_ids),
        ("course group_id", group_ids),
        ("manual rule_id", manual_ids),
        ("additional credit rule_id", additional_ids),
    ):
        if len(values) != len(set(values)):
            errors.append(f"duplicate {label}")

    def check_source(owner: str, source_id: str | None) -> None:
        if source_id not in source_id_set:
            errors.append(f"{owner} references missing source_document {source_id}")

    for course in rule.get("courses", []):
        owner = f"course {course.get('course_id')}"
        check_source(owner, course.get("source_document"))
        for alternative in course.get("alternatives", []):
            check_source(f"{owner} alternative", alternative.get("source_document"))
    for group in rule.get("course_groups", []):
        owner = f"course group {group.get('group_id')}"
        check_source(owner, group.get("source_document"))
        missing = sorted(set(group.get("course_ids", [])) - course_id_set)
        if missing:
            errors.append(f"{owner} references missing courses {missing}")
        minimum = group.get("minimum_courses")
        maximum = group.get("maximum_courses")
        option_count = len(group.get("course_ids", []))
        if minimum is not None and minimum > option_count:
            errors.append(f"{owner} minimum_courses exceeds course count")
        if maximum is not None and maximum > option_count:
            errors.append(f"{owner} maximum_courses exceeds course count")
        if minimum is not None and maximum is not None and minimum > maximum:
            errors.append(f"{owner} minimum_courses exceeds maximum_courses")
        rule_kind = group.get("rule_kind")
        if rule_kind == "choose_n_from_m" and (minimum is None or option_count == 0):
            errors.append(f"{owner} choose_n_from_m needs courses and minimum_courses")
        if rule_kind == "minimum_credits" and group.get("minimum_credits") is None:
            errors.append(f"{owner} minimum_credits rule lacks minimum_credits")
        if rule_kind == "maximum_counted_credits" and group.get("maximum_counted_credits") is None:
            errors.append(f"{owner} maximum_counted_credits rule lacks maximum_counted_credits")
        if rule_kind == "cross_category" and not group.get("category_requirements"):
            errors.append(f"{owner} cross_category rule lacks category_requirements")
    for prerequisite in rule.get("prerequisites", []):
        owner = f"prerequisite for {prerequisite.get('course_id')}"
        check_source(owner, prerequisite.get("source_document"))
        referenced = {
            prerequisite.get("course_id"),
            *prerequisite.get("prerequisite_course_ids", []),
        }
        missing = sorted(referenced - course_id_set)
        if missing:
            errors.append(f"{owner} references missing courses {missing}")
    for group in rule.get("non_duplicated_counting_groups", []):
        owner = f"non-duplicated group {group.get('group_id')}"
        check_source(owner, group.get("source_document"))
        missing = sorted(set(group.get("course_ids", [])) - course_id_set)
        if missing:
            errors.append(f"{owner} references missing courses {missing}")
        if group.get("maximum_counted_courses", 0) > len(group.get("course_ids", [])):
            errors.append(f"{owner} maximum_counted_courses exceeds course count")
    for manual in rule.get("manual_review_rules", []):
        check_source(f"manual rule {manual.get('rule_id')}", manual.get("source_document"))
    for additional in rule.get("additional_credit_rules", []):
        check_source(
            f"additional credit rule {additional.get('rule_id')}",
            additional.get("source_document"),
        )
    if rule.get("review_status") == "ai_approved":
        review = rule.get("ai_review", {})
        if rule.get("coverage") != "complete":
            errors.append("ai_approved rule requires complete coverage")
        if review.get("decision") != "ai_approved":
            errors.append("ai_approved rule requires matching ai_review decision")
        blockers = graduation_rule_disqualifiers(rule)
        if blockers:
            errors.append(f"ai_approved rule has blockers {blockers}")
        if any(
            str(item.get("rule_id") or "").endswith(PENDING_REVIEW_SUFFIX)
            for item in rule.get("manual_review_rules", [])
        ):
            errors.append("ai_approved rule still contains pending table review")
    return errors


def validate_common_references(rule: dict) -> list[str]:
    source_ids = {source.get("source_id") for source in rule.get("sources", [])}
    referenced: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            source_document = value.get("source_document")
            if isinstance(source_document, str):
                referenced.add(source_document)
            source_refs = value.get("source_refs")
            if isinstance(source_refs, list):
                referenced.update(item for item in source_refs if isinstance(item, str))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(rule.get("requirements", {}))
    missing = sorted(referenced - source_ids)
    return [f"common rule references missing sources {missing}"] if missing else []


def build_graduation_rules_api(root: Path) -> dict:
    source_root = root / "data" / "graduation-rules"
    common_path = source_root / "common" / "113-plus.json"
    common = load_json(common_path, None)
    if not common:
        raise RuntimeError(f"missing common graduation rules: {common_path}")
    common_errors = validate_common_references(common)
    if common_errors:
        raise RuntimeError("; ".join(common_errors))

    department_paths = sorted(source_root.glob("[0-9][0-9][0-9]/bachelor/*.json"))
    departments: list[dict] = []
    for path in department_paths:
        rule = load_json(path, None)
        if not rule:
            raise RuntimeError(f"invalid department graduation rules: {path}")
        errors = validate_department_references(rule)
        if errors:
            raise RuntimeError(f"{path}: {'; '.join(errors)}")
        departments.append(rule)

    approved_by_ai = 0
    for year in sorted({rule["entry_year"] for rule in departments}):
        year_rules = [rule for rule in departments if rule["entry_year"] == year]
        approved_by_ai += apply_graduation_ai_review_audit(root, year, year_rules)
        for rule in year_rules:
            errors = validate_department_references(rule)
            if errors:
                raise RuntimeError(
                    f"AI-reviewed {rule['department_code']}: {'; '.join(errors)}"
                )

    api = root / "api" / "v1" / "graduation-rules"
    write_json(api / "common" / "113-plus.json", common)
    expected_by_year: dict[str, set[str]] = {}
    for rule in departments:
        year = rule["entry_year"]
        code = rule["department_code"]
        expected_by_year.setdefault(year, set()).add(code)
        write_json(api / year / "bachelor" / f"{code}.json", rule)

    for year_directory in api.glob("[0-9][0-9][0-9]"):
        bachelor_directory = year_directory / "bachelor"
        expected_codes = expected_by_year.get(year_directory.name, set())
        for path in bachelor_directory.glob("*.json"):
            if path.stem not in expected_codes:
                path.unlink()

    schema_dest = root / "api" / "v1" / "schemas"
    schema_dest.mkdir(parents=True, exist_ok=True)
    for name in (COMMON_SCHEMA, DEPARTMENT_SCHEMA):
        shutil.copyfile(root / "schemas" / name, schema_dest / name)

    years = sorted(expected_by_year)
    index = {
        "schema_version": SCHEMA_VERSION,
        "latest_entry_year": years[-1] if years else None,
        "entry_years": years,
        "entry_year_summary": [
            {
                "entry_year": year,
                "department_count": sum(
                    rule["entry_year"] == year for rule in departments
                ),
                "minimum_graduation_credits_available_count": sum(
                    rule["entry_year"] == year
                    and rule.get("credit_requirements", {}).get(
                        "minimum_graduation_credits"
                    )
                    is not None
                    for rule in departments
                ),
                "ai_approved_department_count": sum(
                    rule["entry_year"] == year
                    and rule.get("review_status") == "ai_approved"
                    for rule in departments
                ),
                "manual_review_required_department_count": sum(
                    rule["entry_year"] == year
                    and rule.get("review_status") == "manual_review_required"
                    for rule in departments
                ),
            }
            for year in years
        ],
        "degree_levels": ["bachelor"],
        "department_count": len(departments),
        "reviewed_department_count": sum(
            rule.get("review_status") == "reviewed" for rule in departments
        ),
        "ai_approved_department_count": sum(
            rule.get("review_status") == "ai_approved" for rule in departments
        ),
        "manual_review_required_department_count": sum(
            rule.get("review_status") == "manual_review_required" for rule in departments
        ),
        "paths": {
            "common_113_plus": "common/113-plus.json",
            "department_template": "{entry_year}/bachelor/{department_code}.json",
            "latest_department_template": (
                f"{years[-1]}/bachelor/{{department_code}}.json" if years else None
            ),
            "common_schema": "../schemas/graduation-common-rule.schema.json",
            "department_schema": "../schemas/graduation-department-rule.schema.json",
        },
        "departments": [
            {
                "entry_year": rule["entry_year"],
                "degree_level": rule["degree_level"],
                "department_code": rule["department_code"],
                "department_name_zh": rule["department_name_zh"],
                "department_name_en": rule["department_name_en"],
                "review_status": rule["review_status"],
                "coverage": rule["coverage"],
                "blocking_reasons": rule.get("ai_review", {}).get(
                    "blocking_reason_details", []
                ),
                "path": (f"{rule['entry_year']}/bachelor/{rule['department_code']}.json"),
            }
            for rule in departments
        ],
    }
    write_json(api / "index.json", index)
    if approved_by_ai != index["ai_approved_department_count"]:
        raise RuntimeError("AI-approved graduation-rule count does not match index")
    return index
