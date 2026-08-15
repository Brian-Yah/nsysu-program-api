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
    course_names = {
        course.get("course_name_snapshot") for course in data.get("course_catalog", [])
    }
    constraint_ids = set()
    constraints = data.get("structured_requirements", {}).get(
        "course_count_constraints", []
    )
    for constraint in constraints:
        constraint_id = constraint.get("constraint_id")
        if constraint_id in constraint_ids:
            errors.append(f"{path}: duplicate constraint_id {constraint_id}")
        constraint_ids.add(constraint_id)
        names = constraint.get("course_names", [])
        if constraint.get("max_courses", 0) > len(names):
            errors.append(f"{path}: {constraint_id} max_courses exceeds course_names")
        missing = sorted(set(names) - course_names)
        if missing:
            errors.append(f"{path}: {constraint_id} references missing courses {missing}")
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
    requirement_validator = Draft202012Validator(
        requirement_schema, format_checker=FormatChecker()
    )
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
