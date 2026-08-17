import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "data" / "published" / "115-1"
POLICY_ID = "nsysu_program_policy_2026_08_16"


def programs() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(PUBLISHED.glob("prog_*.json"))
    ]


def credit_values(program: dict, *, kind: str, scope_kind: str) -> list[float]:
    return [
        float(item["minimum_credits" if kind == "minimum_credits" else "maximum_counted_credits"])
        for item in program["structured_requirements"].get("credit_constraints", [])
        if item.get("kind") == kind and item.get("scope", {}).get("kind") == scope_kind
    ]


def test_all_142_programs_inherit_the_versioned_institutional_policy() -> None:
    items = programs()
    assert Counter(item["type"] for item in items) == {
        "integrated_program": 63,
        "department_professional_program": 5,
        "micro_program": 74,
    }
    assert all(POLICY_ID in item["institutional_policy_ids"] for item in items)


def test_universal_credit_baselines_are_effective() -> None:
    for item in programs():
        requirements = item["structured_requirements"]
        if item["type"] == "integrated_program":
            assert requirements["minimum_total_credits"] >= 15
            outside = [
                rule["minimum_credits"]
                for rule in requirements["credit_constraints"]
                if rule.get("scope", {}).get("kind") == "course_eligibility"
                and {"home_department", "double_major", "minor"}
                <= set(rule["scope"].get("excluded_affiliations", []))
            ]
            assert max(outside) >= 6
        elif item["type"] == "micro_program":
            assert requirements["minimum_total_credits"] >= 9
            outside = [
                rule["minimum_credits"]
                for rule in requirements["credit_constraints"]
                if rule.get("scope", {}).get("kind") == "course_eligibility"
                and {"home_department", "double_major", "minor"}
                <= set(rule["scope"].get("excluded_affiliations", []))
            ]
            assert max(outside) >= 3
        else:
            assert requirements["minimum_total_credits"] >= 15
            assert (
                max(credit_values(item, kind="minimum_credits", scope_kind="professional_module"))
                >= 9
            )
            caps = [
                rule["maximum_counted_credits"]
                for rule in requirements["credit_constraints"]
                if rule.get("scope", {}).get("kind") == "course_attributes"
                and "cross_college_general_education" in rule["scope"].get("course_attributes", [])
                and "cross_college_bachelor" in rule["scope"].get("student_categories", [])
            ]
            assert min(caps) <= 6


def test_catalog_entry_ids_are_unique_and_groups_resolve() -> None:
    for item in programs():
        courses = item["course_catalog"]
        ids = [course["catalog_entry_id"] for course in courses]
        assert len(ids) == len(set(ids)), item["program_id"]
        known = set(ids)
        assert all(
            not course.get("catalog_entry_group_id") or course["catalog_entry_group_id"] in known
            for course in courses
        ), item["program_id"]


def test_financial_engineering_keeps_stronger_pdf_rules_without_duplicates() -> None:
    item = next(
        program for program in programs() if program["program_id"] == "prog_b6ba18c54c2d55bc"
    )
    requirements = item["structured_requirements"]
    assert requirements["minimum_total_credits"] == 21
    program_minimums = credit_values(item, kind="minimum_credits", scope_kind="program")
    assert program_minimums == [21]
    outside = [
        (rule["minimum_credits"], rule["scope"].get("student_categories"))
        for rule in requirements["credit_constraints"]
        if rule.get("scope", {}).get("kind") == "course_eligibility"
    ]
    assert (9, ["general_student"]) in outside
    assert (6, ["double_major_or_minor_student"]) in outside
    assert (6, None) in outside
