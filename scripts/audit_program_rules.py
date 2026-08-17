#!/usr/bin/env python3
"""Audit published program rules for duplicates, missing baselines, and vague manual items."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

POLICY_ID = "nsysu_program_policy_2026_08_16"
BASELINES = {
    "integrated_program": {"minimum_total_credits": 15.0, "outside_credits": 6.0},
    "department_professional_program": {
        "minimum_total_credits": 15.0,
        "minimum_professional_module_credits": 9.0,
        "maximum_cross_college_elective_credits": 6.0,
    },
    "micro_program": {"minimum_total_credits": 9.0, "outside_credits": 3.0},
}
RULE_COLLECTIONS = (
    "credit_constraints",
    "entry_selection_constraints",
    "course_count_constraints",
    "program_course_selection_constraints",
    "named_group_selection_constraints",
    "no_double_count_constraints",
    "manual_requirements",
)
EVIDENCE_FIELDS = {
    "constraint_id",
    "requirement_id",
    "source_page",
    "source_text",
    "source_url",
    "source_kind",
    "validation_status",
    "source_evidence",
}
VAGUE_MANUAL_PATTERN = re.compile(r"(?:需人工確認|人工判斷|依規定辦理|另行認定|其他規定|相關規定)$")


def load_programs(root: Path, version: str) -> list[dict]:
    directory = root / "data" / "published" / version
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("prog_*.json"))
    ]


def _semantic_signature(item: dict) -> str:
    semantic = {key: value for key, value in item.items() if key not in EVIDENCE_FIELDS}
    return json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _outside_minimum(requirements: dict) -> float:
    values = []
    required_affiliations = {"home_department", "double_major", "minor"}
    for constraint in requirements.get("credit_constraints", []):
        if constraint.get("kind") != "minimum_credits":
            continue
        scope = constraint.get("scope", {})
        excluded = set(scope.get("excluded_affiliations", []))
        if (
            scope.get("kind") == "course_eligibility"
            and required_affiliations <= excluded
            and scope.get("excluded_course_roles") == ["all"]
        ):
            values.append(float(constraint.get("minimum_credits", 0)))
    return max(values, default=0.0)


def _professional_module_minimum(requirements: dict) -> float:
    values = []
    for constraint in requirements.get("credit_constraints", []):
        if constraint.get("kind") != "minimum_credits":
            continue
        scope = constraint.get("scope", {})
        groups = set(scope.get("requirement_groups", []))
        labels = {str(value).replace(" ", "") for value in scope.get("requirement_labels", [])}
        if (
            scope.get("kind") == "professional_module"
            or "core" in groups
            or any("專業模組" in value for value in labels)
        ):
            values.append(float(constraint.get("minimum_credits", 0)))
    return max(values, default=0.0)


def _cross_college_elective_cap(requirements: dict) -> float | None:
    values = []
    for constraint in requirements.get("credit_constraints", []):
        if constraint.get("kind") != "maximum_counted_credits":
            continue
        scope = constraint.get("scope", {})
        if "cross_college_general_education" in scope.get(
            "course_attributes", []
        ) and "cross_college_bachelor" in scope.get("student_categories", []):
            values.append(float(constraint["maximum_counted_credits"]))
    return min(values) if values else None


def audit_program(program: dict) -> list[dict]:
    findings = []
    requirements = program.get("structured_requirements", {})
    program_id = program["program_id"]

    if POLICY_ID not in program.get("institutional_policy_ids", []):
        findings.append({"kind": "missing_institutional_policy_ref"})

    baseline = BASELINES[program["type"]]
    total = requirements.get("minimum_total_credits")
    if total is None or float(total) < baseline["minimum_total_credits"]:
        findings.append(
            {
                "kind": "missing_or_weak_total_baseline",
                "actual": total,
                "expected_minimum": baseline["minimum_total_credits"],
            }
        )

    outside = baseline.get("outside_credits")
    if outside is not None and _outside_minimum(requirements) < outside:
        findings.append(
            {
                "kind": "missing_or_weak_outside_baseline",
                "actual": _outside_minimum(requirements),
                "expected_minimum": outside,
            }
        )

    if program["type"] == "department_professional_program":
        module_minimum = _professional_module_minimum(requirements)
        if module_minimum < baseline["minimum_professional_module_credits"]:
            findings.append(
                {
                    "kind": "missing_or_weak_professional_module_baseline",
                    "actual": module_minimum,
                    "expected_minimum": baseline["minimum_professional_module_credits"],
                }
            )
        cap = _cross_college_elective_cap(requirements)
        if cap is None or cap > baseline["maximum_cross_college_elective_credits"]:
            findings.append(
                {
                    "kind": "missing_or_weak_cross_college_elective_cap",
                    "actual": cap,
                    "expected_maximum": baseline["maximum_cross_college_elective_credits"],
                }
            )

    for collection in RULE_COLLECTIONS:
        rules = requirements.get(collection, [])
        counts = Counter(_semantic_signature(rule) for rule in rules)
        for signature, count in counts.items():
            if count > 1:
                findings.append(
                    {
                        "kind": "duplicate_rule",
                        "collection": collection,
                        "count": count,
                        "semantic_signature": json.loads(signature),
                    }
                )

    entry_ids = [item.get("catalog_entry_id") for item in program.get("course_catalog", [])]
    for entry_id, count in Counter(entry_ids).items():
        if entry_id and count > 1:
            findings.append(
                {"kind": "duplicate_catalog_entry_id", "catalog_entry_id": entry_id, "count": count}
            )

    known_ids = set(entry_ids)
    for course in program.get("course_catalog", []):
        group_id = course.get("catalog_entry_group_id")
        if group_id and group_id not in known_ids:
            findings.append(
                {
                    "kind": "orphan_catalog_entry_group_id",
                    "catalog_entry_id": course.get("catalog_entry_id"),
                    "catalog_entry_group_id": group_id,
                }
            )

    course_signatures = []
    for course in program.get("course_catalog", []):
        course_signatures.append(
            (
                tuple(course.get("opening_units", [])),
                course.get("course_name_snapshot"),
                course.get("credits_snapshot"),
                course.get("requirement_group"),
                course.get("requirement_section"),
                course.get("requirement_label"),
                course.get("program_course_name_snapshot"),
            )
        )
    for signature, count in Counter(course_signatures).items():
        if count > 1:
            findings.append(
                {
                    "kind": "duplicate_course_row",
                    "count": count,
                    "course_name": signature[1],
                    "opening_units": list(signature[0]),
                }
            )

    for manual in requirements.get("manual_requirements", []):
        description = re.sub(r"\s+", " ", manual.get("description", "")).strip()
        if len(description) < 18 or VAGUE_MANUAL_PATTERN.search(description):
            findings.append(
                {
                    "kind": "vague_manual_requirement",
                    "requirement_id": manual.get("requirement_id"),
                    "description": description,
                }
            )

    return [
        {"program_id": program_id, "name_zh": program["name_zh"], **finding} for finding in findings
    ]


def audit(root: Path, version: str) -> dict:
    programs = load_programs(root, version)
    findings = [finding for program in programs for finding in audit_program(program)]
    return {
        "schema_version": 1,
        "academic_version": version,
        "program_count": len(programs),
        "finding_count": len(findings),
        "counts": dict(sorted(Counter(item["kind"] for item in findings).items())),
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--academic-version", default="115-1")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = audit(args.root.resolve(), args.academic_version)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("program_count", "finding_count", "counts")},
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and report["finding_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
