from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / "schemas/program.schema.json").read_text(encoding="utf-8"))
validator = Draft202012Validator(schema, format_checker=FormatChecker())
errors = []
ids = set()
for path in (root / "data/published").glob("*/*.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("program_id") in ids:
        errors.append(f"{path}: duplicate program_id {data['program_id']}")
    ids.add(data.get("program_id"))
    errors.extend(f"{path}: {error.message}" for error in validator.iter_errors(data))
    courses = data.get("course_catalog", [])
    course_names = {course.get("course_name_snapshot") for course in courses}
    entry_ids = {course.get("catalog_entry_id") for course in courses}
    program_course_names = {
        course.get("program_course_name_snapshot")
        for course in courses
        if course.get("program_course_name_snapshot")
    }
    constraint_ids = set()
    requirements = data.get("structured_requirements", {})
    constraint_groups = (
        requirements.get("course_count_constraints", []),
        requirements.get("entry_selection_constraints", []),
        requirements.get("program_course_selection_constraints", []),
        requirements.get("no_double_count_constraints", []),
        requirements.get("named_group_selection_constraints", []),
        requirements.get("credit_constraints", []),
    )
    for group in constraint_groups:
        for constraint in group:
            constraint_id = constraint.get("constraint_id")
            if constraint_id in constraint_ids:
                errors.append(f"{path}: duplicate constraint_id {constraint_id}")
            constraint_ids.add(constraint_id)
    for constraint in requirements.get("course_count_constraints", []):
        constraint_id = constraint.get("constraint_id")
        names = constraint.get("course_names", [])
        if constraint.get("max_courses", 0) > len(names):
            errors.append(f"{path}: {constraint_id} max_courses exceeds course_names")
        missing = sorted(set(names) - course_names)
        if missing:
            errors.append(f"{path}: {constraint_id} references missing courses {missing}")
        missing_entries = sorted(set(constraint.get("catalog_entry_ids", [])) - entry_ids)
        if missing_entries:
            errors.append(f"{path}: {constraint_id} references missing entries {missing_entries}")
        subject = constraint.get("program_course_name_snapshot")
        if subject and subject not in program_course_names:
            errors.append(f"{path}: {constraint_id} references missing subject {subject}")
    for constraint in requirements.get("entry_selection_constraints", []):
        constraint_id = constraint.get("constraint_id")
        referenced = constraint.get("catalog_entry_ids", [])
        missing_entries = sorted(set(referenced) - entry_ids)
        if missing_entries:
            errors.append(f"{path}: {constraint_id} references missing entries {missing_entries}")
        max_entries = constraint.get("max_entries")
        if max_entries is not None and max_entries > len(referenced):
            errors.append(f"{path}: {constraint_id} max_entries exceeds entries")
        if max_entries is not None and constraint.get("min_entries", 0) > max_entries:
            errors.append(f"{path}: {constraint_id} min_entries exceeds max_entries")
        counted_maximum = constraint.get("max_entries_counted_for_requirement")
        if counted_maximum is not None and counted_maximum < constraint.get(
            "min_entries", 0
        ):
            errors.append(f"{path}: {constraint_id} counted maximum is below minimum")
        if counted_maximum is not None and counted_maximum > len(referenced):
            errors.append(f"{path}: {constraint_id} counted maximum exceeds entries")
        destination = constraint.get("excess_credit_destination")
        if (counted_maximum is None) != (destination is None):
            errors.append(f"{path}: {constraint_id} overflow fields must be paired")
    for constraint in requirements.get("program_course_selection_constraints", []):
        constraint_id = constraint.get("constraint_id")
        subjects = constraint.get("program_course_names", [])
        missing_subjects = sorted(set(subjects) - program_course_names)
        if missing_subjects:
            errors.append(f"{path}: {constraint_id} references missing subjects {missing_subjects}")
        if constraint.get("max_program_courses", 0) > len(subjects):
            errors.append(f"{path}: {constraint_id} max_program_courses exceeds subjects")
        if constraint.get("min_program_courses", 0) > constraint.get(
            "max_program_courses", 0
        ):
            errors.append(f"{path}: {constraint_id} program minimum exceeds maximum")
    for constraint in requirements.get("named_group_selection_constraints", []):
        constraint_id = constraint.get("constraint_id")
        options = constraint.get("options", [])
        if constraint.get("max_groups", 0) > len(options):
            errors.append(f"{path}: {constraint_id} max_groups exceeds options")
        if constraint.get("min_groups", 0) > constraint.get("max_groups", 0):
            errors.append(f"{path}: {constraint_id} group minimum exceeds maximum")
        for option in options:
            missing_entries = sorted(set(option.get("catalog_entry_ids", [])) - entry_ids)
            if missing_entries:
                errors.append(
                    f"{path}: {constraint_id} references missing entries {missing_entries}"
                )
    labels = {
        course.get("requirement_label")
        for course in courses
        if course.get("requirement_label")
    }
    for constraint in requirements.get("credit_constraints", []):
        constraint_id = constraint.get("constraint_id")
        missing_labels = sorted(
            set(constraint.get("scope", {}).get("requirement_labels", [])) - labels
        )
        if missing_labels:
            errors.append(
                f"{path}: {constraint_id} references missing requirement labels "
                f"{missing_labels}"
            )
        missing_scope_entries = sorted(
            set(constraint.get("scope", {}).get("catalog_entry_ids", [])) - entry_ids
        )
        if missing_scope_entries:
            errors.append(
                f"{path}: {constraint_id} scope references missing entries "
                f"{missing_scope_entries}"
            )
        missing_program_courses = sorted(
            set(constraint.get("scope", {}).get("program_course_names", []))
            - program_course_names
        )
        if missing_program_courses:
            errors.append(
                f"{path}: {constraint_id} scope references missing program courses "
                f"{missing_program_courses}"
            )
        minimum = constraint.get("minimum_credits")
        maximum = constraint.get("maximum_counted_credits")
        if minimum is not None and maximum is not None and minimum > maximum:
            errors.append(f"{path}: {constraint_id} minimum exceeds counted maximum")
    minimum_core = requirements.get("minimum_core_credits")
    maximum_core = requirements.get("maximum_core_credits")
    if minimum_core is not None and maximum_core is not None and minimum_core > maximum_core:
        errors.append(f"{path}: minimum_core_credits exceeds maximum_core_credits")
    legacy_core = requirements.get("core_credits_text_value")
    if minimum_core is not None and legacy_core != minimum_core:
        errors.append(f"{path}: core_credits_text_value must mirror minimum_core_credits")
    conflicts = requirements.get("source_conflicts", [])
    unresolved = False
    total_unresolved = False
    for conflict in conflicts:
        candidate_ids = [
            candidate.get("candidate_id") for candidate in conflict.get("candidates", [])
        ]
        if len(candidate_ids) != len(set(candidate_ids)):
            errors.append(f"{path}: {conflict.get('conflict_id')} has duplicate candidates")
        selected = conflict.get("selected_candidate_id")
        status = conflict.get("resolution_status")
        if status == "unresolved":
            unresolved = True
            total_unresolved = total_unresolved or (
                conflict.get("semantic_key") == "program.minimum_total_credits"
            )
            if selected is not None or conflict.get("resolution_note") is not None:
                errors.append(
                    f"{path}: {conflict.get('conflict_id')} unresolved conflict has resolution"
                )
        elif status == "resolved":
            if selected not in candidate_ids:
                errors.append(
                    f"{path}: {conflict.get('conflict_id')} selects a missing candidate"
                )
            if not str(conflict.get("resolution_note") or "").strip():
                errors.append(
                    f"{path}: {conflict.get('conflict_id')} resolved conflict lacks note"
                )
            if conflict.get("semantic_key") == "program.minimum_total_credits":
                selected_value = next(
                    (
                        candidate.get("value")
                        for candidate in conflict.get("candidates", [])
                        if candidate.get("candidate_id") == selected
                    ),
                    None,
                )
                if requirements.get("minimum_total_credits") != selected_value:
                    errors.append(
                        f"{path}: resolved total conflict does not match canonical minimum"
                    )
    if unresolved and data.get("review_status") in {"approved", "ai_approved"}:
        errors.append(f"{path}: unresolved source conflict cannot be approved")
    summary = requirements.get("completion_summary", {})
    if summary:
        expected_total = (
            None if total_unresolved else requirements.get("minimum_total_credits")
        )
        if summary.get("minimum_total_credits") != expected_total:
            errors.append(f"{path}: completion summary total is inconsistent")
    if data.get("review_status") == "ai_approved":
        forbidden = (
            "entry_selection_constraints",
            "course_count_constraints",
            "program_course_selection_constraints",
            "named_group_selection_constraints",
            "no_double_count_constraints",
            "manual_requirements",
            "source_conflicts",
        )
        if any(requirements.get(key) for key in forbidden):
            errors.append(f"{path}: ai_approved program contains a special rule")
        if summary.get("model_status") != "complete":
            errors.append(f"{path}: ai_approved program must have a complete model")
    for requirement in requirements.get("manual_requirements", []):
        satisfaction = requirement.get("satisfaction")
        options = requirement.get("options")
        if (satisfaction is None) != (options is None):
            errors.append(
                f"{path}: {requirement.get('requirement_id')} satisfaction/options mismatch"
            )
        if satisfaction == "any_of" and len(options or []) < 2:
            errors.append(
                f"{path}: {requirement.get('requirement_id')} any_of needs two options"
            )
        requirement_type = requirement.get("requirement_type")
        if requirement_type == "activity_hours" and (
            "minimum_hours" not in requirement or "minimum_count" in requirement
        ):
            errors.append(
                f"{path}: {requirement.get('requirement_id')} invalid activity hours fields"
            )
        if requirement_type == "activity_count" and (
            "minimum_count" not in requirement or "minimum_hours" in requirement
        ):
            errors.append(
                f"{path}: {requirement.get('requirement_id')} invalid activity count fields"
            )
if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"Validated {len(ids)} published programs")

requirement_schema_path = root / "schemas/graduation-requirement.schema.json"
collection_schema_path = root / "schemas/graduation-requirements.schema.json"
graduation_root = root / "data/graduation-requirements"
if (
    requirement_schema_path.exists()
    and collection_schema_path.exists()
    and graduation_root.exists()
):
    requirement_schema = json.loads(requirement_schema_path.read_text(encoding="utf-8"))
    collection_schema = json.loads(collection_schema_path.read_text(encoding="utf-8"))
    registry = Registry().with_resource(
        requirement_schema["$id"], Resource.from_contents(requirement_schema)
    )
    collection_validator = Draft202012Validator(
        collection_schema, format_checker=FormatChecker(), registry=registry
    )
    requirement_validator = Draft202012Validator(requirement_schema, format_checker=FormatChecker())
    graduation_errors = []
    graduation_count = 0
    for path in graduation_root.glob("*/bachelor.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        graduation_errors.extend(
            f"{path}: {error.message}" for error in collection_validator.iter_errors(data)
        )
        if data.get("department_count") != len(data.get("requirements", [])):
            graduation_errors.append(f"{path}: department_count does not match requirements")
        for requirement in data.get("requirements", []):
            graduation_count += 1
            graduation_errors.extend(
                f"{path} ({requirement.get('department_code')}): {error.message}"
                for error in requirement_validator.iter_errors(requirement)
            )
    if graduation_errors:
        print("\n".join(graduation_errors))
        sys.exit(1)
    print(f"Validated {graduation_count} graduation requirements")
